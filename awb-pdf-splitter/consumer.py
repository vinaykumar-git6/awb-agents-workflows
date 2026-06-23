"""Service Bus consumer: listen to the splitter queue, split PDFs, store results.

For each message on the queue (an Event Grid `Microsoft.Storage.BlobCreated`
event carrying a POINTER to the uploaded PDF), this worker:

  1. Reads the blob URL from the event (data.url).
  2. Downloads the source PDF from Blob Storage (managed identity, keyless).
  3. Splits it into one PDF per AWB number.
  4. Writes each split PDF to:
        awb-input/pdf/<timestamp>/splitted-awb/<awb>.pdf
     where <timestamp> is taken from the source blob path
        awb-input/pdf/<timestamp>/<original>.pdf

The actual file is never carried on the queue — only the pointer.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient

import db_events
import storage
from splitter import split_pdf

logger = logging.getLogger("awb.consumer")

# Errors that mean the referenced blob is not a usable PDF. These messages will
# never succeed on retry, so they are dead-lettered instead of abandoned.
try:
    from pypdf.errors import PdfReadError, PdfStreamError

    PERMANENT_ERRORS: tuple[type[Exception], ...] = (
        PdfReadError,
        PdfStreamError,
        ValueError,
        json.JSONDecodeError,
    )
except Exception:  # noqa: BLE001 - pypdf always present, but stay defensive
    PERMANENT_ERRORS = (ValueError, json.JSONDecodeError)

# Fully qualified namespace, e.g. awb-sb-ek.servicebus.windows.net
SERVICEBUS_NAMESPACE = os.getenv("SERVICEBUS_NAMESPACE", "")
QUEUE_NAME = os.getenv("SERVICEBUS_QUEUE", "aws-splitter-q")
# Sub-folder under the source timestamp folder where split AWBs are written.
SPLIT_SUBFOLDER = os.getenv("SPLIT_SUBFOLDER", "splitted-awb")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID") or None


def _credential() -> DefaultAzureCredential:
    if AZURE_CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
    return DefaultAzureCredential()


def is_enabled() -> bool:
    return bool(SERVICEBUS_NAMESPACE)


def extract_blob_url(message_body: str) -> str | None:
    """Pull the blob URL out of an Event Grid BlobCreated event message."""
    payload = json.loads(message_body)
    # Event Grid may deliver a single event object or an array of events.
    event = payload[0] if isinstance(payload, list) else payload
    data = event.get("data", {})
    url = data.get("url")
    if not url:
        return None
    if event.get("eventType") and event["eventType"] != "Microsoft.Storage.BlobCreated":
        return None
    return url


def dest_prefix_for(blob_url: str) -> str:
    """Build awb-input/pdf/<timestamp>/splitted-awb from the source blob path.

    Source path: <container>/pdf/<timestamp>/<filename>.pdf
    Returns the in-container prefix: pdf/<timestamp>/<SPLIT_SUBFOLDER>
    """
    path = urlparse(blob_url).path.lstrip("/")
    parts = path.split("/")  # [container, 'pdf', timestamp, filename.pdf]
    # Drop container and filename; keep the middle folders (pdf/<timestamp>/...).
    middle = parts[1:-1] if len(parts) > 2 else parts[1:]
    return "/".join(middle + [SPLIT_SUBFOLDER])


def process_message(message_body: str) -> list[str]:
    """Handle one queue message end-to-end. Returns the blob paths written."""
    blob_url = extract_blob_url(message_body)
    if not blob_url:
        logger.warning("Message had no usable blob URL; skipping.")
        return []

    blob_path = urlparse(blob_url).path.lstrip("/")
    # Skip our own split outputs to avoid an infinite re-processing loop, and
    # ignore anything that is not a PDF (e.g. images/html uploaded under pdf/).
    if f"/{SPLIT_SUBFOLDER}/" in f"/{blob_path}":
        logger.info("Skipping split output (avoids recursion): %s", blob_path)
        return []
    if not blob_path.lower().endswith(".pdf"):
        logger.info("Skipping non-PDF blob: %s", blob_path)
        return []

    doc_id = db_events.doc_id_from_url(blob_url)
    document_name = blob_path.rsplit("/", 1)[-1]

    # State transition: the parent PDF has arrived and splitting is starting.
    db_events.publish(
        {
            "stage": "awb_input",
            "state": "inprogress",
            "docId": doc_id,
            "documentName": document_name,
            "source": "splitter",
            "blobUrl": blob_url,
            "blobName": blob_path,
        }
    )

    try:
        logger.info("Processing blob: %s", blob_url)
        pdf_bytes = storage.download_blob(blob_url)
        files = split_pdf(pdf_bytes)
        if not files:
            logger.warning("No AWB numbers detected in %s", blob_url)
            db_events.publish(
                {
                    "stage": "awb_input",
                    "state": "failed",
                    "docId": doc_id,
                    "documentName": document_name,
                    "source": "splitter",
                    "blobUrl": blob_url,
                    "blobName": blob_path,
                    "errorDetail": "No AWB numbers detected in document.",
                }
            )
            return []

        dest_prefix = dest_prefix_for(blob_url)
        written = storage.upload_to_prefix(files, dest_prefix)
        logger.info("Wrote %d split AWB(s) under %s", len(written), dest_prefix)
    except Exception as exc:  # noqa: BLE001 - report failure, then re-raise
        logger.exception("Splitting failed for %s", blob_url)
        db_events.publish(
            {
                "stage": "awb_input",
                "state": "failed",
                "docId": doc_id,
                "documentName": document_name,
                "source": "splitter",
                "blobUrl": blob_url,
                "blobName": blob_path,
                "errorDetail": str(exc)[:500],
            }
        )
        raise

    # Parent document split successfully.
    db_events.publish(
        {
            "stage": "awb_input",
            "state": "completed",
            "docId": doc_id,
            "documentName": document_name,
            "source": "splitter",
            "blobUrl": blob_url,
            "blobName": blob_path,
            "pageCount": len(files),
        }
    )

    # One completed awb_split row per detected AWB, linked to the parent docId.
    for container_path in written:
        child_name = container_path.rsplit("/", 1)[-1]  # <awb>.pdf
        awb_number = child_name.rsplit(".pdf", 1)[0]
        db_events.publish(
            {
                "stage": "awb_split",
                "state": "completed",
                "docId": doc_id,
                "parentDocId": doc_id,
                "awbNumber": awb_number,
                "documentName": child_name,
                "source": "splitter",
                "blobUrl": db_events.blob_url_for(container_path),
                "blobName": container_path,
            }
        )

    return written


def run() -> None:
    """Blocking loop that consumes messages until the process stops."""
    if not is_enabled():
        logger.info("SERVICEBUS_NAMESPACE not set; consumer disabled.")
        return

    logger.info(
        "Starting Service Bus consumer on %s / %s", SERVICEBUS_NAMESPACE, QUEUE_NAME
    )
    # Run forever. The receiver iterator returns after max_wait_time seconds of
    # idle, so we wrap it in an outer loop to keep listening indefinitely. The
    # client/receiver are also recreated on connection errors so the worker
    # self-heals instead of dying.
    while True:
        try:
            client = ServiceBusClient(SERVICEBUS_NAMESPACE, credential=_credential())
            with client:
                receiver = client.get_queue_receiver(
                    queue_name=QUEUE_NAME, max_wait_time=30
                )
                with receiver:
                    for message in receiver:
                        try:
                            process_message(str(message))
                            receiver.complete_message(message)
                        except PERMANENT_ERRORS as exc:  # poison: do not retry
                            logger.exception("Invalid message content; dead-lettering.")
                            receiver.dead_letter_message(
                                message,
                                reason="InvalidContent",
                                error_description=str(exc)[:200],
                            )
                        except Exception:  # noqa: BLE001 - transient: retry later
                            logger.exception("Transient failure; abandoning for retry.")
                            receiver.abandon_message(message)
        except Exception:  # noqa: BLE001 - connection-level error: reconnect
            logger.exception("Service Bus receiver error; reconnecting in 5s.")
            time.sleep(5)


def start_background() -> threading.Thread | None:
    """Start the consumer in a daemon thread (used by the FastAPI app)."""
    if not is_enabled():
        logger.info("SERVICEBUS_NAMESPACE not set; background consumer not started.")
        return None
    thread = threading.Thread(target=run, name="sb-consumer", daemon=True)
    thread.start()
    return thread

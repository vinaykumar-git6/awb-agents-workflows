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
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient

import storage
from splitter import split_pdf

logger = logging.getLogger("awb.consumer")

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

    logger.info("Processing blob: %s", blob_url)
    pdf_bytes = storage.download_blob(blob_url)
    files = split_pdf(pdf_bytes)
    if not files:
        logger.warning("No AWB numbers detected in %s", blob_url)
        return []

    dest_prefix = dest_prefix_for(blob_url)
    written = storage.upload_to_prefix(files, dest_prefix)
    logger.info("Wrote %d split AWB(s) under %s", len(written), dest_prefix)
    return written


def run() -> None:
    """Blocking loop that consumes messages until the process stops."""
    if not is_enabled():
        logger.info("SERVICEBUS_NAMESPACE not set; consumer disabled.")
        return

    logger.info(
        "Starting Service Bus consumer on %s / %s", SERVICEBUS_NAMESPACE, QUEUE_NAME
    )
    client = ServiceBusClient(SERVICEBUS_NAMESPACE, credential=_credential())
    with client:
        receiver = client.get_queue_receiver(queue_name=QUEUE_NAME, max_wait_time=30)
        with receiver:
            for message in receiver:
                try:
                    process_message(str(message))
                    receiver.complete_message(message)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to process message; abandoning.")
                    receiver.abandon_message(message)


def start_background() -> threading.Thread | None:
    """Start the consumer in a daemon thread (used by the FastAPI app)."""
    if not is_enabled():
        logger.info("SERVICEBUS_NAMESPACE not set; background consumer not started.")
        return None
    thread = threading.Thread(target=run, name="sb-consumer", daemon=True)
    thread.start()
    return thread

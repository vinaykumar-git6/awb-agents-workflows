"""Publish stage/state events to the async-db-update-q queue.

Each event is a JSON message consumed by the awb-db-updater worker, which
upserts rows into the PostgreSQL `skycargo` schema. Publishing is best-effort:
a failure here is logged but never breaks the splitting pipeline.

Authentication is keyless (managed identity) via DefaultAzureCredential.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

logger = logging.getLogger("awb.dbevents")

SERVICEBUS_NAMESPACE = os.getenv("SERVICEBUS_NAMESPACE", "")
DB_UPDATE_QUEUE = os.getenv("DB_UPDATE_QUEUE", "async-db-update-q")
BLOB_ACCOUNT_URL = os.getenv("BLOB_ACCOUNT_URL", "")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID") or None


def _credential() -> DefaultAzureCredential:
    if AZURE_CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
    return DefaultAzureCredential()


def is_enabled() -> bool:
    return bool(SERVICEBUS_NAMESPACE)


def doc_id_from_url(blob_url: str) -> str:
    """Derive the shared parent docId from a blob URL.

    Both the source PDF (``<container>/pdf/<timestamp>/<file>.pdf``) and the
    split outputs (``<container>/pdf/<timestamp>/splitted-awb/<awb>.pdf``) share
    the ``pdf/<timestamp>`` prefix, so it is a stable key linking parent + child
    rows across the splitter and the OCR worker.
    """
    path = urlparse(blob_url).path.lstrip("/")
    parts = path.split("/")  # [container, 'pdf', '<timestamp>', ...]
    if len(parts) >= 3:
        return f"{parts[1]}/{parts[2]}"
    return path


def blob_url_for(container_relative_path: str) -> str | None:
    """Build a full https blob URL from ``<container>/<path>`` if possible."""
    if not BLOB_ACCOUNT_URL:
        return None
    return f"{BLOB_ACCOUNT_URL.rstrip('/')}/{container_relative_path.lstrip('/')}"


def publish(message: dict[str, Any]) -> None:
    """Send a single db-update message. Never raises (best-effort)."""
    if not is_enabled():
        logger.debug("DB events disabled (SERVICEBUS_NAMESPACE not set).")
        return
    try:
        client = ServiceBusClient(SERVICEBUS_NAMESPACE, credential=_credential())
        with client:
            sender = client.get_queue_sender(queue_name=DB_UPDATE_QUEUE)
            with sender:
                sender.send_messages(
                    ServiceBusMessage(
                        json.dumps(message), content_type="application/json"
                    )
                )
        logger.info(
            "Published db-update stage=%s state=%s doc=%s awb=%s",
            message.get("stage"),
            message.get("state"),
            message.get("docId"),
            message.get("awbNumber") or "-",
        )
    except Exception:  # noqa: BLE001 - best-effort, do not break the pipeline
        logger.exception("Failed to publish db-update message (continuing).")

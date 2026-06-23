"""Service Bus consumer for async-db-update-q.

For each message it parses a DbUpdateMessage and upserts the relevant rows in
the `skycargo` schema (managed-identity / keyless auth to both Service Bus and
PostgreSQL).

Reliability model (mirrors awb-ocr-worker):
  * PERMANENT errors (bad JSON / invalid contract) -> dead-letter immediately.
  * TRANSIENT errors (DB unavailable, token refresh, networking):
      retry with EXPONENTIAL BACKOFF by re-scheduling the message with an
      increasing delay (tracked via the `attempt` application property).
      After MAX_ATTEMPTS the message is dead-lettered.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage

from . import db
from .logging_setup import get_logger
from .messages import DbUpdateMessage

logger = get_logger("awb.db.consumer")

SERVICEBUS_NAMESPACE = os.getenv("SERVICEBUS_NAMESPACE", "")
QUEUE_NAME = os.getenv("SERVICEBUS_QUEUE", "async-db-update-q")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID") or None

MAX_ATTEMPTS = int(os.getenv("DB_MAX_ATTEMPTS", "5"))
BACKOFF_BASE_SECONDS = float(os.getenv("DB_BACKOFF_BASE_SECONDS", "10"))
BACKOFF_MAX_SECONDS = float(os.getenv("DB_BACKOFF_MAX_SECONDS", "600"))

# Errors that will never succeed on retry -> dead-letter straight away.
PERMANENT_ERRORS: tuple[type[Exception], ...] = (ValueError, json.JSONDecodeError)


def is_enabled() -> bool:
    return bool(SERVICEBUS_NAMESPACE)


def _credential() -> DefaultAzureCredential:
    if AZURE_CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
    return DefaultAzureCredential()


def parse_message(body: str) -> DbUpdateMessage:
    payload = json.loads(body)
    if isinstance(payload, list):
        payload = payload[0]
    return DbUpdateMessage.from_dict(payload)


def process_message(body: str) -> None:
    message = parse_message(body)
    db.apply_update(message)


def _attempt_of(message) -> int:
    props = message.application_properties or {}
    raw = props.get(b"attempt") or props.get("attempt") or 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _backoff_seconds(attempt: int) -> float:
    return min(BACKOFF_BASE_SECONDS * (2 ** attempt), BACKOFF_MAX_SECONDS)


def _reschedule(sender, message, attempt: int) -> None:
    delay = _backoff_seconds(attempt)
    enqueue_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
    clone = ServiceBusMessage(
        str(message),
        application_properties={"attempt": attempt + 1},
        content_type=message.content_type,
    )
    sender.schedule_messages(clone, enqueue_at)
    logger.warning(
        "Transient failure; rescheduled attempt %d in %.0fs.", attempt + 1, delay
    )


def run() -> None:
    if not is_enabled():
        logger.info("SERVICEBUS_NAMESPACE not set; consumer disabled.")
        return
    if not db.is_enabled():
        logger.warning("PGHOST/PGUSER not set; DB writes will fail.")

    logger.info("Starting DB updater on %s / %s", SERVICEBUS_NAMESPACE, QUEUE_NAME)
    while True:
        try:
            client = ServiceBusClient(SERVICEBUS_NAMESPACE, credential=_credential())
            with client:
                receiver = client.get_queue_receiver(
                    queue_name=QUEUE_NAME, max_wait_time=30
                )
                sender = client.get_queue_sender(queue_name=QUEUE_NAME)
                with receiver, sender:
                    for message in receiver:
                        attempt = _attempt_of(message)
                        try:
                            process_message(str(message))
                            receiver.complete_message(message)
                        except PERMANENT_ERRORS as exc:
                            logger.exception("Permanent error; dead-lettering.")
                            receiver.dead_letter_message(
                                message,
                                reason="PermanentError",
                                error_description=str(exc)[:200],
                            )
                        except Exception as exc:  # noqa: BLE001 - transient
                            if attempt + 1 >= MAX_ATTEMPTS:
                                logger.exception(
                                    "Retries exhausted (%d); dead-lettering.", attempt
                                )
                                receiver.dead_letter_message(
                                    message,
                                    reason="RetriesExhausted",
                                    error_description=str(exc)[:200],
                                )
                            else:
                                _reschedule(sender, message, attempt)
                                receiver.complete_message(message)
        except Exception:  # noqa: BLE001 - connection-level error: reconnect
            logger.exception("Service Bus receiver error; reconnecting in 5s.")
            time.sleep(5)


def start_background() -> threading.Thread | None:
    if not is_enabled():
        logger.info("SERVICEBUS_NAMESPACE not set; background consumer not started.")
        return None
    thread = threading.Thread(target=run, name="db-updater", daemon=True)
    thread.start()
    return thread

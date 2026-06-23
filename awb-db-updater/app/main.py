"""FastAPI host exposing a health probe and running the DB-updater consumer.

The Service Bus consumer runs in a background thread so the container has an
HTTP surface for Container Apps health checks while it processes messages.
"""
from __future__ import annotations

from fastapi import FastAPI

from app import consumer
from app.logging_setup import configure

configure()

app = FastAPI(title="AWB DB Updater", version="1.0.0")


@app.on_event("startup")
def _start_consumer() -> None:
    consumer.start_background()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

"""Message contract for the async-db-update-q queue.

Producers across the pipeline publish one JSON message per state transition.
The worker upserts the corresponding row(s) into the `skycargo` schema.

Example message::

    {
        "stage": "awb_split",          # awb_input | awb_split | awb_output
        "state": "completed",          # inprogress | completed | failed
        "docId": "DOC-2026-0001",      # logical parent document id
        "awbNumber": "176-12345678",   # omit/empty for the awb_input parent row
        "parentDocId": "DOC-2026-0001",# defaults to docId
        "documentName": "manifest.pdf",
        "flightNumber": "EK0123",
        "date": "2026-06-23",
        "source": "sharepoint",
        "pageRange": [0, 1],
        "blobUrl": "https://.../176-12345678.pdf",
        "blobName": "176-12345678.pdf",
        "outputJsonUrl": "https://.../176-12345678.json",
        "outputMdUrl": "https://.../176-12345678.md",
        "ocrEngine": "document_intelligence",
        "ocrConfidence": 0.97,
        "validationPassed": true,
        "failedCheckCount": 0,
        "pageCount": 2,
        "attempt": 0,
        "errorDetail": null,
        "receivedAt": "2026-06-23T09:00:00Z",
        "processedAt": "2026-06-23T09:00:12Z",
        "processingSeconds": 12.3
    }
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

VALID_STAGES = {"awb_input", "awb_split", "awb_output"}
VALID_STATES = {"inprogress", "completed", "failed"}


@dataclass
class DbUpdateMessage:
    stage: str
    state: str
    doc_id: str
    awb_number: str = ""
    parent_doc_id: str | None = None
    document_name: str | None = None
    flight_number: str | None = None
    doc_date: str | None = None
    source: str | None = None
    page_range: list[int] = field(default_factory=list)
    blob_url: str | None = None
    blob_name: str | None = None
    output_json_url: str | None = None
    output_md_url: str | None = None
    ocr_engine: str | None = None
    ocr_confidence: float | None = None
    validation_passed: bool | None = None
    failed_check_count: int = 0
    page_count: int | None = None
    attempt: int = 0
    error_detail: str | None = None
    received_at: str | None = None
    processed_at: str | None = None
    processing_seconds: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DbUpdateMessage":
        stage = str(data.get("stage", "")).strip()
        state = str(data.get("state", "")).strip()
        doc_id = str(data.get("docId") or data.get("doc_id") or "").strip()

        if stage not in VALID_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}")
        if state not in VALID_STATES:
            raise ValueError(f"Invalid state: {state!r}")
        if not doc_id:
            raise ValueError("Missing docId")

        return cls(
            stage=stage,
            state=state,
            doc_id=doc_id,
            awb_number=str(data.get("awbNumber") or data.get("awb_number") or ""),
            parent_doc_id=data.get("parentDocId") or data.get("parent_doc_id"),
            document_name=data.get("documentName") or data.get("document_name"),
            flight_number=data.get("flightNumber") or data.get("flight_number"),
            doc_date=data.get("date") or data.get("docDate") or data.get("doc_date"),
            source=data.get("source"),
            page_range=list(data.get("pageRange") or data.get("page_range") or []),
            blob_url=data.get("blobUrl") or data.get("blob_url"),
            blob_name=data.get("blobName") or data.get("blob_name"),
            output_json_url=data.get("outputJsonUrl") or data.get("output_json_url"),
            output_md_url=data.get("outputMdUrl") or data.get("output_md_url"),
            ocr_engine=data.get("ocrEngine") or data.get("ocr_engine"),
            ocr_confidence=data.get("ocrConfidence") or data.get("ocr_confidence"),
            validation_passed=data.get("validationPassed")
            if data.get("validationPassed") is not None
            else data.get("validation_passed"),
            failed_check_count=int(
                data.get("failedCheckCount") or data.get("failed_check_count") or 0
            ),
            page_count=data.get("pageCount") or data.get("page_count"),
            attempt=int(data.get("attempt") or 0),
            error_detail=data.get("errorDetail") or data.get("error_detail"),
            received_at=data.get("receivedAt") or data.get("received_at"),
            processed_at=data.get("processedAt") or data.get("processed_at"),
            processing_seconds=data.get("processingSeconds")
            or data.get("processing_seconds"),
        )

"""PostgreSQL access using Microsoft Entra ID managed-identity tokens.

The Container App's managed identity is exchanged for a short-lived PostgreSQL
access token (passwordless). The managed identity must be registered as a
PostgreSQL role on the flexible server (see infra README).

All writes target the `skycargo` schema:
  * awb_processing  — operational metadata per pipeline stage.
  * awb_analytics   — flattened reporting table for Power BI.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from azure.identity import DefaultAzureCredential

from .logging_setup import get_logger
from .messages import DbUpdateMessage

logger = get_logger("awb.db")

# Entra ID scope for Azure Database for PostgreSQL.
_PG_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"

PGHOST = os.getenv("PGHOST", "")
PGDATABASE = os.getenv("PGDATABASE", "postgres")
PGPORT = int(os.getenv("PGPORT", "5432"))
PGUSER = os.getenv("PGUSER", "")  # the managed identity's PostgreSQL role name
PGSSLMODE = os.getenv("PGSSLMODE", "require")
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID") or None


def is_enabled() -> bool:
    return bool(PGHOST and PGUSER)


def _credential() -> DefaultAzureCredential:
    if AZURE_CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
    return DefaultAzureCredential()


_cred = _credential()


def _token() -> str:
    return _cred.get_token(_PG_SCOPE).token


@contextmanager
def _conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(
        host=PGHOST,
        dbname=PGDATABASE,
        user=PGUSER,
        port=PGPORT,
        sslmode=PGSSLMODE,
        password=_token(),
        autocommit=False,
        connect_timeout=15,
        options="-c search_path=skycargo,public",
    )
    try:
        yield conn
    finally:
        conn.close()


def _parent_id(conn: psycopg.Connection, parent_doc_id: str) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM skycargo.awb_processing
        WHERE doc_id = %s AND stage = 'awb_input'
        ORDER BY id
        LIMIT 1
        """,
        (parent_doc_id,),
    ).fetchone()
    return row[0] if row else None


def _upsert_processing(conn: psycopg.Connection, m: DbUpdateMessage) -> None:
    parent_id = None
    if m.stage != "awb_input":
        parent_id = _parent_id(conn, m.parent_doc_id or m.doc_id)

    conn.execute(
        """
        INSERT INTO skycargo.awb_processing
            (doc_id, parent_id, stage, state, awb_number, document_name,
             flight_number, doc_date, source, page_range, blob_url, blob_name,
             output_json_url, output_md_url, ocr_engine, ocr_confidence,
             validation_passed, failed_check_count, attempt, error_detail)
        VALUES
            (%(doc_id)s, %(parent_id)s, %(stage)s, %(state)s, %(awb_number)s,
             %(document_name)s, %(flight_number)s, %(doc_date)s, %(source)s,
             %(page_range)s, %(blob_url)s, %(blob_name)s, %(output_json_url)s,
             %(output_md_url)s, %(ocr_engine)s, %(ocr_confidence)s,
             %(validation_passed)s, %(failed_check_count)s, %(attempt)s,
             %(error_detail)s)
        ON CONFLICT (doc_id, stage, awb_number) DO UPDATE SET
            parent_id          = COALESCE(EXCLUDED.parent_id, awb_processing.parent_id),
            state              = EXCLUDED.state,
            document_name      = COALESCE(EXCLUDED.document_name, awb_processing.document_name),
            flight_number      = COALESCE(EXCLUDED.flight_number, awb_processing.flight_number),
            doc_date           = COALESCE(EXCLUDED.doc_date, awb_processing.doc_date),
            source             = COALESCE(EXCLUDED.source, awb_processing.source),
            page_range         = COALESCE(EXCLUDED.page_range, awb_processing.page_range),
            blob_url           = COALESCE(EXCLUDED.blob_url, awb_processing.blob_url),
            blob_name          = COALESCE(EXCLUDED.blob_name, awb_processing.blob_name),
            output_json_url    = COALESCE(EXCLUDED.output_json_url, awb_processing.output_json_url),
            output_md_url      = COALESCE(EXCLUDED.output_md_url, awb_processing.output_md_url),
            ocr_engine         = COALESCE(EXCLUDED.ocr_engine, awb_processing.ocr_engine),
            ocr_confidence     = COALESCE(EXCLUDED.ocr_confidence, awb_processing.ocr_confidence),
            validation_passed  = COALESCE(EXCLUDED.validation_passed, awb_processing.validation_passed),
            failed_check_count = EXCLUDED.failed_check_count,
            attempt            = EXCLUDED.attempt,
            error_detail       = EXCLUDED.error_detail,
            updated_at         = now()
        """,
        {
            "doc_id": m.doc_id,
            "parent_id": parent_id,
            "stage": m.stage,
            "state": m.state,
            "awb_number": m.awb_number,
            "document_name": m.document_name,
            "flight_number": m.flight_number,
            "doc_date": m.doc_date or None,
            "source": m.source,
            "page_range": m.page_range or None,
            "blob_url": m.blob_url,
            "blob_name": m.blob_name,
            "output_json_url": m.output_json_url,
            "output_md_url": m.output_md_url,
            "ocr_engine": m.ocr_engine,
            "ocr_confidence": m.ocr_confidence,
            "validation_passed": m.validation_passed,
            "failed_check_count": m.failed_check_count,
            "attempt": m.attempt,
            "error_detail": m.error_detail,
        },
    )


def _upsert_analytics(conn: psycopg.Connection, m: DbUpdateMessage) -> None:
    """Populate the Power BI table once an AWB has a final output outcome."""
    final_state = m.state  # completed | failed
    conn.execute(
        """
        INSERT INTO skycargo.awb_analytics
            (doc_id, awb_number, document_name, flight_number, doc_date, source,
             final_state, validation_passed, failed_check_count, ocr_engine,
             ocr_confidence, page_count, retry_attempts, received_at,
             processed_at, processing_seconds, output_json_url, output_md_url)
        VALUES
            (%(doc_id)s, %(awb_number)s, %(document_name)s, %(flight_number)s,
             %(doc_date)s, %(source)s, %(final_state)s, %(validation_passed)s,
             %(failed_check_count)s, %(ocr_engine)s, %(ocr_confidence)s,
             %(page_count)s, %(retry_attempts)s, %(received_at)s, %(processed_at)s,
             %(processing_seconds)s, %(output_json_url)s, %(output_md_url)s)
        ON CONFLICT (doc_id, awb_number) DO UPDATE SET
            document_name      = COALESCE(EXCLUDED.document_name, awb_analytics.document_name),
            flight_number      = COALESCE(EXCLUDED.flight_number, awb_analytics.flight_number),
            doc_date           = COALESCE(EXCLUDED.doc_date, awb_analytics.doc_date),
            source             = COALESCE(EXCLUDED.source, awb_analytics.source),
            final_state        = EXCLUDED.final_state,
            validation_passed  = COALESCE(EXCLUDED.validation_passed, awb_analytics.validation_passed),
            failed_check_count = EXCLUDED.failed_check_count,
            ocr_engine         = COALESCE(EXCLUDED.ocr_engine, awb_analytics.ocr_engine),
            ocr_confidence     = COALESCE(EXCLUDED.ocr_confidence, awb_analytics.ocr_confidence),
            page_count         = COALESCE(EXCLUDED.page_count, awb_analytics.page_count),
            retry_attempts     = EXCLUDED.retry_attempts,
            received_at        = COALESCE(EXCLUDED.received_at, awb_analytics.received_at),
            processed_at       = COALESCE(EXCLUDED.processed_at, awb_analytics.processed_at),
            processing_seconds = COALESCE(EXCLUDED.processing_seconds, awb_analytics.processing_seconds),
            output_json_url    = COALESCE(EXCLUDED.output_json_url, awb_analytics.output_json_url),
            output_md_url      = COALESCE(EXCLUDED.output_md_url, awb_analytics.output_md_url)
        """,
        {
            "doc_id": m.doc_id,
            "awb_number": m.awb_number,
            "document_name": m.document_name,
            "flight_number": m.flight_number,
            "doc_date": m.doc_date or None,
            "source": m.source,
            "final_state": final_state,
            "validation_passed": m.validation_passed,
            "failed_check_count": m.failed_check_count,
            "ocr_engine": m.ocr_engine,
            "ocr_confidence": m.ocr_confidence,
            "page_count": m.page_count,
            "retry_attempts": m.attempt,
            "received_at": m.received_at,
            "processed_at": m.processed_at,
            "processing_seconds": m.processing_seconds,
            "output_json_url": m.output_json_url,
            "output_md_url": m.output_md_url,
        },
    )


def apply_update(m: DbUpdateMessage) -> None:
    """Apply one message to the database inside a single transaction."""
    with _conn() as conn:
        with conn.transaction():
            _upsert_processing(conn, m)
            # Feed analytics once an AWB reaches a terminal output outcome.
            if m.stage == "awb_output" and m.state in ("completed", "failed") and m.awb_number:
                _upsert_analytics(conn, m)
    logger.info(
        "Applied update doc_id=%s stage=%s state=%s awb=%s",
        m.doc_id, m.stage, m.state, m.awb_number or "-",
    )

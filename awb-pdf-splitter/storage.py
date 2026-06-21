"""Upload split AWB PDFs to Azure Blob Storage using managed identity (keyless).

Blob layout is configurable via env vars. Default path:
    <container>/<document_name>/<date>/<flight>/<awb>.pdf
e.g. awb-input/AWB_BATCH_2026-06-18/2026-06-18/EK0123/176-12345678.pdf
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient

# ---- Configurable via environment variables ----
# Storage account blob endpoint, e.g. https://stskycargoawbdata.blob.core.windows.net
BLOB_ACCOUNT_URL = os.getenv("BLOB_ACCOUNT_URL", "")
# Destination container (the "awb_input" root).
BLOB_CONTAINER = os.getenv("BLOB_CONTAINER", "awb-input")
# Virtual path template within the container. Available fields:
#   {document_name} {date} {flight} {awb}
BLOB_PATH_TEMPLATE = os.getenv(
    "BLOB_PATH_TEMPLATE", "{document_name}/{date}/{flight}/{awb}.pdf"
)
# Optional user-assigned managed identity client id (omit for system-assigned).
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID") or None


def _credential() -> DefaultAzureCredential:
    if AZURE_CLIENT_ID:
        return DefaultAzureCredential(managed_identity_client_id=AZURE_CLIENT_ID)
    return DefaultAzureCredential()


def build_blob_path(document_name: str, date: str, flight: str, awb: str) -> str:
    """Render the configured blob path template for one AWB file."""
    return BLOB_PATH_TEMPLATE.format(
        document_name=document_name,
        date=date,
        flight=flight,
        awb=awb,
    )


def is_enabled() -> bool:
    """Blob upload is active only when a storage account URL is configured."""
    return bool(BLOB_ACCOUNT_URL)


def upload_split_pdfs(
    files: dict[str, bytes],
    *,
    document_name: str,
    date: str,
    flight: str,
) -> list[str]:
    """Upload each per-AWB PDF to blob storage. Returns the blob paths written."""
    if not BLOB_ACCOUNT_URL:
        raise RuntimeError("BLOB_ACCOUNT_URL is not configured.")

    service = BlobServiceClient(account_url=BLOB_ACCOUNT_URL, credential=_credential())
    container = service.get_container_client(BLOB_CONTAINER)

    written: list[str] = []
    for filename, data in files.items():
        awb = filename.rsplit(".pdf", 1)[0]
        blob_path = build_blob_path(document_name, date, flight, awb)
        container.upload_blob(
            name=blob_path,
            data=data,
            overwrite=True,
            content_type="application/pdf",
        )
        written.append(f"{BLOB_CONTAINER}/{blob_path}")
    return written


def _service_client() -> BlobServiceClient:
    if not BLOB_ACCOUNT_URL:
        raise RuntimeError("BLOB_ACCOUNT_URL is not configured.")
    return BlobServiceClient(account_url=BLOB_ACCOUNT_URL, credential=_credential())


def download_blob(blob_url: str) -> bytes:
    """Download a blob given its full https URL using managed identity."""
    parsed = urlparse(blob_url)
    # Path is /<container>/<blob path...>
    parts = parsed.path.lstrip("/").split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Unexpected blob URL: {blob_url}")
    container_name, blob_name = parts
    service = _service_client()
    blob = service.get_blob_client(container=container_name, blob=blob_name)
    return blob.download_blob().readall()


def upload_to_prefix(files: dict[str, bytes], dest_prefix: str) -> list[str]:
    """Upload each per-AWB PDF under <container>/<dest_prefix>/<awb>.pdf."""
    service = _service_client()
    container = service.get_container_client(BLOB_CONTAINER)
    prefix = dest_prefix.strip("/")

    written: list[str] = []
    for filename, data in files.items():
        blob_path = f"{prefix}/{filename}"
        container.upload_blob(
            name=blob_path,
            data=data,
            overwrite=True,
            content_type="application/pdf",
        )
        written.append(f"{BLOB_CONTAINER}/{blob_path}")
    return written


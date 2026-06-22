# awb-pdf-splitter

Private, event-driven worker that **splits a multi-AWB PDF batch (many Air
Waybill numbers in one file) into one PDF per AWB** and stores the results back
in Blob Storage. It is the **first processing stage** of the SkyCargo AWB
pipeline. A small FastAPI surface is also exposed for health checks and manual,
on-demand splitting.

```
Blob upload (awb-input/pdf/…)
        │  Microsoft.Storage.BlobCreated
        ▼
   Event Grid system topic   (subject filter: pdf/…, *.pdf)
        │
        ▼
   Service Bus queue  aws-splitter-q     (pointer only — the PDF is never on the queue)
        │
        ▼
   awb-pdf-splitter (this app, private ACA)
        │  download → split per AWB → upload
        ▼
   awb-split/…/splitted-awb/<awb>.pdf
        │  BlobCreated → awb-worker-q → awb-ocr-worker (next stage)
```

## What it does (event-driven path)

For every message on `aws-splitter-q` — an Event Grid `BlobCreated` event that
carries a **pointer** to the uploaded PDF, not the file itself — the background
consumer:

1. Reads the blob URL from the event (`data.url`).
2. Downloads the source PDF from Blob Storage using its **managed identity**
   (keyless — no connection strings or account keys).
3. Splits the PDF into one document per detected AWB number.
4. Uploads each split PDF to the output container under
   `…/<timestamp>/splitted-awb/<awb>.pdf`.

Split outputs are written to a **separate container** (`awb-split`) so they do
not re-trigger the splitter's own Event Grid subscription — this structurally
prevents an infinite split-of-a-split loop.

## How AWB detection works

- AWB number format: `NNN-NNNNNNNN` (3-digit airline prefix + 8-digit serial),
  e.g. `176-12345678`.
- Each page is scanned for an AWB number. A new AWB number starts a new output
  PDF; pages with no AWB number are treated as continuation pages of the
  current bill.
- Assumes **text-based** PDFs. For scanned/image PDFs, OCR them first.

## Source files

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app. Starts the Service Bus consumer in a background thread on startup; exposes `/health` and manual `/split*` endpoints. |
| `consumer.py` | Service Bus receive loop, event parsing, dead-letter / abandon logic, self-healing reconnect. |
| `splitter.py` | PDF parsing and per-AWB split logic (`split_pdf`, `build_zip`). |
| `storage.py` | Blob download (source) and upload (split outputs) via managed identity. |
| `Dockerfile` / `requirements.txt` | Container image and dependencies. |

## Reliability

- **Self-healing loop** — the receiver runs inside `while True`; when the
  iterator returns after an idle window or a connection drops, it reconnects
  (5 s backoff) instead of letting the worker thread die.
- **Poison-message handling** — content that can never succeed (corrupt/non-PDF:
  `PdfReadError`, `PdfStreamError`, `ValueError`, `JSONDecodeError`) is sent to
  the **dead-letter queue**. Other (transient) failures are **abandoned** so
  Service Bus redelivers them.
- **Recursion guard** — messages whose blob path contains `/splitted-awb/`, or
  that are not `*.pdf`, are skipped.

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICEBUS_NAMESPACE` | _(unset → consumer disabled)_ | Fully qualified namespace, e.g. `awb-sb-ek.servicebus.windows.net`. |
| `SERVICEBUS_QUEUE` | `aws-splitter-q` | Queue to consume. |
| `BLOB_ACCOUNT_URL` | _(unset → blob disabled)_ | e.g. `https://awbstorageek.blob.core.windows.net`. |
| `BLOB_OUTPUT_CONTAINER` | `awb-split` | Container for split PDFs (separate from input → breaks recursion). |
| `SPLIT_SUBFOLDER` | `splitted-awb` | Sub-folder under the source timestamp folder. |
| `AZURE_CLIENT_ID` | _(unset → system-assigned identity)_ | Set only for a user-assigned identity. |

## Manual HTTP API (optional)

Useful for testing or ad-hoc splitting; the production flow is the queue
consumer above.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health probe. |
| POST | `/split` | Upload a PDF (`file`), returns a ZIP of per-AWB PDFs. |
| POST | `/split/manifest` | Upload a PDF (`file`), returns detected AWB list (JSON). |
| POST | `/split/blob` | Split and write each AWB to blob storage (`document_name`, `date`, `flight` form fields). |

```bash
curl -X POST "http://localhost:8000/split" -F "file=@multi-awb.pdf" -o awb-split.zip
```

## Identity & RBAC

Runs with a **system-assigned managed identity**. Required role assignments:

| Scope | Role |
|-------|------|
| ACR (`acrvk012826`) | AcrPull |
| Storage (`awbstorageek`) | Storage Blob Data Contributor |
| Service Bus (`awb-sb-ek`) | Azure Service Bus Data Receiver |

## Hosting

- **Azure Container Apps**, internal environment `cae-skycargo-internal`
  (VNet-injected into `vnet-ek`), **internal ingress only** — no public endpoint.
- Reaches Storage and Service Bus over **private endpoints** via the hub VNet's
  Private DNS zones.

### Autoscaling (KEDA)

```
min replicas : 0
max replicas : 5
scaler       : azure-servicebus (identity: system)
queue        : aws-splitter-q
threshold    : 5 messages per replica
```

Scales to zero when the queue is empty; KEDA spins replicas back up on new
messages.

## Run locally

```powershell
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Deploy privately to Azure Container Apps

```powershell
cd ../infrastructure/3-aws-splitter-aca
./deploy.ps1   # az acr build (no local Docker) then az deployment group create
```

This builds the image in ACR and deploys the app into the **VNet-injected,
internal-only** environment with its RBAC. The result is reachable only on its
internal FQDN from inside `vnet-ek` (or peered networks) — **no public
endpoint**.

# awb-ocr-worker

Private, event-driven worker that **runs OCR on split AWB PDFs** using Azure AI
Document Intelligence and writes the structured results back to Blob Storage. It
is the **second processing stage** of the SkyCargo AWB pipeline, consuming the
output of [`awb-pdf-splitter`](../awb-pdf-splitter/README.md).

```
awb-split/…/splitted-awb/<awb>.pdf
        │  Microsoft.Storage.BlobCreated   (subject filter: awb-split/…, *.pdf)
        ▼
   Event Grid system topic
        │
        ▼
   Service Bus queue  awb-worker-q     (pointer only — the PDF is never on the queue)
        │
        ▼
   awb-ocr-worker (this app, private ACA)
        │  download → Document Intelligence OCR → write artifacts
        ▼
   awb-output/…/<awb>.json   +   awb-output/…/<awb>.md
```

## What it does

For every message on `awb-worker-q` — an Event Grid `BlobCreated` event that
carries a **pointer** to a split AWB PDF — the background consumer:

1. Reads the blob URL from the event (`data.url`).
2. Downloads the PDF from Blob Storage using its **managed identity** (keyless).
3. Runs OCR via **Azure AI Document Intelligence** (`prebuilt-layout` by
   default), guarded by retry + circuit breaker.
4. Writes two artifacts to the `awb-output` container, mirroring the source
   path: `<prefix>.json` (full Document Intelligence result) and `<prefix>.md`
   (human-readable summary + extracted text).

## Metadata events

The worker **publishes `awb_output` state events** to the `async-db-update-q`
Service Bus queue (keyless, managed identity), consumed by
[`awb-db-updater`](../awb-db-updater/README.md):

| Event | When |
|-------|------|
| `awb_output` / `inprogress` | OCR started (`receivedAt`) |
| `awb_output` / `completed` | OCR + normalization succeeded (`outputJsonUrl`, `outputMdUrl`, `processingSeconds`) — drives the `awb_analytics` Power BI row |
| `awb_output` / `failed` | permanent error or retries exhausted (`attempt`, `errorDetail`) |

A stable `docId` (`pdf/<timestamp>`, derived from the blob path) links the output
row back to the `awb_input` parent created by the splitter. Publishing is
**best-effort** — a publish failure is logged but never fails OCR.

## Source files

| File | Responsibility |
|------|----------------|
| `main.py` | FastAPI app. Starts the Service Bus consumer in a background thread on startup; exposes `/health`. |
| `consumer.py` | Service Bus receive loop, event parsing, exponential-backoff redelivery, dead-letter logic. |
| `ocr.py` | Document Intelligence client + `run_ocr` (retry & circuit breaker), and JSON/Markdown builders. |
| `storage.py` | Blob download (source PDF) and upload (`.json` + `.md`) via managed identity. |
| `db_events.py` | Publishes `awb_output` stage events to `async-db-update-q` (managed identity, best-effort). |
| `circuit_breaker.py` | Minimal circuit breaker (`CLOSED`/`OPEN`/`HALF_OPEN`). |
| `Dockerfile` / `requirements.txt` | Container image and dependencies. |

## Reliability — retry, backoff & circuit breaker

Resilience is layered so a failing OCR backend degrades gracefully instead of
losing work or hammering the service:

1. **Per-call retry (in-process)** — `ocr.run_ocr` retries transient errors
   (`ServiceRequestError`, `ServiceResponseError`, `HttpResponseError` other
   than 4xx≠429) with **tenacity exponential backoff** (`OCR_MAX_RETRIES`
   attempts).
2. **Circuit breaker** — after `OCR_CB_FAILURE_THRESHOLD` consecutive failures
   the breaker **opens** and fails fast (`CircuitOpenError`) for
   `OCR_CB_RECOVERY_SECONDS`, then half-opens to test recovery. This protects a
   struggling Document Intelligence backend from a thundering herd.
3. **Cross-message exponential backoff** — if a message still fails (transient
   error or open circuit), the consumer **re-schedules** it onto the same queue
   with an increasing delay (`schedule_messages`), tracking the count in the
   `attempt` application property: delay = `min(BACKOFF_BASE · 2^attempt,
   BACKOFF_MAX)`.
4. **Dead-lettering** — permanent errors (corrupt/non-PDF: `PdfReadError`,
   `PdfStreamError`, `ValueError`, `JSONDecodeError`) are dead-lettered
   immediately; transient failures are dead-lettered once `MAX_ATTEMPTS` is
   exhausted.
5. **Self-healing loop** — the receiver runs inside `while True`; on idle return
   or connection drop it reconnects (5 s backoff).

## Configuration (environment variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVICEBUS_NAMESPACE` | _(unset → consumer disabled)_ | Fully qualified namespace, e.g. `awb-sb-ek.servicebus.windows.net`. |
| `SERVICEBUS_QUEUE` | `awb-worker-q` | Queue to consume. |
| `DB_UPDATE_QUEUE` | `async-db-update-q` | Queue for `awb_output` stage events (managed identity sender). |
| `BLOB_ACCOUNT_URL` | _(unset → blob disabled)_ | e.g. `https://awbstorageek.blob.core.windows.net`. |
| `BLOB_OUTPUT_CONTAINER` | `awb-output` | Container for OCR artifacts. |
| `DOCUMENTINTELLIGENCE_ENDPOINT` | _(required)_ | e.g. `https://docintelligencmbc.cognitiveservices.azure.com/`. |
| `DOCUMENTINTELLIGENCE_MODEL` | `prebuilt-layout` | Document Intelligence model id. |
| `DOCUMENTINTELLIGENCE_API_KEY` | _(unset → managed identity)_ | Optional key auth; prefer managed identity. |
| `OCR_MAX_RETRIES` | `3` | In-process retry attempts per OCR call. |
| `OCR_BACKOFF_BASE` / `OCR_BACKOFF_MAX` | `2` / `30` | Tenacity exponential backoff (seconds). |
| `OCR_CB_FAILURE_THRESHOLD` | `5` | Failures before the circuit opens. |
| `OCR_CB_RECOVERY_SECONDS` | `30` | Open-circuit cool-down before half-open. |
| `OCR_MAX_ATTEMPTS` | `5` | Cross-message redeliveries before dead-letter. |
| `OCR_BACKOFF_BASE_SECONDS` / `OCR_BACKOFF_MAX_SECONDS` | `10` / `600` | Queue reschedule backoff bounds. |
| `AZURE_CLIENT_ID` | _(unset → system-assigned identity)_ | Set only for a user-assigned identity. |

## Identity & RBAC

Runs with a **system-assigned managed identity**. Required role assignments:

| Scope | Role | Why |
|-------|------|-----|
| ACR (`acrvk012826`) | AcrPull | pull the image |
| Storage (`awbstorageek`) | Storage Blob Data Contributor | read PDFs, write artifacts |
| Service Bus (`awb-sb-ek`) | Azure Service Bus Data **Receiver** | consume `awb-worker-q` |
| Service Bus (`awb-sb-ek`) | Azure Service Bus Data **Sender** | re-schedule messages for backoff + publish to `async-db-update-q` |
| Document Intelligence (`docintelligencmbc`) | Cognitive Services User | call OCR |

> The **Sender** role is essential here: it covers both the exponential-backoff
> re-enqueue onto `awb-worker-q` and publishing `awb_output` events to
> `async-db-update-q`.

## Hosting

- **Azure Container Apps**, internal environment `cae-skycargo-internal`
  (VNet-injected into `vnet-ek`), **internal ingress only** — no public endpoint.
- Reaches Storage, Service Bus, and Document Intelligence over **private
  endpoints** via the hub VNet's Private DNS zones.

### Autoscaling (KEDA)

```
min replicas : 0
max replicas : 5
scaler       : azure-servicebus (identity: system)
queue        : awb-worker-q
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
cd ../infrastructure/5-awb-ocr-aca
./deploy.ps1   # az acr build (no local Docker) then az deployment group create
```

Because the AcrPull role and the app are created in the same deployment, the
first revision can occasionally time out before the role propagates. If that
happens, the role assignments still get created — just restart the revision (or
re-run `deploy.ps1`, which is idempotent) and the image pull succeeds.

## Health check

`GET /health` returns the configuration state:

```json
{ "status": "ok", "ocr_configured": true, "blob_configured": true, "servicebus_configured": true }
```

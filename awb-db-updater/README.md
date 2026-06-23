# 7 — awb-db-updater (Azure Container Apps)

Background worker that consumes the **`async-db-update-q`** Service Bus queue and
upserts AWB metadata into the `skycargo` schema on the PostgreSQL flexible server
(`devpostgresvinay`). Authentication to **both** Service Bus and PostgreSQL is
keyless, using the Container App's **system-assigned managed identity**.

## What it writes

| Table | When |
|-------|------|
| `skycargo.awb_processing` | Every message — upsert keyed on `(doc_id, stage, awb_number)`. Split/output rows are linked to their `awb_input` parent via `parent_id`. |
| `skycargo.awb_analytics`  | When `stage = awb_output` and `state ∈ {completed, failed}` — the Power BI reporting row. |

### Message contract (`async-db-update-q`)

```jsonc
{
  "stage": "awb_split",          // awb_input | awb_split | awb_output
  "state": "completed",          // inprogress | completed | failed
  "docId": "DOC-2026-0001",
  "awbNumber": "176-12345678",   // omit for the awb_input parent row
  "parentDocId": "DOC-2026-0001",// defaults to docId
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
```

## Reliability

- Invalid JSON / bad contract → **dead-letter** immediately.
- DB / transient errors → **exponential backoff** by rescheduling the message
  (tracked via the `attempt` property); dead-lettered after `DB_MAX_ATTEMPTS`.

## Deploy

```powershell
cd skycargo-ocr/infrastructure/7-awb-db-updater-aca
./deploy.ps1
```

`deploy.ps1`:
1. Builds the image into ACR (`az acr build`).
2. Deploys `main.bicep` — internal Container App + Service Bus Data Receiver/Sender RBAC + KEDA queue-length autoscaling.
3. Registers the app's managed identity as a PostgreSQL Entra principal.

Then grant least-privilege DB access by running `grant-skycargo.sql` against the
server **from inside the VNet** (private endpoint only):

```bash
psql "host=devpostgresvinay.postgres.database.azure.com dbname=postgres \
      user=<entra-admin> sslmode=require" -f grant-skycargo.sql
```

## Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `SERVICEBUS_NAMESPACE` | — | `<ns>.servicebus.windows.net` |
| `SERVICEBUS_QUEUE` | `async-db-update-q` | Queue to consume |
| `PGHOST` | — | PostgreSQL FQDN |
| `PGDATABASE` | `postgres` | Database |
| `PGUSER` | `awb-db-updater` | PostgreSQL role = managed identity name |
| `PGSSLMODE` | `require` | TLS mode |
| `DB_MAX_ATTEMPTS` | `5` | Retries before dead-letter |

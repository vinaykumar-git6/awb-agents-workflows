# SkyCargo Infrastructure

Infrastructure-as-Code (Bicep) for the SkyCargo AWB platform.

## Structure

```
infrastructure/
  1-logic-apps-consumption/  # Logic Apps Consumption (SharePoint -> Blob ingest)
  2-fileEvents/              # Event Grid system topics + Service Bus subscriptions
  3-aws-splitter-aca/        # awb-pdf-splitter Container App (private) + RBAC
  4-network-peering/         # vnet-ek <-> hub peering for private DNS/endpoints
  5-awb-ocr-aca/             # awb-ocr-worker Container App (private) + RBAC
  6-postgres-schema/         # skycargo schema (awb_processing, awb_analytics)
  7-awb-db-updater-aca/      # awb-db-updater Container App (private) + RBAC + queue
  8-powerbi-analytics/       # Power BI project (PBIP) on skycargo.awb_analytics
```

Each component folder contains a `README.md`. Bicep-deployed components
(`1`–`5`, `7`) also include `main.bicep` + `main.bicepparam`; `6` ships SQL and
`8` ships a Power BI project (PBIP).

## Conventions

- `targetScope = 'resourceGroup'` unless noted otherwise.
- Keyless / managed-identity access only (no shared keys, no secrets in params).
- Built-in role assignments are created in the template that owns the workload identity.
- Tags applied to every resource via a shared `tags` object.

## Deploy

```powershell
az deployment group create `
  --resource-group <rg-name> `
  --template-file 3-aws-splitter-aca/main.bicep `
  --parameters 3-aws-splitter-aca/main.bicepparam
```

> Several folders (`3-`, `5-`, `7-`) include a `deploy.ps1` that first builds the
> container image into ACR (`az acr build`) and then deploys the template.

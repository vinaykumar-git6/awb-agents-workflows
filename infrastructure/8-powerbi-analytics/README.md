# 8 — Power BI analytics (skycargo.awb_analytics)

Source-controlled **Power BI project (PBIP)** that reports on the
`skycargo.awb_analytics` table populated by [`awb-db-updater`](../../awb-db-updater/README.md).
It connects to the PostgreSQL flexible server (`devpostgresvinay`) over
**DirectQuery** so the dashboard always reflects live pipeline state.

```
skycargo.awb_analytics  ──►  skycargo.vw_awb_analytics  ──►  PBIP (DirectQuery)  ──►  Power BI Service
        (awb-db-updater writes)         (reporting view)            (this folder)         (workspace + gateway)
```

## Contents

| Path | Purpose |
|------|---------|
| `reporting-view.sql` | Creates `skycargo.vw_awb_analytics`, the stable reporting source. |
| `deploy-view.ps1` | Applies `reporting-view.sql` using a keyless Entra token. |
| `SkyCargoAnalytics.pbip` | PBIP entry point — open this in **Power BI Desktop**. |
| `SkyCargoAnalytics.SemanticModel/` | TMDL model: `awb_analytics` (DirectQuery) + `Date` table, relationships, and measures. |
| `SkyCargoAnalytics.Report/` | Report pages **Overview** + **Quality & SLA**, branded with the SkyCargo theme. |

## Semantic model

- **Tables** — `awb_analytics` (DirectQuery on `skycargo.vw_awb_analytics`) and an
  import `Date` table for time intelligence, related on `processed_date`.
- **Parameters** — `PgServer` and `PgDatabase` (override per environment without
  editing the model).
- **Key measures** — Total / Completed / Failed AWBs, Success & Failure Rate %,
  Validation Pass Rate %, Avg OCR Confidence, Avg & P95 Processing Seconds,
  Total Pages, Avg Retries, AWBs (Last 30 Days).

## Prerequisites

1. Apply the schema once: [`6-postgres-schema/schema.sql`](../6-postgres-schema/schema.sql).
2. Create the reporting view (from inside the VNet, or via pgAdmin while public
   access is enabled). Easiest is the helper script, which fetches a keyless
   Entra token automatically:

   ```powershell
   ./deploy-view.ps1
   # or override target:
   ./deploy-view.ps1 -Server <fqdn> -Database postgres
   ```

   Equivalent manual command:

   ```bash
   psql "host=devpostgresvinay.postgres.database.azure.com dbname=postgres \
         user=<entra-admin> sslmode=require" -f reporting-view.sql
   ```

## Open & edit locally

1. Install **Power BI Desktop** (with *Preview features → Power BI Project (.pbip)*
   enabled).
2. Open `SkyCargoAnalytics.pbip`.
3. When prompted, set the `PgServer` / `PgDatabase` parameters and sign in with
   **Microsoft Entra (Azure AD)** auth — keyless, matching the rest of the
   platform.

## Private connectivity (recommended)

The pipeline keeps PostgreSQL reachable privately via the private endpoint
`pepostgresvinay`. To let **Power BI Service** query it without exposing the DB
publicly, route refresh/DirectQuery through a gateway inside the VNet:

| Option | How |
|--------|-----|
| **VNet data gateway** (preferred) | Create a *Virtual Network data gateway* bound to a subnet in `vnet-ek` (or a peered VNet that resolves `privatelink.postgres.database.azure.com`). No VM to manage. |
| **On-premises data gateway (standard)** | Install the gateway on a VM in `vnet-ek`; it resolves the private endpoint via the linked Private DNS zone. |

Then in the Power BI Service dataset settings, bind the data source to the
gateway and use **OAuth2 / Entra** credentials. Keep the DB's
`publicNetworkAccess` disabled in production — public access is only a
convenience for local pgAdmin browsing.

> The `awb-db-updater` Container App already writes to PostgreSQL privately from
> `vnet-ek`; the gateway gives Power BI the same private path for reads.

## Publish

1. In Power BI Desktop: **Home → Publish** to a workspace.
2. In the Service: **Dataset → Settings → Gateway connection**, map the
   PostgreSQL source to the VNet/on-prem gateway and set credentials.
3. Set a **scheduled refresh** (DirectQuery needs gateway connectivity but no
   import refresh; tile caches refresh on the schedule).

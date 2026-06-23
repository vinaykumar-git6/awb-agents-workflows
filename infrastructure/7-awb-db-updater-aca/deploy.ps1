# Deploy the awb-db-updater PRIVATELY to Azure Container Apps in vnet-ek.
#
# Phases:
#   1. Build the container image into ACR (Bicep can't build images).
#   2. Deploy main.bicep — adds the internal Container App + Service Bus RBAC.
#   3. Grant the app's managed identity access to PostgreSQL (can't be done in
#      Bicep). This registers the identity as a PostgreSQL Entra role and grants
#      it privileges on the skycargo schema.
#
# Run from this folder:  ./deploy.ps1

$ErrorActionPreference = 'Stop'

# ---- Settings (keep in sync with main.bicepparam) ----
$RG          = 'emirates-ai-usecase'
$ACR         = 'acrvk012826'
$ACR_RG      = 'azure-vk-rg'
$IMAGE       = 'awb-db-updater:v1'
$SOURCE_DIR  = '../../awb-db-updater'
$APP         = 'awb-db-updater'

# PostgreSQL flexible server (private endpoint only).
$PG_RG       = 'azure-vk-rg'
$PG_SERVER   = 'devpostgresvinay'
$PG_DB       = 'postgres'

# ---- 1. Build the image into the existing ACR (no local Docker) ----
az acr build -g $ACR_RG -r $ACR -t $IMAGE $SOURCE_DIR

# ---- 2. Deploy the private ACA app + Service Bus RBAC ----
az deployment group create `
    --resource-group $RG `
    --template-file main.bicep `
    --parameters main.bicepparam

# ---- 3. Grant the managed identity access to PostgreSQL ----
# Get the app's system-assigned identity (principalId + clientId).
$principalId = az containerapp show -g $RG -n $APP --query identity.principalId -o tsv
Write-Host "App managed identity principalId: $principalId"

# Register the managed identity as a PostgreSQL Entra (AAD) role. Requires that
# you are an Entra admin on the server. The role name must match PGUSER env var.
az postgres flexible-server microsoft-entra-admin create `
    --resource-group $PG_RG `
    --server-name $PG_SERVER `
    --display-name $APP `
    --object-id $principalId `
    --type ServicePrincipal

# Grant the role privileges on the skycargo schema. Because the server is
# private-endpoint only, run grant-skycargo.sql from a host inside vnet-ek
# (e.g. exec into another container app / a VM with psql), e.g.:
#
#   psql "host=$PG_SERVER.postgres.database.azure.com dbname=$PG_DB \
#         user=<entra-admin> sslmode=require" -f grant-skycargo.sql
#
Write-Host "`nNext: run grant-skycargo.sql against the server from inside the VNet."

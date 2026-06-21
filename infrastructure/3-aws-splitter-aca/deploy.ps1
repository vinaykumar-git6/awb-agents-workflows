# Deploy the awb-pdf-splitter PRIVATELY to Azure Container Apps in vnet-ek.
#
# Two phases:
#   1. Build the container image into ACR (Bicep can't build images).
#   2. Deploy main.bicep, which provisions the internal (VNet-injected) ACA
#      environment, the Container App (internal ingress only), and RBAC.
#
# Run from this folder:  ./deploy.ps1

$ErrorActionPreference = 'Stop'

# ---- Settings (keep in sync with main.bicepparam) ----
$RG            = 'emirates-ai-usecase'
$LOCATION      = 'uaenorth'
$ACR           = 'skycargoacrek'                 # must be globally unique
$IMAGE         = 'awb-pdf-splitter:v1'
$SOURCE_DIR    = '../../awb-pdf-splitter'         # app source (Dockerfile lives here)

# ---- 1. Ensure the ACR exists and build the image in it (no local Docker) ----
if (-not (az acr show -g $RG -n $ACR 2>$null)) {
    az acr create -g $RG -n $ACR --sku Standard | Out-Null
}
az acr build -g $RG -r $ACR -t $IMAGE $SOURCE_DIR

# ---- 2. Deploy the private ACA infrastructure + app ----
az deployment group create `
    --resource-group $RG `
    --template-file main.bicep `
    --parameters main.bicepparam

# ---- 3. Show the internal FQDN (reachable only from inside vnet-ek) ----
Write-Host "`nInternal FQDN (private, no public ingress):"
az containerapp show -g $RG -n awb-pdf-splitter --query properties.configuration.ingress.fqdn -o tsv

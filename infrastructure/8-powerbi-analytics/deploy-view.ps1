<#
.SYNOPSIS
    Creates / refreshes the skycargo.vw_awb_analytics reporting view used by the
    SkyCargo Power BI dashboard.

.DESCRIPTION
    Connects to the Azure PostgreSQL flexible server with a Microsoft Entra token
    (keyless) and applies reporting-view.sql. Run from a host that can resolve and
    reach the server (inside the VNet via the private endpoint, or from your
    laptop while public network access is enabled).

.PARAMETER Server
    PostgreSQL FQDN. Defaults to the dev server.

.PARAMETER Database
    Target database. Defaults to 'postgres'.

.EXAMPLE
    ./deploy-view.ps1
.EXAMPLE
    ./deploy-view.ps1 -Server myserver.postgres.database.azure.com -Database postgres
#>
[CmdletBinding()]
param(
    [string] $Server   = 'devpostgresvinay.postgres.database.azure.com',
    [string] $Database  = 'postgres',
    [string] $SqlFile   = (Join-Path $PSScriptRoot 'reporting-view.sql')
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    throw "psql not found on PATH. Install the PostgreSQL client tools first."
}
if (-not (Test-Path $SqlFile)) {
    throw "SQL file not found: $SqlFile"
}

Write-Host "Acquiring Entra token for PostgreSQL..." -ForegroundColor Cyan
$user  = (az ad signed-in-user show --query userPrincipalName -o tsv)
$token = (az account get-access-token `
            --resource-type oss-rdbms `
            --query accessToken -o tsv)

if (-not $token) { throw "Failed to obtain access token. Run 'az login' first." }

Write-Host "Applying $SqlFile to $Database on $Server (user: $user)..." -ForegroundColor Cyan
$env:PGPASSWORD = $token
try {
    psql "host=$Server port=5432 dbname=$Database user=$user sslmode=require" `
        -v ON_ERROR_STOP=1 -f $SqlFile
    if ($LASTEXITCODE -ne 0) { throw "psql exited with code $LASTEXITCODE" }
    Write-Host "Reporting view deployed successfully." -ForegroundColor Green
}
finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}

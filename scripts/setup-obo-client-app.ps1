# =============================================================================
# Register MSAL.js Client SPA App (PowerShell)
# =============================================================================
# Creates a regular Entra app registration for MSAL.js browser sign-in.
# The user signs into THIS app -> gets Tc -> sends Tc to agent -> OBO exchange.
#
# Cross-platform: works on Windows PowerShell 7+, macOS, and Linux.
# Requires: Azure CLI (`az`) on PATH.
#
# Usage:
#   # reads TENANT_ID from sidecar/dev/.env  (or specify -EnvFile / -TenantId)
#   pwsh ./scripts/setup-obo-client-app.ps1
#   pwsh ./scripts/setup-obo-client-app.ps1 -EnvFile ./sidecar/dev/.env
#   pwsh ./scripts/setup-obo-client-app.ps1 -TenantId <guid>
# =============================================================================

[CmdletBinding()]
param(
    [string]$EnvFile,
    [string]$TenantId,
    [string]$AppName      = 'Agent Demo Client SPA',
    [string]$RedirectUri  = 'http://localhost:3001'
)

$ErrorActionPreference = 'Stop'

# ── Locate .env ─────────────────────────────────────────────────────────────
if (-not $EnvFile) {
    $candidates = @(
        (Join-Path $PSScriptRoot '..\sidecar\dev\.env'),
        (Join-Path $PSScriptRoot '..\sidecar\aws\.env'),
        (Join-Path $PSScriptRoot '..\sidecar\gcp\.env'),
        (Join-Path $PSScriptRoot '.env')
    )
    $EnvFile = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if ($EnvFile -and (Test-Path $EnvFile)) {
    Write-Host "📄 Reading $EnvFile"
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$' -and -not $_.TrimStart().StartsWith('#')) {
            $name  = $Matches[1]
            $value = $Matches[2].Trim('"').Trim("'")
            if (-not (Get-Variable -Name $name -Scope Script -ErrorAction SilentlyContinue)) {
                Set-Variable -Name $name -Value $value -Scope Script
            }
        }
    }
}

if (-not $TenantId) { $TenantId = $script:TENANT_ID }
if (-not $TenantId) {
    throw "TENANT_ID not set. Pass -TenantId <guid> or put TENANT_ID in an .env file."
}

Write-Host ''
Write-Host '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
Write-Host '  Register MSAL.js Client SPA App'
Write-Host "  Tenant: $TenantId"
Write-Host '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
Write-Host ''

# ── Check az CLI ────────────────────────────────────────────────────────────
if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI (az) not found on PATH. See https://learn.microsoft.com/cli/azure/install-azure-cli"
}

# ── Check login ─────────────────────────────────────────────────────────────
Write-Host '🔐 Checking Azure CLI login...'
$currentTenant = az account show --query tenantId -o tsv 2>$null
if ($currentTenant -ne $TenantId) {
    az login --tenant $TenantId --allow-no-subscriptions -o none
}
Write-Host '   ✅ Logged in'
Write-Host ''

# ── Create or find app ──────────────────────────────────────────────────────
Write-Host "🌐 Creating app registration: $AppName"
$clientSpaAppId = az ad app list --display-name $AppName --query "[0].appId" -o tsv 2>$null

if ($clientSpaAppId -and $clientSpaAppId -ne 'None') {
    Write-Host "   ✅ Already exists: $clientSpaAppId"
} else {
    $clientSpaAppId = az ad app create `
        --display-name $AppName `
        --sign-in-audience 'AzureADMyOrg' `
        --query appId -o tsv
    Write-Host "   ✅ Created: $clientSpaAppId"
    Start-Sleep -Seconds 5
}
Write-Host ''

# ── Set SPA redirect URI ────────────────────────────────────────────────────
Write-Host "🔗 Setting SPA redirect URI -> $RedirectUri"
$spaJson = '{"redirectUris":["' + $RedirectUri + '"]}'
az ad app update --id $clientSpaAppId --set "spa=$spaJson" | Out-Null
Write-Host '   ✅ Done'
Write-Host ''

# ── Ensure service principal exists ─────────────────────────────────────────
Write-Host '👤 Ensuring service principal exists...'
$spId = az ad sp list --filter "appId eq '$clientSpaAppId'" --query "[0].id" -o tsv 2>$null
if (-not $spId -or $spId -eq 'None') {
    az ad sp create --id $clientSpaAppId -o none
    Write-Host '   ✅ Created service principal'
} else {
    Write-Host '   ✅ Already exists'
}
Write-Host ''

# ── Save to .env ────────────────────────────────────────────────────────────
if ($EnvFile -and (Test-Path $EnvFile)) {
    Write-Host "📝 Saving CLIENT_SPA_APP_ID to $EnvFile"
    $content = Get-Content $EnvFile -Raw
    if ($content -match '(?m)^CLIENT_SPA_APP_ID=.*$') {
        $content = [regex]::Replace($content, '(?m)^CLIENT_SPA_APP_ID=.*$', "CLIENT_SPA_APP_ID=$clientSpaAppId")
        Set-Content -Path $EnvFile -Value $content -NoNewline
        Write-Host '   ✅ Updated CLIENT_SPA_APP_ID'
    } else {
        Add-Content -Path $EnvFile -Value ''
        Add-Content -Path $EnvFile -Value '# Client SPA App for MSAL.js user sign-in (OBO)'
        Add-Content -Path $EnvFile -Value "CLIENT_SPA_APP_ID=$clientSpaAppId"
        Write-Host '   ✅ Added CLIENT_SPA_APP_ID'
    }
    Write-Host ''
}

Write-Host '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
Write-Host '  ✅ Done!'
Write-Host ''
Write-Host "  Client SPA App ID: $clientSpaAppId"
Write-Host "  Redirect URI:      $RedirectUri"
Write-Host '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

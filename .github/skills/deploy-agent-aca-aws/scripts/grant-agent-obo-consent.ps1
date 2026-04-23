param([string]$AgentAppId, [string]$TenantId)
$ErrorActionPreference='Stop'
Connect-MgGraph -Scopes 'AppRoleAssignment.ReadWrite.All','DelegatedPermissionGrant.ReadWrite.All','Application.Read.All','Directory.Read.All' -TenantId $TenantId -NoWelcome | Out-Null

$agentSp = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/servicePrincipals(appId='$AgentAppId')?`$select=id,displayName"
Write-Host "Agent SP: $($agentSp.id)  ($($agentSp.displayName))"

$graphSp = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/servicePrincipals(appId='00000003-0000-0000-c000-000000000000')?`$select=id"
Write-Host "Graph SP: $($graphSp.id)"

# Check existing
$existing = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?`$filter=clientId eq '$($agentSp.id)' and resourceId eq '$($graphSp.id)'"
Write-Host "Existing grants: $($existing.value.Count)"
$existing.value | ForEach-Object { Write-Host "  scope='$($_.scope)' consentType=$($_.consentType)" }

$hasUserRead = $existing.value | Where-Object { $_.scope -match 'User\.Read' } | Select-Object -First 1
if ($hasUserRead) {
    Write-Host "User.Read already granted ($($hasUserRead.scope)). Nothing to do."
    return
}

$body = @{
    clientId = $agentSp.id
    consentType = 'AllPrincipals'
    resourceId = $graphSp.id
    scope = 'User.Read'
} | ConvertTo-Json
$r = Invoke-MgGraphRequest -Method POST -Uri 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants' -Body $body -ContentType 'application/json'
Write-Host "Granted. id=$($r.id) scope='$($r.scope)'"

# Microsoft Entra Agent ID - PowerShell Functions
# Complete workflow automation for creating and managing Agent Identities

#region Helper Functions

function Get-DecodedJwtToken {
    <#
    .SYNOPSIS
    Decodes a JWT token and returns the payload as formatted JSON.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Token
    )
    
    try {
        $tokenParts = $Token.Split('.')
        if ($tokenParts.Count -lt 2) {
            throw "Invalid JWT token format"
        }
        
        $payload = $tokenParts[1]
        while ($payload.Length % 4 -ne 0) {
            $payload += '='
        }
        
        $decodedBytes = [System.Convert]::FromBase64String($payload.Replace('-', '+').Replace('_', '/'))
        $decodedJson = [System.Text.Encoding]::UTF8.GetString($decodedBytes)
        
        return ($decodedJson | ConvertFrom-Json | ConvertTo-Json -Depth 10)
    }
    catch {
        Write-Error "Failed to decode JWT token: $_"
        return $null
    }
}

#endregion

#region Step 1: Setup

function Connect-EntraAgentIDEnvironment {
    <#
    .SYNOPSIS
    Connects to Azure and Microsoft Graph with required permissions using user identity.
    
    .PARAMETER TenantId
    The Entra tenant ID. If not provided, will attempt to get from current context.
    #>
    param(
        [Parameter(Mandatory = $false)]
        [string]$TenantId
    )
    
    Write-Host "[LOCK] Step 1: Connecting to Azure and Microsoft Graph..." -ForegroundColor Cyan
    Write-Host ""
    
    # Check current Graph connection first
    $currentContext = Get-MgContext -ErrorAction SilentlyContinue
    
    if ($currentContext) {
        Write-Host "[INFO] Current Microsoft Graph Connection:" -ForegroundColor Cyan
        Write-Host "  Account:     $($currentContext.Account)" -ForegroundColor White
        Write-Host "  Tenant ID:   $($currentContext.TenantId)" -ForegroundColor White
        Write-Host "  Scopes:      $($currentContext.Scopes -join ', ')" -ForegroundColor Gray
        Write-Host ""
        
        # Use tenant from current context if not provided
        if (-not $TenantId) {
            $TenantId = $currentContext.TenantId
        }
    } else {
        Write-Host "[WARN]  Not currently logged in to Microsoft Graph" -ForegroundColor Yellow
        Write-Host ""
    }
    
    # Get tenant ID from Azure context if still not available
    if (-not $TenantId) {
        try {
            $context = Get-AzContext -ErrorAction SilentlyContinue
            if ($context) {
                $TenantId = $context.Tenant.Id
                Write-Host "  Found tenant from Azure context: $TenantId" -ForegroundColor Gray
            }
        }
        catch {
            # Ignore error
        }
        
        if (-not $TenantId) {
            try {
                $TenantId = az account show --query tenantId -o tsv 2>$null
                if ($TenantId) {
                    Write-Host "  Found tenant from Azure CLI: $TenantId" -ForegroundColor Gray
                }
            }
            catch {
                # Ignore error
            }
        }
    }
    
    if (-not $TenantId) {
        throw "No tenant ID available. Please sign in first with: Connect-AzAccount or az login"
    }
    
    # Check required scopes
    # NOTE: Do NOT include Directory.ReadWrite.All - it BLOCKS Agent deletion (Microsoft Known Issue)
    $requiredScopes = @(
        "AgentIdentityBlueprint.AddRemoveCreds.All",
        "AgentIdentityBlueprint.Create",
        "AgentIdentityBlueprint.DeleteRestore.All",
        "AgentIdentity.DeleteRestore.All",
        "DelegatedPermissionGrant.ReadWrite.All",
        "Application.Read.All",
        "AgentIdentityBlueprintPrincipal.Create",
        "AppRoleAssignment.ReadWrite.All",
        "AgentIdUser.ReadWrite.IdentityParentedBy",
        "Directory.Read.All",
        "User.Read"
    )
    
    $needsReconnect = $false
    if ($currentContext) {
        # Check if all required scopes are present
        $missingScopes = $requiredScopes | Where-Object { $_ -notin $currentContext.Scopes }
        if ($missingScopes.Count -gt 0) {
            Write-Host "  WARNING: Missing required scopes: $($missingScopes -join ', ')" -ForegroundColor Yellow
            Write-Host "  The script may fail without these scopes." -ForegroundColor Yellow
            Write-Host ""
            Write-Host "  Please reconnect manually with all required scopes:" -ForegroundColor Yellow
            Write-Host "  Disconnect-MgGraph" -ForegroundColor White
            $scopesString = ($requiredScopes | ForEach-Object { "'$_'" }) -join ','
            Write-Host "  Connect-MgGraph -Scopes $scopesString -TenantId $TenantId" -ForegroundColor White
            Write-Host ""
            throw "Missing required Microsoft Graph scopes. Please reconnect with all scopes as shown above."
        } else {
            Write-Host "  All required scopes present" -ForegroundColor Green
            Write-Host ""
        }
    } else {
        Write-Host "  ERROR: Not connected to Microsoft Graph" -ForegroundColor Red
        Write-Host ""
        Write-Host "  Please connect manually first:" -ForegroundColor Yellow
        $scopesString = ($requiredScopes | ForEach-Object { "'$_'" }) -join ','
        Write-Host "  Connect-MgGraph -Scopes $scopesString -TenantId $TenantId" -ForegroundColor White
        Write-Host ""
        throw "Not connected to Microsoft Graph. Please connect as shown above."
    }
    
    Write-Host ""
    Write-Host "Connected to tenant: $TenantId" -ForegroundColor Green
    Write-Host ""
    
    return @{
        TenantId = $TenantId
        Account  = $currentContext.Account
    }
} 
#endregion

#region Step 2: Blueprint Creation 

function New-AgentIdentityBlueprint {
    <#
    .SYNOPSIS
    Creates an Agent Identity Blueprint with a service principal and client secret.
    
    .PARAMETER BlueprintName
    The display name for the blueprint. REQUIRED - you must provide a meaningful name.
    
    .PARAMETER TenantId
    The Entra tenant ID.
    
    .EXAMPLE
    New-AgentIdentityBlueprint -BlueprintName "Production Blueprint" -TenantId "xxx-xxx"
    #>
    param(
        [Parameter(Mandatory = $true, HelpMessage = "Please provide a name for the blueprint (e.g., 'Production Blueprint', 'Weather Agent Blueprint')")]
        [ValidateNotNullOrEmpty()]
        [string]$BlueprintName,
        
        [Parameter(Mandatory = $true)]
        [string]$TenantId
    )
    
    Write-Host "[INFO] Step 2: Creating Agent Identity Blueprint..." -ForegroundColor Cyan
    
    # Verify Microsoft Graph connection before proceeding
    $currentContext = Get-MgContext -ErrorAction SilentlyContinue
    if (-not $currentContext) {
        Write-Host ""
        Write-Host "  ERROR: Microsoft Graph connection lost" -ForegroundColor Red
        Write-Host "  Please reconnect and try again:" -ForegroundColor Yellow
        Write-Host "  Connect-MgGraph -Scopes 'AgentIdentityBlueprint.AddRemoveCreds.All','AgentIdentityBlueprint.Create','DelegatedPermissionGrant.ReadWrite.All','Application.Read.All','AgentIdentityBlueprintPrincipal.Create','AppRoleAssignment.ReadWrite.All','AgentIdUser.ReadWrite.IdentityParentedBy','Directory.Read.All','User.Read' -TenantId $TenantId" -ForegroundColor White
        Write-Host ""
        throw "Not connected to Microsoft Graph"
    }
    
    Write-Host "  Current connection: $($currentContext.Account)" -ForegroundColor Gray
    Write-Host "  Tenant: $($currentContext.TenantId)" -ForegroundColor Gray
    Write-Host "  Blueprint Name: $BlueprintName" -ForegroundColor Gray
    Write-Host ""
    
    # Get current user ID
    try {
        Write-Host "  Getting user information..." -ForegroundColor Gray
        $me = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/me" -ErrorAction Stop
        $myUserId = $me.id
        Write-Host "  User ID: $myUserId" -ForegroundColor Gray
    }
    catch {
        Write-Host ""
        Write-Host "  ERROR: Failed to get user information from Microsoft Graph" -ForegroundColor Red
        Write-Host "  Error details: $($_.Exception.Message)" -ForegroundColor Gray
        Write-Host ""
        
        if ($_.Exception.Message -like "*DeviceCodeCredential*") {
            Write-Host "  DeviceCodeCredential error detected" -ForegroundColor Yellow
            Write-Host "  This usually means the connection needs to be refreshed." -ForegroundColor Yellow
            Write-Host ""
            Write-Host "  Please disconnect and reconnect:" -ForegroundColor Cyan
            Write-Host "  Disconnect-MgGraph" -ForegroundColor White
            Write-Host "  Connect-MgGraph -Scopes 'AgentIdentityBlueprint.AddRemoveCreds.All','AgentIdentityBlueprint.Create','DelegatedPermissionGrant.ReadWrite.All','Application.Read.All','AgentIdentityBlueprintPrincipal.Create','AppRoleAssignment.ReadWrite.All','AgentIdUser.ReadWrite.IdentityParentedBy','Directory.Read.All','User.Read' -TenantId $TenantId" -ForegroundColor White
            Write-Host ""
        } else {
            Write-Host "  This may indicate:" -ForegroundColor Yellow
            Write-Host "  - Microsoft.Graph module version mismatch" -ForegroundColor White
            Write-Host "  - Expired or invalid authentication token" -ForegroundColor White
            Write-Host "  - Network connectivity issues" -ForegroundColor White
            Write-Host ""
            Write-Host "  Try reconnecting:" -ForegroundColor Cyan
            Write-Host "  Disconnect-MgGraph" -ForegroundColor White
            Write-Host "  Connect-MgGraph -Scopes 'AgentIdentityBlueprint.AddRemoveCreds.All','AgentIdentityBlueprint.Create','DelegatedPermissionGrant.ReadWrite.All','Application.Read.All','AgentIdentityBlueprintPrincipal.Create','AppRoleAssignment.ReadWrite.All','AgentIdUser.ReadWrite.IdentityParentedBy','Directory.Read.All','User.Read' -TenantId $TenantId" -ForegroundColor White
            Write-Host ""
        }
        throw $_
    }
    
    # Create blueprint
    $body = @{
        "@odata.type"         = "Microsoft.Graph.AgentIdentityBlueprint"
        displayName           = $BlueprintName
        "sponsors@odata.bind" = @("https://graph.microsoft.com/v1.0/users/$myUserId")
        "owners@odata.bind"   = @("https://graph.microsoft.com/v1.0/users/$myUserId")
    }
    
    $blueprint = Invoke-MgGraphRequest -Method POST `
        -Uri "https://graph.microsoft.com/beta/applications/" `
        -Headers @{ "OData-Version" = "4.0" } `
        -Body ($body | ConvertTo-Json)
    
    Write-Host "  [OK] Blueprint created: $($blueprint.appId)" -ForegroundColor Green
    
    # Create blueprint principal
    $principalBody = @{ appId = $blueprint.appId }
    $principal = Invoke-MgGraphRequest -Method POST `
        -Uri "https://graph.microsoft.com/beta/serviceprincipals/graph.agentIdentityBlueprintPrincipal" `
        -Headers @{ "OData-Version" = "4.0" } `
        -Body ($principalBody | ConvertTo-Json)
    
    Write-Host "  [OK] Blueprint Principal created: $($principal.id)" -ForegroundColor Green
    
    # Wait for principal to propagate
    Write-Host "  [WAIT] Waiting for principal to propagate..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
    
    # Add client secret
    $blueprintApp = (Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/applications?`$filter=appId eq '$($blueprint.appId)'").value[0]
    
    $secretBody = @{
        passwordCredential = @{
            displayName = "Agent ID Secret " + $BlueprintName
        }
    }
    
    $secret = Invoke-MgGraphRequest -Method POST `
        -Uri "https://graph.microsoft.com/beta/applications/$($blueprintApp.id)/addPassword" `
        -Body ($secretBody | ConvertTo-Json)
    
    # Debug: Check what properties the secret object has
    if (-not $secret.secretText) {
        Write-Host "  [WARN]  DEBUG: Secret object properties:" -ForegroundColor Yellow
        $secret | ConvertTo-Json -Depth 5 | Write-Host
        Write-Error "Secret object doesn't have 'secretText' property!"
        throw "Failed to get client secret from addPassword response"
    }
    
    Write-Host "  [OK] Client secret created (length: $($secret.secretText.Length) chars)" -ForegroundColor Green
    Write-Host "`n  [KEY] CLIENT SECRET (copy this now):" -ForegroundColor Yellow
    Write-Host "  $($secret.secretText)" -ForegroundColor White
    Write-Host ""
    
    # Verify the secret works before proceeding
    Write-Host "  [WAIT] Verifying client secret is valid..." -ForegroundColor Yellow
    $maxRetries = 10
    $retryCount = 0
    $secretValid = $false
    
    while (-not $secretValid -and $retryCount -lt $maxRetries) {
        try {
            $testTokenBody = @{
                client_id     = $blueprint.appId
                scope         = "https://graph.microsoft.com/.default"
                grant_type    = "client_credentials"
                client_secret = $secret.secretText
            }
            
            $testResponse = Invoke-RestMethod -Method POST `
                -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" `
                -ContentType "application/x-www-form-urlencoded" `
                -Body $testTokenBody `
                -ErrorAction Stop
            
            if ($testResponse.access_token) {
                $secretValid = $true
                Write-Host "  [OK] Client secret verified and working!" -ForegroundColor Green
            }
        }
        catch {
            $retryCount++
            if ($retryCount -lt $maxRetries) {
                Write-Host "  [WAIT] Secret not ready yet, waiting... (attempt $retryCount/$maxRetries)" -ForegroundColor Yellow
                Start-Sleep -Seconds 3
            }
            else {
                Write-Warning "  [WARN]  Secret verification failed after $maxRetries attempts. Proceeding anyway..."
            }
        }
    }
    
    Write-Host "  [WARN]  SAVE THIS SECRET - you won't see it again!" -ForegroundColor Yellow
    
    return @{
        BlueprintName     = $BlueprintName
        BlueprintAppId    = $blueprint.appId
        BlueprintObjectId = $blueprint.id
        PrincipalId       = $principal.id
        ClientSecret      = $secret.secretText
        UserId            = $myUserId
    }
}

#endregion

#region Step 3: Agent Identity Creation

function New-AgentIdentity {
    <#
    .SYNOPSIS
    Creates an Agent Identity from a blueprint.
    
    .PARAMETER AgentName
    The display name for the agent identity. If not provided, auto-generates with timestamp.
    
    .PARAMETER BlueprintAppId
    The App ID of the blueprint to use.
    
    .PARAMETER ClientSecret
    The client secret of the blueprint.
    
    .PARAMETER TenantId
    The Entra tenant ID.
    
    .PARAMETER UserId
    The user ID to set as sponsor.
    #>
    param(
        [Parameter(Mandatory = $false)]
        [string]$AgentName,
        
        [Parameter(Mandatory = $true)]
        [string]$BlueprintAppId,
        
        [Parameter(Mandatory = $true)]
        [string]$ClientSecret,
        
        [Parameter(Mandatory = $true)]
        [string]$TenantId,
        
        [Parameter(Mandatory = $true)]
        [string]$UserId
    )
    
    Write-Host "[AGENT] Step 3: Creating Agent Identity..." -ForegroundColor Cyan
    
    # Generate agent name with timestamp if not provided
    if (-not $AgentName) {
        $AgentName = "RZ PoC Agent (" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + ")"
    }
    
    # Get blueprint token for agent creation
    Write-Host "  [KEY] Using Blueprint App ID: $BlueprintAppId" -ForegroundColor Gray
    Write-Host "  [KEY] Secret length: $($ClientSecret.Length) characters" -ForegroundColor Gray
    
    $tokenBody = @{
        client_id     = $BlueprintAppId
        scope         = "https://graph.microsoft.com/.default"
        grant_type    = "client_credentials"
        client_secret = $ClientSecret
    }
    
    # Retry logic for token acquisition (secret may need time to propagate)
    $maxRetries = 5
    $retryCount = 0
    $blueprintToken = $null
    
    while (-not $blueprintToken -and $retryCount -lt $maxRetries) {
        try {
            $tokenResponse = Invoke-RestMethod -Method POST `
                -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" `
                -ContentType "application/x-www-form-urlencoded" `
                -Body $tokenBody `
                -ErrorAction Stop
            
            $blueprintToken = $tokenResponse.access_token
            Write-Host "  [OK] Got blueprint token for agent creation" -ForegroundColor Green
        }
        catch {
            $retryCount++
            $errorResponse = $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction SilentlyContinue
            
            if ($errorResponse.error -eq "invalid_client" -and $retryCount -lt $maxRetries) {
                Write-Host "  [WAIT] Secret not ready yet, waiting... (attempt $retryCount/$maxRetries)" -ForegroundColor Yellow
                Start-Sleep -Seconds 3
            }
            else {
                Write-Error "  [ERROR] Failed to get blueprint token. This usually means:"
                Write-Error "     - The client secret is invalid or expired"
                Write-Error "     - The blueprint application was deleted"
                Write-Error "     - Blueprint App ID: $BlueprintAppId"
                Write-Error "  Error: $($_.ErrorDetails.Message)"
                throw
            }
        }
    }
    
    if (-not $blueprintToken) {
        throw "Failed to acquire blueprint token after $maxRetries retries"
    }
    
    # Verify the token has the right claims
    try {
        $tokenPayload = Get-DecodedJwtToken -Token $blueprintToken | ConvertFrom-Json
        if ($tokenPayload.roles -notcontains "AgentIdentity.CreateAsManager") {
            Write-Warning "  [WARN]  Token doesn't have AgentIdentity.CreateAsManager role. Waiting for permissions to propagate..."
            Start-Sleep -Seconds 10
        }
    }
    catch {
        Write-Warning "  [WARN]  Could not decode token, proceeding anyway..."
    }
    
    # Create agent identity
    $agentIdentityBody = @{
        displayName              = $AgentName
        agentIdentityBlueprintId = $BlueprintAppId
        "sponsors@odata.bind"    = @("https://graph.microsoft.com/v1.0/users/$UserId")
    }
    
    $agentIdentity = Invoke-RestMethod -Method POST `
        -Uri "https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity" `
        -Headers @{
        "Authorization" = "Bearer $blueprintToken"
        "OData-Version" = "4.0"
        "Content-Type"  = "application/json"
    } `
        -Body ($agentIdentityBody | ConvertTo-Json)
    
    Write-Host "  [OK] Agent Identity created!" -ForegroundColor Green
    Write-Host "  App ID: $($agentIdentity.appId)" -ForegroundColor Gray
    Write-Host "  Service Principal ID: $($agentIdentity.id)" -ForegroundColor Gray
    
    return @{
        AgentName          = $AgentName
        AgentIdentityAppId = $agentIdentity.appId
        AgentIdentitySP    = $agentIdentity.id
    }
}

#endregion

#region Step 4: Token Exchange

function Get-AgentIdentityToken {
    <#
    .SYNOPSIS
    Performs the two-token exchange to get an agent identity access token.
    
    .PARAMETER BlueprintAppId
    The App ID of the blueprint.
    
    .PARAMETER ClientSecret
    The client secret of the blueprint.
    
    .PARAMETER AgentIdentityAppId
    The App ID of the agent identity.
    
    .PARAMETER TenantId
    The Entra tenant ID.
    
    .PARAMETER ShowClaims
    If specified, decodes and displays token claims.
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$BlueprintAppId,
        
        [Parameter(Mandatory = $true)]
        [string]$ClientSecret,
        
        [Parameter(Mandatory = $true)]
        [string]$AgentIdentityAppId,
        
        [Parameter(Mandatory = $true)]
        [string]$TenantId,
        
        [Parameter(Mandatory = $false)]
        [switch]$ShowClaims
    )
    
    Write-Host "[SYNC] Step 4: Performing Token Exchange (T1 -> T2)..." -ForegroundColor Cyan
    
    # Get T1 token (Blueprint impersonation token)
    $t1Body = @{
        client_id     = $BlueprintAppId
        scope         = "api://AzureADTokenExchange/.default"
        grant_type    = "client_credentials"
        client_secret = $ClientSecret
        fmi_path      = $AgentIdentityAppId
    }
    
    $t1Response = Invoke-RestMethod -Method POST `
        -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $t1Body
    
    $blueprintToken = $t1Response.access_token
    Write-Host "  [OK] Got T1 token (Blueprint impersonation)" -ForegroundColor Green
    
    if ($ShowClaims) {
        Write-Host "  T1 Claims:" -ForegroundColor Gray
        $t1Claims = Get-DecodedJwtToken -Token $blueprintToken
        Write-Host $t1Claims -ForegroundColor DarkGray
    }
    
    # Exchange T1 for T2 token (Agent identity token)
    $t2Body = @{
        client_id             = $AgentIdentityAppId
        scope                 = "https://graph.microsoft.com/.default"
        grant_type            = "client_credentials"
        client_assertion_type = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        client_assertion      = $blueprintToken
    }
    
    $t2Response = Invoke-RestMethod -Method POST `
        -Uri "https://login.microsoftonline.com/$TenantId/oauth2/v2.0/token" `
        -ContentType "application/x-www-form-urlencoded" `
        -Body $t2Body
    
    $agentToken = $t2Response.access_token
    Write-Host "  [OK] Got T2 token (Agent identity)" -ForegroundColor Green
    
    if ($ShowClaims) {
        Write-Host "  T2 Claims:" -ForegroundColor Gray
        $t2Claims = Get-DecodedJwtToken -Token $agentToken
        Write-Host $t2Claims -ForegroundColor DarkGray
    }
    
    return @{
        T1Token     = $blueprintToken
        T2Token     = $agentToken
        AccessToken = $agentToken
    }
}

#endregion

#region Step 5: Add Permissions

function Add-AgentIdentityPermissions {
    <#
    .SYNOPSIS
    Adds Microsoft Graph API permissions to an agent identity.
    
    .PARAMETER AgentIdentitySP
    The service principal ID of the agent identity.
    
    .PARAMETER Permissions
    Array of permission names (e.g., "User.Read.All", "User.ReadWrite.All").
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$AgentIdentitySP,
        
        [Parameter(Mandatory = $false)]
        [string[]]$Permissions = @("User.Read.All")
    )
    
    Write-Host "[LOCK] Step 5: Adding Permissions to Agent Identity..." -ForegroundColor Cyan
    
    # Wait for agent service principal to be queryable
    Write-Host "  [WAIT] Verifying agent service principal is available..." -ForegroundColor Yellow
    $maxRetries = 10
    $retryCount = 0
    $spExists = $false
    
    while (-not $spExists -and $retryCount -lt $maxRetries) {
        try {
            $testSP = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentIdentitySP" -ErrorAction SilentlyContinue
            if ($testSP) {
                $spExists = $true
                Write-Host "  [OK] Agent service principal is ready" -ForegroundColor Green
            }
        }
        catch {
            $retryCount++
            if ($retryCount -lt $maxRetries) {
                Write-Host "  [WAIT] Waiting for service principal propagation (attempt $retryCount/$maxRetries)..." -ForegroundColor Yellow
                Start-Sleep -Seconds 3
            }
        }
    }
    
    if (-not $spExists) {
        Write-Error "  [ERROR] Agent service principal not found after $maxRetries attempts. ID: $AgentIdentitySP"
        return
    }
    
    # Get Microsoft Graph Service Principal ID
    $graphSPs = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=displayName eq 'Microsoft Graph'"
    $graphSP = $graphSPs.value[0].id
    Write-Host "  Microsoft Graph SP ID: $graphSP" -ForegroundColor Gray
    
    # Permission mapping - Add more permissions here as needed
    $permissionMap = @{
        "User.Read.All"                    = "df021288-bdef-4463-88db-98f22de89214"
        "User.ReadWrite.All"               = "741f803b-c850-494e-b5df-cde7c675a1ca"
        "Directory.Read.All"               = "7ab1d382-f21e-4acd-a863-ba3e13f7da61"
        "Directory.ReadWrite.All"          = "19dbc75e-c2e2-444c-a770-ec69d8559fc7"
        "Mail.Read"                        = "810c84a8-4a9e-49e6-bf7d-12d183f40d01"
        "Mail.ReadWrite"                   = "e2a3a72e-5f79-4c64-b1b1-878b674786c9"
        "Calendars.Read"                   = "798ee544-9d2d-430c-a058-570e29e34338"
        "Calendars.ReadWrite"              = "ef54d2bf-783f-4e0f-bca1-3210c0444d99"
        "Contacts.Read"                    = "089fe4d0-434a-44c5-8827-41ba8a0b17f5"
        "Contacts.ReadWrite"               = "6918b873-d17a-4dc1-b314-35f528134491"
        "Files.Read.All"                   = "01d4889c-1287-42c6-ac1f-5d1e02578ef6"
        "Files.ReadWrite.All"              = "75359482-378d-4052-8f01-80520e7db3cd"
        "Sites.Read.All"                   = "332a536c-c7ef-4017-ab91-336970924f0d"
        "Sites.ReadWrite.All"              = "9492366f-7969-46a4-8d15-ed1a20078fff"
        "Group.Read.All"                   = "5b567255-7703-4780-807c-7be8301ae99b"
        "Group.ReadWrite.All"              = "62a82d76-70ea-41e2-9197-370581804d09"
        "Team.ReadBasic.All"               = "2280dda6-0bfd-44ee-a2f4-cb867cfc4c1e"
        "TeamSettings.Read.All"            = "242607bd-1d2c-432c-82eb-bdb27baa23ab"
        "TeamSettings.ReadWrite.All"       = "bdd80a03-d9bc-451d-b7c4-ce7c63fe3c8f"
        "Channel.ReadBasic.All"            = "59a6b24b-4225-4393-8165-ebaec5f55d7a"
        "ChannelSettings.Read.All"         = "c97b873f-f59f-49aa-8a0e-52b32d762124"
        "ChannelSettings.ReadWrite.All"    = "243cded2-bd16-4fd6-a953-ff8177894c3d"
        "Reports.Read.All"                 = "230c1aed-a721-4c5d-9cb4-a90514e508ef"
        "Application.Read.All"             = "9a5d68dd-52b0-4cc2-bd40-abcf44ac3a30"
        "Application.ReadWrite.All"        = "1bfefb4e-e0b5-418b-a88f-73c46d2cc8e9"
    }
    
    foreach ($permission in $Permissions) {
        if (-not $permissionMap.ContainsKey($permission)) {
            Write-Warning "  [WARN]  Unknown permission: $permission (skipping)"
            continue
        }
        
        $appRoleId = $permissionMap[$permission]
        
        try {
            $permissionBody = @{
                principalId = $AgentIdentitySP
                resourceId  = $graphSP
                appRoleId   = $appRoleId
            }
            
            Invoke-MgGraphRequest -Method POST `
                -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$AgentIdentitySP/appRoleAssignments" `
                -Body ($permissionBody | ConvertTo-Json) | Out-Null
            
            Write-Host "  [OK] Added permission: $permission" -ForegroundColor Green
        }
        catch {
            if ($_.Exception.Message -like "*already exists*") {
                Write-Host "  [INFO]  Permission already exists: $permission" -ForegroundColor Yellow
            }
            else {
                Write-Error "  [ERROR] Failed to add $permission : $_"
            }
        }
    }
    
    Write-Host "  [WARN]  Remember to get a new token to use these permissions!" -ForegroundColor Yellow
}

#endregion

#region Step 5b: Create Agent Users

function New-AgentUser {
    <#
    .SYNOPSIS
    Creates an Agent User for the specified agent identity.
    
    .PARAMETER AgentIdentityId
    The App ID (client ID) of the agent identity.
    
    .PARAMETER DisplayName
    Optional display name for the agent user. If not provided, auto-generates with agent name and timestamp.
    
    .PARAMETER AgentName
    The agent identity name (used for auto-generating display name).
    
    .EXAMPLE
    New-AgentUser -AgentIdentityId "12345678-1234-1234-1234-123456789abc" -DisplayName "My Agent User"
    
    .EXAMPLE
    New-AgentUser -AgentIdentityId "12345678-1234-1234-1234-123456789abc" -AgentName "Weather Agent"
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$AgentIdentityId,
        
        [Parameter(Mandatory = $false)]
        [string]$DisplayName,
        
        [Parameter(Mandatory = $false)]
        [string]$AgentName
    )
    
    Write-Host "[USER] Step 5b: Creating Agent User..." -ForegroundColor Cyan
    
    # Generate display name if not provided
    if (-not $DisplayName) {
        if ($AgentName) {
            $DisplayName = "$AgentName User " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        } else {
            $DisplayName = "Agent User " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
        }
    }
    
    Write-Host "  Agent Identity ID: $AgentIdentityId" -ForegroundColor Gray
    Write-Host "  Display Name:      $DisplayName" -ForegroundColor Gray
    Write-Host ""
    
    # Look up the Service Principal Object ID for this Agent Identity App ID
    # The identityParentId must be the Service Principal Object ID, not the App ID
    Write-Host "  [INFO] Looking up Service Principal for Agent Identity..." -ForegroundColor Gray
    
    # Retry logic to handle Service Principal propagation delay
    $maxRetries = 5
    $retryCount = 0
    $servicePrincipal = $null
    $servicePrincipalObjectId = $null
    
    while (-not $servicePrincipal -and $retryCount -lt $maxRetries) {
        $retryCount++
        
        try {
            $servicePrincipal = Get-MgServicePrincipal -Filter "appId eq '$AgentIdentityId'" -ErrorAction Stop
            
            if ($servicePrincipal) {
                $servicePrincipalObjectId = $servicePrincipal.Id
                Write-Host "  [OK] Service Principal found: $servicePrincipalObjectId" -ForegroundColor Green
                Write-Host ""
            }
            else {
                # Service Principal not found yet
                if ($retryCount -lt $maxRetries) {
                    Write-Host "  [WAIT] Service Principal not available yet, waiting... (attempt $retryCount/$maxRetries)" -ForegroundColor Yellow
                    Start-Sleep -Seconds 3
                }
            }
        }
        catch {
            # Error during lookup - likely propagation delay
            if ($retryCount -lt $maxRetries) {
                Write-Host "  [WAIT] Service Principal lookup error, retrying... (attempt $retryCount/$maxRetries)" -ForegroundColor Yellow
                Start-Sleep -Seconds 3
            }
        }
    }
    
    # If we exhausted retries without finding the Service Principal
    if (-not $servicePrincipalObjectId) {
        Write-Host "  [ERROR] Failed to find Service Principal for Agent Identity after $maxRetries attempts" -ForegroundColor Red
        Write-Host "  App ID: $AgentIdentityId" -ForegroundColor Red
        Write-Host "  This may indicate:" -ForegroundColor Yellow
        Write-Host "    • Service Principal propagation is taking longer than usual (15+ seconds)" -ForegroundColor Yellow
        Write-Host "    • Agent Identity was not created successfully" -ForegroundColor Yellow
        Write-Host "    • Insufficient permissions to query Service Principals (need Directory.Read.All)" -ForegroundColor Yellow
        Write-Host ""
        throw "Cannot create Agent User: Service Principal not found for App ID: $AgentIdentityId after $maxRetries attempts"
    }
    
    # Create agent user via Graph API
    # Generate mailNickname and userPrincipalName from display name
    $mailNickname = $DisplayName -replace '[^a-zA-Z0-9]', ''
    if ($mailNickname.Length -eq 0) {
        $mailNickname = "AgentUser" + (Get-Random -Minimum 1000 -Maximum 9999)
    }
    
    # Get tenant domain for UPN
    try {
        $org = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/organization"
        $verifiedDomain = $org.value[0].verifiedDomains | Where-Object { $_.isDefault -eq $true } | Select-Object -First 1
        $domain = $verifiedDomain.name
    }
    catch {
        Write-Warning "Could not get verified domain, using onmicrosoft.com"
        # Fallback - will likely fail, but let's try
        $domain = "onmicrosoft.com"
    }
    
    $userPrincipalName = "$mailNickname@$domain"
    
    $body = @{
        "@odata.type"     = "microsoft.graph.agentUser"
        accountEnabled    = $true
        displayName       = $DisplayName
        mailNickname      = $mailNickname
        userPrincipalName = $userPrincipalName
        identityParentId  = $servicePrincipalObjectId  # USE SERVICE PRINCIPAL OBJECT ID, NOT APP ID
    }
    
    try {
        $agentUser = Invoke-MgGraphRequest -Method POST `
            -Uri "https://graph.microsoft.com/beta/users/microsoft.graph.agentUser" `
            -Body ($body | ConvertTo-Json)
        
        Write-Host "  [OK] Agent User created successfully!" -ForegroundColor Green
        Write-Host "  Agent User ID:          $($agentUser.id)" -ForegroundColor Gray
        Write-Host "  Display Name:           $($agentUser.displayName)" -ForegroundColor Gray
        Write-Host "  User Principal Name:    $($agentUser.userPrincipalName)" -ForegroundColor Gray
        Write-Host ""
        
        return @{
            Id                 = $agentUser.id
            AgentUserId        = $agentUser.id
            DisplayName        = $agentUser.displayName
            UserPrincipalName  = $agentUser.userPrincipalName
            AgentIdentityId    = $AgentIdentityId
        }
    }
    catch {
        Write-Host "  [ERROR] Failed to create agent user" -ForegroundColor Red
        Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host ""
        
        if ($_.Exception.Message -like "*Forbidden*" -or $_.Exception.Message -like "*403*") {
            Write-Host "  This usually means you need the AgentIdUser.ReadWrite.IdentityParentedBy scope." -ForegroundColor Yellow
            Write-Host "  Reconnect with:" -ForegroundColor Yellow
            Write-Host "  Disconnect-MgGraph" -ForegroundColor White
            Write-Host "  Connect-MgGraph -Scopes 'AgentIdUser.ReadWrite.IdentityParentedBy',...other scopes" -ForegroundColor White
        }
        elseif ($_.Exception.Message -like "*not found*" -or $_.Exception.Message -like "*BadRequest*") {
            Write-Host "  The Agent User API endpoint may not be available in your tenant yet." -ForegroundColor Yellow
            Write-Host "  This is a beta API and may not be rolled out to all tenants." -ForegroundColor Yellow
        }
        
        Write-Host ""
        throw $_
    }
}

function Get-AgentUsersList {
    <#
    .SYNOPSIS
    Lists all agent users in the tenant.
    
    .EXAMPLE
    Get-AgentUsersList
    #>
    Write-Host "[USER] Agent Users:" -ForegroundColor Cyan
    
    try {
        # Query users with agentUser type
        $agentUsers = Invoke-MgGraphRequest -Method GET `
            -Uri "https://graph.microsoft.com/beta/users?`$filter=userType eq 'AgentUser'"
        
        if ($agentUsers.value.Count -eq 0) {
            Write-Host "  No agent users found" -ForegroundColor Yellow
        } else {
            $agentUsers.value | Select-Object displayName, userPrincipalName, id | Format-Table -AutoSize
        }
    }
    catch {
        Write-Host "  [ERROR] Failed to list agent users: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  Try using: Get-MgUser -Filter \"userType eq 'AgentUser'\"" -ForegroundColor Yellow
    }
}

#endregion

#region Step 6: Test Agent Token

function Test-AgentIdentityToken {
    <#
    .SYNOPSIS
    Tests the agent identity token by calling Microsoft Graph API.
    
    .PARAMETER AccessToken
    The agent identity access token (T2 token).
    
    .PARAMETER MaxRetries
    Maximum number of retry attempts. Default: 10
    
    .PARAMETER RetryDelaySeconds
    Seconds to wait between retries. Default: 10
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$AccessToken,
        
        [Parameter(Mandatory = $false)]
        [int]$MaxRetries = 10,
        
        [Parameter(Mandatory = $false)]
        [int]$RetryDelaySeconds = 10
    )
    
    Write-Host "[TEST] Step 6: Testing Agent Identity Token..." -ForegroundColor Cyan
    
    $retryCount = 0
    $success = $false
    $lastError = $null
    
    while (-not $success -and $retryCount -lt $MaxRetries) {
        $retryCount++
        
        try {
            $response = Invoke-RestMethod -Method GET `
                -Uri "https://graph.microsoft.com/v1.0/users?`$top=5" `
                -Headers @{
                "Authorization" = "Bearer $AccessToken"
                "Content-Type"  = "application/json"
            }
            
            Write-Host "  [OK] Successfully called Graph API!" -ForegroundColor Green
            Write-Host "  Retrieved $($response.value.Count) users:`n" -ForegroundColor Gray
            
            # Display users in a formatted table
            $userTable = $response.value | Select-Object @{
                Name       = 'Display Name'
                Expression = { $_.displayName }
            }, @{
                Name       = 'User Principal Name'
                Expression = { $_.userPrincipalName }
            }, @{
                Name       = 'ID'
                Expression = { $_.id }
            } | Format-Table -AutoSize | Out-String
            
            Write-Host $userTable
            
            $success = $true
            return $true
        }
        catch {
            $lastError = $_
            
            if ($retryCount -lt $MaxRetries) {
                Write-Host "  [WAIT] Attempt $retryCount/$MaxRetries failed. Waiting $RetryDelaySeconds seconds before retry..." -ForegroundColor Yellow
                Write-Host "  Error: $($_.Exception.Message)" -ForegroundColor Gray
                Start-Sleep -Seconds $RetryDelaySeconds
            }
        }
    }
    
    # All retries exhausted - show error details
    Write-Host "  [ERROR] Failed to call Graph API after $MaxRetries attempts" -ForegroundColor Red
    Write-Host "  Error: $lastError" -ForegroundColor Red
    
    # Show token claims to help diagnose
    Write-Host "`n  [INFO] Token claims (to verify permissions):" -ForegroundColor Yellow
    try {
        $claims = Get-DecodedJwtToken -Token $AccessToken | ConvertFrom-Json
        Write-Host "  - Audience: $($claims.aud)" -ForegroundColor Gray
        Write-Host "  - App ID: $($claims.appid)" -ForegroundColor Gray
        if ($claims.roles) {
            Write-Host "  - Roles: $($claims.roles -join ', ')" -ForegroundColor Gray
        }
        else {
            Write-Host "  - Roles: NONE (permissions not yet in token)" -ForegroundColor Yellow
        }
    }
    catch {
        Write-Host "  Could not decode token claims" -ForegroundColor Gray
    }
    
    Write-Host "`n  [TIP] Tip: Permissions may take few minutes to fully propagate in Entra." -ForegroundColor Cyan
    Write-Host "      Try getting a new token in a few minutes if roles are missing.`n" -ForegroundColor Cyan
    
    return $false
}

#endregion

#region Main Orchestration Function

function Start-EntraAgentIDWorkflow {
    <#
    .SYNOPSIS
    Complete end-to-end workflow to create and configure an Entra Agent Identity.
    
    .DESCRIPTION
    This function orchestrates all steps:
    1. Connect to Azure and Microsoft Graph
    2. Create Agent Identity Blueprint
    3. Create Agent Identity
    4. Perform Token Exchange (T1 -> T2)
    5. Add Microsoft Graph Permissions
    6. Get new token with permissions
    7. Test the agent token
    
    .PARAMETER TenantId
    The Entra tenant ID. If not provided, uses current context.
    
    .PARAMETER BlueprintName
    Blueprint name. REQUIRED - you must provide a meaningful name for the blueprint.
    
    .PARAMETER AgentName
    Agent name. REQUIRED - you must provide a meaningful name for the agent identity.
    
    .PARAMETER Permissions
    Array of Graph API permissions to add to the agent identity. Default: @("User.Read.All")
    
    .PARAMETER CreateAgentUser
    If specified, creates an agent user for the agent identity.
    
    .PARAMETER AgentUserDisplayName
    Custom display name for the agent user. If not provided, auto-generates based on agent name.
    
    .PARAMETER SkipTest
    If specified, skips the API test at the end.
    
    .EXAMPLE
    Start-EntraAgentIDWorkflow -BlueprintName "Production Blueprint" -AgentName "Weather Agent"
    
    .EXAMPLE
    Start-EntraAgentIDWorkflow -BlueprintName "Dev Blueprint" -AgentName "Weather Agent" -CreateAgentUser
    
    .EXAMPLE
    Start-EntraAgentIDWorkflow -BlueprintName "My Blueprint" -AgentName "My Agent" -Permissions @("User.Read.All", "Directory.Read.All") -CreateAgentUser -AgentUserDisplayName "My Weather Agent User"
    #>
    param(
        [Parameter(Mandatory = $false)]
        [string]$TenantId,
        
        [Parameter(Mandatory = $true, HelpMessage = "Please provide a name for the blueprint (e.g., 'Production Blueprint', 'Dev Blueprint')")]
        [ValidateNotNullOrEmpty()]
        [string]$BlueprintName,
        
        [Parameter(Mandatory = $true, HelpMessage = "Please provide a name for the agent identity (e.g., 'Weather Agent', 'Sales Agent')")]
        [ValidateNotNullOrEmpty()]
        [string]$AgentName,
        
        [Parameter(Mandatory = $false)]
        [string[]]$Permissions = @("User.Read.All"),
        
        [Parameter(Mandatory = $false)]
        [switch]$CreateAgentUser,
        
        [Parameter(Mandatory = $false)]
        [string]$AgentUserDisplayName,
        
        [Parameter(Mandatory = $false)]
        [switch]$SkipTest
    )
    
    Write-Host "`n============================================================" -ForegroundColor Cyan
    Write-Host "   Microsoft Entra Agent ID - Complete Workflow" -ForegroundColor Cyan
    Write-Host "============================================================`n" -ForegroundColor Cyan
    
    Write-Host "[INFO]  Note: This workflow creates NEW blueprint and agent identities each time." -ForegroundColor Yellow
    Write-Host "   Old blueprints will remain in your tenant until manually deleted." -ForegroundColor Yellow
    Write-Host ""
  
    try {
        # Step 1: Connect
        $connection = Connect-EntraAgentIDEnvironment -TenantId $TenantId
        Start-Sleep -Seconds 1
        
        # Step 2: Create Blueprint (always creates a new one)
        Write-Host "[NOTE] Creating a NEW blueprint for this workflow..." -ForegroundColor Cyan
        $blueprintParams = @{
            BlueprintName = $BlueprintName
            TenantId      = $connection.TenantId
        }
        
        $blueprint = New-AgentIdentityBlueprint @blueprintParams
        Write-Host "  [WAIT] Waiting for blueprint to fully propagate..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        
        # Step 3: Create Agent Identity
        $agent = New-AgentIdentity `
            -AgentName $AgentName `
            -BlueprintAppId $blueprint.BlueprintAppId `
            -ClientSecret $blueprint.ClientSecret `
            -TenantId $connection.TenantId `
            -UserId $blueprint.UserId
        Start-Sleep -Seconds 3
        
        # Step 4: Get Initial Token (before permissions)
        Write-Host "`n[INFO] Getting initial token (before permissions)..." -ForegroundColor Cyan
        $tokens1 = Get-AgentIdentityToken `
            -BlueprintAppId $blueprint.BlueprintAppId `
            -ClientSecret $blueprint.ClientSecret `
            -AgentIdentityAppId $agent.AgentIdentityAppId `
            -TenantId $connection.TenantId
        Start-Sleep -Seconds 1
        
        # Step 5: Add Permissions
        Add-AgentIdentityPermissions `
            -AgentIdentitySP $agent.AgentIdentitySP `
            -Permissions $Permissions
        Write-Host "  [WAIT] Waiting for permissions to propagate (15 seconds)..." -ForegroundColor Gray
        Write-Host "     Note: Permission propagation to new tokens can take 5-10 minutes in Entra" -ForegroundColor Yellow
        Start-Sleep -Seconds 15
        
        # Step 5b: Create Agent User (optional)
        $agentUser = $null
        if ($CreateAgentUser) {
            Write-Host ""
            $agentUserParams = @{
                AgentIdentityId = $agent.AgentIdentityAppId
                AgentName       = $agent.AgentName
            }
            if ($AgentUserDisplayName) {
                $agentUserParams.DisplayName = $AgentUserDisplayName
            }
            $agentUser = New-AgentUser @agentUserParams
            Start-Sleep -Seconds 2
        }
        
        # Step 6: Get New Token (with permissions)
        Write-Host "`n[SYNC] Getting new token with permissions..." -ForegroundColor Cyan
        $tokens2 = Get-AgentIdentityToken `
            -BlueprintAppId $blueprint.BlueprintAppId `
            -ClientSecret $blueprint.ClientSecret `
            -AgentIdentityAppId $agent.AgentIdentityAppId `
            -TenantId $connection.TenantId `
            -ShowClaims
        Start-Sleep -Seconds 1
        
        # Step 7: Test Token
        $testResult = $false
        if (-not $SkipTest) {
            $testResult = Test-AgentIdentityToken -AccessToken $tokens2.AccessToken
        }
        
        # Summary
        Write-Host "`n============================================================" -ForegroundColor Green
        Write-Host "   [OK] Workflow Completed Successfully!" -ForegroundColor Green
        Write-Host "============================================================`n" -ForegroundColor Green
        
        Write-Host "[INFO] Summary:" -ForegroundColor Cyan
        Write-Host "  Tenant ID:                $($connection.TenantId)" -ForegroundColor Gray
        Write-Host "  Blueprint Name:           $($blueprint.BlueprintName)" -ForegroundColor Gray
        Write-Host "  Blueprint App ID:         $($blueprint.BlueprintAppId)" -ForegroundColor Gray
        Write-Host "  Agent Name:               $($agent.AgentName)" -ForegroundColor Gray
        Write-Host "  Agent App ID:             $($agent.AgentIdentityAppId)" -ForegroundColor Gray
        Write-Host "  Agent Service Principal:  $($agent.AgentIdentitySP)" -ForegroundColor Gray
        Write-Host "  Permissions Added:        $($Permissions -join ', ')" -ForegroundColor Gray
        if ($agentUser) {
            Write-Host "  Agent User Created:       YES" -ForegroundColor Green
            Write-Host "    - User ID:              $($agentUser.AgentUserId)" -ForegroundColor Gray
            Write-Host "    - Display Name:         $($agentUser.DisplayName)" -ForegroundColor Gray
            Write-Host "    - UPN:                  $($agentUser.UserPrincipalName)" -ForegroundColor Gray
        }
        if (-not $SkipTest) {
            $testStatus = if ($testResult) { "[OK] PASSED" } else { "[WARN]  FAILED (permissions may need time to propagate)" }
            Write-Host "  API Test Result:          $testStatus" -ForegroundColor $(if ($testResult) { "Green" } else { "Yellow" })
            
            if (-not $testResult) {
                Write-Host "`n[TIP] To retry the test after permissions propagate (wait 5-10 minutes):" -ForegroundColor Cyan
                Write-Host "   `$newToken = Get-AgentIdentityToken -BlueprintAppId '$($blueprint.BlueprintAppId)' ``" -ForegroundColor Gray
                Write-Host "       -ClientSecret '<secret>' ``" -ForegroundColor Gray
                Write-Host "       -AgentIdentityAppId '$($agent.AgentIdentityAppId)' ``" -ForegroundColor Gray
                Write-Host "       -TenantId '$($connection.TenantId)' -ShowClaims" -ForegroundColor Gray
                Write-Host "   Test-AgentIdentityToken -AccessToken `$newToken.AccessToken`n" -ForegroundColor Gray
            }
        }
        Write-Host ""
        
        # Return all context for further use
        $result = @{
            Connection = $connection
            Blueprint  = $blueprint
            Agent      = $agent
            Tokens     = $tokens2
        }
        
        if ($agentUser) {
            $result.AgentUser = $agentUser
        }
        
        return $result
    }
    catch {
        Write-Host "`n============================================================" -ForegroundColor Red
        Write-Host "   [ERROR] Workflow Failed" -ForegroundColor Red
        Write-Host "============================================================`n" -ForegroundColor Red
        Write-Error "Error: $_"
        throw
    }
}

#endregion

#region Quick Access Functions

function Get-AgentIdentityList {
    <#
    .SYNOPSIS
    Lists all agent identities in the tenant.
    #>
    Write-Host "[AGENT] Agent Identities:" -ForegroundColor Cyan
    $agentIdentities = Invoke-MgGraphRequest -Method GET `
        -Uri "https://graph.microsoft.com/beta/servicePrincipals/graph.agentIdentity"
    
    $agentIdentities.value | Select-Object displayName, appId, id | Format-Table -AutoSize
}

function Get-BlueprintList {
    <#
    .SYNOPSIS
    Lists all blueprints in the tenant.
    #>
    Write-Host "[INFO] Blueprints:" -ForegroundColor Cyan
    $blueprints = Invoke-MgGraphRequest -Method GET `
        -Uri "https://graph.microsoft.com/beta/applications/graph.agentIdentityBlueprint"
    
    $blueprints.value | Select-Object displayName, appId, id | Format-Table -AutoSize
}

#endregion

# Script loaded message
Write-Host "`n[OK] Entra Agent ID Functions loaded!" -ForegroundColor Green
Write-Host ""
Write-Host "Quick Start:" -ForegroundColor Cyan
Write-Host "  Start-EntraAgentIDWorkflow -BlueprintName 'My Blueprint' -AgentName 'My Agent'" -ForegroundColor Yellow
Write-Host ""
Write-Host "With Agent User:" -ForegroundColor Cyan
Write-Host "  Start-EntraAgentIDWorkflow -BlueprintName 'My Blueprint' -AgentName 'My Agent' -CreateAgentUser" -ForegroundColor Yellow
Write-Host ""
Write-Host "Note: BlueprintName and AgentName are REQUIRED parameters" -ForegroundColor Yellow
Write-Host ""
Write-Host "Additional Functions:" -ForegroundColor Cyan
Write-Host "  New-AgentIdentityBlueprint -BlueprintName '<name>' -TenantId '<tenant-id>'" -ForegroundColor Gray
Write-Host "  New-AgentUser -AgentIdentityId '<agent-app-id>' -DisplayName 'Agent User Name'" -ForegroundColor Gray
Write-Host "  Get-AgentUsersList" -ForegroundColor Gray
Write-Host "  Get-AgentIdentityList" -ForegroundColor Gray
Write-Host "  Get-BlueprintList`n" -ForegroundColor Gray

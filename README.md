# Microsoft Entra Agent ID - Lab Guide

## What You'll Learn

This lab guide walks you through the complete workflow for Microsoft Entra Agent ID. You will create a Blueprint application, Blueprint service principal, and Agent Identity, then use the Agent Identity token to access Microsoft Graph API. The lab uses PowerShell automation to demonstrate the two-token exchange mechanism and permission management.

**What you'll accomplish:**
- Create a Blueprint application (the factory for agent identities)
- Create a Blueprint service principal
- Create an Agent Identity
- Perform two-token exchange (T1 → T2) to get access tokens
- Assign Microsoft Graph permissions to your agent
- Call Microsoft Graph API using the Agent Identity token

**Time required:** 30-45 minutes

---

## What is Entra Agent ID?

[Microsoft Entra Agent ID](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/) is a feature in Microsoft Entra that provides secure authentication for AI agents. Key benefits:

- **No stored credentials**: Agents don't have client secrets
- **Blueprint pattern**: Create multiple agents from one template
- **Two-token exchange**: Blueprint vouches for agent (T1 → T2)
- **Audit trail**: Track all agent actions and decisions
- **Least privilege**: Each agent gets only required permissions

---

## Architecture Overview

### How It Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SETUP PHASE (One-Time)                              │
└─────────────────────────────────────────────────────────────────────────────┘

Step 1: Create Blueprint Application
┌──────────────────────────────┐
│  Agent Identity Blueprint    │  Factory for creating agents
│  - Display Name              │  Registered in Entra ID
│  - App ID                    │  Unique identifier
│  - Client Secret             │  Credentials for authentication
└──────────────────────────────┘

Step 2: Create Blueprint Principal
┌──────────────────────────────┐
│  Blueprint Service Principal │  Allows blueprint to act in tenant
│  - Links to Blueprint App    │  Created from blueprint
│  - Has permissions to create │  Can spawn agent identities
│    agent identities          │
└──────────────────────────────┘

Step 3: Create Agent Identity
┌──────────────────────────────┐
│  Agent Identity              │  Individual AI agent (no credentials)
│  - Display Name              │  No client secret needed
│  - App ID                    │  Relies on blueprint for auth
│  - Linked to Blueprint       │  Inherits from blueprint pattern
└──────────────────────────────┘

Step 4: Assign Permissions to Agent
┌──────────────────────────────┐
│  Microsoft Graph Permissions │  What the agent can access
│  - User.Read.All             │  Read all users
│  - Directory.Read.All        │  Read directory data
│  - (Custom permissions)      │  Based on agent's role
└──────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    RUNTIME PHASE (Every API Call)                           │
└─────────────────────────────────────────────────────────────────────────────┘

Step 5: Two-Token Exchange Flow (T1 → T2)

    ┌─────────────────────┐
    │  1. Blueprint Auth  │  ← Blueprint authenticates with client secret
    │     (Client ID +    │
    │      Secret)        │
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  2. Get T1 Token    │  ← Token scoped to "AzureADTokenExchange"
    │     (Intermediate)  │  ← Contains: fmi_path → Agent App ID
    │                     │  ← Blueprint asserts: "I vouch for this agent"
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  3. Exchange T1→T2  │  ← Agent uses T1 as client_assertion
    │     (client_assertion│  ← Exchange for T2 token
    │      grant)         │  ← No credentials needed by agent!
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  4. Get T2 Token    │  ← Final access token representing agent
    │     (Agent Token)   │  ← Contains: appid (Agent ID)
    │                     │  ← Contains: roles (permissions)
    │                     │  ← Contains: xms_frd (federation proof)
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  5. Call Graph API  │  ← Use T2 token in Authorization header
    │     with T2 Token   │  ← GET /users?$top=5
    │                     │  ← Graph validates token & permissions
    └──────────┬──────────┘
               │
               ▼
    ┌─────────────────────┐
    │  6. Return Users    │  ← JSON response with user data
    │     (JSON Response) │  ← Only returns data agent has permission for
    └─────────────────────┘
```

### Key Components

**Agent Identity Blueprint**
- A factory application that creates agent identities
- Has credentials (client secret) and permission to spawn agents
- Think of it as a class in object-oriented programming

**Blueprint Principal**
- Service principal for the blueprint
- Allows blueprint to operate within the tenant
- Can create agent identities

**Agent Identity**
- Individual AI agent created from the blueprint
- Has no credentials - relies on blueprint for authentication
- Think of it as an instance of the blueprint class

**T1 Token (Intermediate)**
- Blueprint authenticates with its client secret
- Requests token scoped to `api://AzureADTokenExchange/.default`
- Contains `fmi_path` claim pointing to the agent
- Proves: "Blueprint vouches for this agent"

**T2 Token (Agent Token)**
- Final access token representing the agent identity
- Obtained by exchanging T1 token
- Contains agent's App ID, permissions (roles), and federation proof
- Used to call Microsoft Graph API

**Microsoft Graph Permissions**
- Role-based permissions assigned to the agent
- Examples: `User.Read.All`, `Directory.Read.All`
- Appear in the `roles` claim of the T2 token
- Determine what the agent can access

---

## Lab Prerequisites

### Software Requirements
- Azure CLI installed
- PowerShell 7.5 or higher (`brew install --cask powershell` on Mac)
- Azure subscription with active tenant
- Microsoft Graph PowerShell SDK: `Install-Module Microsoft.Graph -Scope CurrentUser`

### Required Permissions

You need the following Microsoft Graph API delegated permissions:

| Permission | Purpose |
|------------|----------|
| `AgentIdentityBlueprint.Create` | Create Agent Identity Blueprints |
| `AgentIdentityBlueprint.AddRemoveCreds.All` | Add/remove credentials for blueprints |
| `AgentIdentityBlueprintPrincipal.Create` | Create service principals for blueprints |
| `DelegatedPermissionGrant.ReadWrite.All` | Manage delegated permission grants |
| `Application.Read.All` | Read application registrations |
| `AppRoleAssignment.ReadWrite.All` | Assign Microsoft Graph permissions to agents |
| `User.Read` | Read signed-in user profile (for testing) |

**Required Entra ID Role** (one of the following):
- Global Administrator (recommended for initial setup)
- Cloud Application Administrator (can create apps and service principals)
- Application Administrator (can create apps and service principals)

Note: The PowerShell functions automatically request these permissions when you connect to Microsoft Graph. You will be prompted to consent during sign-in.

---

## Clone the Repository

First, clone this repository to your local machine:

```bash
git clone https://github.com/razi-rais/3P-Agent-ID-Demo.git
cd 3P-Agent-ID-Demo
```

---

## Pre-requisite: Authentication Setup

**⚠️ IMPORTANT: Complete these authentication steps BEFORE running any PowerShell scripts.**

### Step 1: Connect to Azure

Open your terminal and sign in to Azure with your tenant ID:

```bash
# Azure CLI login with tenant ID (use device code if in Cloud Shell)
az login --use-device-code --tenant <your-tenant-id>

# Example:
# az login --use-device-code --tenant 9c5d5b12-72f9-422d-9f50-6fba35f988ab
```

> 💡 **Why specify tenant ID?** Some users may not have any subscriptions, or may have access to multiple tenants. Specifying the tenant ID ensures you authenticate to the correct Entra ID tenant.

Wait for authentication to complete and verify your tenant is selected.

### Step 2: Connect to Microsoft Graph

Open PowerShell and authenticate with Microsoft Graph with all required Agent Identity scopes:

```powershell
# Launch PowerShell (if not already in it)
pwsh

# Get your tenant ID from Azure context
$tenantId = (az account show --query tenantId -o tsv)
Write-Host "Tenant ID: $tenantId"

# Connect to Microsoft Graph with required Agent Identity scopes
Connect-MgGraph -Scopes "AgentIdentityBlueprint.AddRemoveCreds.All","AgentIdentityBlueprint.Create","DelegatedPermissionGrant.ReadWrite.All","Application.Read.All","AgentIdentityBlueprintPrincipal.Create","AppRoleAssignment.ReadWrite.All","User.Read" -TenantId $tenantId -UseDeviceCode
```

**What happens:**
1. A device code will be displayed (e.g., `ERZ7QUVBF`)
2. Open https://microsoft.com/devicelogin in your browser
3. Enter the code and sign in with your user account
4. You'll be asked to consent to the required permissions
5. Once complete, you'll see "Welcome to Microsoft Graph!" in PowerShell

**✅ Verify your connection:**
```powershell
# Check current connection
Get-MgContext

# Should show:
# - Your account email
# - Tenant ID
# - All 7 required scopes (AgentIdentityBlueprint.*, Application.Read.All, etc.)
```

> 💡 **Why this is required:** The PowerShell scripts need these permissions to create Agent Identity Blueprints, create agent identities, and assign Graph API permissions. Authenticating first ensures the scripts can perform all operations without interruption.

> 🌐 **Works in Azure Cloud Shell:** Device code authentication works perfectly in Cloud Shell! You can run these commands in Cloud Shell before running the PowerShell scripts.

---

## Automated Workflow (Recommended)

### What You'll Do

This exercise uses PowerShell functions to automate the complete Agent ID workflow. The script will create a blueprint, create an agent identity, perform token exchange, assign permissions, and test the token by calling Microsoft Graph API.

**IMPORTANT:** Ensure you have completed [Pre-requisite: Authentication Setup](#pre-requisite-authentication-setup) before running this workflow.

### Run the Workflow

```powershell
# 1. Load the functions
. ./EntraAgentID-Functions.ps1

# 2. Run the complete workflow (auto-detects tenant from Azure context)
$result = Start-EntraAgentIDWorkflow

# OR specify tenant ID explicitly
$result = Start-EntraAgentIDWorkflow -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"

# OR with custom permissions for this specific agent
$result = Start-EntraAgentIDWorkflow -Permissions @("User.Read.All", "Directory.Read.All")

# 3. Use the returned context
$result.Tokens.AccessToken  # Agent identity access token (T2)
$result.Blueprint.BlueprintAppId  # Blueprint App ID
$result.Agent.AgentIdentityAppId  # Agent App ID
```

### What Happens

The workflow performs these steps automatically:
1. ✅ Verifies you're authenticated to Azure and Microsoft Graph
2. Creates Agent Identity Blueprint with credentials
3. Creates Agent Identity from blueprint
4. Performs T1 to T2 token exchange
5. Adds Microsoft Graph permissions to agent
6. Gets new token with permissions
7. Tests token by calling Graph API and displays actual user data

### Features

The automated workflow includes:
- Secret verification (checks client secret works before proceeding)
- Smart delays (built-in propagation delays for Entra consistency)
- Retry logic (auto-retries for service principal and permission propagation)
- Token claims display (shows JWT token claims when using `-ShowClaims`)
- Live testing (calls Graph API and displays real user data)
- Complete status (shows pass/fail status for API test in summary)

---

## Manual Step-by-Step Functions

You can also run each step individually to understand the process better.

**IMPORTANT:** Ensure you have completed [Pre-requisite: Authentication Setup](#pre-requisite-authentication-setup) before using these functions.

```powershell
# Connect to environment (verifies existing Azure/Graph connections)
$connection = Connect-EntraAgentIDEnvironment

# Create blueprint
$blueprint = New-AgentIdentityBlueprint -TenantId $connection.TenantId

# Create agent identity
$agent = New-AgentIdentity -BlueprintAppId $blueprint.BlueprintAppId `
    -ClientSecret $blueprint.ClientSecret `
    -TenantId $connection.TenantId `
    -UserId $blueprint.UserId

# Get agent token (with optional claims display)
$tokens = Get-AgentIdentityToken -BlueprintAppId $blueprint.BlueprintAppId `
    -ClientSecret $blueprint.ClientSecret `
    -AgentIdentityAppId $agent.AgentIdentityAppId `
    -TenantId $connection.TenantId `
    -ShowClaims

# Add permissions to specific agent
Add-AgentIdentityPermissions -AgentIdentitySP $agent.AgentIdentitySP `
    -Permissions @("User.Read.All")

# Test token (calls Graph API and shows actual users)
# Test token (calls Graph API and shows actual users)
Test-AgentIdentityToken -AccessToken $tokens.AccessToken

# List all agent identities
Get-AgentIdentityList

# List all blueprints
Get-BlueprintList

# Decode and inspect any JWT token
Get-DecodedJwtToken -Token $tokens.AccessToken
```

---

## Troubleshooting

### Issue: "Authorization_RequestDenied" when testing token

**Cause:** Permissions not yet propagated to token (Entra eventual consistency)

**Solution:** Wait 5-10 minutes and get a new token:
```powershell
$newTokens = Get-AgentIdentityToken `
    -BlueprintAppId $result.Blueprint.BlueprintAppId `
    -ClientSecret $result.Blueprint.ClientSecret `
    -AgentIdentityAppId $result.Agent.AgentIdentityAppId `
    -TenantId $result.Connection.TenantId `
    -ShowClaims

Test-AgentIdentityToken -AccessToken $newTokens.AccessToken
```

### Issue: "Invalid client secret" error

**Cause:** Secret not yet valid for authentication (propagation delay)

**Solution:** The script auto-verifies secrets with retry logic (up to 30 seconds)

### Issue: "Resource does not exist" when adding permissions

**Cause:** Agent service principal not yet queryable

**Solution:** The script auto-verifies service principal exists with retry logic (up to 30 seconds)

### Issue: Workflow creates multiple blueprints

**Expected behavior:** Each workflow run creates a new blueprint and agent

**Cleanup:** Use Azure Portal or CLI to delete old blueprints if needed

**Issue: Workflow creates multiple blueprints**
- **Expected**: Each workflow run creates a NEW blueprint and agent
- **Cleanup**: Use Azure Portal or CLI to delete old blueprints if needed

---

## Manual Step-by-Step Guide

If you prefer to understand each step in detail, follow the manual process below:

## Part 1: Setup and Authentication {#part-1}

### Step 1: Connect to Azure

```bash
# Azure CLI login
az login --use-device-code
```

### Step 2: Connect to Microsoft Graph

```powershell
# Launch PowerShell
pwsh

# Set your tenant ID
$tenantId = (Get-AzContext).Tenant.Id   #"<your-tenant-id>"

# Connect to Graph with required scopes for Agent ID management
# Note: You will be prompted to consent to these permissions in your browser
Connect-MgGraph -Scopes "AgentIdentityBlueprint.AddRemoveCreds.All","AgentIdentityBlueprint.Create","DelegatedPermissionGrant.ReadWrite.All","Application.Read.All","AgentIdentityBlueprintPrincipal.Create","AppRoleAssignment.ReadWrite.All","User.Read" -TenantId $tenantId
```

> ℹ️ **Permissions Consent**: A browser window will open asking you to consent to the required permissions. You must have sufficient privileges (Global Admin, Cloud Application Admin, or Application Admin role) to grant these permissions.

---

## Part 2: Create Agent Identity Blueprint

### Step 3: Get Your User ID (for Blueprint Sponsor/Owner)

```powershell
$me = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/me"
$myUserId = $me.id
Write-Host "Your User ID: $myUserId"
```

### Step 4: Create the Blueprint

```powershell
$blueprintName = "Agent Blueprint " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")

$body = @{
    "@odata.type" = "Microsoft.Graph.AgentIdentityBlueprint"
    displayName = $blueprintName
    "sponsors@odata.bind" = @(
        "https://graph.microsoft.com/v1.0/users/$myUserId"
    )
    "owners@odata.bind" = @(
        "https://graph.microsoft.com/v1.0/users/$myUserId"
    )
}

$blueprint = Invoke-MgGraphRequest -Method POST `
    -Uri "https://graph.microsoft.com/beta/applications/" `
    -Headers @{ "OData-Version" = "4.0" } `
    -Body ($body | ConvertTo-Json)

$blueprintAppId = $blueprint.appId
Write-Host "✅ Blueprint created!"
Write-Host "App ID: $($blueprint.appId)"
Write-Host "Object ID: $($blueprint.id)"
```

**My Blueprint App ID**: `b427ef29-2abf-4ce7-b69e-b1b599fd5cfb`

### Step 5: Create Blueprint Principal

```powershell
$principalBody = @{
    appId = $blueprintAppId
}

$principal = Invoke-MgGraphRequest -Method POST `
    -Uri "https://graph.microsoft.com/beta/serviceprincipals/graph.agentIdentityBlueprintPrincipal" `
    -Headers @{ "OData-Version" = "4.0" } `
    -Body ($principalBody | ConvertTo-Json)

Write-Host "✅ Blueprint Principal created!"
Write-Host "Principal ID: $($principal.id)"
```

### Step 6: Add Client Secret to Blueprint

```powershell
$blueprintApp = (Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/applications?`$filter=appId eq '$blueprintAppId'").value[0]

$secretBody = @{
    passwordCredential = @{
        displayName = "Agent ID Secret " + $blueprintName

    }
}

$secret = Invoke-MgGraphRequest -Method POST `
    -Uri "https://graph.microsoft.com/beta/applications/$($blueprintApp.id)/addPassword" `
    -Body ($secretBody | ConvertTo-Json)

Write-Host "✅ Client secret created!"
Write-Host "Secret Value: $($secret.secretText)"
$clientSecret = $secret.secretText
```

⚠️ **Save this secret** - you won't see it again!

### Step 7: List Blueprints

```powershell
$blueprints = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/applications/graph.agentIdentityBlueprint"

$blueprints.value | Select-Object displayName, appId, id | Format-Table
```

---

## Part 3: Create Agent Identity

### Step 8: Get Blueprint Token (for Agent Creation)

```powershell

$tokenBody = @{
    client_id     = $blueprintAppId
    scope         = "https://graph.microsoft.com/.default"
    grant_type    = "client_credentials"
    client_secret = $clientSecret
}

$tokenResponse = Invoke-RestMethod -Method POST `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
    -ContentType "application/x-www-form-urlencoded" `
    -Body $tokenBody

$blueprintToken = $tokenResponse.access_token
Write-Host "✅ Got blueprint access token (length: $($blueprintToken.Length))"
```

**Token Claims (Blueprint Token for Graph API)**:
```json
{
  "aud": "https://graph.microsoft.com",
  "roles": ["AgentIdentity.CreateAsManager"],
  "appid": "b427ef29-2abf-4ce7-b69e-b1b599fd5cfb",
  "app_displayname": "Agent Blueprint"
}
```

### Step 9: Create Agent Identity

```powershell
$agentIdentityBody = @{
    displayName =  "Agent Identity " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    agentIdentityBlueprintId = $blueprintAppId
    "sponsors@odata.bind" = @(
        "https://graph.microsoft.com/v1.0/users/$myUserId"
    )
}

$agentIdentity = Invoke-RestMethod -Method POST `
    -Uri "https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity" `
    -Headers @{
        "Authorization" = "Bearer $blueprintToken"
        "OData-Version" = "4.0"
        "Content-Type"  = "application/json"
    } `
    -Body ($agentIdentityBody | ConvertTo-Json)

Write-Host "✅ Agent Identity created!"
Write-Host "Agent Identity Service Principal ID: $($agentIdentity.id)"
Write-Host "Agent Identity AppId: $($agentIdentity.appId)"

$agentIdentityAppId = $agentIdentity.appId
$agentIdentitySP = $agentIdentity.id
```

**My Agent Identity App ID**: `601ed0a4-706c-4851-8af2-5dfa755f54b4`
**My Agent Identity Service Principal ID**: `601ed0a4-706c-4851-8af2-5dfa755f54b4`

### Step 10: List Agent Identities

```powershell
$agentIdentities = Invoke-MgGraphRequest -Method GET `
    -Uri "https://graph.microsoft.com/beta/servicePrincipals/graph.agentIdentity"

$agentIdentities.value | Select-Object displayName, appId, id | Format-Table
```

---

## Part 4: Token Exchange Flow (T1 → T2)

### Step 11: Get T1 Token (Blueprint Impersonation Token)
```powershell

$tokenBody = @{
    client_id     = $blueprintAppId
    scope         = "https://graph.microsoft.com/.default"
    grant_type    = "client_credentials"
    client_secret = $clientSecret
}

$tokenResponse = Invoke-RestMethod -Method POST `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
    -ContentType "application/x-www-form-urlencoded" `
    -Body $tokenBody

### Step 11: Get T1 Token (Blueprint Impersonation Token)

The T1 token is an intermediate token that represents the relationship between the blueprint and agent identity. It uses the special `fmi_path` parameter.

```powershell

# Get T1 token with fmi_path pointing to agent identity
$t1Body = @{
    client_id     = $blueprintAppId
    scope         = "api://AzureADTokenExchange/.default"
    grant_type    = "client_credentials"
    client_secret = $clientSecret
    fmi_path      = $agentIdentityAppId
}

$t1Response = Invoke-RestMethod -Method POST `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
    -ContentType "application/x-www-form-urlencoded" `
    -Body $t1Body

$blueprintToken = $t1Response.access_token
Write-Host "✅ Got blueprint token (T1) - Claims: $(Get-DecodedJwtToken -Token $blueprintToken)"          

**T1 Token Claims**:
```json
{
  "aud": "fb60f99c-7a34-4190-8149-302f77469936",
  "oid": "35ae6a25-5692-456d-bb39-31494b5c35ae",
  "azp": "85075aa5-1d73-42de-812a-95348218e4b2",
  "sub": "/eid1/c/pub/t/<tenant>/a/<blueprint>/f3897825-fd03-45f5-90eb-fdbf26135650",
  "idtyp": "app"
}
```

Key claims:
- `aud`: Token Exchange Service
- `oid`: Blueprint principal ID
- `azp`: Blueprint app ID
- `sub`: Federated credential representing blueprint → agent relationship

### Step 12: Exchange T1 for T2 (Agent Identity Token)

```powershell
$t2Body = @{
    client_id              = $agentIdentityAppId
    scope                  = "https://graph.microsoft.com/.default"
    grant_type             = "client_credentials"
    client_assertion_type  = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    client_assertion       = $blueprintToken
}

$t2Response = Invoke-RestMethod -Method POST `
    -Uri "https://login.microsoftonline.com/$tenantId/oauth2/v2.0/token" `
    -ContentType "application/x-www-form-urlencoded" `
    -Body $t2Body

$agentToken = $t2Response.access_token
Write-Host "✅ Got agent identity token (T2) - length: $($agentToken.Length)"
Write-Host "✅ Got blueprint token (T1) - Claims: $(Get-DecodedJwtToken -Token $agentToken)"   
```

**T2 Token Claims** (Initial - No Permissions):
```json
{
  "aud": "https://graph.microsoft.com",
  "appid": "601ed0a4-706c-4851-8af2-5dfa755f54b4",
  "app_displayname": "Example Agent",
  "oid": "601ed0a4-706c-4851-8af2-5dfa755f54b4",
  "idtyp": "app",
  "xms_act_fct": "9 3 11",
  "xms_sub_fct": "11 3 9",
  "xms_par_app_azp": "85075aa5-1d73-42de-812a-95348218e4b2"
}
```

Key claims:
- `aud`: Target service (Microsoft Graph)
- `appid`/`oid`/`sub`: Agent identity service principal
- `xms_act_fct`: `9 3 11` = AI agent token
- `xms_par_app_azp`: Blueprint that created this agent

---

## Part 5: Add Microsoft Graph Permissions

### Option A: Add Permissions to Individual Agent Identities

Agent identities have no credentials and initially no permissions. Add permissions to specific agent identity service principals:

```powershell
# Get Microsoft Graph Service Principal ID
$graphSPs = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=displayName eq 'Microsoft Graph'"
$graphSP = $graphSPs.value[0].id

# Note: $agentIdentitySP variable was already set in Step 9 when we created the agent identity

# Add User.Read.All permission to Agent Identity
$permission1Body = @{
    principalId = $agentIdentitySP
    resourceId  = $graphSP
    appRoleId   = "df021288-bdef-4463-88db-98f22de89214"  # User.Read.All
}

Invoke-MgGraphRequest -Method POST `
    -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$agentIdentitySP/appRoleAssignments" `
    -Body ($permission1Body | ConvertTo-Json)

Write-Host "✅ Added User.Read.All permission"

# Add User.ReadWrite.All permission to the same Agent Identity
$permission2Body = @{
    principalId = $agentIdentitySP
    resourceId  = $graphSP
    appRoleId   = "741f803b-c850-494e-b5df-cde7c675a1ca"  # User.ReadWrite.All
}

Invoke-MgGraphRequest -Method POST `
    -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$agentIdentitySP/appRoleAssignments" `
    -Body ($permission2Body | ConvertTo-Json)

Write-Host "✅ Added User.ReadWrite.All permission"
```

### Option B: Add Delegated Permissions to Blueprint (Inherited by All Agents)

To add permissions that ALL agent identities created from this blueprint will automatically inherit:

```powershell
# Get the blueprint application object ID
$blueprintApp = (Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/beta/applications?`$filter=appId eq '$blueprintAppId'").value[0]
$blueprintObjectId = $blueprintApp.id

# Add delegated permissions to the blueprint
$delegatedPermissions = @{
    requiredResourceAccess = @(
        @{
            resourceAppId = "00000003-0000-0000-c000-000000000000"  # Microsoft Graph
            resourceAccess = @(
                @{
                    id = "df021288-bdef-4463-88db-98f22de89214"  # User.Read.All (delegated)
                    type = "Scope"  # "Scope" for delegated, "Role" for application
                },
                @{
                    id = "741f803b-c850-494e-b5df-cde7c675a1ca"  # User.ReadWrite.All (delegated)
                    type = "Scope"
                }
            )
        }
    )
}

# Update the blueprint
Invoke-MgGraphRequest -Method PATCH `
    -Uri "https://graph.microsoft.com/beta/applications/$blueprintObjectId" `
    -Body ($delegatedPermissions | ConvertTo-Json -Depth 10)

Write-Host "✅ Delegated permissions added to blueprint!"

# Enable inheritance for all agent identities created from this blueprint
$blueprintConfig = @{
    inheritDelegatedPermissions = $true
}

Invoke-MgGraphRequest -Method PATCH `
    -Uri "https://graph.microsoft.com/beta/applications/$blueprintObjectId" `
    -Body ($blueprintConfig | ConvertTo-Json)

Write-Host "✅ Permission inheritance enabled!"
```

**Note**: After updating the blueprint with delegated permissions, any NEW agent identities created from it will automatically have these permissions. Existing agent identities won't get them retroactively - you'll need to add permissions to them using Option A.

### Common Microsoft Graph App Role IDs

| Permission | App Role ID | Description |
|-----------|-------------|-------------|
| User.Read.All | `df021288-bdef-4463-88db-98f22de89214` | Read all users' full profiles |
| Directory.Read.All | `7ab1d382-f21e-4acd-a863-ba3e13f7da61` | Read directory data |
| User.ReadWrite.All | `741f803b-c850-494e-b5df-cde7c675a1ca` | Read and write all users' full profiles |
| Mail.Read | `810c84a8-4a9e-49e6-bf7d-12d183f40d01` | Read mail in all mailboxes |

---

## Part 6: Get Token with Permissions & Call Graph API

### Step 14: Get New T2 Token (with permissions)

After adding permissions, repeat the token exchange flow (Step 11 → Step 12) to get a new token.

### Step 14: Get New T2 Token (with permissions)

After adding permissions, repeat the token exchange flow (Step 11 → Step 12) to get a new token.

**T2 Token Claims** (With Permissions):
```json
{
  "aud": "https://graph.microsoft.com",
  "appid": "601ed0a4-706c-4851-8af2-5dfa755f54b4",
  "app_displayname": "Example Agent",
  "roles": [
    "User.Read.All"
  ],
  "oid": "601ed0a4-706c-4851-8af2-5dfa755f54b4",
  "idtyp": "app",
  "xms_act_fct": "9 3 11",
  "xms_par_app_azp": "b427ef29-2abf-4ce7-b69e-b1b599fd5cfb"
}
```

✅ Note the `roles` claim now includes `User.Read.All`

### Step 15: Call Microsoft Graph API

```powershell
$response = Invoke-RestMethod -Method GET `
  -Uri "https://graph.microsoft.com/v1.0/users" `
  -Headers @{
    "Authorization" = "Bearer $agentToken"
    "Content-Type" = "application/json"
  }

$response.value | Select-Object displayName, userPrincipalName | Format-Table
```

---

## Part 7: Decode JWT Tokens (Optional)

### PowerShell Function to Decode JWT Tokens

```powershell
function Get-DecodedJwtToken {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Token
    )
    
    try {
        # Split token into parts
        $tokenParts = $Token.Split('.')
        
        if ($tokenParts.Count -lt 2) {
            throw "Invalid JWT token format"
        }
        
        # Get the payload (second part)
        $payload = $tokenParts[1]
        
        # Add padding if needed
        while ($payload.Length % 4 -ne 0) {
            $payload += '='
        }
        
        # Decode from Base64
        $decodedBytes = [System.Convert]::FromBase64String($payload.Replace('-', '+').Replace('_', '/'))
        $decodedJson = [System.Text.Encoding]::UTF8.GetString($decodedBytes)
        
        # Return formatted JSON string
        return ($decodedJson | ConvertFrom-Json | ConvertTo-Json -Depth 10)
    }
    catch {
        Write-Error "Failed to decode JWT token: $_"
        return $null
    }
}

# Usage examples:
# Decode the agent token
$decodedToken = Get-DecodedJwtToken -Token $agentToken
Write-Host $decodedToken

# Decode the blueprint token
$decodedBlueprintToken = Get-DecodedJwtToken -Token $blueprintToken
Write-Host $decodedBlueprintToken

# Or decode any JWT token string directly
$decoded = Get-DecodedJwtToken -Token "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
Write-Host $decoded
```

---

## Understanding Token Claims

### Agent ID Specific Claims (xms_* facets)

| Claim | Example Value | Meaning |
|-------|--------------|---------|
| `xms_act_fct` | `9 3 11` | Actor facet: AI agent token |
| `xms_sub_fct` | `11 3 9` | Subject facet: Agent acting as itself |
| `xms_par_app_azp` | `<blueprint-app-id>` | Parent blueprint that created this agent |
| `idtyp` | `app` | Identity type: application (not user) |

These claims enable downstream services to:
- Identify that the caller is an AI agent
- Enforce agent-specific policies
- Trace which blueprint created the agent
- Audit agent actions

---

## Understanding `.default` Scope

The `.default` scope means:
- Request **all permissions** that have been pre-configured for the application
- Does NOT grant permissions automatically
- Only includes permissions already added via Azure Portal/CLI

**Examples**:
- Agent with `User.Read.All` configured → `.default` includes it
- Agent with no permissions → token has no `roles` claim
- Agent with multiple permissions → all included in `roles` array

---

## Use Cases

### 1. Slack Support Bot

**Blueprint**: "SlackSupportBot"
- Permissions: `User.Read`, `Chat.Read`, `Team.ReadBasic.All`
- `InheritDelegatedPermissions=true`

**Agent Identities**:
- `support-bot-#engineering` → Additional: `Devops.Read`, `Sites.Read.All`
- `support-bot-#sales` → Additional: `Dynamics.Read`
- `support-bot-#hr-confidential` → Additional: `WorkforceIntegration.Read.All`
- `support-bot-#executive` → Additional: elevated permissions

### 2. Data Processing Agent (GDPR Compliance)

**Blueprint**: "DataProcessingAgent"
- Permissions: `User.Read.All`, `Reports.Read.All`, Sharepoint access

**Agent Identities** (scoped by region):
- `processor-eu` → Azure RBAC: `eu-west-storage` (GDPR boundary)
- `processor-us` → Azure RBAC: `us-east-storage`
- `processor-apac` → Azure RBAC: `apac-storage`

Each agent can only access storage in its region, ensuring data residency compliance.

---

## Troubleshooting

### Error: "Insufficient privileges to complete the operation"

**Cause**: Token doesn't have required Graph API permissions  
**Solution**: 
1. Add permissions using `az rest` (Step 13)
2. Request new T2 token (permissions only appear in new tokens)

### Error: "Permission being assigned already exists on the object"

**Cause**: Permission already added  
**Solution**: This is fine - just request a new token

### Error: "Service principals of agent blueprints cannot be set as the source"

**Cause**: Trying to add permissions to Blueprint instead of Agent Identity  
**Solution**: 
- ❌ Don't use Blueprint App ID for permissions
- ✅ Use Agent Identity App ID: `601ed0a4-706c-4851-8af2-5dfa755f54b4`

### Token has no `roles` claim

**Cause**: No permissions configured for the Agent Identity  
**Solution**: Follow Step 13 to add permissions

### T2 token exchange fails

**Cause**: T1 token may be expired or malformed  
**Solution**: 
1. Verify T1 uses `scope: "api://AzureADTokenExchange/.default"`
2. Verify T1 includes `fmi_path: $agentIdentityAppId`
3. Regenerate T1 token

---

## Key Identities Reference

| Type | Display Name | App ID | Object ID |
|------|-------------|--------|--------|
| Blueprint | Agent Blueprint | `b427ef29-2abf-4ce7-b69e-b1b599fd5cfb` | `01a18127-1e44-46ae-9824-92e4f2366e6c` |
| Agent Identity | Example Agent | `601ed0a4-706c-4851-8af2-5dfa755f54b4` | `601ed0a4-706c-4851-8af2-5dfa755f54b4` |
| Tenant | Default Directory | - | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |

---

## Security Best Practices

1. **Credentials Management**
   - Never commit secrets to version control
   - Use certificates or Federated Identity Credentials in production (not client secrets)
   - Rotate secrets regularly in Azure Portal

2. **Least Privilege**
   - Only add required permissions to agent identities
   - Use blueprint inheritance for common permissions
   - Scope RBAC roles narrowly

3. **Token Management**
   - Tokens expire after ~1 hour
   - Store tokens in memory, not files
   - Refresh tokens before expiration

4. **Monitoring**
   - Enable Entra sign-in logs
   - Monitor agent identity usage
   - Set up alerts for suspicious activity

5. **Production Considerations**
   - Use managed identities when possible
   - Implement proper error handling for token refresh
   - Consider token caching strategies
   - Implement circuit breakers for external API calls

---

## References

### Official Microsoft Documentation
- [Microsoft Entra Agent ID Documentation](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/)
- [Agent Identity Blueprint Reference](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-blueprint)
- [Agent Token Claims](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/agent-token-claims)
- [Microsoft Graph API Overview](https://learn.microsoft.com/en-us/graph/api/overview)
- [Graph Permissions Reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [OAuth 2.0 Client Credentials Flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow)
- [Microsoft Graph PowerShell SDK](https://learn.microsoft.com/en-us/powershell/microsoftgraph/overview)
- [Azure CLI Reference](https://learn.microsoft.com/en-us/cli/azure/)

# Lab Guide: Testing Microsoft Entra Agent Identity with Sidecar

> **📚 Learning Path:** This guide covers the **fundamentals** of Agent Identity tokens using simple PowerShell commands. Once you understand the basics, check out the [LLM Agent Demo](./llm-agent/README.md) for a complete end-to-end example with a chat UI, real weather API, and visual token flow.

## What This Lab Does

**Simple Scenario:** You have an AI agent that needs to read user data from your Entra tenant. This lab shows you how to use the Microsoft Entra sidecar to securely get an Agent Identity token, then use that token to call Microsoft Graph API and retrieve users. The sidecar manages all the credentials - your application just requests tokens.

## ⚠️ Prerequisites

Before starting this guide, you **must** complete the setup in the [Main README](../README.md):

1. ✅ Create a Blueprint application
2. ✅ Create a Blueprint service principal  
3. ✅ Create an Agent Identity
4. ✅ Assign permissions to your Agent

**👉 [Complete the Main README setup first](../README.md)** - This guide assumes you have a working Blueprint and Agent Identity.

**What you'll do:**
1. Deploy a sidecar container and configure it with your Blueprint credentials
2. Request an Agent Identity token for your specific AI agent
3. Call Microsoft Graph API to get users (proves your agent has the User.Read.All permission)
4. Understand the two-token exchange (Blueprint → Agent Identity)

**Estimated time:** 30 minutes

---

## Lab Objectives

By the end of this lab, you will:
- ✅ Deploy and configure the Microsoft Entra Agent ID sidecar
- ✅ Understand how two-token exchange works (Blueprint → Agent)
- ✅ Acquire Agent Identity tokens for autonomous agents
- ✅ Call Microsoft Graph API to retrieve users using Agent Identity
- ✅ Verify the token contains the permissions you assigned

---

## Why Agent Identity?

Traditional approaches require storing secrets in your app code or need a human user to log in. Agent Identity provides a better way:

- **Individual identity for each AI agent** - Better audit trails
- **Secrets managed by sidecar** - Your app never sees credentials
- **Granular permissions per agent** - Each agent gets only what it needs
- **No user login required** - Agents operate autonomously

**Example:** A company has 3 AI agents: EmailBot (Mail.Read), CalendarBot (Calendar.Read), and DataBot (User.Read.All). Each agent gets its own identity and specific permissions. This lab simulates DataBot.

---

## What You'll Learn

This lab demonstrates how to use the **Microsoft Entra SDK for Agent ID sidecar** - a containerized service that:
- Handles token acquisition for Agent Identities (AI agents)
- Performs secure two-token exchange (Blueprint → Agent)
- Eliminates the need to manage secrets in your application code
- Provides both token-only and full API proxy patterns

**Use cases:**
- Autonomous AI agents that need to access Microsoft 365 data
- Third-party applications requiring delegated access
- Secure service-to-service communication with Agent Identity

---

## Architecture Overview

### Complete Lab Flow

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         COMPLETE ARCHITECTURE FLOW                              │
└─────────────────────────────────────────────────────────────────────────────────┘

PATTERN A: Get Token Only (Step 1 & 2)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ┌──────────────────────┐
    │   Your App/Script    │
    │   (PowerShell)       │
    └──────────┬───────────┘
               │
               │ ① GET /AuthorizationHeaderUnauthenticated/graph
               │    ?AgentIdentity=54785f2d...
               ↓
    ┌─────────────────────────────────────────────────────────────────┐
    │              Sidecar Container                                  │
    │                                                                 │
    │  Blueprint App ID: 03f6638f...                                  │
    │  Blueprint Secret: ***                                          │
    │                                                                 │
    │  ② Two-Token Exchange (T1/T2):                                 │
    │     ┌────────────────────────────────────────────────┐          │
    │     │ • Request T1 with Blueprint credentials        │          │
    │     │   (03f6638f... + secret)                       │          │
    │     │              ↓                                 │          │
    │     │   Microsoft Entra ID                           │          │
    │     │              ↓                                 │          │
    │     │ • Receive Blueprint Token (T1)                 │          │
    │     │              ↓                                 │          │
    │     │ • Exchange T1 → Agent Token (T2)               │          │
    │     │   for Agent ID: 54785f2d...                    │          │
    │     │   with scopes: User.Read.All                   │          │
    │     │              ↓                                 │          │
    │     │ • Receive Agent Token (T2)                     │          │
    │     └────────────────────────────────────────────────┘          │
    │                                                                 │
    │  ③ Returns: {"authorizationHeader": "Bearer eyJ..."}           │
    │                                                                 │
    └──────────┬──────────────────────────────────────────────────────┘
               │
               │ ④ Token received
               ↓
    ┌──────────────────────┐
    │   Your App/Script    │  $token = "Bearer eyJ..."
    └──────────┬───────────┘
               │
               │ ⑤ Call API with token
               │    GET https://graph.microsoft.com/v1.0/users
               │    Authorization: Bearer eyJ...
               ↓
    ┌──────────────────────────────────────────────────────┐
    │           Microsoft Graph API                        │
    │  • Validates token signature                         │
    │  • Checks permission (User.Read.All)                 │
    │  • Returns user data                                 │
    └──────────┬───────────────────────────────────────────┘
               │
               │ ⑥ JSON response
               ↓
    ┌──────────────────────┐
    │   Your App/Script    │  ✓ Validate/process results
    │                      │  {"value":[{"displayName":"Adam",...}]}
    └──────────────────────┘


PATTERN B: Call Downstream API Directly (Step 4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ┌──────────────────────┐
    │   Your App/Script    │
    │   (PowerShell)       │
    └──────────┬───────────┘
               │
               │ ① POST /DownstreamApiUnauthenticated/graph
               │    ?optionsOverride.RelativePath=users
               │    &AgentIdentity=54785f2d...
               ↓
    ┌─────────────────────────────────────────────────────────────────┐
    │              Sidecar Container                                  │
    │                                                                 │
    │  Blueprint App ID: 03f6638f...                                  │
    │  Blueprint Secret: ***                                          │
    │                                                                 │
    │  ② Two-Token Exchange (T1/T2):                                 │
    │     ┌────────────────────────────────────────────────┐          │
    │     │ • Request T1 with Blueprint credentials        │          │
    │     │   (03f6638f... + secret)                       │          │
    │     │              ↓                                 │          │
    │     │   Microsoft Entra ID                           │          │
    │     │              ↓                                 │          │
    │     │ • Receive Blueprint Token (T1)                 │          │
    │     │              ↓                                 │          │
    │     │ • Exchange T1 → Agent Token (T2)               │          │
    │     │   for Agent ID: 54785f2d...                    │          │
    │     │              ↓                                 │          │
    │     │ • Receive Agent Token (T2)                     │          │
    │     └────────────────────────────────────────────────┘          │
    │                                                                 │
    │  ③ Call Graph API with T2:                                     │
    │     ┌────────────────────────────────────────────────┐          │
    │     │ GET https://graph.microsoft.com/v1.0/users     │          │
    │     │ Authorization: Bearer eyJ... (T2)              │          │
    │     │              ↓                                 │          │
    │     │   Microsoft Graph API                          │          │
    │     │              ↓                                 │          │
    │     │ • Validates token                              │          │
    │     │ • Returns user data                            │          │
    │     └────────────────────────────────────────────────┘          │
    │                                                                 │
    │  ④ Wraps response:                                             │
    │     {"statusCode":200, "content":"{...users...}"}               │
    │                                                                 │
    └──────────┬──────────────────────────────────────────────────────┘
               │
               │ ⑤ Wrapped results
               ↓
    ┌──────────────────────┐
    │   Your App/Script    │  [OK] Validate/process results
    │                      │  Extract from content field
    └──────────────────────┘


KEY DIFFERENCES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pattern A: /AuthorizationHeaderUnauthenticated
  ✓ Get token only (T2)
  ✓ Your script calls downstream API
  ✓ Full control over API call
  ✓ Can inspect/validate token claims

Pattern B: /DownstreamApiUnauthenticated
  ✓ Token (T2) + API call in one request
  ✓ Sidecar calls downstream API for you
  ✓ Simpler code
  ✓ Response wrapped in statusCode/content
```

### Key Concepts
               ↓
    ┌──────────────────────┐
    │   Your App/Script    │  ✓ Validate/process results
    │                      │  {"value":[{"displayName":"Adam",...}]}
    └──────────────────────┘


PATTERN B: Call Downstream API Directly (Step 4)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    ┌──────────────────────┐
    │   Your App/Script    │
    │   (PowerShell)       │
    └──────────┬───────────┘
               │
               │ ① POST /DownstreamApiUnauthenticated/graph
               │    ?optionsOverride.RelativePath=users
               │    &AgentIdentity=54785f2d...
               ↓
    ┌────────────────────────────────────────────────────────┐
    │              Sidecar Container                         │
    │  Blueprint App ID: 03f6638f...                         │
    │  Blueprint Secret: ***                                 │
    └──────────┬─────────────────────────────────────────────┘
               │
               │ ② Two-Token Exchange (same as Pattern A)
               │    • Gets Blueprint Token (T1)
               │    • Exchanges T1 → Agent Token (T2)
               ↓
    ┌──────────────────────────────────────────────────────┐
    │           Microsoft Entra ID                         │
    │  • Issues T1, returns T2 for Agent 54785f2d...       │
    └──────────┬───────────────────────────────────────────┘
               │
               │ ③ Sidecar gets Agent Token (T2)
               ↓
    ┌────────────────────────────────────────────────────────┐
    │              Sidecar Container                         │
    │  ④ Calls Graph API with T2                            │
    │     GET https://graph.microsoft.com/v1.0/users         │
    │     Authorization: Bearer eyJ...                       │
    └──────────┬─────────────────────────────────────────────┘
               │
               │ ⑤ Graph validates & returns data
               ↓
    ┌──────────────────────────────────────────────────────┐
    │           Microsoft Graph API                        │
    │  • Returns user data to sidecar                      │
    └──────────┬───────────────────────────────────────────┘
               │
               │ ⑥ Wrapped response
               ↓
    ┌────────────────────────────────────────────────────────┐
    │              Sidecar Container                         │
    │  Returns: {"statusCode":200,                           │
    │            "content":"{...users...}"}                  │
    └──────────┬─────────────────────────────────────────────┘
               │
               │ ⑦ Results received
               ↓
    ┌──────────────────────┐
    │   Your App/Script    │  ✓ Validate/process results
    │                      │  Extract from content field
    └──────────────────────┘


KEY DIFFERENCES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Pattern A: /AuthorizationHeaderUnauthenticated
  ✓ Get token only
  ✓ Your script calls downstream API
  ✓ Full control over API call
  ✓ Can inspect/validate token claims

Pattern B: /DownstreamApiUnauthenticated
  ✓ Token + API call in one request
  ✓ Sidecar calls downstream API
  ✓ Simpler code
  ✓ Response wrapped in statusCode/content
```

### Key Concepts

**Blueprint Application** 🔷
- Factory/template for creating Agent Identities
- Holds the client secret (stored in sidecar)
- Your application never needs to know the secret

**Agent Identity** 🤖
- Individual AI agent with its own identity
- Gets its own access token
- Has specific permissions assigned

**Two-Token Flow** 🔄
1. Application requests token for specific Agent ID
2. Sidecar uses Blueprint credentials to get Blueprint token (T1)
3. Sidecar exchanges T1 for Agent Identity token (T2)
4. Application receives Agent Identity token (T2)

---

## Prerequisites

Before starting this lab, ensure you have:

- Docker and Docker Compose installed
- Agent Identity Blueprint created (via PowerShell workflow)
- Agent Identity created with permissions assigned

If you haven't run the PowerShell workflow yet:
```powershell
# In the repository root
. ./EntraAgentID-Functions.ps1
Start-EntraAgentIDWorkflow
```

---

## Lab Steps

### Step 1: Prepare Environment Configuration

Copy the example environment file:
```powershell
cd sidecar
Copy-Item .env.example .env
```

### Step 2: Configure Your Credentials

Edit `.env` with your values from the PowerShell workflow output:

```powershell
# Get these from: Start-EntraAgentIDWorkflow output
TENANT_ID=<your-tenant-id>
BLUEPRINT_APP_ID=<blueprint-app-id>      # <- IMPORTANT: Blueprint, not Agent!
AGENT_CLIENT_ID=<agent-app-id>           # <- This is your Agent ID
BLUEPRINT_CLIENT_SECRET=<blueprint-secret>
```

**💡 Pro Tip:** The sidecar is configured with **Blueprint** credentials, but you'll request tokens for the **Agent** identity.

### Step 3: Start the Sidecar Container

Launch the sidecar:
```powershell
docker-compose up -d
```

**Expected output:**
```
[+] Running 2/2
 ✔ Network sidecar_agent-network  Created
 ✔ Container agent-id-sidecar     Started
```

### Step 4: Verify Deployment

Check that the sidecar is healthy:
```powershell
Invoke-RestMethod -Uri "http://localhost:5001/healthz"
```

**Expected response:** `Healthy`

View container logs:
```powershell
docker-compose logs -f sidecar
```

**✅ Success indicators:**
- Container status: `Up`
- Health endpoint returns `Healthy`
- No error messages in logs

---

## Lab Steps

### Step 1: Get an Agent Identity Token

**Goal:** Request a secure token for your AI agent from the sidecar

**What you're simulating:** Your AI agent needs to authenticate to access Microsoft 365 data.

**Steps:**

1. **Set your Agent App ID** (get this from PowerShell workflow output):
```powershell
$agentAppId = "YOUR-AGENT-APP-ID"
```

2. **Request a token from the sidecar:**
```powershell
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"
$response
```

3. **Observe the response:**
```json
{
  "authorizationHeader": "Bearer eyJ0eXAiOiJKV1QiLCJub25jZSI6..."
}
```

**🎓 What just happened?**

```
Your Request
    ↓
Sidecar receives: "Give me a token for Agent ID abc123"
    ↓
Sidecar thinks: "I have Blueprint credentials, let me use those..."
    ↓
Step 1: Sidecar → Microsoft Entra: "I'm the Blueprint, give me Blueprint token (T1)"
    ↓
Step 2: Sidecar → Microsoft Entra: "Here's T1, exchange it for Agent abc123 token (T2)"
    ↓
Step 3: Microsoft Entra validates and returns Agent Identity token (T2)
    ↓
Sidecar returns T2 to your application
    ↓
Your Application now has Agent Identity token! 🎉
```

**Security win:** Your application never saw the Blueprint secret! The sidecar handled everything.

---

### Step 2: Call Microsoft Graph API to Get Users

**Goal:** Use the Agent Identity token to retrieve user data from Microsoft 365

**What you're simulating:** Your AI agent (DataBot) needs to look up users in your organization's directory.

**PowerShell:**
```powershell
# Step 1: Get the Agent Identity token
$agentAppId = "YOUR-AGENT-APP-ID"
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"

# Step 2: Extract the token (already includes "Bearer " prefix)
$authHeader = $response.authorizationHeader

# Step 3: Call Microsoft Graph to list users
$users = Invoke-RestMethod -Method GET `
    -Uri "https://graph.microsoft.com/v1.0/users?`$top=5" `
    -Headers @{ "Authorization" = $authHeader }

# Step 4: Display results
Write-Host "`n[OK] Successfully retrieved users using Agent Identity!" -ForegroundColor Green
Write-Host "Agent has access to $($users.'@odata.count') users`n" -ForegroundColor Cyan
$users.value | Select-Object displayName, userPrincipalName, jobTitle | Format-Table
```

**Expected Result:** 
```
[OK] Successfully retrieved users using Agent Identity!
Agent has access to users

displayName    userPrincipalName                          jobTitle
-----------    -----------------                          --------
John Doe       john@contoso.com                          Developer
Jane Smith     jane@contoso.com                          Manager
Bob Johnson    bob@contoso.com                           Analyst
```

**🎓 What just happened?**

```
Your Application
    ↓ (with Agent Identity token)
Microsoft Graph API
    ↓
Graph validates: "Is this token valid?"
    ↓
Graph checks: "Does this Agent have User.Read.All permission?"
    ↓
✅ Token valid + Permission granted = Return user data
    ↓
Your Application receives user list
```

**This proves:**
- ✅ Your Agent Identity is working
- ✅ The permissions you assigned (User.Read.All) are active
- ✅ Your AI agent can now safely access Microsoft 365 data
- ✅ Everything is logged under the Agent's identity (audit trail)

---

### Step 3: Verify Token Claims (Optional Deep Dive)

**Goal:** Inspect the token to see exactly what permissions and identity it contains

**PowerShell:**
```powershell
# Get a fresh token
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"
$token = $response.authorizationHeader -replace "Bearer ", ""

# Decode the token (JWT tokens are base64 encoded)
$tokenParts = $token.Split('.')
$payload = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(
    $tokenParts[1].PadRight($tokenParts[1].Length + (4 - $tokenParts[1].Length % 4) % 4, '=')
))

# Parse and display
$claims = $payload | ConvertFrom-Json

Write-Host "`n[INFO] Token Analysis:" -ForegroundColor Cyan
Write-Host "App ID (appid):     $($claims.appid)" -ForegroundColor Yellow
Write-Host "Audience (aud):     $($claims.aud)" -ForegroundColor Yellow
Write-Host "Issuer (iss):       $($claims.iss)" -ForegroundColor Yellow
Write-Host "Roles/Permissions:  $($claims.roles -join ', ')" -ForegroundColor Green
Write-Host "Agent Identity (xms_frd): $($claims.xms_frd)" -ForegroundColor Magenta
Write-Host "`nThis token proves the Agent has these permissions: $($claims.roles -join ', ')`n"
```

**What you'll see:**
```
[INFO] Token Analysis:
App ID (appid):     54785f2d-278e-492f-b1d3-21e7595be303
Audience (aud):     https://graph.microsoft.com
Issuer (iss):       https://sts.windows.net/[tenant-id]/
Roles/Permissions:  User.Read.All
Agent Identity (xms_frd): FederatedAgent

[OK] This token proves the Agent has these permissions: User.Read.All
```

**🎓 What this means:**
- **appid**: This is YOUR Agent's unique ID
- **aud**: Token is for Microsoft Graph API
- **roles**: The permissions you assigned in the PowerShell workflow
- **xms_frd**: Special claim proving this is an Agent Identity (not a regular app)

---

### Step 4: One-Request API Call (Simplified Pattern)

**Goal:** Use the simplified endpoint that combines token acquisition and API calling

**What you're simulating:** For simple scenarios, you can skip manual token management and let the sidecar do everything.

**PowerShell:**
```powershell
# IMPORTANT: Use -Method POST even though we're doing a GET to the downstream API
$agentAppId = "YOUR-AGENT-APP-ID"
$relativePath = "users?`$top=5"

Write-Host "[INFO] Calling Graph API via sidecar proxy..." -ForegroundColor Cyan
$response = Invoke-RestMethod `
    -Method POST `
    -Uri "http://localhost:5001/DownstreamApiUnauthenticated/graph?optionsOverride.RelativePath=$relativePath&AgentIdentity=$agentAppId"

# Parse the wrapped response
$data = $response.content | ConvertFrom-Json
Write-Host "[OK] Retrieved users via simplified endpoint!`n" -ForegroundColor Green
$data.value | Select-Object displayName, userPrincipalName | Format-Table
```

**[INFO] What's different?**

**Step 2 (Two steps):**
```
1. Your App -> Sidecar: "Give me token"
2. Your App -> Graph API: "Here's my token, give me data"
```

**Step 4 (One step):**
```
1. Your App -> Sidecar: "Get users for me"
   Sidecar -> Graph API: (handles token + request)
   Sidecar -> Your App: "Here's the data"
```

**Trade-offs:**
- [OK] **Simplified:** Less code, one request
- [OK] **Good for:** Simple CRUD operations
- [WARN] **Less flexible:** Can't reuse token, limited HTTP control
- [WARN] **Response wrapped:** Need to parse `content` field

**When to use:**
- Use Step 2 pattern (token-only) for: Production apps, complex scenarios, token reuse
- Use Step 4 pattern (full-proxy) for: Quick prototypes, simple demos, single API calls

---

## Understanding the Endpoints

### Available Sidecar Endpoints

### Health Check
```powershell
Invoke-RestMethod -Uri "http://localhost:5001/healthz"
```
Expected: `Healthy`

### Get Agent Identity Token

**Use the `AgentIdentity` query parameter with your Agent App ID:**

```powershell
# Replace with your Agent App ID from workflow
$agentAppId = "YOUR-AGENT-APP-ID"
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"
$response
```

**Example Response:**
```json
{
  "authorizationHeader": "Bearer eyJ0eXAiOiJKV1QiLCJub25jZSI6..."
}
```

### Extract Token and Call Microsoft Graph API

```powershell
# Get token
$agentAppId = "YOUR-AGENT-APP-ID"
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"

# Extract Bearer token (already includes "Bearer " prefix)
$authHeader = $response.authorizationHeader

# Use with Microsoft Graph
$users = Invoke-RestMethod -Method GET `
    -Uri "https://graph.microsoft.com/v1.0/users?`$top=5" `
    -Headers @{ "Authorization" = $authHeader }

$users.value | Select-Object displayName, userPrincipalName
```

## Available Sidecar Endpoints

| Endpoint | Method | Auth Required | Description |
|----------|--------|---------------|-------------|
| `/healthz` | GET | No | Health check |
| `/AuthorizationHeaderUnauthenticated/graph?AgentIdentity={agentAppId}` | GET | No | **For autonomous agents** - Get Agent Identity token only |
| `/DownstreamApiUnauthenticated/graph?optionsOverride.RelativePath={path}&AgentIdentity={agentAppId}` | **POST** | No | **For autonomous agents** - Get token AND call API in one request |
| `/AuthorizationHeader/graph?AgentIdentity={agentAppId}` | GET | Yes (Bearer token) | **For interactive agents** - Get Agent Identity token on-behalf-of user |
| `/DownstreamApi/graph?optionsOverride.RelativePath={path}&AgentIdentity={agentAppId}` | Matches downstream | Yes (Bearer token) | **For interactive agents** - Get token AND call API with user context |

### Understanding Endpoint Authentication

**🔓 Unauthenticated Endpoints** (No incoming token required):
- `/AuthorizationHeaderUnauthenticated` - Get Agent Identity token only
- `/DownstreamApiUnauthenticated` - Get token AND call API in one request
- Your application requests tokens directly without needing an incoming user/app token
- **These are for autonomous agents operating independently**

**🔒 Authenticated Endpoints** (Require incoming Bearer token):
- `/AuthorizationHeader` - For on-behalf-of (OBO) scenarios
- `/DownstreamApi` - For proxied API calls with user context
- Your application must already have a valid bearer token to pass to the sidecar
- Used for interactive agents or when chaining token flows

### When to Use Each Endpoint

**For Autonomous Agents** (No user context):

**Option 1: `/AuthorizationHeaderUnauthenticated`** (✅ Recommended)
- Get Agent Identity token only
- You make the HTTP call to downstream API yourself
- Full control over HTTP client, headers, retries
- Can reuse token for multiple API calls
- Best for complex scenarios or multiple APIs

**Option 2: `/DownstreamApiUnauthenticated`** (Simplified)
- Get token AND call API in one request
- Sidecar handles token acquisition and HTTP call
- Less code, simpler integration
- Best for straightforward API calls
- Response wrapped in JSON with statusCode, headers, content

**For Interactive Agents** (With user context):
- `/AuthorizationHeader` - Token only with user context
- `/DownstreamApi` - Token + API call with user context
- Require incoming bearer token from user session

### Using DownstreamApiUnauthenticated for Autonomous Agents

The `/DownstreamApiUnauthenticated` endpoint combines token acquisition and API calling in a single request - perfect for straightforward scenarios:

**PowerShell Example:**
```powershell
# Get users list in one call (no incoming token required)
# IMPORTANT: Use -Method POST even for downstream GET operations!
$agentAppId = "YOUR-AGENT-APP-ID"
$relativePath = "users?`$top=5"

$response = Invoke-RestMethod `
    -Method POST `
    -Uri "http://localhost:5001/DownstreamApiUnauthenticated/graph?optionsOverride.RelativePath=$relativePath&AgentIdentity=$agentAppId"

# Response is wrapped - parse the content
$data = $response.content | ConvertFrom-Json
$data.value | Select-Object displayName, userPrincipalName
```

**Response Format:**
```json
{
  "statusCode": 200,
  "headers": {
    "content-type": "application/json; charset=utf-8"
  },
  "content": "{\"@odata.context\":\"...\",\"value\":[...]}"
}
```

**⚠️ Important Notes:**
- The **sidecar endpoint** `/DownstreamApiUnauthenticated` always requires **POST method**
- To specify the **downstream API** HTTP method (GET, POST, PUT, DELETE), use `optionsOverride.HttpMethod` query parameter
- If `optionsOverride.HttpMethod` is not specified, downstream API defaults to GET
- Example: POST to sidecar, but GET from Microsoft Graph (default behavior)

**When to use `/DownstreamApiUnauthenticated` vs `/AuthorizationHeaderUnauthenticated`:**
- Use `/DownstreamApiUnauthenticated` for simple, single API calls (less code)
- Use `/AuthorizationHeaderUnauthenticated` when you need token reuse or full HTTP control

---

## Troubleshooting

### Issue: Container name conflict

**Error:** `The container name "/agent-id-sidecar" is already in use`

**Solution:**
```powershell
# Stop and remove existing container
docker stop agent-id-sidecar
docker rm agent-id-sidecar

# Restart from sidecar folder
docker-compose up -d
```

---

### Issue: 405 Method Not Allowed

**Error:** `Response status code does not indicate success: 405 (Method Not Allowed)`

**Common causes:**

1. **Using GET instead of POST for `/DownstreamApiUnauthenticated`**
   ```powershell
   # ❌ Wrong - Missing -Method POST
   $response = Invoke-RestMethod -Uri "http://localhost:5001/DownstreamApiUnauthenticated/..."
   
   # ✅ Correct - Must use POST
   $response = Invoke-RestMethod -Method POST -Uri "http://localhost:5001/DownstreamApiUnauthenticated/..."
   ```

2. **Using authenticated endpoints without bearer token**
   - Use `Unauthenticated` endpoints for autonomous agents

---

### Issue: Empty token or "Access token is empty"

**Cause:** `.env` file has placeholder values

**Solution:**
```powershell
# Verify .env has actual values (not placeholders)
Get-Content .env

# Get your Blueprint App ID from PowerShell
Get-BlueprintList

# Update .env and restart
docker-compose down
docker-compose up -d
```

---

### Issue: Token doesn't have permissions

**Symptoms:** API returns 403 Forbidden

**Solution:**
- Wait 5-10 minutes for permissions to propagate in Microsoft Entra ID
- Verify permissions were added to the **Agent Identity** (not Blueprint)
- Check permissions with PowerShell:
  ```powershell
  Test-AgentIdentityToken -AgentAppId "your-agent-app-id"
  ```

---

### Issue: Endpoint returns 404

**Causes:**
- Using `/health` instead of `/healthz`
- Downstream API name doesn't match configuration (must be `graph`)

---

### Issue: DownstreamApi returns unexpected results

**Check:**
- Response is wrapped - parse the `content` field
- Use `optionsOverride.RelativePath` format (not just `RelativePath`)
- For POST/PUT/DELETE, add `optionsOverride.HttpMethod` parameter

---

## Troubleshooting

### Issue: Container name conflict

**Error:** `The container name "/agent-id-sidecar" is already in use`

**Cause:** A sidecar container is already running (possibly from the root project folder)

**Solution:**
```powershell
# Check if container is running
docker ps | Select-String "agent-id-sidecar"

# Stop the existing container
docker stop agent-id-sidecar

# Remove the existing container
docker rm agent-id-sidecar

# Or use docker-compose from where it was started
docker-compose down

# Then start from sidecar folder
docker-compose up -d
```

### Container won't start
```powershell
docker-compose logs sidecar
```

### Invalid credentials error
- Verify your `.env` file has the correct values
- **Most common issue:** Using Agent App ID instead of Blueprint App ID in `BLUEPRINT_APP_ID`
- Make sure the Blueprint Client Secret is the full secret (not truncated)
- Run `Get-Content .env` to verify all values are populated (no "REPLACE_WITH_" placeholders)

### Empty token or "Access token is empty" error
**This means `.env` has placeholder values instead of actual IDs.**

Get your Blueprint App ID from PowerShell:
```powershell
# If workflow result still in session
$result.Blueprint.BlueprintAppId

# Or query all blueprints
Get-BlueprintList
```

Then update `.env` and restart:
```powershell
# Edit .env file with your preferred editor
notepad .env
# Or on macOS/Linux:
# nano .env

# Restart containers
docker-compose down
docker-compose up -d
```

### Endpoint returns 404
- Check you're using `/healthz` (with 'z') not `/health`
- Verify the downstream API name is `graph` (configured in docker-compose.yml)

### Token doesn't have permissions
- Wait 5-10 minutes for permissions to propagate in Microsoft Entra ID
- Permissions are added to the Agent Identity, not the Blueprint
- Verify permissions were added via `Test-AgentIdentityToken` in PowerShell

### 405 Method Not Allowed error
**Error:** `Response status code does not indicate success: 405 (Method Not Allowed)`

**Common causes:**

1. **Using GET instead of POST for `/DownstreamApiUnauthenticated`**
   - `/DownstreamApiUnauthenticated` endpoint **requires POST method**, even for downstream GET operations
   - Solution: Add `-Method POST` to your Invoke-RestMethod call

2. **Using authenticated endpoints without bearer token**
   - `/AuthorizationHeader` or `/DownstreamApi` require incoming bearer token
   - Solution: Use Unauthenticated endpoints for autonomous agents

**Example - Correct approach for autonomous agents:**
```powershell
# ✅ Option 1: Get token only (GET is OK)
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"

# ✅ Option 2: Get token AND call API (MUST use POST)
$response = Invoke-RestMethod `
    -Method POST `
    -Uri "http://localhost:5001/DownstreamApiUnauthenticated/graph?optionsOverride.RelativePath=users&AgentIdentity=$agentAppId"
```

**Example - Interactive agent with user token:**
```powershell
# ✅ Correct - Auth header required for authenticated endpoints
$response = Invoke-RestMethod `
    -Uri "http://localhost:5001/DownstreamApi/graph?optionsOverride.RelativePath=me&AgentIdentity=$agentAppId" `
    -Headers @{ "Authorization" = "Bearer $userToken" }
```

### DownstreamApi endpoint returns unexpected results
- Use `optionsOverride.RelativePath` query parameter format (not just `RelativePath`)
- Response is wrapped in JSON with `statusCode`, `headers`, and `content` fields
- Parse the `content` field to get the actual API response
- For POST/PUT/PATCH requests, add `optionsOverride.HttpMethod` parameter
- See [official documentation](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/call-downstream-api) for complete examples

---

## Lab Cleanup

Stop and remove containers:
```powershell
docker-compose down
```

Remove volumes (if any):
```powershell
docker-compose down -v
```

---

## Additional Resources

### Official Documentation
- [Microsoft Entra SDK for Agent Identities](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/microsoft-entra-sdk-for-agent-identities)
- [SDK Endpoints Reference](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/endpoints)
- [Call a downstream API](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/call-downstream-api)
- [Python integration examples](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/using-from-python)
- [TypeScript integration examples](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/using-from-typescript)

### Repository Resources
- Main README: [../README.md](../README.md)
- PowerShell Functions: [../EntraAgentID-Functions.ps1](../EntraAgentID-Functions.ps1)

---

## Lab Summary

**🎉 Congratulations! You've completed the lab!**

### What You Accomplished

✅ **Deployed a secure token service** (Microsoft Entra SDK sidecar)  
✅ **Configured it with Blueprint credentials** (secrets stay in container)  
✅ **Requested Agent Identity tokens** (two-token exchange in action)  
✅ **Called Microsoft Graph API** to retrieve users  
✅ **Verified your agent's permissions** (User.Read.All in token claims)  
✅ **Tested both patterns** (token-only and full-proxy)

### The Complete Flow You Just Tested

```
┌─────────────────────────────────────────────────────────────────┐
│              WHAT YOU JUST DID IN THIS LAB                      │
└─────────────────────────────────────────────────────────────────┘

Prerequisites (Done via PowerShell):
  [OK] Created Blueprint Application
  [OK] Created Agent Identity  
  [OK] Assigned User.Read.All permission to Agent

Lab Steps:
  1. Deployed sidecar with Blueprint credentials
  2. Requested Agent Identity token -> Got JWT with appid & permissions
  3. Called Graph API -> Retrieved users successfully
  4. Verified token claims -> Saw User.Read.All permission
  5. Tested simplified proxy -> Same result, less code

Result:
  [OK] Your AI agent can now safely access Microsoft 365 data!
  [LOCK] Secrets managed by sidecar (never exposed to your app)
  [INFO] All actions audited under Agent's identity
  [OK] Production-ready authentication pattern
```

### Real-World Impact

**Before Agent Identity:**
```
❌ Store secrets in app configuration (security risk)
❌ Broad service principal permissions (over-privileged)
❌ Hard to audit individual AI agents (all use same identity)
❌ Complex token management code (boilerplate)
```

**After Agent Identity (What You Just Learned):**
```
✅ Secrets in sidecar only (secure by design)
✅ Granular per-agent permissions (least privilege)
✅ Individual agent audit trails (compliance ready)
✅ Simple token requests (sidecar handles complexity)
```

### Key Concepts Mastered

1. **Two-Token Exchange**: Blueprint token (T1) → Agent token (T2)
2. **Secret Management**: Sidecar stores credentials, app never sees them
3. **Token Patterns**: 
   - `/AuthorizationHeaderUnauthenticated` for flexibility
   - `/DownstreamApiUnauthenticated` for simplicity
4. **JWT Claims**: Understanding `appid`, `roles`, `xms_frd` in tokens
5. **Autonomous Agents**: No user context required, agent acts independently

### What You Can Build Now

**Production Scenarios You're Ready For:**

🤖 **AI-Powered Apps**
- Customer support chatbot that reads user info
- Meeting scheduler that accesses calendars  
- Email assistant that manages messages
- Data analytics agent that queries SharePoint

🏢 **Enterprise Integrations**
- Third-party SaaS connecting to Microsoft 365
- Partner applications accessing your tenant data
- Automation scripts with proper agent identity
- Multi-agent systems with individual permissions

🔐 **Secure By Design**
- No secrets in application code
- Centralized credential management
- Per-agent permission scoping
- Complete audit trail per agent

### Next Steps

1. **Build Your First Agent App**
   - Use the patterns from Step 2 in your application
   - Integrate with Python, Node.js, or your preferred language
   - See: [Python examples](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/using-from-python) | [TypeScript examples](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/scenarios/using-from-typescript)

2. **Explore More Permissions**
   - Try Mail.Read for email scenarios
   - Test Calendar.Read for scheduling
   - Combine multiple permissions for richer agents
   - See: [Grant agent access guide](https://learn.microsoft.com/en-us/entra/agent-id/identity-professional/grant-agent-access-microsoft-365)

3. **Deploy to Production**
   - Move sidecar to Kubernetes/AKS
   - Use managed identities for Blueprint credentials
   - Implement monitoring and logging
   - Set up agent lifecycle management

4. **Learn More**
   - Review the [main README](../README.md) for full PowerShell workflow
   - Study the [PowerShell functions](../EntraAgentID-Functions.ps1) source code
   - Read [Microsoft's Agent ID overview](https://learn.microsoft.com/en-us/entra/agent-id/identity-platform/what-is-agent-id-platform)

### Key Takeaways

💡 **Remember:**
- Sidecar manages secrets - your app never sees them
- Blueprint credentials stay in the sidecar container  
- Agent Identity is passed as a query parameter
- Autonomous agents don't need incoming user tokens
- The sidecar eliminates boilerplate token management code
- Each agent gets its own identity and audit trail
- This is production-ready, not just a demo pattern

**You're now ready to build secure, enterprise-grade AI agents with Microsoft Entra Agent Identity!** 🚀

---

## Architecture Reference

### Detailed Component Interaction

```
┌─────────────────────────────────────────────────────────────────┐
│                    DETAILED ARCHITECTURE                         │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────┐
│  Your App       │
│  (Port 8080)    │
└────────┬────────┘
         │ HTTP: GET /AuthorizationHeaderUnauthenticated/graph?AgentIdentity=xxx
         ↓
┌─────────────────┐
│  Sidecar        │
│  (Port 5001)    │  ← Configured with Blueprint credentials
└────────┬────────┘
         │ Two-Token Exchange (T1 → T2)
         ↓
┌─────────────────┐
│  Microsoft      │
│  Entra ID       │
└─────────────────┘
```

**Key Points:**
- Sidecar is configured with **Blueprint** App ID and Secret
- Your app requests tokens by passing **Agent** App ID as query parameter
- Sidecar handles the two-token exchange automatically
- Your app never needs to handle secrets!

---

## Quick Reference

### Common Commands

```powershell
# Start sidecar
docker-compose up -d

# Check health
Invoke-RestMethod -Uri "http://localhost:5001/healthz"

# View logs
docker-compose logs -f sidecar

# Stop sidecar
docker-compose down
```

### Token Patterns

**Pattern 1: Token Only** (Recommended for most scenarios)
```powershell
$agentAppId = "AGENT_ID"
$response = Invoke-RestMethod -Uri "http://localhost:5001/AuthorizationHeaderUnauthenticated/graph?AgentIdentity=$agentAppId"
$response.authorizationHeader
```

**Pattern 2: Token + API Call** (Simplified integration)
```powershell
$agentAppId = "AGENT_ID"
$response = Invoke-RestMethod -Method POST -Uri "http://localhost:5001/DownstreamApiUnauthenticated/graph?optionsOverride.RelativePath=users&AgentIdentity=$agentAppId"
$response.content | ConvertFrom-Json
```

---

## Interactive Demo: LLM Weather Agent

The docker-compose includes an interactive demo with a chat UI that shows Agent Identity in action.

### What's Included

| Service | Port | Description |
|---------|------|-------------|
| Sidecar | 5001 | Microsoft Entra Agent ID sidecar |
| Weather API | 8080 | Mock weather API that validates Agent ID tokens |
| LLM Agent | 3000 | Chat UI with weather agent |
| Ollama | 11434 | Local LLM for natural responses (optional) |

### Start the Full Demo

```powershell
# Make sure .env is configured
cd sidecar
docker-compose up -d

# Open the chat UI
Start-Process "http://localhost:3000"
```

### How It Works

1. **You ask:** "What's the weather in Seattle?"
2. **LLM Agent** calls Sidecar to get Agent Identity token
3. **Sidecar** performs T1/T2 exchange with Entra ID
4. **LLM Agent** calls Weather API with the token
5. **Weather API** validates the Agent Identity token
6. **Response** is displayed with debug info showing the token flow

### Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Chat UI       │───▶│   Sidecar       │───▶│  Microsoft      │
│  (Port 3000)    │    │  (Port 5001)    │    │  Entra ID       │
└────────┬────────┘    └─────────────────┘    └─────────────────┘
         │                                            │
         │ Agent Identity Token (T2)                  │
         ▼                                            ▼
┌─────────────────┐                          ┌─────────────────┐
│  Weather API    │◀─────────────────────────│  Token (T2)     │
│  (Port 8080)    │   Validates token        └─────────────────┘
└─────────────────┘
```

### Demo Without Ollama

The LLM (Ollama) is optional. Without it, the agent will still:
- Get tokens from the sidecar
- Call the Weather API
- Display formatted weather data
- Show the complete debug flow

To run without Ollama (faster startup):
```powershell
docker-compose up -d sidecar weather-api llm-agent
```

---

## Next Steps

Now that you understand the fundamentals of Agent Identity tokens, try the **complete end-to-end demo**:

👉 **[LLM Agent Demo](./llm-agent/README.md)** - A visual demonstration featuring:
- 🖥️ **Chat UI** - Interactive web interface to ask weather questions
- 🔍 **Token Flow Debug Panel** - Watch the Agent Identity token flow in real-time
- 🌤️ **Real Weather API** - Calls Open-Meteo for actual weather data
- 🔐 **Token Validation** - API validates JWT before returning data
- 📊 **JWT Claims Display** - See the decoded token claims (appid, oid, tid, roles)

```powershell
# Quick start the full demo
cd sidecar
docker-compose up -d
Start-Process "http://localhost:3000"
```

---

*End of Lab Guide*


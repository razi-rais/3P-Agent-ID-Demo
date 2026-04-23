---
title: "Tutorial: Deploy an AWS Bedrock agent with the Microsoft Entra Agent ID sidecar on Azure Container Apps"
description: Deploy a production AWS Bedrock agent to Azure Container Apps. The agent authenticates to Microsoft Entra through the Agent ID sidecar and to AWS STS through workload identity federation, with no stored secrets.
ms.topic: tutorial
ms.date: 04/22/2026
---

# Tutorial: Deploy an AWS Bedrock agent with the Microsoft Entra Agent ID sidecar on Azure Container Apps

In this tutorial, you deploy a sample AI agent that uses **AWS Bedrock** as its model provider and the **Microsoft Entra Agent ID sidecar** for identity. The agent runs as a multi-container app on **Azure Container Apps** and authenticates to both clouds without any long-lived credentials stored in app settings, environment variables, or the container registry.

In this tutorial, you learn how to:

> [!div class="checklist"]
> * Create a Microsoft Entra Agent Identity Blueprint, Agent Identity, and OBO client app.
> * Provision an Azure Container Apps environment with a system-assigned managed identity.
> * Federate the managed identity to Microsoft Entra and to AWS IAM.
> * Build and deploy a four-container app: agent, Entra Agent ID sidecar, downstream API, and token refresher.
> * Verify the autonomous and on-behalf-of (OBO) identity flows end to end.

## 1. Overview

### 1.1 What you build

A single Azure Container App that exposes a browser UI at `https://<app>.<region>.azurecontainerapps.io`. The app contains four containers that share `localhost` and an ephemeral volume:

| Container | Image | Role |
|---|---|---|
| `llm-agent` | `agent-id-aws/llm-agent` (your ACR) | Public-facing Flask + LangChain agent. Receives user chat requests on port **3000**, decides when to call a tool, uses `boto3` to call **AWS Bedrock** for LLM completions, and calls `weather-api` for downstream data. Uses `AWS_WEB_IDENTITY_TOKEN_FILE=/azure-token/token` so `boto3` federates to AWS automatically. |
| `sidecar` | `mcr.microsoft.com/entra-sdk/auth-sidecar` (Microsoft) | The **Microsoft Entra Agent ID auth sidecar**. Listens on `localhost:5000` (not exposed externally). `llm-agent` calls it to get Agent Identity tokens — app-only (**TR**, autonomous flow) or on-behalf-of a user (**TU**, OBO flow). Authenticates to Entra as the Blueprint app using `SignedAssertionFromManagedIdentity` — no client secret. |
| `weather-api` | `agent-id-aws/weather-api` (your ACR) | Sample downstream API on `localhost:8080`. Validates the Agent Identity JWT on every request (JWKS signature check, issuer, audience, `appid`) and returns real Open-Meteo data only if the call is from the expected Agent Identity. Demonstrates how a production downstream service should authorize agent calls. |
| `token-refresher` | `agent-id-aws/token-refresher` (your ACR) | Background worker, no ports. Every ~50 minutes: reads the Container App's managed-identity assertion from IMDS, exchanges it at Entra's `/oauth2/v2.0/token` endpoint for a v1 JWT that AWS STS accepts, and writes the JWT to `/azure-token/token`. `boto3` in `llm-agent` reads this file whenever it calls `AssumeRoleWithWebIdentity`. |

**Why four containers and not one.** The Entra Agent ID sidecar and the token refresher are security-critical components that each do one job and are easy to audit in isolation. Keeping the agent image free of Entra and AWS credential-fetching code also means you can swap the agent framework (LangChain → Semantic Kernel → anything) without touching either auth path.

**How they're wired:**

```
user ──HTTPS──▶ llm-agent:3000
                    │ localhost:5000 ──▶ sidecar  (Agent ID tokens)
                    │ localhost:8080 ──▶ weather-api  (validates TR/TU)
                    │ reads /azure-token/token ◀── token-refresher  (writes)
                    │
                    └──▶ AWS Bedrock (using AWS_WEB_IDENTITY_TOKEN_FILE)
```

Only `llm-agent` is reachable from outside the Container App. `sidecar`, `weather-api`, and `token-refresher` are all `localhost`-only, in line with the Entra Agent ID SDK security model.

When you're done:

* No `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, or `BLUEPRINT_CLIENT_SECRET` exists anywhere in Azure, AWS, or the container images.
* Every token in the system rotates automatically (managed identity: ~24 h; AWS STS: ~1 h; Agent Identity tokens: minutes).
* Revocation is a single command: remove the managed identity from the Container App, and both federation chains break instantly.

### 1.2 Architecture

#### 1.2.1 High-level overview

```
┌───────────────────────────────┐          ┌─────────────────────────────────┐
│   User's browser (MSAL.js)    │          │           AWS account           │
└──────────────┬────────────────┘          │  ┌───────────────────────────┐  │
               │                           │  │  AWS Bedrock              │  │
               │ HTTPS                     │  │  Claude 3 Haiku           │  │
               ▼                           │  └─────────────▲─────────────┘  │
┌────────────────────────────────────────┐ │                │                │
│  Azure Container Apps (one app, 4      │ │  ┌─────────────┴─────────────┐  │
│  containers on shared localhost)       │ │  │  AWS STS                  │  │
│                                        │ │  │  AssumeRoleWithWebIdentity│  │
│  ┌────────────┐  ┌────────────┐       │ │  └─────────────▲─────────────┘  │
│  │  llm-agent │──│  sidecar   │       │ │                │                │
│  │  (boto3)   │  │  (Entra    │       │ └────────────────┼────────────────┘
│  └─────┬──────┘  │   SDK)     │       │                  │ federated JWT
│        │         └─────┬──────┘       │                  │
│  ┌─────▼──────┐  ┌─────▼──────┐       │                  │
│  │weather-api │  │ token-     │───────┼──────────────────┘
│  │(validates  │  │ refresher  │       │
│  │ Agent ID)  │  └────────────┘       │                  ┌────────────────┐
│  └────────────┘                        │                  │  Microsoft     │
│                                        │                  │  Entra ID      │
│  System-assigned managed identity ◀────┼──────────────────┤  (tenant)      │
└────────────────────────────────────────┘                  └────────────────┘
```

#### 1.2.2 Identity and federation

Two federation chains, one identity:

```
                    ┌─────────────────────────────────┐
                    │  System-assigned managed        │
                    │  identity on the Container App  │
                    │  oid = <MI_OBJECT_ID>           │
                    └──────┬─────────────────┬────────┘
                           │                 │
       Chain A: MI → Entra Blueprint    Chain B: MI → AWS STS
       (sidecar signs assertions)       (agent calls Bedrock)
                           │                 │
                           ▼                 ▼
        ┌──────────────────────────┐   ┌──────────────────────────┐
        │  Blueprint app           │   │  Intermediary Entra app  │
        │  Federated credential:   │   │  (v1 access tokens)      │
        │    subject = MI_OBJECT_ID│   │  Federated credential:   │
        │    aud = api://AzureAD-  │   │    subject = MI_OBJECT_ID│
        │          TokenExchange   │   │    aud = api://AzureAD-  │
        └──────────────┬───────────┘   │          TokenExchange   │
                       │               └──────────────┬───────────┘
                       ▼                              ▼
        Graph + weather-api                AWS OIDC IdP (v1 issuer)
                                           IAM role trust:
                                             aud = api://<app>
                                             sub = <SP OID>
                                                     │
                                                     ▼
                                           AWS STS → Bedrock
```

**Key invariants:**

* The managed identity's **object ID** is the `subject` in both federated credentials. No other credential material exists.
* **Chain A is direct** — audience stays `api://AzureADTokenExchange` (a fixed Microsoft convention).
* **Chain B uses a token exchange** — the token refresher swaps the MI assertion for a v1 JWT minted for an intermediary Entra app. This is required because AWS STS rejects the v2.0 audience that Azure managed identities emit by default. See [§3](#3-plan-your-federation-topology).

#### 1.2.3 How the Azure managed identity and AWS trust each other

In plain terms, this design is **an OIDC trust between the Container App's managed identity and an AWS IAM role**. No shared secret, access key, or service account password exists anywhere.

AWS stores only *trust rules*:

* An **OIDC identity provider** records which issuer (`sts.windows.net/<tenant>/`) AWS is willing to accept JWTs from.
* An **IAM role trust policy** pins the exact `aud` (audience) and `sub` (subject) that must appear in the JWT.

At runtime, the sequence is:

1. The Container App's managed identity receives a short-lived JWT from Microsoft Entra (via IMDS).
2. The token refresher exchanges that JWT at Entra's `/oauth2/v2.0/token` endpoint for a v1 JWT minted for the intermediary app (audience = `api://<intermediary-app>`, subject = intermediary app's service-principal object ID).
3. `boto3` presents that JWT to `sts:AssumeRoleWithWebIdentity`. AWS fetches Entra's public JWKS, verifies the signature, confirms `iss`, `aud`, and `sub` match the trust policy, and returns temporary AWS credentials.
4. `boto3` uses those credentials to call `bedrock:InvokeModel`. When they expire (~1 h), it re-reads the JWT file and calls STS again — transparently.

**The trust is pinned by three JWT claims:**

| Claim | Value in this deployment | What it anchors |
|---|---|---|
| `iss` | `https://sts.windows.net/<tenant>/` | *Who signed the token.* Registered in AWS as the OIDC identity provider. |
| `aud` | `api://<intermediary-app-id>` | *Who the token was minted for.* Required by the OIDC provider's client-ID list and the role trust policy. |
| `sub` | `<intermediary-app-SP-object-id>` (which the MI can assert because of the Entra federated credential) | *Which identity the token represents.* Pinned in the IAM role trust policy's condition. |

**Why there's an intermediary app in the middle.** A direct MI → AWS trust would be cleaner, but Azure managed identities emit v2.0 tokens whose audience is a GUID that AWS STS rejects. The intermediary Entra app is a stateless "token shape adapter" — it has no credentials of its own and issues tokens only when the MI hands it a valid federated assertion. The ultimate trust is still MI ↔ AWS.

**What rotates, what doesn't:**

* **Ephemeral:** every JWT in the chain is ≤ 1 h old; AWS STS credentials are ≤ 1 h old; `boto3` refreshes them automatically.
* **Permanent:** the OIDC provider, role trust policy, and federated credentials. You touch them only on tenant migration, app recreation, or policy tightening.

### 1.3 Why not static credentials

Static credentials survive leaks (often for months). Federated tokens in this design live at most one hour. CloudTrail and Entra audit logs tie every token back to the managed identity's object ID, so every API call is attributable to this specific Container App. Rotation is automatic and invisible.

### 1.4 Why Azure Container Apps

* **Multi-container is first-class.** This tutorial uses four containers; Container Apps handles them natively.
* **No VM quota wall.** Azure Container Apps consumption plan allocates CPU/memory per app, not per App Service Plan VM. MSDN and trial subscriptions often have zero quota for Basic/Standard App Service Plans but work cleanly on Container Apps.
* **Sidecar semantics match.** Containers share `localhost` and volumes, which is exactly what the Entra Agent ID SDK's security model requires.

## 2. Prerequisites

### 2.1 Azure

* A subscription where you can create resource groups, ACR, Container Apps, and managed identities.
* One of the following Microsoft Entra roles for the signing-in user:
  * **Global Administrator**, or
  * **Agent ID Administrator** (template ID `db506228-d27e-4b7d-95e5-295956d6615f`), or
  * **Agent ID Developer** (template ID `adb2368d-a9be-41b5-8667-d96778e081b0`).
* Application Administrator alone is **not sufficient** — the Blueprint APIs require an Agent ID role.

### 2.2 AWS

* An AWS account with Bedrock model access enabled in your target region for **Anthropic Claude 3 Haiku**. Request access from the Bedrock console if needed.
* An IAM principal with permission to create OIDC identity providers and IAM roles.

### 2.3 Tooling

| Tool | Minimum version | Notes |
|---|---|---|
| `az` CLI | 2.60 | Signed in to the target tenant. |
| `aws` CLI | v2 | Signed in to the target account and region. |
| `pwsh` | 7.4 | Required for Agent ID Blueprint Graph operations (see [§14.3](#143-request_badrequest-directoryaccessasuserall)). |
| `Microsoft.Graph.Authentication` | 2.35 | `Install-Module Microsoft.Graph.Authentication -Scope CurrentUser`. |
| `Microsoft.Graph.Beta.Applications` | 2.35 | Same. |
| Docker Desktop | 4.30 | With `buildx` for `--platform linux/amd64` builds. |

### 2.4 Repository

Clone the sample repository. All paths in this tutorial are relative to the repository root.

```bash
git clone https://github.com/<org>/3P-Agent-ID-Demo.git
cd 3P-Agent-ID-Demo
```

## 2.5 Choose your SKUs

Before provisioning anything, pick a SKU for each of the following. The table lists **demo defaults** in bold; the warning blocks below describe the silent-failure modes that happen when you accept a default without thinking. All values are set as shell variables in [§4](#4-set-variables).

| Decision | Variable | Demo default | Alternatives | When to change |
|---|---|---|---|---|
| ACR SKU | `ACR_SKU` | **`Basic`** (~$5/mo) | `Standard` (~$20/mo), `Premium` (~$50/mo) | Use `Standard` if you rebuild more than once a day; `Premium` for private endpoint / geo-replication. |
| ACA workload profile | `ACA_WORKLOAD_PROFILE` | **`Consumption`** | `Dedicated-D4` (~$140/mo reserved), `D8`, `D16`, GPU | Use Dedicated if cold starts are unacceptable or you need >4 vCPU / replica. |
| Environment logs | `LOGS_DESTINATION` | **`log-analytics`** (~$2.76/GB) | `none` (free), `azure-monitor` | Keep `log-analytics` for the first deploy. `none` means you cannot debug sidecar auth errors after the fact. |
| Replicas | `MIN_REPLICAS` / `MAX_REPLICAS` | **`1` / `1`** | `0` (scale-to-zero), `1..N` | `min=0` saves money but the first request after idle cold-starts all four containers. |
| Bedrock region + model | `AWS_REGION` + `BEDROCK_MODEL_ID` | **`us-east-2`** + `us.anthropic.claude-3-haiku-20240307-v1:0` | Any region with Bedrock Claude access | The `us.` prefix is an **inference profile** — it only works in `us-east-1`, `us-east-2`, `us-west-2`. |

> [!WARNING]
> **ACR Basic + rapid iteration.** The Basic SKU has a 1,000 pulls/minute cap and 10 GB storage. Ten revision pushes during active development can trigger throttled pulls — Container Apps reports `ImagePullBackOff` with no SKU hint. Upgrade to `Standard` during active development and downgrade after.

> [!WARNING]
> **`Consumption` + `minReplicas = 0`.** The first request after the app idles triggers a full cold start: image pull, Entra SDK warmup, and the first Bedrock call. Demos frequently time out at 30+ seconds. Use `minReplicas = 1` on Consumption, or move to a Dedicated profile.

> [!WARNING]
> **`LOGS_DESTINATION=none`.** Cheapest, but when the next `AADSTS*` or AWS STS error is transient and you need to correlate across containers, the system logs are already gone. Always start with `log-analytics` — switch off later if cost matters.

> [!WARNING]
> **Bedrock model ID / region mismatch.** Requesting model access in the Bedrock console returns "Access granted" for the base model ID (`anthropic.claude-3-haiku-…`). The inference-profile form (`us.anthropic.…`) requires the region to be in the profile's region group. Outside `us-east-*` / `us-west-2`, use the regional base model ID.

For the full decision matrix, see the skill reference: [`sku-sizing.md`](../../.github/skills/deploy-agent-aca-aws/references/sku-sizing.md).

## 3. Plan your federation topology

### 3.1 Why the AWS leg needs a token exchange

A Container App's system-assigned managed identity receives tokens from Azure Instance Metadata Service (IMDS). Those tokens have:

* `iss = https://login.microsoftonline.com/<tenant>/v2.0`
* `aud = fb60f99c-7a34-4190-8149-302f77469936` (a GUID — Microsoft's `AzureADTokenExchange` first-party app)

AWS STS's `AssumeRoleWithWebIdentity` validates the `aud` claim against the OIDC provider's client-ID list. It rejects Microsoft's v2.0 token even when the GUID is registered, returning:

```
InvalidIdentityToken: Incorrect token audience
```

The supported pattern is to **exchange the managed identity's assertion for a v1 access token** minted for an intermediary Entra app registration whose `identifierUri` is in the form `api://<app-id>`. The v1 token has `iss = https://sts.windows.net/<tenant>/` and `aud = api://<app-id>`, both of which AWS STS accepts.

### 3.2 Why the Entra leg is direct

The sidecar authenticates to Microsoft Entra as the Blueprint app by signing an assertion with the managed identity (`SignedAssertionFromManagedIdentity`). Entra itself consumes this assertion, so the audience stays `api://AzureADTokenExchange` and no exchange is needed.

### 3.3 Final object inventory

After you finish this tutorial, the following objects exist:

| Object | Where | Purpose |
|---|---|---|
| Agent Identity Blueprint | Entra | Defines the Agent Identity family. Holds the federated credential for chain A. |
| Agent Identity | Entra | The actual agent principal. Holds Graph app and delegated permissions. |
| Client SPA app | Entra | Browser-side MSAL.js sign-in surface for OBO flows. |
| Intermediary STS app | Entra | Target app for chain B's v1 token exchange. Holds the federated credential for chain B. |
| OIDC identity provider (`sts.windows.net/<tenant>/`) | AWS IAM | Lets AWS trust v1 Entra tokens. |
| IAM role `BedrockInvokerFromAzure` | AWS IAM | Trust policy pins the intermediary app SP OID; permissions policy scopes Bedrock. |
| Resource group, ACR, managed environment, Container App | Azure | Runs the four-container app with the system-assigned MI. |

## 4. Set variables

Run this block once at the start of your shell. Every subsequent command references these variables. The SKU variables come from [§2.5](#25-choose-your-skus) — confirm each choice before you `source` the file.

```bash
# Azure identity
export TENANT_ID="<your-tenant-id>"
export SUBSCRIPTION_ID="<your-subscription-id>"
export RG="rg-agent-id-aws"
export LOCATION="eastus2"
export ACR_NAME="agentidaws$(openssl rand -hex 3)"   # must be globally unique
export APP_NAME="agent-id-aws-$(openssl rand -hex 3)" # must be globally unique

# SKU decisions (see §2.5 — confirm each one)
export ACR_SKU="Basic"                     # Basic | Standard | Premium
export ACA_WORKLOAD_PROFILE="Consumption"  # Consumption | Dedicated-D4 | Dedicated-D8 | ...
export LOGS_DESTINATION="log-analytics"    # none | log-analytics | azure-monitor
export LOG_ANALYTICS_WORKSPACE_ID=""       # leave blank to auto-create one
export MIN_REPLICAS="1"
export MAX_REPLICAS="1"

# AWS
export AWS_ACCOUNT_ID="<your-aws-account-id>"
export AWS_REGION="us-east-2"
export BEDROCK_MODEL_ID="us.anthropic.claude-3-haiku-20240307-v1:0"
export AWS_ROLE_NAME="BedrockInvokerFromAzure"

# Sign in
az login --tenant "$TENANT_ID"
az account set --subscription "$SUBSCRIPTION_ID"
aws sts get-caller-identity     # confirm AWS identity
```

> [!TIP]
> Persist these to a file (for example `/tmp/deploy-vars.sh`) so you can `source` them in a new shell. Avoid expressions like `$(openssl rand ...)` in that file — they re-evaluate every time and produce different names.

## 5. Phase 1 — Create the Microsoft Entra Agent ID objects

### 5.1 Create the Blueprint and Agent Identity

```bash
pwsh -NoProfile -Command "
. ./scripts/EntraAgentID-Functions.ps1
Connect-MgGraph -Scopes `
  'AgentIdentityBlueprint.AddRemoveCreds.All',`
  'AgentIdentityBlueprint.Create',`
  'AgentIdentityBlueprint.DeleteRestore.All',`
  'AgentIdentity.DeleteRestore.All',`
  'DelegatedPermissionGrant.ReadWrite.All',`
  'Application.Read.All',`
  'AgentIdentityBlueprintPrincipal.Create',`
  'AppRoleAssignment.ReadWrite.All',`
  'Directory.Read.All',`
  'User.Read' -TenantId '$TENANT_ID' -NoWelcome
\$r = Start-EntraAgentIDWorkflow ``
  -BlueprintName 'AWS Bedrock Demo Blueprint' ``
  -AgentName 'AWS Bedrock Weather Agent' ``
  -Permissions @('User.Read.All')
Write-Host \"BLUEPRINT_APP_ID=\$(\$r.Blueprint.BlueprintAppId)\"
Write-Host \"AGENT_CLIENT_ID=\$(\$r.Agent.AgentIdentityAppId)\"
"
```

Capture the output values into your shell:

```bash
export BLUEPRINT_APP_ID="<from-output>"
export AGENT_CLIENT_ID="<from-output>"
```

### 5.2 Register the Client SPA app

```bash
cat > scripts/.env <<EOF
TENANT_ID=${TENANT_ID}
BLUEPRINT_APP_ID=${BLUEPRINT_APP_ID}
AGENT_CLIENT_ID=${AGENT_CLIENT_ID}
EOF

bash scripts/setup-obo-client-app.sh
# adds CLIENT_SPA_APP_ID=... to scripts/.env
export CLIENT_SPA_APP_ID=$(grep '^CLIENT_SPA_APP_ID=' scripts/.env | cut -d= -f2)
```

### 5.3 Configure the Blueprint for OBO

The shipped `setup-obo-blueprint.sh` uses a Graph query that fails for Agent Identity Blueprint types. Use the equivalent PowerShell, which uses the key-lookup form `/beta/applications(appId='<id>')` and connects with narrow scopes that don't include `Directory.AccessAsUser.All`:

```powershell
pwsh -NoProfile -File scripts/setup-obo-blueprint.ps1 `
  -BlueprintAppId $env:BLUEPRINT_APP_ID `
  -ClientSpaAppId $env:CLIENT_SPA_APP_ID `
  -AgentAppId $env:AGENT_CLIENT_ID `
  -TenantId $env:TENANT_ID
```

This script sets `identifierUris = [ api://<blueprint-app-id> ]`, adds the `access_as_user` delegated scope, registers the Client SPA as a known client, and grants `AllPrincipals` consent for the Client SPA → Blueprint `access_as_user` scope.

### 5.4 Admin-consent the Agent's delegated Graph permission

OBO exchanges the user's token for Graph permissions scoped to the Agent's service principal. Without admin consent for the delegated permission, users hit `AADSTS65001` in the browser. Grant it once:

```powershell
pwsh -NoProfile -Command "
Connect-MgGraph -Scopes 'AppRoleAssignment.ReadWrite.All','DelegatedPermissionGrant.ReadWrite.All' -TenantId '$env:TENANT_ID' -NoWelcome
\$agent = Invoke-MgGraphRequest GET \"https://graph.microsoft.com/v1.0/servicePrincipals(appId='\$env:AGENT_CLIENT_ID')?`\$select=id\"
\$graph = Invoke-MgGraphRequest GET \"https://graph.microsoft.com/v1.0/servicePrincipals(appId='00000003-0000-0000-c000-000000000000')?`\$select=id\"
\$body = @{ clientId=\$agent.id; consentType='AllPrincipals'; resourceId=\$graph.id; scope='User.Read' } | ConvertTo-Json
Invoke-MgGraphRequest POST 'https://graph.microsoft.com/v1.0/oauth2PermissionGrants' -Body \$body -ContentType 'application/json'
"
```

> [!NOTE]
> `Start-EntraAgentIDWorkflow` grants **application** Graph permissions (for example `User.Read.All`). OBO requires an additional **delegated** grant (`User.Read`). This step is separate because only OBO flows need it.

> [!div class="checklist"]
> * Blueprint app ID: `$BLUEPRINT_APP_ID`
> * Agent Identity app ID: `$AGENT_CLIENT_ID`
> * Client SPA app ID: `$CLIENT_SPA_APP_ID`

## 6. Phase 2 — Create the Azure infrastructure

### 6.1 Resource group, ACR, managed environment

All commands use the SKU variables set in [§4](#4-set-variables) (originally chosen in [§2.5](#25-choose-your-skus)).

```bash
az group create --name "$RG" --location "$LOCATION" -o none

az acr create --resource-group "$RG" --name "$ACR_NAME" --sku "$ACR_SKU" --admin-enabled false -o none

az provider register --namespace Microsoft.App --wait

# Log Analytics workspace (required when LOGS_DESTINATION=log-analytics)
if [[ "$LOGS_DESTINATION" == "log-analytics" && -z "$LOG_ANALYTICS_WORKSPACE_ID" ]]; then
  az monitor log-analytics workspace create -g "$RG" -n "${APP_NAME}-logs" -l "$LOCATION" -o none
  export LOG_ANALYTICS_WORKSPACE_ID=$(az monitor log-analytics workspace show -g "$RG" -n "${APP_NAME}-logs" --query customerId -o tsv)
  export LOG_ANALYTICS_WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "${APP_NAME}-logs" --query primarySharedKey -o tsv)
fi

# Build env create args from SKU choices
ENV_ARGS=(--resource-group "$RG" --name "${APP_NAME}-env" --location "$LOCATION" --logs-destination "$LOGS_DESTINATION")
[[ "$LOGS_DESTINATION" == "log-analytics" ]] && ENV_ARGS+=(--logs-workspace-id "$LOG_ANALYTICS_WORKSPACE_ID" --logs-workspace-key "$LOG_ANALYTICS_WORKSPACE_KEY")

az containerapp env create "${ENV_ARGS[@]}" -o none

# Add non-Consumption workload profile if requested
if [[ "$ACA_WORKLOAD_PROFILE" != "Consumption" ]]; then
  az containerapp env workload-profile add \
    --resource-group "$RG" --name "${APP_NAME}-env" \
    --workload-profile-name "$ACA_WORKLOAD_PROFILE" \
    --workload-profile-type "$ACA_WORKLOAD_PROFILE" \
    --min-nodes 1 --max-nodes 1 -o none
fi
```

### 6.2 Container App skeleton with system-assigned managed identity

You create the Container App with a placeholder image so that the managed identity exists (and has an object ID) before you write the AWS trust policy.

```bash
CREATE_ARGS=(--resource-group "$RG" --name "$APP_NAME"
  --environment "${APP_NAME}-env"
  --image mcr.microsoft.com/k8se/quickstart:latest
  --system-assigned
  --ingress external --target-port 80
  --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS")
[[ "$ACA_WORKLOAD_PROFILE" != "Consumption" ]] && CREATE_ARGS+=(--workload-profile-name "$ACA_WORKLOAD_PROFILE")

az containerapp create "${CREATE_ARGS[@]}" \
  --query '{fqdn:properties.configuration.ingress.fqdn,mi:identity.principalId}' -o json
```

Capture the two values:

```bash
export APP_FQDN="<fqdn-from-output>"
export MI_OBJECT_ID="<mi-from-output>"
```

### 6.3 Grant `AcrPull` to the managed identity

```bash
ACR_ID=$(az acr show --name "$ACR_NAME" --query id -o tsv)
az role assignment create \
  --assignee-object-id "$MI_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$ACR_ID" \
  --role AcrPull -o none
```

## 7. Phase 3 — Federate the managed identity to the Blueprint

Add a federated identity credential on the Blueprint app that trusts the Container App's managed identity. The sidecar uses this to sign assertions to Entra without any client secret.

```powershell
pwsh -NoProfile -Command "
Connect-MgGraph -Scopes 'AgentIdentityBlueprint.AddRemoveCreds.All' -TenantId '$env:TENANT_ID' -NoWelcome
\$body = @{
  name = 'container-app-mi'
  issuer = \"https://login.microsoftonline.com/$env:TENANT_ID/v2.0\"
  subject = '$env:MI_OBJECT_ID'
  audiences = @('api://AzureADTokenExchange')
  description = 'Container App system MI'
} | ConvertTo-Json -Depth 5
Invoke-MgGraphRequest POST \"https://graph.microsoft.com/beta/applications(appId='$env:BLUEPRINT_APP_ID')/federatedIdentityCredentials\" -Body \$body -ContentType 'application/json'
"
```

The sidecar activates this credential by setting `AzureAd__ClientCredentials__0__SourceType=SignedAssertionFromManagedIdentity` in [Phase 6](#10-phase-6--deploy-the-multi-container-app).

## 8. Phase 4 — Federate the managed identity to AWS Bedrock

### 8.1 Create the intermediary Entra app

```bash
export STS_APP_ID=$(az ad app create \
  --display-name "AWS STS Bedrock Federation" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)
export STS_APP_URI="api://${STS_APP_ID}"

az ad app update --id "$STS_APP_ID" --identifier-uris "$STS_APP_URI" -o none
az ad sp create --id "$STS_APP_ID" -o none
export STS_SP_OID=$(az ad sp show --id "$STS_APP_ID" --query id -o tsv)
```

Set `requestedAccessTokenVersion = 1` via Graph. `az` doesn't expose this field directly:

```bash
GRAPH_TOKEN=$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)
curl -s -X PATCH \
  -H "Authorization: Bearer $GRAPH_TOKEN" -H "Content-Type: application/json" \
  "https://graph.microsoft.com/v1.0/applications(appId='$STS_APP_ID')" \
  -d '{"api":{"requestedAccessTokenVersion":1}}'
```

> [!IMPORTANT]
> The `api://<self-appId>` form is required. Tenant policies commonly block custom identifier URIs like `api://my-name`; using the app's own ID is always allowed.

### 8.2 Federate the managed identity to the intermediary app

```bash
cat > /tmp/sts-fic.json <<EOF
{
  "name": "container-app-mi",
  "issuer": "https://login.microsoftonline.com/${TENANT_ID}/v2.0",
  "subject": "${MI_OBJECT_ID}",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Container App system MI"
}
EOF
az ad app federated-credential create --id "$STS_APP_ID" --parameters /tmp/sts-fic.json -o none
```

### 8.3 Create the AWS OIDC identity provider

Register the **v1 issuer**, `https://sts.windows.net/<tenant>/`, with the trailing slash. The client-ID list contains the intermediary app's identifier URI.

```bash
aws iam create-open-id-connect-provider \
  --url "https://sts.windows.net/${TENANT_ID}/" \
  --client-id-list "$STS_APP_URI" \
  --thumbprint-list "626d44e704d1ceabe3bf0d53397464ac8080142c" \
  --query OpenIDConnectProviderArn --output text
```

### 8.4 Create the IAM role

Trust policy — pins both the audience and the intermediary app's service principal object ID:

```bash
cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/sts.windows.net/${TENANT_ID}/" },
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "sts.windows.net/${TENANT_ID}/:aud": "${STS_APP_URI}",
        "sts.windows.net/${TENANT_ID}/:sub": "${STS_SP_OID}"
      }
    }
  }]
}
EOF
```

Permissions policy — Bedrock only, specific model ARNs only:

```bash
cat > /tmp/bedrock-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:InvokeModel",
    "Resource": [
      "arn:aws:bedrock:*::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
      "arn:aws:bedrock:*:${AWS_ACCOUNT_ID}:inference-profile/${BEDROCK_MODEL_ID}"
    ]
  }]
}
EOF
```

Create the role:

```bash
aws iam create-role \
  --role-name "$AWS_ROLE_NAME" \
  --assume-role-policy-document file:///tmp/trust-policy.json

aws iam put-role-policy \
  --role-name "$AWS_ROLE_NAME" \
  --policy-name BedrockInvokeOnly \
  --policy-document file:///tmp/bedrock-policy.json

export AWS_ROLE_ARN=$(aws iam get-role --role-name "$AWS_ROLE_NAME" --query 'Role.Arn' --output text)
```

## 9. Phase 5 — Build and push container images

```bash
az acr login --name "$ACR_NAME"

docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-aws/llm-agent:1.0.0" \
  --push sidecar/aws

docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-aws/weather-api:1.0.0" \
  --push sidecar/weather-api

docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-aws/token-refresher:1.0.0" \
  --push sidecar/aws/azure-token-refresher
```

The Entra Agent ID sidecar image is pulled directly from Microsoft Container Registry at runtime; you don't need to push it.

## 10. Phase 6 — Deploy the multi-container app

Write a YAML manifest that describes all four containers, wires up the shared `EmptyDir` volume, uses the managed identity to pull from ACR, and sets every environment variable the containers expect. **No secrets appear anywhere** in this manifest.

```bash
ENV_ID=$(az containerapp env show -g "$RG" -n "${APP_NAME}-env" --query id -o tsv)

cat > /tmp/containerapp.yaml <<YAML
properties:
  managedEnvironmentId: "${ENV_ID}"
  configuration:
    activeRevisionsMode: Single
    ingress:
      external: true
      targetPort: 3000
      transport: auto
      traffic:
        - weight: 100
          latestRevision: true
    registries:
      - server: ${ACR_NAME}.azurecr.io
        identity: system
  template:
    volumes:
      - name: azure-token
        storageType: EmptyDir
    containers:
      - name: llm-agent
        image: ${ACR_NAME}.azurecr.io/agent-id-aws/llm-agent:1.0.0
        resources: { cpu: 0.5, memory: 1Gi }
        volumeMounts:
          - { volumeName: azure-token, mountPath: /azure-token }
        env:
          - { name: TENANT_ID, value: "${TENANT_ID}" }
          - { name: BLUEPRINT_APP_ID, value: "${BLUEPRINT_APP_ID}" }
          - { name: AGENT_APP_ID, value: "${AGENT_CLIENT_ID}" }
          - { name: AGENT_CLIENT_ID, value: "${AGENT_CLIENT_ID}" }
          - { name: CLIENT_SPA_APP_ID, value: "${CLIENT_SPA_APP_ID}" }
          - { name: SIDECAR_URL, value: "http://localhost:5000" }
          - { name: WEATHER_API_URL, value: "http://localhost:8080" }
          - { name: AWS_REGION, value: "${AWS_REGION}" }
          - { name: BEDROCK_MODEL_ID, value: "${BEDROCK_MODEL_ID}" }
          - { name: AWS_ROLE_ARN, value: "${AWS_ROLE_ARN}" }
          - { name: AWS_WEB_IDENTITY_TOKEN_FILE, value: "/azure-token/token" }
      - name: sidecar
        image: mcr.microsoft.com/entra-sdk/auth-sidecar:1.0.0-azurelinux3.0-distroless
        resources: { cpu: 0.25, memory: 0.5Gi }
        env:
          - { name: AzureAd__Instance, value: "https://login.microsoftonline.com/" }
          - { name: AzureAd__TenantId, value: "${TENANT_ID}" }
          - { name: AzureAd__ClientId, value: "${BLUEPRINT_APP_ID}" }
          - { name: AzureAd__ClientCredentials__0__SourceType, value: "SignedAssertionFromManagedIdentity" }
          - { name: AzureAd__ClientCredentials__0__ManagedIdentityClientId, value: "" }
          - { name: DownstreamApis__graph-app__BaseUrl, value: "https://graph.microsoft.com/v1.0/" }
          - { name: DownstreamApis__graph-app__Scopes__0, value: "https://graph.microsoft.com/.default" }
          - { name: DownstreamApis__graph-app__RequestAppToken, value: "true" }
          - { name: DownstreamApis__graph__BaseUrl, value: "https://graph.microsoft.com/v1.0/" }
          - { name: DownstreamApis__graph__Scopes__0, value: "https://graph.microsoft.com/.default" }
          - { name: ASPNETCORE_ENVIRONMENT, value: "Production" }
          - { name: ASPNETCORE_URLS, value: "http://+:5000" }
      - name: weather-api
        image: ${ACR_NAME}.azurecr.io/agent-id-aws/weather-api:1.0.0
        resources: { cpu: 0.25, memory: 0.5Gi }
        env:
          - { name: TENANT_ID, value: "${TENANT_ID}" }
          - { name: VALIDATE_TOKEN_SIGNATURE, value: "true" }
      - name: token-refresher
        image: ${ACR_NAME}.azurecr.io/agent-id-aws/token-refresher:1.0.0
        resources: { cpu: 0.25, memory: 0.5Gi }
        volumeMounts:
          - { volumeName: azure-token, mountPath: /azure-token }
        env:
          - { name: TENANT_ID, value: "${TENANT_ID}" }
          - { name: STS_APP_ID, value: "${STS_APP_ID}" }
          - { name: STS_APP_URI, value: "${STS_APP_URI}" }
          - { name: AWS_WEB_IDENTITY_TOKEN_FILE, value: "/azure-token/token" }
    scale:
      minReplicas: 1
      maxReplicas: 1
YAML

az containerapp update -g "$RG" -n "$APP_NAME" --yaml /tmp/containerapp.yaml \
  --query '{rev:properties.latestRevisionName,fqdn:properties.configuration.ingress.fqdn}' -o json
```

Deactivate the placeholder revision:

```bash
PREV_REV=$(az containerapp revision list -g "$RG" -n "$APP_NAME" \
  --query "[?properties.template.containers[0].name=='$APP_NAME'].name | [0]" -o tsv)
[ -n "$PREV_REV" ] && az containerapp revision deactivate -g "$RG" -n "$APP_NAME" --revision "$PREV_REV"
```

> [!IMPORTANT]
> Total CPU and memory across all containers must match a valid Container Apps consumption combination. The manifest above totals **1.25 vCPU / 2.5 Gi**, a supported combo. If you adjust, pick another pair from [Container Apps resource allocation](https://learn.microsoft.com/en-us/azure/container-apps/containers).

## 11. Phase 7 — Post-deployment wiring

### 11.1 Add the app's FQDN to the Client SPA redirect URIs

The Client SPA app was created in [§5.2](#52-register-the-client-spa-app) with only `http://localhost:3003` as a redirect URI. The production FQDN must be added manually.

```bash
GRAPH_TOKEN=$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)
PROD_URI="https://${APP_FQDN}"
BODY=$(python3 -c "
import json
existing = json.loads('''$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
  "https://graph.microsoft.com/v1.0/applications(appId='$CLIENT_SPA_APP_ID')?\$select=spa" \
  | python3 -c 'import sys,json; print(json.dumps(json.load(sys.stdin).get(\"spa\",{}).get(\"redirectUris\",[])))')''')
uris = list(dict.fromkeys(existing + ['$PROD_URI']))
print(json.dumps({'spa': {'redirectUris': uris}}))
")
curl -s -X PATCH \
  -H "Authorization: Bearer $GRAPH_TOKEN" -H "Content-Type: application/json" \
  "https://graph.microsoft.com/v1.0/applications(appId='$CLIENT_SPA_APP_ID')" \
  -d "$BODY"
```

Alternatively, in the Azure Portal: **Microsoft Entra ID** → **App registrations** → **Agent Demo Client SPA** → **Authentication** → **Single-page application** → **Add URI** → `https://<APP_FQDN>` → **Save**.

## 12. Phase 8 — Verify

### 12.1 Find your app's URL

If you're returning to a deployment in a new shell, retrieve the FQDN directly from the Container App:

```bash
export APP_FQDN=$(az containerapp show \
  --resource-group "$RG" \
  --name "$APP_NAME" \
  --query 'properties.configuration.ingress.fqdn' -o tsv)
echo "https://${APP_FQDN}"
```

Output:

```
https://yourFDQN.azurecontainerapps.io
```

The FQDN is stable for the life of the Container App — it doesn't change across revisions, deployments, or restarts. You can also find it in the Azure Portal: **Container Apps** → *your app* → **Overview** → **Application Url**.

### 12.2 Status check

```bash
curl -sS "https://${APP_FQDN}/api/status" | python3 -m json.tool
# Expected:
# "bedrock_available": true,
# "bedrock_model": "us.anthropic.claude-3-haiku-20240307-v1:0",
# "sidecar_url": "http://localhost:5000"
```

### 12.3 Autonomous flow

```bash
curl -sS -X POST "https://${APP_FQDN}/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Weather in Dallas?","token_flow":"autonomous","mode":"bedrock","use_langchain":false}' \
  | python3 -m json.tool
```

The response `debug` array should contain steps `0.A START` through `5. COMPLETE`. `success` is `true`.

### 12.4 OBO flow

Open `https://<APP_FQDN>` in your browser, sign in via the MSAL popup, and ask the same question with **Identity Flow = OBO**. The trace panel shows the user-context exchange.

### 12.5 Token refresher logs

```bash
az containerapp logs show -g "$RG" -n "$APP_NAME" --container token-refresher --type console --tail 5
```

Each iteration logs:

```
[refresher] wrote /azure-token/token (... chars)
  iss=https://sts.windows.net/<tenant>/ aud=api://<STS_APP_ID>
  sub=<STS_SP_OID> oid=<STS_SP_OID>
```

If `iss` or `aud` differ from these, the exchange is misconfigured.

### 12.6 CloudTrail check

In the AWS console, filter CloudTrail **Event history** by:

* **Event source** `sts.amazonaws.com`
* **Event name** `AssumeRoleWithWebIdentity`

Each event should show:

* `userIdentity.type = WebIdentityUser`
* `userIdentity.identityProvider = sts.windows.net/<tenant>/`
* `requestParameters.roleArn` matching `$AWS_ROLE_ARN`

If `AssumeRole` (without `WithWebIdentity`) appears, or `AccessDenied` is logged on `bedrock:InvokeModel`, the federation is broken; see [§14](#14-troubleshooting).

## 13. Rotate, revoke, and respond

| Scenario | Action |
|---|---|
| Suspect the Container App is compromised | `az containerapp identity remove -g $RG -n $APP_NAME --system-assigned` — breaks both chains instantly. |
| Tighten Bedrock permissions | Edit `bedrock-policy.json`, re-run `aws iam put-role-policy`. Effective immediately. |
| Tenant migration | Re-run [§8.3](#83-create-the-aws-oidc-identity-provider) with the new tenant ID; update the trust policy in [§8.4](#84-create-the-iam-role); add a federated credential on the new Blueprint and intermediary app. |
| Managed identity rotated (app deleted/recreated) | Capture new `MI_OBJECT_ID`. Update both federated credentials (Blueprint and intermediary app) and the IAM role trust policy. |
| Routine AWS credential rotation | None — STS credentials auto-rotate each hour. |
| Routine Entra credential rotation | None — managed identity tokens auto-rotate (~24 h). |

## 14. Troubleshooting

### 14.1 `InvalidIdentityToken: Incorrect token audience`

The token refresher is writing the raw managed-identity token instead of the exchanged v1 token, or the intermediary app isn't set to v1. Confirm:

```bash
az ad app show --id "$STS_APP_ID" --query 'api.requestedAccessTokenVersion'  # must print 1
```

Check the refresher logs in [§12.5](#125-token-refresher-logs). The `iss` must be `https://sts.windows.net/<tenant>/`, not `.../v2.0`.

### 14.2 `AADSTS65001: user or administrator has not consented`

You skipped [§5.4](#54-admin-consent-the-agents-delegated-graph-permission). Run it and retry the OBO flow.

### 14.3 `Request_BadRequest ... Directory.AccessAsUser.All`

`az` CLI access tokens always include `Directory.AccessAsUser.All`, which the Agent Identity Blueprint APIs explicitly reject. Use PowerShell's `Connect-MgGraph` with a narrow scope list for any Blueprint Graph call, as shown throughout [§5](#5-phase-1--create-the-microsoft-entra-agent-id-objects) and [§7](#7-phase-3--federate-the-managed-identity-to-the-blueprint).

### 14.4 `AccessDeniedException: bedrock:InvokeModel`

The permissions policy is missing your model's ARN. Add it to `bedrock-policy.json` and re-apply.

### 14.5 `ContainerAppInvalidResourceTotal`

The sum of `cpu` and `memory` across containers doesn't match a supported consumption combination. See the valid combos listed in the error message and adjust your manifest.

### 14.6 App Service Plan quota is zero

MSDN and some trial subscriptions have no VM quota for App Service Plans. This tutorial uses Container Apps specifically to avoid that. If you're following an older guide that used App Service, pivot to this one.

## 15. Cost notes

| Component | Approximate monthly cost at demo volumes |
|---|---|
| Container Apps consumption (1 replica, 1.25 vCPU, 2.5 Gi, ~24/7) | ~$35 |
| Azure Container Registry Basic | ~$5 |
| Managed identity | Free |
| AWS STS + OIDC federation | Free |
| AWS Bedrock Claude 3 Haiku | $0.00025 / 1K input tokens — pennies at demo volumes |
| CloudTrail management events | First copy free |

To reduce cost further, set `minReplicas: 0` and enable HTTP-triggered scale, or swap to a scheduled workload.

## 16. Appendix A — Variables reference

| Variable | Example | Where it comes from |
|---|---|---|
| `TENANT_ID` | `<your-tenant-id>` | Entra tenant. |
| `SUBSCRIPTION_ID` | `<your-subscription-id>` | Azure subscription. |
| `RG` | `rg-agent-id-aws` | You choose. |
| `LOCATION` | `eastus2` | You choose. |
| `ACR_NAME` | `agentidaws23eb33` | You choose; globally unique. |
| `APP_NAME` | `agent-id-aws-23eb33` | You choose; globally unique. |
| `AWS_ACCOUNT_ID` | `648887187133` | AWS account. |
| `AWS_REGION` | `us-east-2` | You choose. |
| `BEDROCK_MODEL_ID` | `us.anthropic.claude-3-haiku-20240307-v1:0` | AWS model inference profile. |
| `AWS_ROLE_NAME` | `BedrockInvokerFromAzure` | You choose. |
| `BLUEPRINT_APP_ID` | `f0f2df91-...` | [§5.1](#51-create-the-blueprint-and-agent-identity). |
| `AGENT_CLIENT_ID` | `82323b11-...` | [§5.1](#51-create-the-blueprint-and-agent-identity). |
| `CLIENT_SPA_APP_ID` | `02c86bde-...` | [§5.2](#52-register-the-client-spa-app). |
| `APP_FQDN` | `agent-id-aws-23eb33.<envhash>.eastus2.azurecontainerapps.io` | [§6.2](#62-container-app-skeleton-with-system-assigned-managed-identity). |
| `MI_OBJECT_ID` | `140bf0a2-...` | [§6.2](#62-container-app-skeleton-with-system-assigned-managed-identity). |
| `STS_APP_ID` | `faee0fc2-...` | [§8.1](#81-create-the-intermediary-entra-app). |
| `STS_APP_URI` | `api://faee0fc2-...` | [§8.1](#81-create-the-intermediary-entra-app). |
| `STS_SP_OID` | `7633d9c4-...` | [§8.1](#81-create-the-intermediary-entra-app). |
| `AWS_ROLE_ARN` | `arn:aws:iam::<acct>:role/BedrockInvokerFromAzure` | [§8.4](#84-create-the-iam-role). |

## 17. Appendix B — The token refresher explained

The refresher is ~50 lines of standard library Python. One loop, three steps, on a 50-minute cadence:

1. **Get the managed-identity assertion.** Call the Container App IMDS endpoint (`IDENTITY_ENDPOINT` + `X-IDENTITY-HEADER`) for `resource=api://AzureADTokenExchange`.
2. **Exchange for a v1 token.** POST to `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token` with `grant_type=client_credentials`, `client_id=<STS_APP_ID>`, `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`, `client_assertion=<MI assertion>`, `scope=api://<STS_APP_ID>/.default`. The response token has `iss=https://sts.windows.net/<tenant>/` and `aud=api://<STS_APP_ID>`.
3. **Atomically write it to the shared file.** Write to `AWS_WEB_IDENTITY_TOKEN_FILE + ".tmp"`, `os.replace()` to the final path. `boto3` reads this file whenever it calls `AssumeRoleWithWebIdentity`.

The refresher exits and restarts on any error, relying on the Container Apps restart policy to recover from transient IMDS or Entra failures.

Source: [`sidecar/aws/azure-token-refresher/refresh.py`](./azure-token-refresher/refresh.py).

## 18. Appendix C — Clean teardown

```bash
# Azure
az group delete --name "$RG" --yes --no-wait

# AWS
aws iam delete-role-policy --role-name "$AWS_ROLE_NAME" --policy-name BedrockInvokeOnly
aws iam delete-role --role-name "$AWS_ROLE_NAME"
aws iam delete-open-id-connect-provider \
  --open-id-connect-provider-arn "arn:aws:iam::${AWS_ACCOUNT_ID}:oidc-provider/sts.windows.net/${TENANT_ID}/"

# Entra
az ad app delete --id "$STS_APP_ID"
az ad app delete --id "$CLIENT_SPA_APP_ID"
# Delete the Blueprint and Agent via the Agent ID portal or Graph — see the Agent ID delete docs.
```

## 19. References

* [AWS STS — `AssumeRoleWithWebIdentity` API reference](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRoleWithWebIdentity.html)
* [AWS IAM — OIDC identity providers](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)
* [Microsoft Entra — workload identity federation](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation)
* [`microsoft-identity-web` — `SignedAssertionFromManagedIdentity`](https://github.com/AzureAD/microsoft-identity-web/wiki/Client-Credentials)
* [Microsoft Entra Agent ID SDK — configuration](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration)
* [Azure Container Apps — containers and resource allocation](https://learn.microsoft.com/en-us/azure/container-apps/containers)
* [Azure Container Apps — managed identity](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity)

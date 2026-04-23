---
title: "Tutorial: Deploy a local-LLM agent with the Microsoft Entra Agent ID sidecar on Azure Container Apps"
description: Deploy a self-contained agent (Ollama local LLM + Microsoft Entra Agent ID sidecar) to Azure Container Apps. No second cloud, no stored secrets, one federation chain.
ms.topic: tutorial
ms.date: 04/22/2026
---

# Tutorial: Deploy a local-LLM agent with the Microsoft Entra Agent ID sidecar on Azure Container Apps

In this tutorial, you deploy a sample AI agent whose model runs **in-cluster on Ollama** and whose identity is brokered by the **Microsoft Entra Agent ID sidecar**. The agent runs as a multi-container app on **Azure Container Apps** and authenticates to Microsoft Entra without any long-lived credentials stored in app settings, environment variables, or the container registry.

Unlike the [AWS Bedrock variant](../aws/deploy-aws-bedrock-agent-sidecar-container-apps.md), this deployment has **no second cloud** — the LLM is a local Ollama container. That removes an entire federation chain and makes it the right choice for demos in airgapped or regulated tenants where AWS/GCP aren't options.

In this tutorial, you learn how to:

> [!div class="checklist"]
> * Create a Microsoft Entra Agent Identity Blueprint, Agent Identity, and OBO client app.
> * Provision an Azure Container Apps environment with a system-assigned managed identity.
> * Federate the managed identity to Microsoft Entra (no AWS, no GCP).
> * Build and deploy a four-container app: agent, Entra Agent ID sidecar, downstream API, and Ollama.
> * Verify the autonomous and on-behalf-of (OBO) identity flows end to end.

## 1. Overview

### 1.1 What you build

A single Azure Container App that exposes a browser UI at `https://<app>.<region>.azurecontainerapps.io`. The app contains four containers that share `localhost`:

| Container | Image | Role |
|---|---|---|
| `llm-agent` | `agent-id-dev/llm-agent` (your ACR) | Public-facing Flask + LangChain agent. Receives user chat requests on port **3000**, decides when to call a tool, uses the Ollama HTTP API for LLM completions, and calls `weather-api` for downstream data. |
| `sidecar` | `mcr.microsoft.com/entra-sdk/auth-sidecar` (Microsoft) | The **Microsoft Entra Agent ID auth sidecar**. Listens on `localhost:5000` (not exposed externally). `llm-agent` calls it to get Agent Identity tokens — app-only (**TR**, autonomous flow) or on-behalf-of a user (**TU**, OBO flow). Authenticates to Entra as the Blueprint app using `SignedAssertionFromManagedIdentity` — no client secret. |
| `weather-api` | `agent-id-dev/weather-api` (your ACR) | Sample downstream API on `localhost:8080`. Validates the Agent Identity JWT on every request (JWKS signature check, issuer, audience, `appid`) and returns real Open-Meteo data only if the call is from the expected Agent Identity. |
| `ollama` | `agent-id-dev/ollama` (your ACR, model baked in) | Local LLM server on `localhost:11434`. Serves Qwen 2.5 (or another small model) for the `llm-agent`'s completions. No outbound network calls at runtime. |

**Why four containers and not one.** The Entra Agent ID sidecar is security-critical and easier to audit in isolation. Keeping Ollama in its own container lets you swap the model (or the LLM server entirely) without touching the agent image. Keeping the agent image free of Entra credential-fetching code also means you can swap frameworks (LangChain → Semantic Kernel → anything) without touching the auth path.

**How they're wired:**

```
user ──HTTPS──▶ llm-agent:3000
                    │ localhost:5000  ──▶ sidecar     (Agent ID tokens)
                    │ localhost:8080  ──▶ weather-api (validates TR/TU)
                    │ localhost:11434 ──▶ ollama      (local LLM)
                    │
                    └── (no external model calls — fully self-contained)
```

Only `llm-agent` is reachable from outside the Container App. `sidecar`, `weather-api`, and `ollama` are all `localhost`-only, in line with the Entra Agent ID SDK security model.

When you're done:

* No `BLUEPRINT_CLIENT_SECRET` exists anywhere in Azure or the container images. The sidecar authenticates to Entra via the managed identity only.
* No external model provider credentials exist either — the LLM runs locally.
* Every Entra token rotates automatically (managed identity: ~24 h; Agent Identity tokens: minutes).
* Revocation is a single command: remove the managed identity from the Container App, and the Entra federation breaks instantly.

### 1.2 Architecture

#### 1.2.1 High-level overview

```
┌───────────────────────────────┐
│   User's browser (MSAL.js)    │
└──────────────┬────────────────┘
               │ HTTPS
               ▼
┌────────────────────────────────────────┐
│  Azure Container Apps (one app, 4      │
│  containers on shared localhost)       │
│                                        │
│  ┌────────────┐  ┌────────────┐       │
│  │  llm-agent │──│  sidecar   │       │
│  │            │  │  (Entra    │       │
│  └─────┬──────┘  │   SDK)     │       │
│        │         └─────┬──────┘       │
│  ┌─────▼──────┐  ┌─────▼──────┐       │
│  │weather-api │  │  ollama    │       │
│  │(validates  │  │ (local LLM)│       │
│  │ Agent ID)  │  └────────────┘       │
│  └────────────┘                        │
│                                        │
│  System-assigned managed identity ─────┼──────▶ Microsoft Entra ID
└────────────────────────────────────────┘          (Blueprint app)
```

#### 1.2.2 Identity and federation — one chain, one direction

There is exactly **one** federation chain in this deployment: the Container App's system-assigned managed identity federates to the Blueprint app so the sidecar can sign Entra assertions without a client secret.

```
    ┌─────────────────────────────────┐
    │  System-assigned managed        │
    │  identity on the Container App  │
    │  oid = <MI_OBJECT_ID>           │
    └─────────────────┬───────────────┘
                      │ (sidecar signs assertions)
                      ▼
    ┌─────────────────────────────────┐
    │  Blueprint app                  │
    │  Federated credential:          │
    │    subject = MI_OBJECT_ID       │
    │    aud = api://AzureAD-         │
    │          TokenExchange          │
    └─────────────────┬───────────────┘
                      ▼
           Graph + weather-api
```

Compared to the AWS variant, this deployment has **no** intermediary Entra app, **no** AWS OIDC provider, **no** IAM role trust policy, and **no** token refresher container. The tradeoff: the LLM is local (Ollama on CPU), not an external managed model.

### 1.3 Why not static credentials

The docker-compose version of this sample uses `BLUEPRINT_CLIENT_SECRET` because it's the simplest pattern for a laptop run. On Azure Container Apps, we promote to `SignedAssertionFromManagedIdentity`: the sidecar signs client assertions using the MI's IMDS-backed token. The secret disappears from `.env`, ACR, and the Container App's env vars. Both patterns are valid — the local one optimizes for simplicity, the ACA one optimizes for secretlessness.

### 1.4 Why Azure Container Apps

* **Multi-container is first-class.** This tutorial uses four containers; Container Apps handles them natively.
* **Sidecar semantics match.** Containers share `localhost`, which is exactly what the Entra Agent ID SDK's security model requires.
* **No VM quota wall.** ACA consumption allocates CPU/memory per app. MSDN and trial subscriptions that can't spin up even a Basic App Service Plan work cleanly on Container Apps.

## 2. Prerequisites

### 2.1 Azure

* A subscription where you can create resource groups, ACR, Container Apps, managed identities, and optionally Log Analytics.
* One of the following Microsoft Entra roles for the signing-in user:
  * **Global Administrator**, or
  * **Agent ID Administrator** (template ID `db506228-d27e-4b7d-95e5-295956d6615f`), or
  * **Agent ID Developer** (template ID `adb2368d-a9be-41b5-8667-d96778e081b0`).
* Application Administrator alone is **not sufficient** — the Blueprint APIs require an Agent ID role.

### 2.2 Tooling

| Tool | Minimum version | Notes |
|---|---|---|
| `az` CLI | 2.60 | Signed in to the target tenant. |
| `pwsh` | 7.4 | Required for Agent ID Blueprint Graph operations (see [§13.3](#133-request_badrequest-directoryaccessasuserall)). |
| `Microsoft.Graph.Authentication` | 2.35 | `Install-Module Microsoft.Graph.Authentication -Scope CurrentUser`. |
| `Microsoft.Graph.Beta.Applications` | 2.35 | Same. |
| Docker Desktop | 4.30 | With `buildx` for `--platform linux/amd64` builds. |

### 2.3 Repository

```bash
git clone https://github.com/<org>/3P-Agent-ID-Demo.git
cd 3P-Agent-ID-Demo
```

## 2.5 Choose your SKUs

Before provisioning anything, pick a SKU for each of the following. The table lists **demo defaults** in bold; the warning blocks describe the silent-failure modes that happen when you accept a default without thinking. All values are set as shell variables in [§4](#4-set-variables).

| Decision | Variable | Demo default | Alternatives | When to change |
|---|---|---|---|---|
| ACR SKU | `ACR_SKU` | **`Basic`** (~$5/mo) | `Standard` (~$20/mo), `Premium` (~$50/mo) | Ollama image is ~1 GB; rebuilds 2–3× a day on Basic can throttle pulls. `Standard` is the safer iteration default. |
| ACA workload profile | `ACA_WORKLOAD_PROFILE` | **`Consumption`** | `Dedicated-D4` (~$140/mo reserved), `D8`, `D16`, GPU | 1.5B model on Consumption CPU takes 5–20 s per answer. Use Dedicated for snappier demos, GPU for 7B+ models. |
| Environment logs | `LOGS_DESTINATION` | **`log-analytics`** (~$2.76/GB) | `none` (free), `azure-monitor` | Keep `log-analytics` for the first deploy. `none` means you cannot debug Ollama pull failures or sidecar auth errors after the fact. |
| Replicas | `MIN_REPLICAS` / `MAX_REPLICAS` | **`1` / `1`** | `0` (scale-to-zero), `1..N` | `min=0` means every cold start re-loads the Ollama model into RAM — user sees 30+ s hang. |
| Ollama model | `OLLAMA_MODEL` | **`qwen2.5:1.5b`** (~1 GB, CPU-friendly) | `qwen2.5:7b` (~4 GB, needs GPU for latency), `llama3.2:1b` | Larger models need Dedicated GPU profile. Stick with 1–3B models on Consumption. |
| Ollama image strategy | `OLLAMA_IMAGE_STRATEGY` | **`baked`** (model layered into image) | `runtime-pull` (smaller image, model pulled on first start) | `runtime-pull` saves ~1 GB in ACR but adds a ~30 s delay on every replica cold start. |

> [!WARNING]
> **ACR Basic + Ollama rebuilds.** Baked Ollama images are ~1 GB. Pushing the same SHA twice is deduplicated, but pushing a different model variant burns storage. Basic's 10 GB quota fills up quickly. Use `Standard` once you start experimenting with multiple models.

> [!WARNING]
> **Consumption + 7B models.** Qwen 2.5 7B on 0.75 vCPU takes 30–60 s per short answer and often times out ACA's default 4-minute ingress timeout. Stay on 1–3B models or move to a Dedicated GPU profile.

> [!WARNING]
> **`LOGS_DESTINATION=none`.** Cheapest, but when Ollama fails to load the model (OOM, corrupt layer, insufficient disk), the crash loop is invisible without Log Analytics. Always start with `log-analytics`.

> [!WARNING]
> **`OLLAMA_IMAGE_STRATEGY=runtime-pull` on scale-to-zero.** Compounds: the first request after idle triggers an image pull AND a model download. User waits 30–60 s for the first answer. Don't combine.

For the full decision matrix, see the skill reference: [`sku-sizing.md`](../../.github/skills/deploy-agent-aca-dev/references/sku-sizing.md).

## 3. Final object inventory

After you finish this tutorial, the following objects exist:

| Object | Where | Purpose |
|---|---|---|
| Agent Identity Blueprint | Entra | Defines the Agent Identity family. Holds the federated credential for the MI. |
| Agent Identity | Entra | The actual agent principal. Holds Graph app and delegated permissions. |
| Client SPA app | Entra | Browser-side MSAL.js sign-in surface for OBO flows. |
| Resource group, ACR, managed environment, Container App | Azure | Runs the four-container app with the system-assigned MI. |
| (optional) Log Analytics workspace | Azure | Only if `LOGS_DESTINATION=log-analytics`. |

Compared to the AWS variant: **no intermediary STS app, no AWS OIDC provider, no IAM role**.

## 4. Set variables

Run this block once at the start of your shell. Every subsequent command references these variables. The SKU variables come from [§2.5](#25-choose-your-skus) — confirm each choice before you `source` the file.

```bash
# Azure identity
export TENANT_ID="<your-tenant-id>"
export SUBSCRIPTION_ID="<your-subscription-id>"
export RG="rg-agent-id-dev"
export LOCATION="eastus2"
export ACR_NAME="agentiddev$(openssl rand -hex 3)"   # must be globally unique
export APP_NAME="agent-id-dev-$(openssl rand -hex 3)" # must be globally unique

# SKU decisions (see §2.5 — confirm each one)
export ACR_SKU="Basic"                     # Basic | Standard | Premium
export ACA_WORKLOAD_PROFILE="Consumption"  # Consumption | Dedicated-D4 | ...
export LOGS_DESTINATION="log-analytics"    # none | log-analytics | azure-monitor
export LOG_ANALYTICS_WORKSPACE_ID=""       # leave blank to auto-create
export MIN_REPLICAS="1"
export MAX_REPLICAS="1"

# Ollama
export OLLAMA_MODEL="qwen2.5:1.5b"
export OLLAMA_IMAGE_STRATEGY="baked"       # baked | runtime-pull

# Sign in
az login --tenant "$TENANT_ID"
az account set --subscription "$SUBSCRIPTION_ID"
```

## 5. Phase 1 — Create the Microsoft Entra Agent ID objects

Identical to the AWS tutorial [§5](../aws/deploy-aws-bedrock-agent-sidecar-container-apps.md#5-phase-1--create-the-microsoft-entra-agent-id-objects). The Entra objects don't know or care whether the LLM is AWS Bedrock or local Ollama.

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
  -BlueprintName 'Dev Local-LLM Blueprint' ``
  -AgentName 'Local LLM Weather Agent' ``
  -Permissions @('User.Read.All')
Write-Host \"BLUEPRINT_APP_ID=\$(\$r.Blueprint.BlueprintAppId)\"
Write-Host \"AGENT_CLIENT_ID=\$(\$r.Agent.AgentIdentityAppId)\"
"
```

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
export CLIENT_SPA_APP_ID=$(grep '^CLIENT_SPA_APP_ID=' scripts/.env | cut -d= -f2)
```

### 5.3 Configure the Blueprint for OBO

```powershell
pwsh -NoProfile -File .github/skills/deploy-agent-aca-dev/scripts/setup-obo-blueprint-for-aca.ps1 `
  -BlueprintAppId $env:BLUEPRINT_APP_ID `
  -ClientSpaAppId $env:CLIENT_SPA_APP_ID `
  -AgentAppId $env:AGENT_CLIENT_ID `
  -TenantId $env:TENANT_ID
```

### 5.4 Admin-consent the Agent's delegated Graph permission

OBO requires a **delegated** `User.Read` grant in addition to the application permissions `Start-EntraAgentIDWorkflow` already granted. Without this, users hit `AADSTS65001`.

```powershell
pwsh -NoProfile -File .github/skills/deploy-agent-aca-dev/scripts/grant-agent-obo-consent.ps1 `
  -AgentAppId $env:AGENT_CLIENT_ID -TenantId $env:TENANT_ID
```

> [!div class="checklist"]
> * Blueprint app ID: `$BLUEPRINT_APP_ID`
> * Agent Identity app ID: `$AGENT_CLIENT_ID`
> * Client SPA app ID: `$CLIENT_SPA_APP_ID`

## 6. Phase 2 — Create the Azure infrastructure

All commands use the SKU variables set in [§4](#4-set-variables).

### 6.1 Resource group, ACR, managed environment

```bash
az group create --name "$RG" --location "$LOCATION" -o none

az acr create --resource-group "$RG" --name "$ACR_NAME" --sku "$ACR_SKU" --admin-enabled false -o none

az provider register --namespace Microsoft.App --wait

if [[ "$LOGS_DESTINATION" == "log-analytics" && -z "$LOG_ANALYTICS_WORKSPACE_ID" ]]; then
  az monitor log-analytics workspace create -g "$RG" -n "${APP_NAME}-logs" -l "$LOCATION" -o none
  export LOG_ANALYTICS_WORKSPACE_ID=$(az monitor log-analytics workspace show -g "$RG" -n "${APP_NAME}-logs" --query customerId -o tsv)
  export LOG_ANALYTICS_WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "${APP_NAME}-logs" --query primarySharedKey -o tsv)
fi

ENV_ARGS=(--resource-group "$RG" --name "${APP_NAME}-env" --location "$LOCATION" --logs-destination "$LOGS_DESTINATION")
[[ "$LOGS_DESTINATION" == "log-analytics" ]] && ENV_ARGS+=(--logs-workspace-id "$LOG_ANALYTICS_WORKSPACE_ID" --logs-workspace-key "$LOG_ANALYTICS_WORKSPACE_KEY")

az containerapp env create "${ENV_ARGS[@]}" -o none

if [[ "$ACA_WORKLOAD_PROFILE" != "Consumption" ]]; then
  az containerapp env workload-profile add \
    --resource-group "$RG" --name "${APP_NAME}-env" \
    --workload-profile-name "$ACA_WORKLOAD_PROFILE" \
    --workload-profile-type "$ACA_WORKLOAD_PROFILE" \
    --min-nodes 1 --max-nodes 1 -o none
fi
```

### 6.2 Container App skeleton with system-assigned managed identity

Create the app with a placeholder image first so the managed identity exists (and has an object ID) before you add the Entra federated credential.

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

The sidecar authenticates to Entra as the Blueprint app using `SignedAssertionFromManagedIdentity`. Add a federated credential on the Blueprint that trusts the Container App's managed identity.

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

The sidecar activates this credential by setting `AzureAd__ClientCredentials__0__SourceType=SignedAssertionFromManagedIdentity` in [Phase 5](#9-phase-5--deploy-the-multi-container-app).

**This is the only federation chain in the deployment.** There is no AWS, no GCP, no intermediary app.

## 8. Phase 4 — Build and push container images

Three images to your ACR: `llm-agent`, `weather-api`, and `ollama`. The sidecar image is pulled from MCR at runtime.

### 8.1 Agent and weather API

```bash
az acr login --name "$ACR_NAME"

docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-dev/llm-agent:1.0.0" \
  --push sidecar/dev

docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-dev/weather-api:1.0.0" \
  --push sidecar/weather-api
```

### 8.2 Ollama image (strategy = baked, recommended)

The baked strategy pre-pulls the model at build time so every replica starts with the weights already on disk.

Create `sidecar/dev/ollama/Dockerfile`:

```dockerfile
FROM ollama/ollama:latest
ENV OLLAMA_HOST=0.0.0.0:11434
RUN ollama serve & \
    sleep 5 && \
    ollama pull qwen2.5:1.5b && \
    pkill ollama
ENTRYPOINT ["ollama", "serve"]
```

Then:

```bash
docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-dev/ollama:1.0.0" \
  --push sidecar/dev/ollama
```

### 8.3 Ollama image (strategy = runtime-pull, smaller image, slower first start)

Skip the custom Dockerfile and use `ollama/ollama:latest` directly in the manifest. The model pulls on first `/api/generate` request (~30 s hang on initial demo). Not recommended for demos.

## 9. Phase 5 — Deploy the multi-container app

```bash
ENV_ID=$(az containerapp env show -g "$RG" -n "${APP_NAME}-env" --query id -o tsv)

# Pick the Ollama image based on the strategy
OLLAMA_IMAGE="${ACR_NAME}.azurecr.io/agent-id-dev/ollama:1.0.0"
[[ "$OLLAMA_IMAGE_STRATEGY" == "runtime-pull" ]] && OLLAMA_IMAGE="docker.io/ollama/ollama:latest"

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
    containers:
      - name: llm-agent
        image: ${ACR_NAME}.azurecr.io/agent-id-dev/llm-agent:1.0.0
        resources: { cpu: 0.5, memory: 1Gi }
        env:
          - { name: TENANT_ID, value: "${TENANT_ID}" }
          - { name: BLUEPRINT_APP_ID, value: "${BLUEPRINT_APP_ID}" }
          - { name: AGENT_APP_ID, value: "${AGENT_CLIENT_ID}" }
          - { name: AGENT_CLIENT_ID, value: "${AGENT_CLIENT_ID}" }
          - { name: CLIENT_SPA_APP_ID, value: "${CLIENT_SPA_APP_ID}" }
          - { name: SIDECAR_URL, value: "http://localhost:5000" }
          - { name: WEATHER_API_URL, value: "http://localhost:8080" }
          - { name: OLLAMA_URL, value: "http://localhost:11434" }
          - { name: OLLAMA_MODEL, value: "${OLLAMA_MODEL}" }
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
        image: ${ACR_NAME}.azurecr.io/agent-id-dev/weather-api:1.0.0
        resources: { cpu: 0.25, memory: 0.5Gi }
        env:
          - { name: TENANT_ID, value: "${TENANT_ID}" }
          - { name: VALIDATE_TOKEN_SIGNATURE, value: "true" }
      - name: ollama
        image: ${OLLAMA_IMAGE}
        resources: { cpu: 0.75, memory: 1.5Gi }
        env:
          - { name: OLLAMA_HOST, value: "0.0.0.0:11434" }
    scale:
      minReplicas: ${MIN_REPLICAS}
      maxReplicas: ${MAX_REPLICAS}
YAML

az containerapp update -g "$RG" -n "$APP_NAME" --yaml /tmp/containerapp.yaml \
  --query '{rev:properties.latestRevisionName,fqdn:properties.configuration.ingress.fqdn}' -o json
```

> [!IMPORTANT]
> Total CPU and memory across all containers must match a valid ACA combo. The manifest above totals **1.75 vCPU / 3.5 Gi**, a supported combo. If you change sizing, check [ACA resource allocation](https://learn.microsoft.com/en-us/azure/container-apps/containers).

## 10. Phase 6 — Post-deployment wiring

Same two manual steps as the AWS variant — both are tenant-level Entra/Graph operations that can't be done before the Container App exists.

### 10.1 Add the app's FQDN to the Client SPA redirect URIs

```bash
bash .github/skills/deploy-agent-aca-dev/scripts/add-spa-redirect-uri.sh
```

This PATCHes `spa.redirectUris` on the Client SPA app directly via Graph. `az ad app update --web-redirect-uris` does **not** affect SPA redirect URIs.

### 10.2 Agent → Graph delegated `User.Read` consent

Already done in [§5.4](#54-admin-consent-the-agents-delegated-graph-permission). If you skipped it, do it now — you'll hit `AADSTS65001` on the OBO flow otherwise.

## 11. Phase 7 — Verify

### 11.1 Find your app's URL

```bash
export APP_FQDN=$(az containerapp show \
  --resource-group "$RG" --name "$APP_NAME" \
  --query 'properties.configuration.ingress.fqdn' -o tsv)
echo "https://${APP_FQDN}"
```

### 11.2 Status check

```bash
curl -sS "https://${APP_FQDN}/api/status" | python3 -m json.tool
# Expected:
# "ollama_available": true
# "ollama_model": "qwen2.5:1.5b"
# "sidecar_url": "http://localhost:5000"
```

### 11.3 Autonomous flow

```bash
curl -sS -X POST "https://${APP_FQDN}/api/chat" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Weather in Dallas?","token_flow":"autonomous","use_langchain":false}' \
  | python3 -m json.tool
```

The response includes the weather from `weather-api` and Qwen's natural-language explanation. First request may take 5–20 s on Consumption.

### 11.4 OBO flow

Open `https://<APP_FQDN>` in your browser, sign in via the MSAL popup, and chat with **Identity Flow = OBO**.

### 11.5 Ollama health

```bash
az containerapp logs show -g "$RG" -n "$APP_NAME" --container ollama --type console --tail 20
```

On first replica start with `OLLAMA_IMAGE_STRATEGY=baked`, you'll see the server start immediately. With `runtime-pull`, you'll see `pulling manifest … success` followed by the `qwen2.5:1.5b` download.

## 12. Rotate

The managed identity rotates its tokens automatically (~24 h). If you migrate tenants:

1. Delete the Blueprint's federated credential and re-add it with the new `subject = <new MI object ID>` and `issuer = https://login.microsoftonline.com/<new tenant>/v2.0`.
2. Update `TENANT_ID` in the Container App's env.

There is no AWS or GCP rotation — because there is no AWS or GCP.

## 13. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ollama_available: false` in status | Ollama container failed to pull model (runtime-pull) or ran out of memory | Check Ollama logs; bump memory to 2Gi; switch to `baked` strategy |
| Ollama container crash-loops with `out of memory` | 7B model on <3 GB RAM | Drop to 1.5B model or move to Dedicated profile |
| `AADSTS65001` on browser OBO sign-in | Missing delegated `User.Read` admin consent | Run `grant-agent-obo-consent.ps1` (see [§5.4](#54-admin-consent-the-agents-delegated-graph-permission)) |
| `AADSTS50011: redirect URI mismatch` | Production `https://<FQDN>` not in SPA redirect URIs | Run `add-spa-redirect-uri.sh` (see [§10.1](#101-add-the-apps-fqdn-to-the-client-spa-redirect-uris)) |
| Graph `$filter=appId eq` returns empty for Blueprint | Agent Identity Blueprint types invisible to `$filter` | Use key-lookup form `/beta/applications(appId='<id>')` — the scripts in this skill already do this |
| <a name="133-request_badrequest-directoryaccessasuserall"></a>`REQUEST_BADREQUEST: Directory.AccessAsUser.All` on Blueprint PATCH | `az account get-access-token --resource graph` includes `Directory.AccessAsUser.All` which Blueprint rejects | Use pwsh `Connect-MgGraph -Scopes …` with narrow scopes (never `.default`) |
| `403 Authorization_RequestDenied` on Blueprint create | Signing-in user has `Application Administrator` but not an Agent ID role | Assign `Agent ID Developer` or `Agent ID Administrator` |
| Container App crash-looping | Invalid CPU/memory combination | Totals must match a valid ACA consumption combo; see error for valid pairs |

## 14. Cost (demo profile, ~24/7)

| Line item | Approx USD/month |
|---|---|
| Container Apps consumption (1 replica, 1.75 vCPU, 3.5 Gi) | ~$50 |
| Azure Container Registry Basic | ~$5 |
| Log Analytics (light traffic) | ~$2 |
| **Total Azure** | **~$57** |
| Per-token model cost | **$0** (Ollama local) |

Switching to Dedicated-D4 for latency adds ~$140/mo and removes cold-start latency. Moving to a GPU profile for larger models adds ~$2k+/mo.

## Appendix A — Secretless migration from docker-compose

| Setting | docker-compose (`sidecar/dev`) | This tutorial (ACA) |
|---|---|---|
| `AzureAd__ClientCredentials__0__SourceType` | `ClientSecret` | `SignedAssertionFromManagedIdentity` |
| `BLUEPRINT_CLIENT_SECRET` | In `.env` | **Deleted** |
| Sidecar network access | Docker bridge | Container App localhost |
| FIC on Blueprint | Not required | Required (added in [§7](#7-phase-3--federate-the-managed-identity-to-the-blueprint)) |

Both configurations are valid: local docker-compose optimizes for setup simplicity; ACA optimizes for secretlessness.

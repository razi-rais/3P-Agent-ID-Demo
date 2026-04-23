---
name: deploy-agent-aca-dev
description: 'AI-led deployment of the local-LLM sample agent (Ollama + Microsoft Entra Agent ID sidecar) to Azure Container Apps. Use when the user wants to deploy sidecar/dev to ACA, run the agent on Azure without AWS/GCP, host Ollama on Container Apps, promote the docker-compose local-dev stack from ClientSecret to SignedAssertionFromManagedIdentity (secretless), or debug AADSTS65001/AADSTS50011/ImagePullBackOff/Ollama OOM errors in this specific deployment. NOT for AWS Bedrock (use deploy-agent-aca-aws) and NOT for local docker-compose on a laptop (use sidecar/dev directly). Chains to entra-agent-id-setup for the Blueprint + Agent Identity + Client SPA objects.'
---

# Deploy Local-LLM Agent to Azure Container Apps (AI-Led)

End-to-end, secretless deployment of `sidecar/dev` (Ollama + Entra Agent ID sidecar) to Azure Container Apps. One federation chain (MI → Entra Blueprint), no second cloud.

**Canonical tutorial:** [sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md](../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md). This skill is the automated, fast-path counterpart.

## When to Use

- User says: "deploy dev to ACA", "deploy the local LLM agent to Azure", "Ollama on Container Apps", "ship sidecar/dev to Azure"
- User wants a self-contained demo in a tenant where AWS/GCP aren't options (airgapped, regulated, customer-owned)
- User hits `AADSTS65001` on OBO sign-in — see [references/post-deploy-manual-steps.md](./references/post-deploy-manual-steps.md)
- User hits `AADSTS50011: redirect URI mismatch` — same reference
- User hits Ollama `out of memory` or `ImagePullBackOff` — see [references/ollama-on-aca.md](./references/ollama-on-aca.md)
- User asks "can I run this without Bedrock / Vertex?"

## Do NOT Use When

- **AWS Bedrock deployment** — use `deploy-agent-aca-aws`
- **GCP Vertex AI deployment** — pending skill `deploy-agent-aca-gcp`
- **Local laptop docker-compose** — use `sidecar/dev/` directly with `docker compose up`, no federation needed

## Prerequisites (verify BEFORE running anything)

1. **Entra role** on signing-in user — one of: `Global Administrator`, `Agent ID Administrator`, `Agent ID Developer`. See [entra-agent-id-setup](../entra-agent-id-setup/SKILL.md).
2. **Tooling**: `az` ≥ 2.60, `pwsh` 7.4+, `Microsoft.Graph.Authentication` + `Microsoft.Graph.Beta.Applications` 2.35+, `docker buildx` for `linux/amd64`.
3. **Tenant-confirmed preflight** — ALWAYS confirm tenant ID + subscription ID with the user before any `az` command that mutates resources.
4. **Entra Agent ID base objects** exist — Blueprint, Agent Identity, Client SPA. If not, run the [entra-agent-id-setup](../entra-agent-id-setup/SKILL.md) skill first.

> **[!NOTE] Windows / PowerShell users**
> The orchestrator script and bash helpers (`deploy-aca-dev.sh`, `add-spa-redirect-uri.sh`, `build-ollama-image.sh`) require bash. On Windows without WSL or Git Bash:
> - Run all `az` commands directly in PowerShell — they work identically.
> - Replace `envsubst` with PowerShell `-replace` operators or `(Get-Content template) -replace '\$\{VAR\}', $value`.
> - Replace `source /tmp/deploy-vars.sh` with `$env:VAR = "value"` assignments.
> - For SPA redirect URI (Step 6), use `az rest --method PATCH` instead of the bash script (see Step 6 notes).
> - The **baked Ollama strategy is only available with local Docker Desktop** (see Step 4).

> **[!NOTE] No local Docker Desktop?**
> If Docker Desktop is unavailable, use `az acr build` for `llm-agent` and `weather-api` images, and set `OLLAMA_IMAGE_STRATEGY=runtime-pull`. See Step 4 for details.

## SKU decisions — ask the user first

Before running any `az` command that provisions resources, confirm each SKU choice **with the user**. Do not silently default to the cheapest tier. The orchestrator requires each SKU variable to be set and fails hard if any is missing. Full tradeoff matrix: [references/sku-sizing.md](./references/sku-sizing.md).

| Variable | Ask | Demo default | Silent-failure mode if chosen wrong |
|---|---|---|---|
| `ACR_SKU` | `Basic` / `Standard` / `Premium` | `Basic` | Ollama image ~1 GB; Basic fills up with 10 variants |
| `ACA_WORKLOAD_PROFILE` | `Consumption` / `Dedicated-D4` / GPU | `Consumption` | 1.5B model on Consumption CPU = 5–20 s per answer |
| `LOGS_DESTINATION` | `none` / `log-analytics` | `log-analytics` | `none` hides Ollama OOM crashes and sidecar auth errors |
| `MIN_REPLICAS` / `MAX_REPLICAS` | counts | `1` / `1` | `min=0` = model reloads into RAM on every cold start |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` / `qwen2.5:7b` / `llama3.2:1b` | `qwen2.5:1.5b` | 7B model on Consumption times out ACA ingress |
| `OLLAMA_IMAGE_STRATEGY` | `baked` / `runtime-pull` | `baked` | `runtime-pull` = 30 s first-request delay |
| `OLLAMA_CPU` / `OLLAMA_MEMORY` | see model table below | `0.75` / `1.5Gi` | Undersized memory = OOM crash-loop; see [ollama-on-aca.md](./references/ollama-on-aca.md) |

**Ollama container sizing by model** (must match — the template no longer hardcodes these):

| Model | `OLLAMA_CPU` | `OLLAMA_MEMORY` | Replica total (CPU/Mem) |
|---|---|---|---|
| `qwen2.5:1.5b` / `llama3.2:1b` | `0.75` | `1.5Gi` | 1.75 / 3.5Gi |
| `qwen2.5:3b` | `1.0` | `2.5Gi` | 2.0 / 4.5Gi |
| `qwen2.5:7b` | `2.0` | `6Gi` | 3.0 / 8.5Gi (needs Dedicated/GPU) |

**When invoking this skill, explicitly state the defaults to the user and ask them to confirm or override — do not assume.**

## Procedure

### Step 0 — Confirm account and populate variables

Ask the user to confirm: tenant ID, subscription ID, SKU choices, Ollama model. Then:

```bash
cp .github/skills/deploy-agent-aca-dev/scripts/deploy-vars.sh.template /tmp/deploy-vars.sh
# edit /tmp/deploy-vars.sh with confirmed values
source /tmp/deploy-vars.sh
az login --tenant "$TENANT_ID" && az account set --subscription "$SUBSCRIPTION_ID"
```

### Step 1 — Create Entra objects (Blueprint, Agent, Client SPA)

Delegate to [entra-agent-id-setup](../entra-agent-id-setup/SKILL.md). Capture `BLUEPRINT_APP_ID`, `AGENT_CLIENT_ID`, `CLIENT_SPA_APP_ID` into `/tmp/deploy-vars.sh`.

Configure the Blueprint for OBO using the ACA-compatible PowerShell script:

```bash
pwsh -NoProfile -File .github/skills/deploy-agent-aca-dev/scripts/setup-obo-blueprint-for-aca.ps1 \
  -BlueprintAppId "$BLUEPRINT_APP_ID" \
  -ClientSpaAppId "$CLIENT_SPA_APP_ID" \
  -AgentAppId "$AGENT_CLIENT_ID" \
  -TenantId "$TENANT_ID"
```

### Step 2 — Azure infrastructure (RG, ACR, env, container app skeleton)

See tutorial [§6](../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md#6-phase-2--create-the-azure-infrastructure). Uses SKU variables. Capture `MI_OBJECT_ID` and `APP_FQDN`, append to `/tmp/deploy-vars.sh`. Grant `AcrPull` to the MI.

> **[!WARNING] Dedicated workload profile provisioning is slow.**
> `az containerapp env workload-profile add` for `Dedicated-D4` can take **20+ minutes**. This is normal Azure behavior, not a failure. Do not cancel and retry — just wait.

> **[!NOTE] Azure Policy may block RG creation.**
> Some tenants have policies requiring tags like `RGMonthlyCost` or `Owner` on resource groups (`SFI-W18-*` policies). If `az group create` returns a policy violation, either add the required tags to the command or use a pre-existing RG.

### Step 3 — Federate MI to Blueprint (only federation chain)

Single Graph call — see tutorial [§7](../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md#7-phase-3--federate-the-managed-identity-to-the-blueprint). Subject = `$MI_OBJECT_ID`, audience = `api://AzureADTokenExchange`.

**Unlike the AWS skill, there is no Step 4 (AWS federation).** The local LLM needs no external cloud.

### Step 4 — Build and push images

Tutorial [§8](../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md#8-phase-4--build-and-push-container-images). Three images: `llm-agent`, `weather-api`, `ollama` (baked) — or two if using `runtime-pull`.

**Option A — Local Docker Desktop available:**

```bash
az acr login --name "$ACR_NAME"
docker buildx build --platform linux/amd64 -t "${ACR_NAME}.azurecr.io/agent-id-dev/llm-agent:1.0.0" --push sidecar/dev
docker buildx build --platform linux/amd64 -t "${ACR_NAME}.azurecr.io/agent-id-dev/weather-api:1.0.0" --push sidecar/weather-api
```

If `OLLAMA_IMAGE_STRATEGY=baked`:

```bash
bash .github/skills/deploy-agent-aca-dev/scripts/build-ollama-image.sh
```

**Option B — No local Docker (use `az acr build`):**

```bash
az acr build --registry "$ACR_NAME" --image agent-id-dev/llm-agent:1.0.0 --platform linux/amd64 sidecar/dev
az acr build --registry "$ACR_NAME" --image agent-id-dev/weather-api:1.0.0 --platform linux/amd64 sidecar/weather-api
```

> **[!IMPORTANT] Baked Ollama images CANNOT be built via `az acr build`.**
> The baked strategy's Dockerfile runs `ollama serve & ollama pull <model>` during `docker build`. ACR Build's environment cannot run background daemons in RUN instructions — `ollama serve` silently fails, the pull hangs, and the image never appears in ACR.
>
> **If Docker Desktop is unavailable, you MUST use `runtime-pull` strategy.** Set `OLLAMA_IMAGE_STRATEGY=runtime-pull` and `OLLAMA_IMAGE=docker.io/ollama/ollama:latest`.

> **[!NOTE] `UnicodeEncodeError` from `az acr build` on Windows**
> `az acr build` may emit `UnicodeEncodeError: 'charmap' codec can't encode characters` from colorama when streaming build logs on Windows terminals with cp1252 encoding. This is cosmetic — the build still succeeds. Ignore it.

### Step 5 — Deploy the multi-container app

```bash
envsubst < .github/skills/deploy-agent-aca-dev/scripts/containerapp.yaml.template > /tmp/containerapp.yaml
az containerapp update -g "$RG" -n "$APP_NAME" --yaml /tmp/containerapp.yaml
```

### Step 6 — Post-deploy manual wiring (REQUIRED)

Two things that cannot be done before deploy:

1. **Add production SPA redirect URI** — `bash .github/skills/deploy-agent-aca-dev/scripts/add-spa-redirect-uri.sh`

   > **[!WARNING] PowerShell: do NOT use `az ad app update --set spa=...`**
   > `az ad app update --set spa='{"redirectUris":[...]}'` fails with `Property spa in payload has a value that does not match schema.` due to JSON escaping issues in PowerShell. Use `az rest --method PATCH` against Graph directly:
   > ```powershell
   > $body = @{ spa = @{ redirectUris = @("http://localhost:3003", "https://$env:APP_FQDN") } } | ConvertTo-Json -Depth 3
   > az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications(appId='$env:CLIENT_SPA_APP_ID')" --body $body --headers "Content-Type=application/json"
   > ```

2. **Grant Agent → Graph delegated `User.Read`** (fixes `AADSTS65001`):

   ```bash
   pwsh -NoProfile -File .github/skills/deploy-agent-aca-dev/scripts/grant-agent-obo-consent.ps1 \
     -AgentAppId "$AGENT_CLIENT_ID" -TenantId "$TENANT_ID"
   ```

Full rationale: [references/post-deploy-manual-steps.md](./references/post-deploy-manual-steps.md).

### Step 7 — Verify

Tutorial [§11](../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md#11-phase-7--verify). `ollama_available: true` in status; autonomous flow returns Qwen-generated answer; OBO works via MSAL popup.

## One-Shot Orchestrator

If all prerequisites are in place and SKU variables are confirmed:

```bash
bash .github/skills/deploy-agent-aca-dev/scripts/deploy-aca-dev.sh
```

Idempotent. Invokes steps 2–5 in order. Steps 0, 1, 6 require human decisions and remain manual.

## Key Artifacts

Persisted in `/tmp/deploy-vars.sh`:

| Variable | Source |
|---|---|
| `TENANT_ID`, `SUBSCRIPTION_ID` | User |
| `ACR_SKU`, `ACA_WORKLOAD_PROFILE`, `LOGS_DESTINATION`, `MIN_REPLICAS`, `MAX_REPLICAS` | User (SKU decisions) |
| `OLLAMA_MODEL`, `OLLAMA_IMAGE_STRATEGY`, `OLLAMA_CPU`, `OLLAMA_MEMORY` | User (model + sizing) |
| `BLUEPRINT_APP_ID`, `AGENT_CLIENT_ID`, `CLIENT_SPA_APP_ID` | Step 1 |
| `MI_OBJECT_ID`, `APP_FQDN` | Step 2 |

**No client secrets, no external-cloud credentials** anywhere.

## References

- [Architecture summary](./references/architecture.md)
- [SKU and sizing decisions](./references/sku-sizing.md)
- [Ollama on Azure Container Apps](./references/ollama-on-aca.md)
- [Post-deploy manual steps (SPA URI + OBO consent)](./references/post-deploy-manual-steps.md)
- [Troubleshooting matrix](./references/troubleshooting.md)
- [Full tutorial](../../../sidecar/dev/deploy-local-llm-agent-sidecar-container-apps.md)

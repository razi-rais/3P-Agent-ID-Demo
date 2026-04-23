---
name: deploy-agent-aca-aws
description: 'AI-led deployment of the AWS Bedrock sample agent with the Microsoft Entra Agent ID sidecar to Azure Container Apps. Use when the user wants to deploy the AWS sample to ACA, provision the AWS-federation intermediary Entra app, wire the token refresher, create the AWS OIDC provider and IAM role for Bedrock, build and push the four container images to ACR, or debug InvalidIdentityToken / AADSTS65001 / Directory.AccessAsUser.All / AcrPull errors in this specific deployment. NOT for local docker-compose (use sidecar/dev) and NOT for GCP (deploy-agent-aca-gcp, pending). Chains to entra-agent-id-setup for the Blueprint + Agent Identity + Client SPA objects.'
---

# Deploy AWS Bedrock Agent to Azure Container Apps (AI-Led)

End-to-end, secretless deployment of the AWS sample agent (`sidecar/aws`) to Azure Container Apps with two federation chains: MI → Entra (direct) and MI → AWS STS (via v1 token exchange through an intermediary Entra app).

**Canonical tutorial:** [sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md](../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md). This skill is the automated, fast-path counterpart — read the tutorial when you need the "why", use this skill when you need to ship.

## When to Use

- User says: "deploy AWS sample to ACA", "deploy agent to Azure Container Apps", "ship the AWS sidecar to Azure"
- User hits `InvalidIdentityToken: Incorrect token audience` — see [references/v1-token-exchange.md](./references/v1-token-exchange.md)
- User hits `AADSTS65001` on OBO sign-in — see [references/post-deploy-manual-steps.md](./references/post-deploy-manual-steps.md)
- User hits Graph `Directory.AccessAsUser.All` rejection on Blueprint PATCH — see [references/troubleshooting.md](./references/troubleshooting.md)
- User asks "why do we need an intermediary app" or "why can't the MI talk to AWS directly"

## Do NOT Use When

- **Local dev** (docker-compose) — use `sidecar/dev/` directly, no federation needed
- **GCP deployment** — pending skill `deploy-agent-aca-gcp` (Workload Identity Federation is different)
- **Azure App Service** — `sidecar/aws/DEPLOY-AZURE-APP-SERVICE.md` exists but is outdated; prefer Container Apps

## Prerequisites (verify BEFORE running anything)

1. **Entra role** on signing-in user — one of: `Global Administrator`, `Agent ID Administrator`, `Agent ID Developer`. `Application Administrator` alone returns 403 on blueprint creation. See [entra-agent-id-setup](../entra-agent-id-setup/SKILL.md).
2. **AWS Bedrock access** enabled for `anthropic.claude-3-haiku-20240307-v1:0` in the target region (request via Bedrock console if needed).
3. **Tooling**: `az` ≥ 2.60, `aws` v2, `pwsh` 7.4+, `Microsoft.Graph.*` 2.35+, `docker buildx` for `linux/amd64`.
4. **Tenant-confirmed preflight** — ALWAYS confirm tenant ID + subscription ID with the user before any `az` command that mutates resources (user has multiple accounts; see user memory).
5. **Entra Agent ID base objects** exist — Blueprint, Agent Identity, Client SPA. If not, run the [entra-agent-id-setup](../entra-agent-id-setup/SKILL.md) skill first.

## SKU decisions — ask the user first

Before running any `az` command that provisions resources, confirm each SKU choice **with the user**. Do not silently default to the cheapest tier. The orchestrator requires each SKU variable to be set and fails hard if any is missing. Full tradeoff matrix: [references/sku-sizing.md](./references/sku-sizing.md).

| Variable | Ask | Demo default | Silent-failure mode if chosen wrong |
|---|---|---|---|
| `ACR_SKU` | `Basic` / `Standard` / `Premium` | `Basic` | Basic throttles pulls on rapid revision churn |
| `ACA_WORKLOAD_PROFILE` | `Consumption` / `Dedicated-D4` / GPU | `Consumption` | Consumption + `minReplicas=0` = visible cold starts |
| `LOGS_DESTINATION` | `none` / `log-analytics` | `log-analytics` | `none` means you cannot debug sidecar auth errors post-hoc |
| `MIN_REPLICAS` / `MAX_REPLICAS` | counts | `1` / `1` | `0` = cold starts; `>1` without autoscale rules = cost surprise |
| `AWS_REGION` + `BEDROCK_MODEL_ID` | (see sku-sizing.md §6) | `us-east-2` + `us.anthropic.claude-3-haiku-…` | Wrong region = `ValidationException` on first Bedrock call |

**When invoking this skill, explicitly state the defaults to the user and ask them to confirm or override — do not assume.**

## Procedure

### Step 0 — Confirm account and populate variables

Ask the user to confirm: tenant ID, subscription ID, AWS account ID, AWS region. Then:

```bash
cp .github/skills/deploy-agent-aca-aws/scripts/deploy-vars.sh.template /tmp/deploy-vars.sh
# edit /tmp/deploy-vars.sh with the confirmed values
source /tmp/deploy-vars.sh
az login --tenant "$TENANT_ID" && az account set --subscription "$SUBSCRIPTION_ID"
aws sts get-caller-identity
```

Every subsequent step re-sources `/tmp/deploy-vars.sh` and appends newly discovered IDs back to the file.

### Step 1 — Create Entra objects (Blueprint, Agent, Client SPA)

Delegate to [entra-agent-id-setup](../entra-agent-id-setup/SKILL.md). Capture `BLUEPRINT_APP_ID`, `AGENT_CLIENT_ID`, `CLIENT_SPA_APP_ID` into `/tmp/deploy-vars.sh`.

Then configure the Blueprint for OBO using the ACA-compatible PowerShell script (the shipped `.sh` form uses a Graph filter that Agent Identity Blueprint types reject):

```bash
pwsh -NoProfile -File .github/skills/deploy-agent-aca-aws/scripts/setup-obo-blueprint-for-aca.ps1 \
  -BlueprintAppId "$BLUEPRINT_APP_ID" \
  -ClientSpaAppId "$CLIENT_SPA_APP_ID" \
  -AgentAppId "$AGENT_CLIENT_ID" \
  -TenantId "$TENANT_ID"
```

### Step 2 — Azure infrastructure (RG, ACR, env, container app skeleton)

See tutorial [§6](../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md#6-phase-2--create-the-azure-infrastructure). Capture `MI_OBJECT_ID` and `APP_FQDN`, append to `/tmp/deploy-vars.sh`. Grant `AcrPull` to the MI.

### Step 3 — Federate MI to Blueprint (chain A)

Single Graph call — see tutorial [§7](../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md#7-phase-3--federate-the-managed-identity-to-the-blueprint). Subject = `$MI_OBJECT_ID`, audience = `api://AzureADTokenExchange`.

### Step 4 — Federate MI to AWS (chain B)

```bash
bash .github/skills/deploy-agent-aca-aws/scripts/setup-intermediary-app.sh  # creates STS app, sets v1 tokens, adds FIC
bash .github/skills/deploy-agent-aca-aws/scripts/setup-iam-role.sh          # OIDC provider + IAM role + Bedrock policy
```

Both scripts source `/tmp/deploy-vars.sh` and append `STS_APP_ID`, `STS_APP_URI`, `STS_SP_OID`, `AWS_ROLE_ARN`, `V1_OIDC_ARN`.

**Why the v1 exchange.** See [references/v1-token-exchange.md](./references/v1-token-exchange.md). Short version: AWS STS rejects Azure-MI v2 audiences; an intermediary Entra app with `requestedAccessTokenVersion=1` and `identifierUris=["api://<self>"]` is the Microsoft-supported adapter.

### Step 5 — Build and push images

Tutorial [§9](../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md#9-phase-5--build-and-push-container-images). Three images: `llm-agent`, `weather-api`, `token-refresher`. The sidecar image is pulled from MCR at runtime.

### Step 6 — Deploy the multi-container app

```bash
envsubst < .github/skills/deploy-agent-aca-aws/scripts/containerapp.yaml.template > /tmp/containerapp.yaml
az containerapp update -g "$RG" -n "$APP_NAME" --yaml /tmp/containerapp.yaml
```

### Step 7 — Post-deploy manual wiring (REQUIRED)

Two things that cannot be done before deploy:

1. **Add production SPA redirect URI** — `bash .github/skills/deploy-agent-aca-aws/scripts/add-spa-redirect-uri.sh`
2. **Grant Agent → Graph delegated `User.Read` admin consent** (fixes `AADSTS65001` on OBO):

   ```bash
   pwsh -NoProfile -File .github/skills/deploy-agent-aca-aws/scripts/grant-agent-obo-consent.ps1 \
     -AgentAppId "$AGENT_CLIENT_ID" -TenantId "$TENANT_ID"
   ```

Full rationale: [references/post-deploy-manual-steps.md](./references/post-deploy-manual-steps.md).

### Step 8 — Verify

Tutorial [§12](../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md#12-phase-8--verify). Autonomous `curl` returns real weather; OBO works via MSAL popup; CloudTrail shows `AssumeRoleWithWebIdentity` events with `identityProvider = sts.windows.net/<tenant>/`.

## One-Shot Orchestrator

If all prerequisites are in place and variables are confirmed, run:

```bash
bash .github/skills/deploy-agent-aca-aws/scripts/deploy-aca-aws.sh
```

This is idempotent and invokes steps 2–6 in order. Steps 0, 1, and 7 require human decisions and remain manual.

## Key Artifacts (what you'll end up with)

Persisted in `/tmp/deploy-vars.sh`:

| Variable | Example |
|---|---|
| `TENANT_ID`, `SUBSCRIPTION_ID`, `AWS_ACCOUNT_ID` | (user-provided) |
| `BLUEPRINT_APP_ID`, `AGENT_CLIENT_ID`, `CLIENT_SPA_APP_ID` | from Step 1 |
| `MI_OBJECT_ID`, `APP_FQDN` | from Step 2 |
| `STS_APP_ID`, `STS_APP_URI`, `STS_SP_OID`, `AWS_ROLE_ARN`, `V1_OIDC_ARN` | from Step 4 |

**No client secrets, no AWS access keys**, anywhere.

## References

- [Architecture summary](./references/architecture.md)
- [SKU and sizing decisions](./references/sku-sizing.md)
- [Why the AWS leg needs a v1 token exchange](./references/v1-token-exchange.md)
- [Post-deploy manual steps (SPA URI + OBO consent)](./references/post-deploy-manual-steps.md)
- [Troubleshooting matrix](./references/troubleshooting.md)
- [Full tutorial](../../../sidecar/aws/deploy-aws-bedrock-agent-sidecar-container-apps.md)

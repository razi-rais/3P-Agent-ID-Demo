#!/usr/bin/env bash
# deploy-aca-dev.sh — fast-path orchestrator for the local-LLM (Ollama) + Entra
# Agent ID sidecar deployment on Azure Container Apps.
#
# Runs steps 2-5 of the SKILL procedure. Idempotent where possible.
#
# NOT run by this script (require human decisions):
#   Step 0: account/tenant/SKU confirmation
#   Step 1: entra-agent-id-setup — run that skill first
#   Step 6: post-deploy SPA URI + OBO admin consent — see post-deploy-manual-steps.md
#
# Usage:
#   source /tmp/deploy-vars.sh
#   bash .github/skills/deploy-agent-aca-dev/scripts/deploy-aca-dev.sh
set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPO_ROOT="$( cd "$SCRIPT_DIR/../../../.." && pwd )"
VARS_FILE="${VARS_FILE:-/tmp/deploy-vars.sh}"

# Required before we start — including ALL SKU vars (fail hard if missing).
for var in TENANT_ID SUBSCRIPTION_ID RG LOCATION ACR_NAME APP_NAME \
           ACR_SKU ACA_WORKLOAD_PROFILE LOGS_DESTINATION MIN_REPLICAS MAX_REPLICAS \
           OLLAMA_MODEL OLLAMA_IMAGE_STRATEGY OLLAMA_CPU OLLAMA_MEMORY \
           BLUEPRINT_APP_ID AGENT_CLIENT_ID CLIENT_SPA_APP_ID; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: \$$var is not set. Source $VARS_FILE first (see deploy-vars.sh.template)." >&2
    echo "Hint: SKU variables (ACR_SKU, ACA_WORKLOAD_PROFILE, LOGS_DESTINATION, MIN/MAX_REPLICAS," >&2
    echo "      OLLAMA_MODEL, OLLAMA_IMAGE_STRATEGY) must be set explicitly — see references/sku-sizing.md." >&2
    exit 1
  fi
done

case "$LOGS_DESTINATION" in
  none|log-analytics|azure-monitor) ;;
  *) echo "ERROR: LOGS_DESTINATION must be one of: none, log-analytics, azure-monitor" >&2; exit 1 ;;
esac
case "$OLLAMA_IMAGE_STRATEGY" in
  baked|runtime-pull) ;;
  *) echo "ERROR: OLLAMA_IMAGE_STRATEGY must be 'baked' or 'runtime-pull'" >&2; exit 1 ;;
esac

echo "================================================================"
echo " Deploy target"
echo "   Tenant:        $TENANT_ID"
echo "   Subscription:  $SUBSCRIPTION_ID"
echo "   Resource group: $RG ($LOCATION)"
echo "   Container App: $APP_NAME"
echo "   ACR:           $ACR_NAME (SKU: $ACR_SKU)"
echo "   Workload:      $ACA_WORKLOAD_PROFILE"
echo "   Logs:          $LOGS_DESTINATION"
echo "   Replicas:      min=$MIN_REPLICAS max=$MAX_REPLICAS"
echo "   Ollama model:  $OLLAMA_MODEL  (strategy: $OLLAMA_IMAGE_STRATEGY)"
echo "   Ollama sizing: ${OLLAMA_CPU} vCPU / ${OLLAMA_MEMORY}"
echo "================================================================"

### Step 2 — Azure infrastructure + container app skeleton ###

echo
echo "[Step 2] Azure infrastructure"
az group create --name "$RG" --location "$LOCATION" -o none
az acr create --resource-group "$RG" --name "$ACR_NAME" --sku "$ACR_SKU" --admin-enabled false -o none 2>/dev/null || true
az provider register --namespace Microsoft.App --wait

ENV_ARGS=(--resource-group "$RG" --name "${APP_NAME}-env" --location "$LOCATION")
case "$LOGS_DESTINATION" in
  none)
    ENV_ARGS+=(--logs-destination none)
    ;;
  log-analytics)
    if [[ -z "${LOG_ANALYTICS_WORKSPACE_ID:-}" ]]; then
      WS_NAME="${APP_NAME}-logs"
      az monitor log-analytics workspace create -g "$RG" -n "$WS_NAME" -l "$LOCATION" -o none 2>/dev/null || true
      LOG_ANALYTICS_WORKSPACE_ID=$(az monitor log-analytics workspace show -g "$RG" -n "$WS_NAME" --query customerId -o tsv)
      LOG_ANALYTICS_WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys -g "$RG" -n "$WS_NAME" --query primarySharedKey -o tsv)
      echo "export LOG_ANALYTICS_WORKSPACE_ID=\"$LOG_ANALYTICS_WORKSPACE_ID\"" >> "$VARS_FILE"
    else
      LOG_ANALYTICS_WORKSPACE_KEY=$(az monitor log-analytics workspace get-shared-keys \
        --workspace-name "$(az monitor log-analytics workspace list --query "[?customerId=='$LOG_ANALYTICS_WORKSPACE_ID'].name | [0]" -o tsv)" \
        -g "$RG" --query primarySharedKey -o tsv 2>/dev/null || echo "")
    fi
    ENV_ARGS+=(--logs-destination log-analytics --logs-workspace-id "$LOG_ANALYTICS_WORKSPACE_ID")
    [[ -n "${LOG_ANALYTICS_WORKSPACE_KEY:-}" ]] && ENV_ARGS+=(--logs-workspace-key "$LOG_ANALYTICS_WORKSPACE_KEY")
    ;;
  azure-monitor)
    ENV_ARGS+=(--logs-destination azure-monitor)
    ;;
esac
az containerapp env create "${ENV_ARGS[@]}" -o none 2>/dev/null || true

if [[ "$ACA_WORKLOAD_PROFILE" != "Consumption" ]]; then
  az containerapp env workload-profile add \
    --resource-group "$RG" --name "${APP_NAME}-env" \
    --workload-profile-name "$ACA_WORKLOAD_PROFILE" \
    --workload-profile-type "$ACA_WORKLOAD_PROFILE" \
    --min-nodes 1 --max-nodes 1 -o none 2>/dev/null || true
fi

if ! az containerapp show -g "$RG" -n "$APP_NAME" >/dev/null 2>&1; then
  CREATE_ARGS=(--resource-group "$RG" --name "$APP_NAME"
    --environment "${APP_NAME}-env"
    --image mcr.microsoft.com/k8se/quickstart:latest
    --system-assigned
    --ingress external --target-port 80
    --min-replicas "$MIN_REPLICAS" --max-replicas "$MAX_REPLICAS")
  [[ "$ACA_WORKLOAD_PROFILE" != "Consumption" ]] && CREATE_ARGS+=(--workload-profile-name "$ACA_WORKLOAD_PROFILE")
  az containerapp create "${CREATE_ARGS[@]}" -o none
fi

APP_FQDN=$(az containerapp show -g "$RG" -n "$APP_NAME" --query 'properties.configuration.ingress.fqdn' -o tsv)
MI_OBJECT_ID=$(az containerapp show -g "$RG" -n "$APP_NAME" --query 'identity.principalId' -o tsv)
export APP_FQDN MI_OBJECT_ID
echo "  APP_FQDN     = $APP_FQDN"
echo "  MI_OBJECT_ID = $MI_OBJECT_ID"

ACR_ID=$(az acr show --name "$ACR_NAME" --query id -o tsv)
az role assignment create \
  --assignee-object-id "$MI_OBJECT_ID" \
  --assignee-principal-type ServicePrincipal \
  --scope "$ACR_ID" --role AcrPull -o none 2>/dev/null || true

{
  echo "export APP_FQDN=\"$APP_FQDN\""
  echo "export MI_OBJECT_ID=\"$MI_OBJECT_ID\""
} >> "$VARS_FILE"

### Step 3 — Federate MI to Blueprint (only federation chain) ###

echo
echo "[Step 3] Federating MI -> Blueprint"
pwsh -NoProfile -Command "
Connect-MgGraph -Scopes 'AgentIdentityBlueprint.AddRemoveCreds.All' -TenantId '$TENANT_ID' -NoWelcome | Out-Null
\$body = @{ name='container-app-mi'; issuer=\"https://login.microsoftonline.com/$TENANT_ID/v2.0\"; subject='$MI_OBJECT_ID'; audiences=@('api://AzureADTokenExchange'); description='Container App system MI' } | ConvertTo-Json -Depth 5
try { Invoke-MgGraphRequest -Method POST -Uri \"https://graph.microsoft.com/beta/applications(appId='$BLUEPRINT_APP_ID')/federatedIdentityCredentials\" -Body \$body -ContentType 'application/json' | Out-Null; Write-Host '  Created FIC on Blueprint' }
catch { Write-Host \"  (may already exist) \$(\$_.Exception.Message)\" }
"

### Step 4 — Build and push images ###

echo
echo "[Step 4] Build and push images"
az acr login --name "$ACR_NAME"
docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-dev/llm-agent:1.0.0" \
  --push "$REPO_ROOT/sidecar/dev"
docker buildx build --platform linux/amd64 \
  -t "${ACR_NAME}.azurecr.io/agent-id-dev/weather-api:1.0.0" \
  --push "$REPO_ROOT/sidecar/weather-api"

if [[ "$OLLAMA_IMAGE_STRATEGY" == "baked" ]]; then
  bash "$SCRIPT_DIR/build-ollama-image.sh"
  # shellcheck disable=SC1090
  source "$VARS_FILE"
else
  export OLLAMA_IMAGE="docker.io/ollama/ollama:latest"
fi

### Step 5 — Deploy multi-container manifest ###

echo
echo "[Step 5] Deploy multi-container manifest"
ENV_ID=$(az containerapp env show -g "$RG" -n "${APP_NAME}-env" --query id -o tsv)
export ENV_ID OLLAMA_IMAGE

if ! command -v envsubst >/dev/null; then
  echo "ERROR: envsubst not found. On macOS: brew install gettext" >&2
  exit 1
fi
envsubst < "$SCRIPT_DIR/containerapp.yaml.template" > /tmp/containerapp.yaml
az containerapp update -g "$RG" -n "$APP_NAME" --yaml /tmp/containerapp.yaml -o none

PREV_REV=$(az containerapp revision list -g "$RG" -n "$APP_NAME" \
  --query "[?properties.template.containers[0].name=='$APP_NAME'].name | [0]" -o tsv 2>/dev/null || echo "")
[[ -n "$PREV_REV" ]] && az containerapp revision deactivate -g "$RG" -n "$APP_NAME" --revision "$PREV_REV" -o none 2>/dev/null || true

echo
echo "================================================================"
echo " Deployed."
echo "   URL: https://$APP_FQDN"
echo
echo " REQUIRED post-deploy manual steps (Step 6):"
echo "   1. Add production SPA URI:"
echo "      bash $SCRIPT_DIR/add-spa-redirect-uri.sh"
echo "   2. Grant Agent -> Graph delegated User.Read:"
echo "      pwsh -File $SCRIPT_DIR/grant-agent-obo-consent.ps1 \\"
echo "        -AgentAppId \"$AGENT_CLIENT_ID\" -TenantId \"$TENANT_ID\""
echo "================================================================"

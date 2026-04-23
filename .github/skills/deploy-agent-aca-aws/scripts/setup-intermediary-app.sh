#!/usr/bin/env bash
# setup-intermediary-app.sh — create the v1-token-emitting Entra app used for AWS STS exchange,
# federate the Container App's managed identity to it, and append discovered IDs to /tmp/deploy-vars.sh.
#
# Prereqs in /tmp/deploy-vars.sh: TENANT_ID, MI_OBJECT_ID.
set -euo pipefail

: "${TENANT_ID:?TENANT_ID not set — source /tmp/deploy-vars.sh}"
: "${MI_OBJECT_ID:?MI_OBJECT_ID not set — run Azure infra step first}"

VARS_FILE="${VARS_FILE:-/tmp/deploy-vars.sh}"

echo "[1/5] Creating intermediary Entra app..."
STS_APP_ID=$(az ad app create \
  --display-name "AWS STS Bedrock Federation" \
  --sign-in-audience AzureADMyOrg \
  --query appId -o tsv)
STS_APP_URI="api://${STS_APP_ID}"

echo "[2/5] Setting identifierUris and creating SP..."
az ad app update --id "$STS_APP_ID" --identifier-uris "$STS_APP_URI" -o none
az ad sp create --id "$STS_APP_ID" -o none
STS_SP_OID=$(az ad sp show --id "$STS_APP_ID" --query id -o tsv)

echo "[3/5] Setting requestedAccessTokenVersion=1 (v1 tokens required by AWS STS)..."
GRAPH_TOKEN=$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)
curl -sS --fail -X PATCH \
  -H "Authorization: Bearer $GRAPH_TOKEN" -H "Content-Type: application/json" \
  "https://graph.microsoft.com/v1.0/applications(appId='$STS_APP_ID')" \
  -d '{"api":{"requestedAccessTokenVersion":1}}' >/dev/null

echo "[4/5] Adding federated identity credential (MI -> intermediary app)..."
FIC=$(mktemp)
cat > "$FIC" <<EOF
{
  "name": "container-app-mi",
  "issuer": "https://login.microsoftonline.com/${TENANT_ID}/v2.0",
  "subject": "${MI_OBJECT_ID}",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "Container App system MI"
}
EOF
az ad app federated-credential create --id "$STS_APP_ID" --parameters "@$FIC" -o none
rm -f "$FIC"

echo "[5/5] Appending discovered IDs to $VARS_FILE"
{
  echo "export STS_APP_ID=\"$STS_APP_ID\""
  echo "export STS_APP_URI=\"$STS_APP_URI\""
  echo "export STS_SP_OID=\"$STS_SP_OID\""
} >> "$VARS_FILE"

echo
echo "Done."
echo "  STS_APP_ID  = $STS_APP_ID"
echo "  STS_APP_URI = $STS_APP_URI"
echo "  STS_SP_OID  = $STS_SP_OID"
echo
echo "Re-source the vars file: source $VARS_FILE"

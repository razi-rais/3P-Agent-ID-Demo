#!/usr/bin/env bash
# add-spa-redirect-uri.sh — append https://$APP_FQDN to the Client SPA app's
# spa.redirectUris. Idempotent. Uses Graph PATCH (az CLI cannot modify SPA URIs).
#
# Prereqs in /tmp/deploy-vars.sh: CLIENT_SPA_APP_ID, APP_FQDN.
set -euo pipefail

: "${CLIENT_SPA_APP_ID:?}"
: "${APP_FQDN:?}"

PROD_URI="https://${APP_FQDN}"
GRAPH_TOKEN=$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)

EXISTING=$(curl -sS --fail -H "Authorization: Bearer $GRAPH_TOKEN" \
  "https://graph.microsoft.com/v1.0/applications(appId='$CLIENT_SPA_APP_ID')?\$select=spa")

BODY=$(python3 - <<PY
import json, sys
data = json.loads('''$EXISTING''')
uris = data.get("spa", {}).get("redirectUris", [])
if "$PROD_URI" not in uris:
    uris.append("$PROD_URI")
print(json.dumps({"spa": {"redirectUris": uris}}))
PY
)

curl -sS --fail -X PATCH \
  -H "Authorization: Bearer $GRAPH_TOKEN" -H "Content-Type: application/json" \
  "https://graph.microsoft.com/v1.0/applications(appId='$CLIENT_SPA_APP_ID')" \
  -d "$BODY" >/dev/null

echo "Added $PROD_URI to Client SPA redirect URIs."

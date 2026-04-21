#!/bin/bash
# =============================================================================
# Register MSAL.js Client SPA App
# =============================================================================
# Creates a regular Entra app registration for MSAL.js browser sign-in.
# The user signs into THIS app → gets Tc → sends Tc to agent → OBO exchange.
#
# Usage:
#   cd sidecar && bash setup-obo-client-app.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ .env not found at $ENV_FILE"; exit 1
fi
set -a; source "$ENV_FILE"; set +a

TENANT_ID="${TENANT_ID:?TENANT_ID not set in .env}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Register MSAL.js Client SPA App"
echo "  Tenant: $TENANT_ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Check login ──────────────────────────────────────────────────────────────
echo "🔐 Checking Azure CLI login..."
CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null || true)
if [[ "$CURRENT_TENANT" != "$TENANT_ID" ]]; then
    az login --tenant "$TENANT_ID" --allow-no-subscriptions -o none
fi
echo "   ✅ Logged in"
echo ""

# ── Create app (or find existing) ───────────────────────────────────────────
APP_NAME="Agent Demo Client SPA"
echo "🌐 Creating app registration: $APP_NAME"

CLIENT_SPA_APP_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv 2>/dev/null || true)

if [[ -n "$CLIENT_SPA_APP_ID" && "$CLIENT_SPA_APP_ID" != "None" ]]; then
    echo "   ✅ Already exists: $CLIENT_SPA_APP_ID"
else
    CLIENT_SPA_APP_ID=$(az ad app create \
        --display-name "$APP_NAME" \
        --sign-in-audience "AzureADMyOrg" \
        --query "appId" -o tsv)
    echo "   ✅ Created: $CLIENT_SPA_APP_ID"
    sleep 5
fi
echo ""

# ── Set SPA redirect URI ────────────────────────────────────────────────────
echo "🔗 Setting SPA redirect URI → http://localhost:3003"
az ad app update --id "$CLIENT_SPA_APP_ID" \
    --set spa='{"redirectUris":["http://localhost:3003"]}'
echo "   ✅ Done"
echo ""

# ── Ensure service principal exists ──────────────────────────────────────────
echo "👤 Ensuring service principal exists..."
SP_ID=$(az ad sp list --filter "appId eq '$CLIENT_SPA_APP_ID'" --query "[0].id" -o tsv 2>/dev/null || true)
if [[ -z "$SP_ID" || "$SP_ID" == "None" ]]; then
    az ad sp create --id "$CLIENT_SPA_APP_ID" -o none
    echo "   ✅ Created service principal"
else
    echo "   ✅ Already exists"
fi
echo ""

# ── Save to .env ─────────────────────────────────────────────────────────────
echo "📝 Saving to .env..."
if grep -q "^CLIENT_SPA_APP_ID=" "$ENV_FILE"; then
    sed -i.bak "s/^CLIENT_SPA_APP_ID=.*/CLIENT_SPA_APP_ID=$CLIENT_SPA_APP_ID/" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
    echo "   ✅ Updated CLIENT_SPA_APP_ID"
else
    echo "" >> "$ENV_FILE"
    echo "# Client SPA App for MSAL.js user sign-in (OBO)" >> "$ENV_FILE"
    echo "CLIENT_SPA_APP_ID=$CLIENT_SPA_APP_ID" >> "$ENV_FILE"
    echo "   ✅ Added CLIENT_SPA_APP_ID"
fi
echo ""

# ── Done ─────────────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Done!"
echo ""
echo "  Client SPA App ID: $CLIENT_SPA_APP_ID"
echo "  Redirect URI:      http://localhost:3003"
echo ""
echo "  Next:"
echo "    docker-compose -f docker-compose-aws.yml up -d --build llm-agent-aws"
echo "    open http://localhost:3003"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

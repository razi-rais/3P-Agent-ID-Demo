#!/bin/bash
# =============================================================================
# Setup Blueprint for OBO (On-Behalf-Of) Flow
# =============================================================================
# Following: https://blog.christianposta.com/entra-agent-id-agw/PART-2.html
#
# This script uses the Graph Beta API to:
#   1. Set Application ID URI on the Blueprint (api://<blueprint-app-id>)
#   2. Add 'access_as_user' delegated scope to the Blueprint
#   3. Grant admin consent for Graph delegated permissions on the Agent Identity
#   4. Add API permission on Client SPA to request Blueprint's scope
#
# Prerequisites:
#   - Azure CLI logged in with admin privileges
#   - .env file with BLUEPRINT_APP_ID, AGENT_CLIENT_ID, CLIENT_SPA_APP_ID, TENANT_ID
#
# Usage:
#   cd sidecar && bash setup-obo-blueprint.sh
#
# Reference:
#   https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration
#   https://blog.christianposta.com/entra-agent-id-agw/PART-2.html
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "❌ .env not found at $ENV_FILE"; exit 1
fi
set -a; source "$ENV_FILE"; set +a

TENANT_ID="${TENANT_ID:?TENANT_ID not set in .env}"
BLUEPRINT_APP_ID="${BLUEPRINT_APP_ID:?BLUEPRINT_APP_ID not set in .env}"
AGENT_CLIENT_ID="${AGENT_CLIENT_ID:?AGENT_CLIENT_ID not set in .env}"
CLIENT_SPA_APP_ID="${CLIENT_SPA_APP_ID:?CLIENT_SPA_APP_ID not set in .env}"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup Blueprint for OBO Flow"
echo "  Blueprint:  $BLUEPRINT_APP_ID"
echo "  Agent:      $AGENT_CLIENT_ID"
echo "  Client SPA: $CLIENT_SPA_APP_ID"
echo "  Tenant:     $TENANT_ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Check login ──────────────────────────────────────────────────────────────
echo "🔐 Checking Azure CLI login..."
CURRENT_TENANT=$(az account show --query tenantId -o tsv 2>/dev/null || true)
if [[ "$CURRENT_TENANT" != "$TENANT_ID" ]]; then
    echo "   Logging in to tenant $TENANT_ID..."
    az login --tenant "$TENANT_ID" --allow-no-subscriptions -o none
fi
echo "   ✅ Logged in"
echo ""

# ── Get access token for Graph Beta ─────────────────────────────────────────
echo "🔑 Getting Graph API token..."
GRAPH_TOKEN=$(az account get-access-token --resource https://graph.microsoft.com --query accessToken -o tsv)
echo "   ✅ Got token (length: ${#GRAPH_TOKEN})"
echo ""

# ── Step 1: Get Blueprint object ID ─────────────────────────────────────────
echo "📋 Step 1: Looking up Blueprint application object..."
BLUEPRINT_OBJ=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
    "https://graph.microsoft.com/beta/applications?\$filter=appId eq '$BLUEPRINT_APP_ID'&\$select=id,displayName,identifierUris,api")

BLUEPRINT_OBJECT_ID=$(echo "$BLUEPRINT_OBJ" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['id'])" 2>/dev/null || true)

if [[ -z "$BLUEPRINT_OBJECT_ID" ]]; then
    echo "   ❌ Could not find Blueprint application. Response:"
    echo "$BLUEPRINT_OBJ" | python3 -m json.tool 2>/dev/null || echo "$BLUEPRINT_OBJ"
    exit 1
fi

BLUEPRINT_NAME=$(echo "$BLUEPRINT_OBJ" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['displayName'])")
EXISTING_URIS=$(echo "$BLUEPRINT_OBJ" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0].get('identifierUris', []))")
EXISTING_SCOPES=$(echo "$BLUEPRINT_OBJ" | python3 -c "import sys,json; d=json.load(sys.stdin); scopes=d['value'][0].get('api',{}).get('oauth2PermissionScopes',[]); print([s.get('value') for s in scopes])")

echo "   ✅ Found: $BLUEPRINT_NAME"
echo "   Object ID: $BLUEPRINT_OBJECT_ID"
echo "   Existing URIs: $EXISTING_URIS"
echo "   Existing scopes: $EXISTING_SCOPES"
echo ""

# ── Step 2: Set Application ID URI ──────────────────────────────────────────
APP_ID_URI="api://$BLUEPRINT_APP_ID"
echo "🌐 Step 2: Setting Application ID URI → $APP_ID_URI"

# Check if already set
if echo "$EXISTING_URIS" | grep -q "$BLUEPRINT_APP_ID"; then
    echo "   ✅ Already set"
else
    HTTP_CODE=$(curl -s -o /tmp/bp_uri_response.json -w "%{http_code}" \
        -X PATCH \
        -H "Authorization: Bearer $GRAPH_TOKEN" \
        -H "Content-Type: application/json" \
        "https://graph.microsoft.com/beta/applications/$BLUEPRINT_OBJECT_ID" \
        -d "{\"identifierUris\": [\"$APP_ID_URI\"]}")

    if [[ "$HTTP_CODE" == "204" ]]; then
        echo "   ✅ Set App ID URI to: $APP_ID_URI"
    else
        echo "   ⚠️  HTTP $HTTP_CODE — Response:"
        cat /tmp/bp_uri_response.json | python3 -m json.tool 2>/dev/null || cat /tmp/bp_uri_response.json
        echo ""
        echo "   If this fails, set it manually in Azure Portal:"
        echo "   → App registrations → $BLUEPRINT_NAME → Expose an API → Set App ID URI"
    fi
fi
echo ""

# ── Step 3: Add access_as_user scope ────────────────────────────────────────
echo "🔑 Step 3: Adding 'access_as_user' delegated scope..."

if echo "$EXISTING_SCOPES" | grep -q "access_as_user"; then
    echo "   ✅ Scope already exists"
else
    SCOPE_ID=$(python3 -c "import uuid; print(str(uuid.uuid4()))")
    
    SCOPE_BODY=$(cat <<EOF
{
    "api": {
        "oauth2PermissionScopes": [
            {
                "id": "$SCOPE_ID",
                "adminConsentDescription": "Allow the application to access the agent on behalf of the signed-in user",
                "adminConsentDisplayName": "Access agent as user",
                "isEnabled": true,
                "type": "User",
                "userConsentDescription": "Allow the application to access the agent on your behalf",
                "userConsentDisplayName": "Access agent as user",
                "value": "access_as_user"
            }
        ]
    }
}
EOF
)

    HTTP_CODE=$(curl -s -o /tmp/bp_scope_response.json -w "%{http_code}" \
        -X PATCH \
        -H "Authorization: Bearer $GRAPH_TOKEN" \
        -H "Content-Type: application/json" \
        "https://graph.microsoft.com/beta/applications/$BLUEPRINT_OBJECT_ID" \
        -d "$SCOPE_BODY")

    if [[ "$HTTP_CODE" == "204" ]]; then
        echo "   ✅ Added 'access_as_user' scope"
        echo "   Full scope: $APP_ID_URI/access_as_user"
    else
        echo "   ⚠️  HTTP $HTTP_CODE — Response:"
        cat /tmp/bp_scope_response.json | python3 -m json.tool 2>/dev/null || cat /tmp/bp_scope_response.json
        echo ""
        echo "   If this fails, add it manually in Azure Portal:"
        echo "   → App registrations → $BLUEPRINT_NAME → Expose an API → Add a scope"
        echo "   → Scope name: access_as_user, Who can consent: Admins and users"
    fi
fi
echo ""

# ── Step 4: Add API permission on Client SPA ────────────────────────────────
echo "📝 Step 4: Adding API permission on Client SPA → Blueprint scope..."

# Get Blueprint service principal ID
BLUEPRINT_SP=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
    "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=appId eq '$BLUEPRINT_APP_ID'&\$select=id")
BLUEPRINT_SP_ID=$(echo "$BLUEPRINT_SP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['id'])" 2>/dev/null || true)

# Get the scope ID from the Blueprint
SCOPE_ID_FROM_BP=$(echo "$BLUEPRINT_OBJ" | python3 -c "
import sys, json
d = json.load(sys.stdin)
scopes = d['value'][0].get('api', {}).get('oauth2PermissionScopes', [])
for s in scopes:
    if s.get('value') == 'access_as_user':
        print(s['id'])
        break
" 2>/dev/null || true)

# If we just created it, re-query to get the scope ID
if [[ -z "$SCOPE_ID_FROM_BP" ]]; then
    echo "   Re-querying Blueprint for scope ID..."
    BLUEPRINT_OBJ2=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
        "https://graph.microsoft.com/beta/applications?\$filter=appId eq '$BLUEPRINT_APP_ID'&\$select=id,api")
    SCOPE_ID_FROM_BP=$(echo "$BLUEPRINT_OBJ2" | python3 -c "
import sys, json
d = json.load(sys.stdin)
scopes = d['value'][0].get('api', {}).get('oauth2PermissionScopes', [])
for s in scopes:
    if s.get('value') == 'access_as_user':
        print(s['id'])
        break
" 2>/dev/null || true)
fi

if [[ -n "$SCOPE_ID_FROM_BP" && -n "$BLUEPRINT_SP_ID" ]]; then
    # Get Client SPA object ID
    CLIENT_SPA_OBJ_ID=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
        "https://graph.microsoft.com/v1.0/applications?\$filter=appId eq '$CLIENT_SPA_APP_ID'&\$select=id" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['id'])")

    # Add the permission
    PERM_BODY="{\"requiredResourceAccess\": [{\"resourceAppId\": \"$BLUEPRINT_APP_ID\", \"resourceAccess\": [{\"id\": \"$SCOPE_ID_FROM_BP\", \"type\": \"Scope\"}]}]}"

    HTTP_CODE=$(curl -s -o /tmp/bp_perm_response.json -w "%{http_code}" \
        -X PATCH \
        -H "Authorization: Bearer $GRAPH_TOKEN" \
        -H "Content-Type: application/json" \
        "https://graph.microsoft.com/v1.0/applications/$CLIENT_SPA_OBJ_ID" \
        -d "$PERM_BODY")

    if [[ "$HTTP_CODE" == "204" ]]; then
        echo "   ✅ Added API permission on Client SPA"
    else
        echo "   ⚠️  HTTP $HTTP_CODE — Response:"
        cat /tmp/bp_perm_response.json | python3 -m json.tool 2>/dev/null || cat /tmp/bp_perm_response.json
    fi
else
    echo "   ⚠️  Could not find scope ID or Blueprint SP. Add manually in Portal."
    echo "   Blueprint SP ID: ${BLUEPRINT_SP_ID:-not found}"
    echo "   Scope ID: ${SCOPE_ID_FROM_BP:-not found}"
fi
echo ""

# ── Step 5: Grant admin consent for Agent Identity → Graph ───────────────────
echo "🛡️  Step 5: Granting admin consent for Agent Identity → Graph..."

# Get the Agent Identity service principal
AGENT_SP=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
    "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=appId eq '$AGENT_CLIENT_ID'&\$select=id")
AGENT_SP_ID=$(echo "$AGENT_SP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['id'])" 2>/dev/null || true)

# Get Microsoft Graph service principal
GRAPH_SP=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
    "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=appId eq '00000003-0000-0000-c000-000000000000'&\$select=id")
GRAPH_SP_ID=$(echo "$GRAPH_SP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['id'])" 2>/dev/null || true)

if [[ -n "$AGENT_SP_ID" && -n "$GRAPH_SP_ID" ]]; then
    # Check if consent already exists
    EXISTING_CONSENT=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
        "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?\$filter=clientId eq '$AGENT_SP_ID' and resourceId eq '$GRAPH_SP_ID'")
    HAS_CONSENT=$(echo "$EXISTING_CONSENT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('value',[]))>0)" 2>/dev/null || echo "False")
    
    if [[ "$HAS_CONSENT" == "True" ]]; then
        echo "   ✅ Admin consent already exists"
        EXISTING_SCOPE=$(echo "$EXISTING_CONSENT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0].get('scope',''))" 2>/dev/null || true)
        echo "   Scopes: $EXISTING_SCOPE"
    else
        CONSENT_BODY=$(cat <<EOF
{
    "clientId": "$AGENT_SP_ID",
    "consentType": "AllPrincipals",
    "resourceId": "$GRAPH_SP_ID",
    "scope": "User.Read openid profile offline_access"
}
EOF
)
        HTTP_CODE=$(curl -s -o /tmp/bp_consent_response.json -w "%{http_code}" \
            -X POST \
            -H "Authorization: Bearer $GRAPH_TOKEN" \
            -H "Content-Type: application/json" \
            "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
            -d "$CONSENT_BODY")

        if [[ "$HTTP_CODE" == "201" ]]; then
            echo "   ✅ Admin consent granted for Agent Identity → Graph"
        else
            echo "   ⚠️  HTTP $HTTP_CODE — Response:"
            cat /tmp/bp_consent_response.json | python3 -m json.tool 2>/dev/null || cat /tmp/bp_consent_response.json
        fi
    fi
else
    echo "   ⚠️  Could not find service principals."
    echo "   Agent SP ID: ${AGENT_SP_ID:-not found}"
    echo "   Graph SP ID: ${GRAPH_SP_ID:-not found}"
fi
echo ""

# ── Step 6: Grant admin consent for Client SPA → Blueprint ──────────────────
echo "🛡️  Step 6: Granting admin consent for Client SPA → Blueprint..."

CLIENT_SPA_SP=$(curl -s -H "Authorization: Bearer $GRAPH_TOKEN" \
    "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=appId eq '$CLIENT_SPA_APP_ID'&\$select=id")
CLIENT_SPA_SP_ID=$(echo "$CLIENT_SPA_SP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['value'][0]['id'])" 2>/dev/null || true)

if [[ -n "$CLIENT_SPA_SP_ID" && -n "$BLUEPRINT_SP_ID" ]]; then
    CONSENT_BODY2=$(cat <<EOF
{
    "clientId": "$CLIENT_SPA_SP_ID",
    "consentType": "AllPrincipals",
    "resourceId": "$BLUEPRINT_SP_ID",
    "scope": "access_as_user"
}
EOF
)
    HTTP_CODE=$(curl -s -o /tmp/bp_consent2_response.json -w "%{http_code}" \
        -X POST \
        -H "Authorization: Bearer $GRAPH_TOKEN" \
        -H "Content-Type: application/json" \
        "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
        -d "$CONSENT_BODY2")

    if [[ "$HTTP_CODE" == "201" ]]; then
        echo "   ✅ Admin consent granted for Client SPA → Blueprint"
    elif [[ "$HTTP_CODE" == "409" ]]; then
        echo "   ✅ Consent already exists"
    else
        echo "   ⚠️  HTTP $HTTP_CODE — Response:"
        cat /tmp/bp_consent2_response.json | python3 -m json.tool 2>/dev/null || cat /tmp/bp_consent2_response.json
    fi
else
    echo "   ⚠️  Could not find service principals."
    echo "   Client SPA SP ID: ${CLIENT_SPA_SP_ID:-not found}"
    echo "   Blueprint SP ID: ${BLUEPRINT_SP_ID:-not found}"
fi
echo ""

# ── Summary ──────────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OBO Blueprint Setup Complete"
echo ""
echo "  Blueprint API:  $APP_ID_URI"
echo "  OBO Scope:      $APP_ID_URI/access_as_user"
echo ""
echo "  MSAL.js should request:"
echo "    scopes: ['$APP_ID_URI/access_as_user']"
echo ""
echo "  The user token (Tc) will have:"
echo "    aud: $APP_ID_URI"
echo ""
echo "  Sidecar validates:"
echo "    AzureAd__ClientId = $BLUEPRINT_APP_ID"
echo "    (audience defaults to api://{ClientId} = $APP_ID_URI ✅)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

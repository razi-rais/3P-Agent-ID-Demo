#!/usr/bin/env bash
# setup-iam-role.sh — create the AWS OIDC identity provider (v1 issuer) and the
# IAM role for Bedrock invocation. Pins aud + sub to the intermediary app.
#
# Prereqs in /tmp/deploy-vars.sh: TENANT_ID, AWS_ACCOUNT_ID, AWS_REGION,
#   STS_APP_URI, STS_SP_OID, AWS_ROLE_NAME, BEDROCK_MODEL_ID.
set -euo pipefail

: "${TENANT_ID:?}"
: "${AWS_ACCOUNT_ID:?}"
: "${STS_APP_URI:?run setup-intermediary-app.sh first}"
: "${STS_SP_OID:?run setup-intermediary-app.sh first}"
: "${AWS_ROLE_NAME:?}"
: "${BEDROCK_MODEL_ID:?}"

VARS_FILE="${VARS_FILE:-/tmp/deploy-vars.sh}"
ISSUER_URL="https://sts.windows.net/${TENANT_ID}/"

echo "[1/3] Creating (or reusing) AWS OIDC identity provider for v1 issuer..."
V1_OIDC_ARN=$(aws iam create-open-id-connect-provider \
  --url "$ISSUER_URL" \
  --client-id-list "$STS_APP_URI" \
  --thumbprint-list "626d44e704d1ceabe3bf0d53397464ac8080142c" \
  --query OpenIDConnectProviderArn --output text 2>/dev/null || true)

if [[ -z "$V1_OIDC_ARN" ]]; then
  # Provider already exists. Look it up.
  V1_OIDC_ARN=$(aws iam list-open-id-connect-providers \
    --query "OpenIDConnectProviderList[?contains(Arn,'sts.windows.net/${TENANT_ID}/')].Arn | [0]" \
    --output text)
  echo "  Reusing existing: $V1_OIDC_ARN"
  # Ensure client-id is registered
  aws iam add-client-id-to-open-id-connect-provider \
    --open-id-connect-provider-arn "$V1_OIDC_ARN" \
    --client-id "$STS_APP_URI" 2>/dev/null || true
fi

echo "[2/3] Writing trust policy (pins aud + sub to intermediary SP)..."
TRUST=$(mktemp)
cat > "$TRUST" <<EOF
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

POLICY=$(mktemp)
cat > "$POLICY" <<EOF
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

echo "[3/3] Creating IAM role and inline policy..."
if aws iam get-role --role-name "$AWS_ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$AWS_ROLE_NAME" --policy-document "file://$TRUST"
else
  aws iam create-role --role-name "$AWS_ROLE_NAME" --assume-role-policy-document "file://$TRUST" >/dev/null
fi
aws iam put-role-policy --role-name "$AWS_ROLE_NAME" --policy-name BedrockInvokeOnly --policy-document "file://$POLICY"
rm -f "$TRUST" "$POLICY"

AWS_ROLE_ARN=$(aws iam get-role --role-name "$AWS_ROLE_NAME" --query 'Role.Arn' --output text)

{
  echo "export V1_OIDC_ARN=\"$V1_OIDC_ARN\""
  echo "export AWS_ROLE_ARN=\"$AWS_ROLE_ARN\""
} >> "$VARS_FILE"

echo
echo "Done."
echo "  V1_OIDC_ARN  = $V1_OIDC_ARN"
echo "  AWS_ROLE_ARN = $AWS_ROLE_ARN"
echo
echo "Re-source the vars file: source $VARS_FILE"

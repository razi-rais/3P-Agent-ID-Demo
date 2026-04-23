"""Refresh an Azure MI -> Entra app-reg exchanged token to a shared file.

Flow:
  1. Call IMDS to get a system-MI assertion (aud = AzureADTokenExchange GUID).
  2. Exchange that assertion at Entra's /oauth2/v2.0/token endpoint (client_credentials
     + client_assertion) for a v1 token minted for STS_APP (identifierUri = api://<guid>).
     This yields iss = https://sts.windows.net/{tenant}/, aud = api://<app-guid>.
  3. Write the exchanged token to AWS_WEB_IDENTITY_TOKEN_FILE so boto3 can use it.

Required env:
  IDENTITY_ENDPOINT, IDENTITY_HEADER  (injected by ACA for system-assigned MI)
  TENANT_ID
  STS_APP_ID         (the Entra app registration's appId)
  STS_APP_URI        (api://<appId>)
  AWS_WEB_IDENTITY_TOKEN_FILE  (path to write to; default /azure-token/token)
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

IDENTITY_ENDPOINT = os.environ["IDENTITY_ENDPOINT"]
IDENTITY_HEADER = os.environ["IDENTITY_HEADER"]
TENANT_ID = os.environ["TENANT_ID"]
STS_APP_ID = os.environ["STS_APP_ID"]
STS_APP_URI = os.environ["STS_APP_URI"]
OUT_FILE = os.environ.get("AWS_WEB_IDENTITY_TOKEN_FILE", "/azure-token/token")
REFRESH_SECONDS = 50 * 60

os.makedirs(os.path.dirname(OUT_FILE) or ".", exist_ok=True)

IMDS_URL = (
    f"{IDENTITY_ENDPOINT}?api-version=2019-08-01"
    f"&resource={urllib.parse.quote('api://AzureADTokenExchange', safe='')}"
)
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"


def get_mi_assertion():
    req = urllib.request.Request(IMDS_URL, headers={"X-IDENTITY-HEADER": IDENTITY_HEADER})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def exchange(assertion):
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": STS_APP_ID,
        "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
        "client_assertion": assertion,
        "scope": f"{STS_APP_URI}/.default",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL,
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["access_token"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"Exchange failed HTTP {e.code}: {body}") from e


def decode_claims(jwt):
    payload = jwt.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


while True:
    try:
        assertion = get_mi_assertion()
        token = exchange(assertion)
        tmp = OUT_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(token)
        os.chmod(tmp, 0o644)
        os.replace(tmp, OUT_FILE)
        claims = decode_claims(token)
        print(
            f"[refresher] wrote {OUT_FILE} ({len(token)} chars) "
            f"iss={claims.get('iss')} aud={claims.get('aud')} sub={claims.get('sub')} oid={claims.get('oid')} appid={claims.get('appid')}",
            flush=True,
        )
    except Exception as e:
        print(f"[refresher] error: {e}", file=sys.stderr, flush=True)
        time.sleep(30)
        continue
    time.sleep(REFRESH_SECONDS)

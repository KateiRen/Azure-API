
import os, sys, requests
from dotenv import load_dotenv
import msal

load_dotenv()

TENANT = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")

AUTH_AUTHORITY = os.getenv("AZURE_AUTH_AUTHORITY", "https://login.microsoftonline.com").rstrip("/")
ARM_SCOPE = os.getenv("AZURE_ARM_SCOPE", "https://management.azure.com/.default")
ARM_BASE = os.getenv("AZURE_API_BASE_URL", "https://management.azure.com").rstrip("/")

missing = [k for k,v in {
    "AZURE_TENANT_ID": TENANT,
    "AZURE_CLIENT_ID": CLIENT_ID,
    "AZURE_CLIENT_SECRET": CLIENT_SECRET,
    "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION_ID,
    "AZURE_RESOURCE_GROUP": RESOURCE_GROUP
}.items() if not v]
if missing:
    sys.exit(f"Missing required env vars: {', '.join(missing)}")

authority = f"{AUTH_AUTHORITY}/{TENANT}"
app = msal.ConfidentialClientApplication(
    client_id=CLIENT_ID,
    client_credential=CLIENT_SECRET,
    authority=authority
)

# Acquire token (v2 endpoint; client-credentials flow)
result = app.acquire_token_for_client(scopes=[ARM_SCOPE])
if "access_token" not in result:
    sys.exit(f"Token request failed: {result.get('error')}: {result.get('error_description')}")

token = result["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Simple ARM sanity check: get the RG you configured
api_version = "2021-04-01"
rg_url = f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}/resourceGroups/{RESOURCE_GROUP}"
resp = requests.get(rg_url, params={"api-version": api_version}, headers=headers, timeout=30)

if resp.status_code == 200:
    rg = resp.json()
    print("✅ ARM access confirmed")
    print(f"Resource Group: {rg.get('name')} | Location: {rg.get('location')}")
elif resp.status_code in (401, 403):
    print("🔐 Authenticated but not authorized. Check RBAC on the subscription/RG for this app.")
    print(resp.text)
else:
    print(f"⚠️ ARM call failed: {resp.status_code}")
    print(resp.text)

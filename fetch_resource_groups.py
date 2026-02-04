"""Minimal helper to list Azure resource groups via the REST management API."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import requests

try:  # Load variables from a local .env if python-dotenv is available.
    from dotenv import load_dotenv

    load_dotenv(override=False)
except ModuleNotFoundError:
    pass

API_VERSION = "2021-04-01"
TOKEN_ENDPOINT_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
)
RESOURCE_GROUPS_PATH = (
    "/subscriptions/{subscription_id}/resourcegroups?api-version={api_version}"
)


def env_var(name: str) -> str:
    value = os.getenv(name)
    if not value:
        sys.exit(f"Missing required environment variable: {name}")
    return value


def request_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    token_url = TOKEN_ENDPOINT_TEMPLATE.format(tenant_id=tenant_id)
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "client_credentials",
        "scope": "https://management.azure.com/.default",
    }
    response = requests.post(token_url, data=payload, timeout=30)
    if response.status_code != 200:
        sys.exit(
            "Failed to obtain access token: "
            f"{response.status_code} {response.text}"
        )
    token_body: Dict[str, Any] = response.json()
    return token_body.get("access_token", "")


def fetch_resource_groups(base_url: str, subscription_id: str, access_token: str) -> List[Dict[str, Any]]:
    url = base_url.rstrip("/") + RESOURCE_GROUPS_PATH.format(
        subscription_id=subscription_id,
        api_version=API_VERSION,
    )
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        sys.exit(
            "Failed to fetch resource groups: "
            f"{response.status_code} {response.text}"
        )
    body: Dict[str, Any] = response.json()
    return body.get("value", [])


def main() -> None:
    tenant_id = env_var("AZURE_TENANT_ID")
    client_id = env_var("AZURE_CLIENT_ID")
    client_secret = env_var("AZURE_CLIENT_SECRET")
    subscription_id = env_var("AZURE_SUBSCRIPTION_ID")
    base_url = env_var("AZURE_API_BASE_URL")

    token = request_access_token(tenant_id, client_id, client_secret)
    if not token:
        sys.exit("Azure AD did not return an access token.")

    resource_groups = fetch_resource_groups(base_url, subscription_id, token)
    if not resource_groups:
        print("No resource groups returned.")
        return

    for group in resource_groups:
        name = group.get("name", "<unknown>")
        location = group.get("location", "<unknown>")
        print(f"{name} ({location})")


if __name__ == "__main__":
    main()

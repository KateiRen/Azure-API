"""Fetch and cache official Azure Locations API metadata."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote, urlparse

from dotenv import load_dotenv
import msal
import requests


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_DIR / "data" / "azure_locations.json"
API_VERSION = "2022-12-01"


def get_configuration() -> dict[str, str]:
    names = ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID")
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    return {
        **{name: os.environ[name] for name in names},
        "authority": (os.getenv("AZURE_AUTH_AUTHORITY") or "https://login.microsoftonline.com").rstrip("/"),
        "scope": os.getenv("AZURE_ARM_SCOPE") or "https://management.azure.com/.default",
        "base": (os.getenv("AZURE_API_BASE_URL") or "https://management.azure.com").rstrip("/"),
    }


def acquire_token(configuration: dict[str, str]) -> str:
    app = msal.ConfidentialClientApplication(
        configuration["AZURE_CLIENT_ID"],
        authority=f"{configuration['authority']}/{configuration['AZURE_TENANT_ID']}",
        client_credential=configuration["AZURE_CLIENT_SECRET"],
    )
    result = app.acquire_token_for_client(scopes=[configuration["scope"]])
    token = result.get("access_token")
    if not isinstance(token, str) or not token:
        detail = result.get("error_description") or result.get("error") or "No access token returned"
        raise ValueError(f"Azure authentication failed: {detail}")
    return token


def validate_locations(locations: object) -> None:
    if not isinstance(locations, list) or not locations:
        raise ValueError("Expected a nonempty Locations API value list.")
    for location in locations:
        if (
            not isinstance(location, dict)
            or not isinstance(location.get("name"), str)
            or not location["name"].strip()
            or (location.get("metadata") is not None and not isinstance(location["metadata"], dict))
        ):
            raise ValueError("Invalid location record in Locations API data.")


def fetch_locations(configuration: dict[str, str], token: str) -> dict:
    url = (
        f"{configuration['base']}/subscriptions/"
        f"{quote(configuration['AZURE_SUBSCRIPTION_ID'], safe='')}/locations"
    )
    source_url = url
    origin = urlparse(url)
    params = {"api-version": API_VERSION, "includeExtendedLocations": "false"}
    locations = []
    seen = set()
    with requests.Session() as session:
        while url:
            parsed = urlparse(url)
            if (
                parsed.scheme != "https" or parsed.hostname != origin.hostname
                or (parsed.port or 443) != (origin.port or 443)
                or parsed.username is not None or parsed.password is not None
            ):
                raise ValueError("Locations API returned an unexpected page origin.")
            if url in seen:
                raise ValueError("Locations API repeated a page.")
            seen.add(url)
            print(f"Fetching Locations API page {len(seen)}...", flush=True)
            response = session.get(
                url, params=params, headers={"Authorization": f"Bearer {token}"},
                timeout=(15, 90), allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise ValueError("Locations API returned an unexpected redirect.")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Invalid Locations API response.")
            page = payload.get("value")
            if not isinstance(page, list):
                raise ValueError("Locations API response is missing its value list.")
            if page:
                validate_locations(page)
            locations.extend(page)
            next_link = payload.get("nextLink")
            if next_link is not None and not isinstance(next_link, str):
                raise ValueError("Locations API returned an invalid nextLink.")
            url = next_link
            params = None
    validate_locations(locations)
    return {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "apiVersion": API_VERSION,
        "subscriptionId": configuration["AZURE_SUBSCRIPTION_ID"],
        "sourceUrl": source_url,
        "includeExtendedLocations": False,
        "value": locations,
    }


def load_cache(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError("Invalid locations cache; use --refresh to replace it.")
    validate_locations(payload.get("value"))
    if not isinstance(payload.get("fetchedAt"), str):
        raise ValueError("Locations cache has no fetch timestamp; use --refresh.")
    timestamp = datetime.fromisoformat(payload["fetchedAt"])
    if timestamp.tzinfo is None:
        raise ValueError("Locations cache timestamp has no timezone; use --refresh.")
    subscription = os.getenv("AZURE_SUBSCRIPTION_ID")
    if subscription and payload.get("subscriptionId") != subscription:
        raise ValueError("Locations cache belongs to a different subscription; use --refresh.")
    return payload


def write_cache(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with temporary_path.open("w", encoding="utf-8") as target:
            json.dump(payload, target, ensure_ascii=True, indent=2)
            target.write("\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def print_summary(payload: dict, path: Path, cached: bool) -> None:
    locations = payload["value"]
    metadata = [item.get("metadata") or {} for item in locations]
    print(f"\n{'Using cached' if cached else 'Saved'} official Azure location metadata: {path}")
    print(f"Fetched at (UTC): {payload['fetchedAt']}")
    print(f"Locations: {len(locations):,}")
    for field in ("geography", "geographyGroup", "physicalLocation"):
        print(f"With {field}: {sum(bool(item.get(field)) for item in metadata):,}")
    print("All returned location fields and nested metadata are preserved.")
    if cached:
        print("No Azure request made. Use --refresh to update this snapshot.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Fetch again and replace the cached snapshot.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON cache file.")
    args = parser.parse_args(argv)
    path = args.output.resolve()
    load_dotenv(PROJECT_DIR / ".env", override=False)
    try:
        cached = path.is_file() and not args.refresh
        if cached:
            payload = load_cache(path)
        else:
            configuration = get_configuration()
            payload = fetch_locations(configuration, acquire_token(configuration))
            write_cache(payload, path)
        print_summary(payload, path, cached)
    except (OSError, ValueError, requests.RequestException) as error:
        print(f"ERROR: Location metadata fetch/cache failed: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


import argparse
import csv
import os
import sys
import time
import requests
import msal
import pandas as pd
from dotenv import load_dotenv
from urllib.parse import urlencode

parser = argparse.ArgumentParser(
    description="Fetch Azure VM SKUs or list available resource types, regions, and zones."
)
parser.add_argument(
    "--listResourceTypes",
    action="store_true",
    help="List distinct resource types and exit without collecting VM records or writing data files.",
)
parser.add_argument(
    "--listRegions",
    action="store_true",
    help="List all Compute SKU regions/locations and their reported zones without writing data files.",
)
args = parser.parse_args()

# --- Load configuration from .env ---
load_dotenv()

TENANT = os.getenv("AZURE_TENANT_ID")
CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")

AUTH_AUTHORITY = (os.getenv("AZURE_AUTH_AUTHORITY") or "https://login.microsoftonline.com").rstrip("/")
ARM_SCOPE = os.getenv("AZURE_ARM_SCOPE") or "https://management.azure.com/.default"
ARM_BASE = (os.getenv("AZURE_API_BASE_URL") or "https://management.azure.com").rstrip("/")

# API version for Microsoft.Compute SKUs (adjust to newest when needed)
COMPUTE_API_VERSION = "2025-04-01"  # works with current docs for Compute; change if needed

missing = [k for k, v in {
    "AZURE_TENANT_ID": TENANT,
    "AZURE_CLIENT_ID": CLIENT_ID,
    "AZURE_CLIENT_SECRET": CLIENT_SECRET,
    "AZURE_SUBSCRIPTION_ID": SUBSCRIPTION_ID
}.items() if not v]
if missing:
    sys.exit(f"Missing env vars: {', '.join(missing)}")

if args.listResourceTypes or args.listRegions:
    listing = []
    if args.listResourceTypes:
        listing.append("resource types")
    if args.listRegions:
        listing.append("regions/locations and their reported zones")
    print(
        f"Listing {' and '.join(listing)}: authenticating with Azure and fetching all "
        "Compute SKU pages. This may take some time. No data files will be written.",
        flush=True,
    )

# --- Acquire token via client credentials on Entra ID v2 endpoint ---
authority = f"{AUTH_AUTHORITY}/{TENANT}"
app = msal.ConfidentialClientApplication(
    CLIENT_ID, authority=authority, client_credential=CLIENT_SECRET
)
result = app.acquire_token_for_client(scopes=[ARM_SCOPE])
if "access_token" not in result:
    sys.exit(f"Token acquisition failed: {result.get('error_description') or result}")

access_token = result["access_token"]
headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

# --- Start timing ---
start_time = time.time()

# --- Helper: robust paging for ARM list operations ---
def fetch_all_pages(first_url, headers, sleep_sec=0.0):
    url = first_url
    while url:
        resp = requests.get(url, headers=headers, timeout=60)
        if not resp.ok:
            raise RuntimeError(f"GET failed {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        for item in payload.get("value", []):
            yield item
        url = payload.get("nextLink")
        if url and sleep_sec:
            time.sleep(sleep_sec)

# --- Pull all Microsoft.Compute SKUs once; filter to virtualMachines only ---
skus_url = (
    f"{ARM_BASE}/subscriptions/{SUBSCRIPTION_ID}"
    f"/providers/Microsoft.Compute/skus?{urlencode({'api-version': COMPUTE_API_VERSION})}"
)

records = []
all_capability_names = set()
all_resource_types = set()
all_zones_by_region: dict[str, set[str]] = {}
rowcount_preview = 0

for sku in fetch_all_pages(skus_url, headers):
    resource_type = sku.get("resourceType")
    if resource_type:
        all_resource_types.add(resource_type)

    if args.listRegions:
        for region in sku.get("locations") or []:
            if region:
                all_zones_by_region.setdefault(region, set())
        for location_info in sku.get("locationInfo") or []:
            region = location_info.get("location")
            if region:
                zones = all_zones_by_region.setdefault(region, set())
                reported_zones = location_info.get("zones")
                if isinstance(reported_zones, list):
                    zones.update(reported_zones)

    if args.listResourceTypes or args.listRegions:
        continue

    # We only care about VM sizes
    if resource_type != "virtualMachines":
        continue

    sku_name = sku.get("name")               # e.g., "Standard_D2as_v5"
    family   = sku.get("family")             # e.g., "Dasv5"
    tier     = sku.get("tier")               # e.g., "Standard"

    # Capabilities are name/value pairs (strings)
    caps_list = sku.get("capabilities", []) or []
    caps = {c.get("name"): c.get("value") for c in caps_list if c.get("name")}
    # Track every capability name to create columns later
    for cap_name in caps.keys():
        all_capability_names.add(cap_name)

    # Some availability/zone metadata lives in locationInfo
    # Each SKU advertises an array of regions in "locations"
    locations = sku.get("locations") or []
    loc_info  = sku.get("locationInfo") or []

    # Build a quick map: region -> (zones list | None)
    zones_by_region = {}
    for li in loc_info:
        region = li.get("location")
        zones = []
        # "zones" is a list like ["1","2","3"] when present
        if isinstance(li.get("zones"), list):
            zones = li["zones"]
        zones_by_region[region] = zones

    for region in locations:
        rec = {
            "Region": region,
            "SkuName": sku_name,
            "Family": family,
            "Tier": tier,
            # Common headline fields first (if present in caps)
            "vCPUs": caps.get("vCPUs") or caps.get("vCpu") or caps.get("vCPUsPerCore"),
            "MemoryGB": caps.get("MemoryGB") or caps.get("MemoryGiB"),
            "GPUs": caps.get("GPUs") or caps.get("Gpu") or caps.get("GPUsCount"),
            "MaxDataDiskCount": caps.get("MaxDataDiskCount"),
            "CpuArchitectureType": caps.get("CpuArchitectureType"),
            "AcceleratedNetworkingEnabled": caps.get("AcceleratedNetworkingEnabled"),
            "PremiumIO": caps.get("PremiumIO"),
            "EphemeralOSDiskSupported": caps.get("EphemeralOSDiskSupported"),
            "HyperVGenerations": caps.get("HyperVGenerations"),
            "TempDiskSizeInGiB": caps.get("TempDiskSizeInGiB") or caps.get("CachedDiskBytes"),
            "AvailabilityZones": ",".join(zones_by_region.get(region, [])) or "",
        }
        # Add ALL remaining capabilities so nothing is lost
        for k, v in caps.items():
            if k not in rec:
                rec[k] = v

        records.append(rec)

        # Small console preview while building
        if rowcount_preview < 5:
            print(f"[preview] {region} | {sku_name} | vCPUs={rec.get('vCPUs')} | "
                  f"Mem(GB)={rec.get('MemoryGB')} | GPUs={rec.get('GPUs')}")
            rowcount_preview += 1

if args.listResourceTypes:
    print("Resource types:")
    for resource_type in sorted(all_resource_types):
        print(f"  {resource_type}")
    print(f"Total resource types: {len(all_resource_types)}")

if args.listRegions:
    print("Regions/locations and reported zones:")
    for region, zones in sorted(all_zones_by_region.items()):
        print(f"  {region}: {', '.join(sorted(zones)) or 'zones not reported'}")
    print(f"Total regions/locations: {len(all_zones_by_region)}")

if args.listResourceTypes or args.listRegions:
    sys.exit(0)

if not records:
    sys.exit("No VM SKU records found. Check permissions or API version.")

# --- Create output directory ---
output_dir = "data"
os.makedirs(output_dir, exist_ok=True)

# --- Write a wide CSV with dynamic columns to capture every capability ---
# Ensure consistent column order: fixed columns first, then the rest (sorted)
fixed_cols = [
    "Region", "SkuName", "Family", "Tier",
    "vCPUs", "MemoryGB", "GPUs",
    "MaxDataDiskCount", "CpuArchitectureType",
    "AcceleratedNetworkingEnabled", "PremiumIO",
    "EphemeralOSDiskSupported", "HyperVGenerations",
    "TempDiskSizeInGiB", "AvailabilityZones"
]
dynamic_caps = sorted([c for c in all_capability_names if c not in fixed_cols])
fieldnames = fixed_cols + dynamic_caps

out_csv = os.path.join(output_dir, "vm_skus_all_regions.csv")
with open(out_csv, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for rec in records:
        writer.writerow(rec)

# --- Write Parquet file ---
out_parquet = os.path.join(output_dir, "vm_skus_all_regions.parquet")
df = pd.DataFrame(records, columns=fieldnames)
df.to_parquet(out_parquet, index=False, engine="pyarrow")

# --- Calculate summary statistics ---
unique_locations = len(set(rec["Region"] for rec in records if rec.get("Region")))
unique_vm_types = len(set(rec["SkuName"] for rec in records if rec.get("SkuName")))

# --- End timing ---
end_time = time.time()
elapsed_time = end_time - start_time

print(f"\n{'='*60}")
print(f"SUMMARY")
print(f"{'='*60}")
print(f"Total rows: {len(records):,}")
print(f"Unique resource types (before VM filter): {len(all_resource_types)}")
print(f"Unique locations: {unique_locations}")
print(f"Unique VM types: {unique_vm_types}")
print(f"Time elapsed: {elapsed_time:.2f} seconds")
print(f"\nOutput files:")
print(f"  - {out_csv}")
print(f"  - {out_parquet}")
print(f"\nTip: filter by Region or SkuName in your spreadsheet/BI tool.")

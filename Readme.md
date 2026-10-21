# Azure VM SKU Explorer

This project provides tools to fetch and explore Azure Virtual Machine SKUs across all regions using the Azure REST API. It includes a data collection script and an interactive Streamlit dashboard for visualization.

## Features

- Fetch all Azure VM SKUs across all regions
- Export data to both CSV and Parquet formats
- Interactive Streamlit dashboard with filtering capabilities
- Five report pages: SKU browser, Family summary, Region Summary, Region Map, and Matrix View
- Shared family-version selections across reports
- Distinct SKU aggregation, region coverage, and a vCPU/memory heatmap
- Detailed statistics and visualizations
- Support for vCPU, memory, GPU, disk, and architecture filtering

## Prerequisites

- **uv** installed ([installation instructions](https://docs.astral.sh/uv/getting-started/installation/))
- **Python 3.13+** required; the project pins Python 3.13 in `.python-version`, and uv can install it automatically
- **Azure Subscription** with appropriate permissions
- **Azure App Registration** (Service Principal) with Reader role

## Setup

### 1. Create an Azure App Registration

Create an app registration in Azure Entra ID (formerly Azure AD) to obtain credentials:

1. Navigate to **Azure Portal** → **Entra ID** → **App registrations**
2. Click **New registration**
3. Enter a name (e.g., "VM SKU Reader")
4. Click **Register**
5. Note the **Application (client) ID** and **Directory (tenant) ID**
6. Go to **Certificates & secrets** → **New client secret**
7. Create a secret and **copy the value immediately** (it won't be shown again)

### 2. Grant Permissions to the Subscription

Assign the Reader role to your app registration:

```azurecli
az role assignment create \
  --assignee <YOUR_APP_CLIENT_ID> \
  --role Reader \
  --scope /subscriptions/<YOUR_SUBSCRIPTION_ID>
```

Verify the role assignment:

```azurecli
az role assignment list --assignee <YOUR_APP_CLIENT_ID>
```

---

Or use the a powershell script for the creation and role assignment all in one:
```powershell
# App name
$appName = "MyAppRegistration"

# Create App Registration
$app = New-AzADApplication -DisplayName $appName

# Create Service Principal
$sp = New-AzADServicePrincipal -ApplicationId $app.AppId

# Create Client Secret (valid for 2 years)
$secret = New-AzADAppCredential `
    -ApplicationId $app.AppId `
    -StartDate (Get-Date) `
    -EndDate (Get-Date).AddYears(2)

# Give Azure AD a few seconds to replicate the Service Principal
Start-Sleep -Seconds 20

# Assign Reader role on current subscription
$subscriptionId = (Get-AzContext).Subscription.Id

New-AzRoleAssignment `
    -ApplicationId $app.AppId `
    -RoleDefinitionName "Reader" `
    -Scope "/subscriptions/$subscriptionId"

# Output
Write-Host ""
Write-Host "=== App Registration Created ==="
Write-Host "Client ID     : $($app.AppId)"
Write-Host "Tenant ID     : $((Get-AzContext).Tenant.Id)"
Write-Host "Client Secret : $($secret.SecretText)"
Write-Host "Subscription  : $subscriptionId"
Write-Host "Role          : Reader"
```

### 3. Configure Environment Variables

Create a `.env` file in the project root with your Azure credentials:

```ini
AZURE_TENANT_ID=<YOUR_TENANT_ID>
AZURE_CLIENT_ID=<YOUR_APP_CLIENT_ID>
AZURE_CLIENT_SECRET=<YOUR_CLIENT_SECRET>
AZURE_SUBSCRIPTION_ID=<YOUR_SUBSCRIPTION_ID>

# Optional: Use custom endpoints (defaults shown)
# AZURE_AUTH_AUTHORITY=https://login.microsoftonline.com
# AZURE_ARM_SCOPE=https://management.azure.com/.default
# AZURE_API_BASE_URL=https://management.azure.com
```

**⚠️ Important:** Never commit the `.env` file to version control. It's already included in `.gitignore`.

### 4. Set Up the Python Environment with uv

Install uv if it is not already available. On Windows:

```powershell
winget install --id astral-sh.uv --exact
```

Open a new terminal after installation, then run the following from the project root on any platform:

```console
uv sync --locked
```

This installs the pinned Python version if needed, creates `.venv`, and installs the exact dependencies from `uv.lock`. No manual virtual environment creation or activation is required: use `uv run` for project commands.

Dependencies are declared in `pyproject.toml`; `uv.lock` records their resolved versions. Keep both files in version control, but do not commit `.venv`.

**Package index:** `pyproject.toml` configures the Microsoft CFS package feed as the default index. Dependency installation requires access to that feed.

## Usage

### Interactive Launcher

```console
uv run --locked python main.py
```

The launcher checks both data files and displays their presence, size, last-modified timestamp, and age compared with the current UTC time. Choose **1** to run the data fetcher, **2** to fetch prices for the saved VM SKUs, **3** to start the Streamlit SKU browser, or **Q** to quit. The browser and price fetching require the Parquet file; if it is missing, the launcher asks you to fetch data first. Data fetching retains the existing refresh-confirmation prompt described below. Price fetching runs `fetch_vm_sku_prices.py` with the default primary-only policy and retains its recreate/update prompt, diagnostics, and provenance reports. For optional nonprimary prices, use the standalone script with `--allow-nonprimary` as documented below. All commands run from the project directory using the launcher's Python environment.

### 1. Fetch Azure VM SKU Data

Run the data collection script to fetch all VM SKUs:

```bash
uv run --locked python fetch_vm_skus_all_regions.py
```

**Example Output:**
```
============================================================
SUMMARY
============================================================
Total rows: 55,795
Unique locations: 76
Unique VM types: 1,339
Time elapsed: 26.21 seconds

Output files:
  - data\vm_skus_all_regions.csv
  - data\vm_skus_all_regions.parquet

Tip: filter by Region or SkuName in your spreadsheet/BI tool.
```

Output files are saved to the `data/` directory.

If either output file already exists, the script shows each existing file's last-modified timestamp and its age relative to the current time (all timestamps use UTC). It asks `Remove existing data files and fetch again? [y/N]:` before contacting Azure. Enter `y` or `yes` to refresh; pressing Enter or answering `n`/`no` keeps the files and exits without fetching. Invalid answers prompt again, and unavailable input stops the script without changing the files. Confirmed files are only removed after authentication and data collection succeed, just before writing the new exports. The listing options below do not prompt or change existing files.

The summary also reports the total number of distinct `resourceType` values returned by the API before the VM-only filter is applied. Exported rows remain limited to `virtualMachines`.

To list all distinct resource types instead of collecting VM data:

```console
uv run --locked python fetch_vm_skus_all_regions.py --listResourceTypes
```

This prints the resource types in alphabetical order and their total count, then exits without collecting VM records or creating or modifying data files. It still requires Azure credentials and fetches all SKU pages to capture every resource type.

To list all regions/locations and their reported availability zones across all Compute SKUs:

```console
uv run --locked python fetch_vm_skus_all_regions.py --listRegions
```

Azure SKU location identifiers (for example, `eastus`) represent regions here. This option combines locations from `locations` and `locationInfo` and lists the union of reported zones for each location, sorted alphabetically, followed by the total location count. Locations without zone information are shown as `zones not reported`; this does not prove that the region has no availability zones. The list reflects the Compute SKUs returned for your subscription, not the complete Azure regions catalog, and does not guarantee availability for an individual SKU.

Both listing options announce what is being fetched and warn that it may take some time before contacting Azure. Neither collects VM records or writes data files. They can also be combined to fetch both lists in one pass:

```console
uv run --locked python fetch_vm_skus_all_regions.py --listResourceTypes --listRegions
```

### 2. Launch Streamlit Dashboard

Start the interactive dashboard:

```bash
uv run --locked streamlit run streamlit_app.py
```

The dashboard provides:
- Shared **Azure VM SKU Explorer** sidebar header at the very top, above the
  report navigation links
- Multi-select filters for region, family, CPU architecture, etc.
- Real-time statistics (total SKUs, unique regions, unique VM types)
- Distribution charts for top regions and VM families
- Min/Max-only statistics for vCPUs, GPUs, memory, and data disk counts, using
  filtered rows. GPUs appear next to vCPUs and include reported zeroes; missing GPU
  data is not assumed to be zero.
- Downloadable filtered results

Both distribution charts show the top ten regions/families in descending SKU
count order, rather than alphabetical order, and recalculate after filtering.

The **SKU browser** main view includes a horizontal row of **Other**, **v2** through
**v7** family-version checkboxes, all selected by default. These combine with the existing
filters and update the results, statistics, charts, and CSV download. Clearing
all version checkboxes shows no SKUs.
Family version selections are shared across **SKU browser**, **Family summary**,
**Region Summary**, **Region Map**, and **Matrix View** within the same browser session. Changing
them on one page updates the checkboxes and results when navigating to another.
Other filters remain page-specific.

In the SKU browser sidebar, **Geography Group** appears above **Region** and uses
the official cached location metadata. Select multiple groups; an empty selection
means all groups. **Not assigned** includes regions missing official geography-group
metadata. Region choices are restricted to the selected groups, and stale region
selections outside those groups are cleared. If the location cache is missing or
invalid, a visible message explains the issue and the geography filter is disabled;
the remaining browser features still work.

The sidebar also provides **With pricing**, **With 1Y RI**, and **With 3Y RI**
checkboxes. They start unchecked and require numeric values in `retail price`,
`1Y RI`, and `3Y RI`, respectively. Zero counts as a price; missing, blank, or
nonnumeric values do not. Checked price types combine with **AND**, together with
the other filters, updating the table, statistics, charts, and CSV download.
A checkbox is disabled when its price column is absent; fetch prices using launcher
option 2 to add the pricing columns. Geography group is also available in the
browser's column selector when location metadata is loaded.
Available `retail price`, `1Y RI`, and `3Y RI` columns are shown by default in
**Filtered Results** and included in its CSV export. They can be hidden with the
existing column selector; absent pricing columns are not added.

**Family summary** and **Region Summary** have the same three pricing checkboxes
in their sidebars. They start unchecked, are disabled for missing columns, and
combine with **AND**. Filtering happens on SKU rows before aggregation, so
distinct counts, region coverage, capability ranges, zone unions, minimum-region
thresholds, and CSV exports reflect only matching SKUs. Groups without matching
SKUs disappear. These pricing selections are page-specific; family-version
selections remain shared across reports.

**Has GPUs**, directly above the exact **GPUs** count filter, includes VMs with
any reported finite GPU count greater than zero. It starts unchecked; zero,
unknown, and invalid counts do not qualify when checked. It combines with the
exact GPU-count selection and the other filters, updating the table, statistics,
charts, and CSV. The checkbox is disabled if the GPU column is absent.

**Premium IO** is an unchecked-by-default checkbox. When checked, only SKUs with
reported true Premium IO support are included; unchecked includes all values,
including unknowns. Boolean and case-insensitive string true values are supported.

**vCPUs Constraints Allowed** is also an unchecked-by-default checkbox. When
checked, the API must report at least one positive allowed vCPU count below the
SKU's reported `vCPUs`. Unchecked includes all SKUs, including missing or unusable
constraint/vCPU data. This tests support for reducing vCPUs, not whether a SKU
already has fewer active vCPUs. Both capability checkboxes combine with the other
filters and are disabled if their required columns are absent. Tier remains in
the results but has no sidebar filter.

Use the sidebar navigation to switch between **SKU browser**, **Family summary**,
**Region Summary**, **Region Map**, and **Matrix View**.
The family summary uses the saved dataset and shared family version selections,
independently of the SKU browser's other filters, with one row per named family.
It includes distinct SKU and region counts, all reported CPU architectures,
minimum/maximum vCPU and GPU counts,
memory (GB), and supported data disk counts. Premium IO lists all reported values
(for example, `False, True` for families with mixed support). Missing capabilities
are excluded from numeric ranges, not assumed to be zero; an entirely unreported
range remains blank. Above the table, set the minimum region count (inclusive)
and select version checkboxes: **Other**, **v2**, **v3**, **v4**, **v5**, **v6**,
and **v7**. All versions are selected by default. Version matching is
case-insensitive and includes qualifiers such as `v2Promo` and `v3_T4`; **Other**
includes families without a v2-v7 version in their name. The filters combine,
and clearing all version checkboxes shows no families. The CSV download contains
only the filtered summary.

**Region Summary** uses the shared **Other**, **v2** through **v7** family version
checkboxes, using the same version matching as Family summary. All are selected
at the start of a new session. Counts, zones, capabilities,
and the CSV download are recalculated from SKUs belonging to the selected family
versions. Regions without matching SKUs are hidden; clearing all checkboxes shows
an empty table. With one row per matching region, it shows distinct VM SKU and
family counts, the union of reported availability zones, zone counts, and distinct SKUs with zone
data. Additional columns summarize CPU architectures, maximum vCPUs, memory (GB),
and GPUs, plus Premium IO, accelerated networking, Ultra SSD, ephemeral OS disk,
Hyper-V generations, and tiers when those fields are present in the export.
Premium IO, accelerated networking, Ultra SSD, ephemeral OS disk, Hyper-V
generations, and tiers are hidden by default; use the table's column visibility
menu to show them. They remain included in the CSV download.
Support columns list every reported value (including mixed `False, True`).
Missing numeric capabilities remain unknown rather than zero. Zone lists are
split and deduplicated across SKUs; **Not reported** and a blank zone count mean
zone availability is unknown, not that a region has no zones. Each SKU may support
only a subset of the region's reported zones.

This view reflects regions listed in the saved Compute SKU response, not the
complete Azure region catalog or guaranteed deployment capacity/subscription
eligibility. The saved export has no geographic metadata, paired-region details,
or SKU restrictions. Download the region summary as CSV using the button below
the table.

When the saved Parquet includes `retail price`, Region Summary also shows
**SKUs with price data** next to **VM SKU Count**. It counts distinct
SKU names per region with a reported numeric retail price, including zero, after
the selected family-version filters. Reservation-only matches do not count.
The column is included in the summary CSV and is omitted when `retail price`
is absent.

**Region Map** places paired 3D bars on an interactive map using the cached official
Azure coordinates in `data/azure_locations.json`. Create that file first with
`uv run --locked python fetch_azure_locations.py`. Blue bars show distinct VM SKU
counts; orange bars show distinct SKUs with numeric retail prices, including zero,
using the same definition as Region Summary. Reservation-only matches do not
count. Both bars use the same linear height scale, offset slightly west/east of
each location to separate them. Hover to see exact counts, and zoom/rotate to
explore nearby or co-located regions.

The shared family-version checkboxes and a **Geography groups** multiselect
filter the map and its CSV export. The selector uses official `geographyGroup`
values, not inferred continents. All available groups start selected; users can
select or deselect multiple entries, and clearing the selection shows no regions.
**Not reported** includes regions without geography-group metadata. Geography
groups are also shown in the table, map tooltips, and CSV export.
Clearing all family versions shows no regions. Only regions in the saved SKU data
are plotted, matched case-insensitively to the official ARM region name. Regions
without metadata or usable coordinates, and logical/non-geographic locations,
are listed separately with their counts and reasons; coordinates are never
guessed. If retail pricing columns are absent, the page explicitly shows only
total SKU bars rather than pretending pricing coverage is zero.

Expand **Region counts and map coordinates** for an accessible table and download
all filtered regions, counts, coordinates, and mapping status as CSV. The metadata
snapshot timestamp is displayed; refreshed metadata is picked up within 60 seconds.
The map uses CARTO basemap tiles and needs network access to display the basemap;
the counts and coordinate table remain available independently of map tiles.

**Matrix View** counts distinct VM SKU names at the intersections of vCPU ranges
(rows) and memory ranges in GB (columns). Repeated listings across regions count
only once per cell. vCPU ranges are **1-2**, **3-4**, **5-8**, and successive
doubling ranges through **513-1024**, with **>1024** for larger sizes. Memory
ranges are **<=4 GB**, **>4-8 GB**, **>8-16 GB**, and successive doubling ranges
through **>8192-16384 GB**, with **>16384 GB** for larger sizes. Upper bounds are
inclusive; all ranges stay visible, and cells without matching SKUs show zero.
Positive cells use a blue heatmap: stronger blue indicates higher counts relative
to the maximum in the filtered matrix. Zero-count cells remain unshaded. Numeric
counts and CSV exports are unchanged by the color effect.
Missing, non-positive, or non-finite CPU/memory values are not counted.

Alongside shared family versions, Matrix View has independent multiselect filters
for **Geography Group**, **Region**, **CPU Architecture Type**, and **# GPUs**;
empty selections mean all values. Geography Group appears immediately left of
Region and uses the same official metadata and **Not assigned** category as the
SKU browser. Region options follow the selected geography groups, and stale
region selections outside those groups are cleared. Missing or invalid location
metadata disables only the geography filter with a visible explanation.
Geography selections combine with the other filters and update the matrix,
heatmap, and CSV. **Not reported** in the GPU filter means unknown, not zero GPUs.
The CSV download contains the filtered matrix with its row and column labels.

The dashboard requires Streamlit 1.65 or newer, including accessible map labels.

## Report Screenshots

These screenshots show the default reports using a saved API export. Counts vary
with the dataset and selected filters. Click an image to inspect the full-size
report.

### SKU Browser

Browse individual region/SKU records with sidebar filters, summary statistics,
distribution charts, selectable result columns, and CSV export.

[![SKU Browser with family-version filters, statistics, charts, and SKU results](docs/screenshots/sku-browser.png)](docs/screenshots/sku-browser.png)

### Family Summary

Compare one row per VM family, including distinct SKU and region counts, hardware
ranges, CPU architectures, and storage support. Filter by minimum region coverage
and shared family versions.

[![Family Summary table with minimum-region and family-version filters](docs/screenshots/family-summary.png)](docs/screenshots/family-summary.png)

### Region Summary

Compare regional SKU coverage, family counts, reported availability zones, CPU
architectures, and hardware maxima. Additional support columns can be revealed
from the table's column visibility menu.

[![Region Summary table showing SKU counts, reported zones, and regional hardware capabilities](docs/screenshots/region-summary.png)](docs/screenshots/region-summary.png)

### Matrix View

Explore distinct VM types by vCPU and memory range. The blue heatmap responds to
family-version, region, CPU architecture, and GPU-count selections.

[![Matrix View showing the vCPU and memory heatmap with region, architecture, and GPU filters](docs/screenshots/matrix-view.png)](docs/screenshots/matrix-view.png)

## Cache Official Azure Region Metadata

Fetch the official subscription Locations API response:

```console
uv run --locked python fetch_azure_locations.py
```

This saves `data/azure_locations.json`, preserving every returned location field
and nested metadata, including geography, geography group, physical location,
coordinates, region type, and paired-region details where Azure supplies them.
The JSON also records its UTC fetch timestamp, API version, subscription ID and
source URL. It does not contain access tokens or client secrets. Normal
locations are requested without extended/edge locations.

The script uses the same `.env` service-principal settings as the SKU fetcher:
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` and
`AZURE_SUBSCRIPTION_ID`, with the existing optional authority/scope/API-base
overrides. The principal must have permission to list subscription locations.

An existing valid cache is reused without authenticating or contacting Azure.
It has no automatic expiration; explicitly refresh it with:

```console
uv run --locked python fetch_azure_locations.py --refresh
```

Use `--output` to choose another JSON cache file. Malformed caches or a cache for
a different configured subscription produce an error rather than being silently
reused. Refreshes replace the old file atomically only after the complete
response has been downloaded and validated; failures preserve the old cache.
Progress and a metadata-coverage summary are printed.

This is subscription-specific location metadata, not a guarantee that every
service or VM SKU is deployable in each location. Logical/global entries are
preserved if returned. Azure geography groups are not continents; this step
does not infer continents or modify the SKU Parquet/CSV or Streamlit reports.
Like other generated data, the cache is excluded from Git by `data/`.

## Enrich Saved SKUs with Pricing

Run the standalone pricing script after collecting VM SKUs:

```console
uv run --locked python fetch_vm_sku_prices.py
```

This reads `data/vm_skus_all_regions.parquet` and **updates that Parquet and
regenerates its sibling CSV in place**, retaining all input rows and capability
columns and adding/replacing exactly three numeric columns:

| Column | Meaning |
| --- | --- |
| `retail price` | Linux/base-compute pay-as-you-go USD per hour |
| `1Y RI` | 1-year reserved-instance effective USD per hour: API term total / 8,760 |
| `3Y RI` | 3-year reserved-instance effective USD per hour: API term total / 26,280 |

If any of these pricing columns already exist, the script asks how to handle them
before contacting the API:

- **1 - Recreate:** clear all three pricing columns in memory and populate them
  from the current API results. Unmatched or conflicting prices will be blank.
- **2 - Update (default):** overwrite each price only when the API returns an
  unambiguous match, including a reported zero price. Keep existing values when
  a match is missing or conflicting, and create any missing pricing columns.
  This reprices already populated cells too, not just blanks. Press Enter to
  select this mode.

After upgrading pricing rules, **recreate mode is recommended** for a clean
rebuild: update mode can retain historical values that the current rules cannot
verify. All currently matched prices are re-evaluated in either mode.

Invalid selections prompt again. If input is unavailable, the script exits with
an error without contacting the API or modifying either file. If no pricing
columns exist, it creates them without prompting. Neither choice deletes columns
from disk before the download and export succeed.

The script uses the public, unauthenticated
[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices).
No Azure credentials are required. Requests are grouped by region and follow all
API pages, with timeouts and bounded retries for throttling/transient failures.
Progress shows the current region, page, downloaded meter count, and matched rows.
Downloading prices for every region can take several minutes or longer.

Matching uses exact `Region` + `SkuName` identifiers (case-insensitive), not
display-name guesses or prices from another region. By default, only primary,
zero-tier, hourly USD VM meters are accepted. Windows (including standalone
`Win` abbreviations), Cloud Services (`Cloud Services` or `CloudServices`), Spot,
Low Priority, DevTest and savings plan prices are excluded **before** choosing
the latest effective date. A product does not have to start with the words
`Virtual Machines`; explicitly labelled Linux products are still accepted.
These are public list prices, not your negotiated rates;
OS/software licensing, taxes, disks, and other services are not included.
RI hourly rates use fixed 365-day years for comparison, not actual monthly billing.

The latest effective price not in the future is selected for each price column.
Conflicting prices at that date produce a warning rather than an arbitrary
minimum. Missing/conflicting prices remain null/blank in recreate mode; update
mode keeps any previous value instead. Retained values may be older than the
current download. Missing prices are never substituted with zero. A missing
match does not prove that a SKU or reservation is unavailable.

The final summary reports coverage for **distinct region/SKU pairs**, including
retail/1Y/3Y counts and percentages of all pairs, RI percentages among
retail-matched pairs, distinct SKU-name coverage, conflicts, and elapsed time.
Repeated input rows do not inflate these percentages. Coverage describes the
final stored values, including retained prices in update mode; the per-region
matched count reports only retail prices found in the current API download.

### Optional nonprimary fallback

Nonprimary fallback is **disabled by default**. To explicitly enable it:

```console
uv run --locked python fetch_vm_sku_prices.py --allow-nonprimary
```

This requests all meters and prefers eligible primary offers independently for
each price column. Only if **no eligible primary offer exists** can an
unambiguous nonprimary offer at its latest effective date be used. It must still
match the exact region/SKU, base-compute offer, currency, unit and reservation
term. The script never resolves a primary conflict by switching to a nonprimary
offer, takes an arbitrary minimum, guesses SKU aliases, or uses another region.
Unknown primary-meter flags are not accepted as fallback candidates.

Each selected fallback is labelled in console progress and provenance. The
primary/nonprimary policy applies to newly selected prices; update mode can
still retain previous fallback values, marked `retained`, even when fallback is
disabled for the current scan. An absent exact regional match remains blank
(or retains its old value in update mode).

### Price provenance and zero-price warnings

Every successful export also creates/replaces
`data/vm_skus_all_regions_pricing_provenance.csv` (or
`<input-stem>_pricing_provenance.csv` beside a custom input). This companion CSV
keeps the dataset's three price columns numeric without adding metadata columns
to the inventory itself.

It records each distinct stored price and its source:

- `fresh_primary` / `fresh_nonprimary`: selected by this API scan, with product,
  SKU and meter names/IDs, raw API price, term, unit, primary flag, effective
  date, fetch time, and pricing-rule version.
- `retained`: not verified by this scan. If the previous provenance report
  contains that exact region/SKU/column/**price value**, the original source and
  fetch time are preserved; only the last-checked timestamp changes. Otherwise,
  the source and original fetch time are explicitly unknown. Retained metadata
  keeps its original rule version, rather than claiming verification by newer
  rules.

For equivalent same-date offers with identical prices, source selection is
deterministic and `EquivalentOfferCount` records the number of distinct offers.
Conflicting prices are still not selected. The summary counts fresh primary,
fresh nonprimary and retained values separately.

Reported zero prices remain zero, but receive console warnings, a provenance
warning, and (for newly selected prices) a `zero_price_verify` diagnostic entry.
They should be checked before interpreting the VM as free.

An existing provenance report is read only for update mode. A malformed or
contradictory report stops the run explicitly rather than assigning unreliable
source metadata; recreate mode rebuilds it without reusing old metadata.

### Pricing diagnostics report

Each completed API scan creates/replaces
`data/vm_skus_all_regions_pricing_diagnostics.csv` automatically. With `--input`,
the report is saved beside that input as `<input-stem>_pricing_diagnostics.csv`.
The script prints the report path and row count.

The report covers each region/SKU/price-column combination that could not be
updated from the current API scan, even when update mode retains an existing
price:

- `conflict`: one row per competing API record at the latest eligible effective
  date. Older prices and excluded offers are not included as conflict candidates.
- `no_api_match`: no exact region/`armSkuName` record was returned in the downloaded
  data. One row is recorded for each missing price column.
- `no_eligible_price`: exact records exist, but none qualifies for that price
  column. Rows list the available offers and reasons such as Windows, Cloud
  Services, Spot, unsupported type/term, another price column, nonhourly units,
  or future dates.
- `zero_price_verify`: a selected API price is zero and requires verification.

Columns include inventory `Region`, `SkuName`, `PriceColumn`, `Status`, `Reason`,
and `ExistingPriceRetained`, plus API product/SKU/meter names and IDs, price type,
reservation term, effective date, raw retail price, currency, unit, tier, primary
meter flag, and the calculated hourly price for eligible candidates.
Repeated identical report rows are removed; a run with no issues still writes
the column headers, replacing any old report.

Diagnostics describe only records returned by the current query; they cannot
prove why Azure omitted a SKU or show records filtered out by the server.
Several offer rows can describe one missing price cell, so the report row count
is not the number of affected SKUs.

The report is written atomically after a successful API scan and **before** the
data exports are replaced. If report writing fails, the data exports remain
unchanged. If a later data-export step fails, the new report still describes that
completed scan, not a successfully saved dataset. A failed download does not
replace the previous report.

The Parquet, data CSV and provenance CSV are fully serialized before any of
those originals is replaced. Download/serialization failures leave them
unchanged. If replacement fails, the script attempts to restore all already
replaced files and reports any rollback failure with retained backup paths.
These replacements are not a filesystem-wide transaction; avoid concurrent
data fetches or writes and back up valuable exports before running. Diagnostics
remain a report of the API scan even if a later export fails.

For a different input file (also updated in place, with its sibling CSV):

```powershell
uv run --locked python fetch_vm_sku_prices.py --input "C:\path\to\skus.parquet"
```

Fetching fresh VM SKU data again replaces the exports and removes these pricing
columns; rerun the pricing script afterward. Restart the dashboard if it still
shows cached data.

## Managing Dependencies

Use uv to update project dependencies rather than installing packages directly with pip:

```console
uv add <package-name>
uv remove <package-name>
```

These commands update `pyproject.toml`, `uv.lock`, and the local environment. Commit both dependency files when they change. After pulling changes, run `uv sync --locked` to synchronize your environment.

To intentionally upgrade all locked dependencies within the constraints in `pyproject.toml`:

```console
uv lock --upgrade
uv sync --locked
```

`requirements.txt` is retained as a legacy dependency list; the uv workflow uses `pyproject.toml` and `uv.lock` instead.

## Tests

Run the automated suite from the project root:

```console
uv run --locked python -m unittest discover -s tests -t . -v
```

The automated tests mock Azure requests and data loading; they do not require Azure credentials or refresh the saved dataset.

The live Azure access check is kept separately in `tests/manual/` and is not included in unittest discovery. To run it explicitly from the project root:

```powershell
uv run --locked python tests\manual\test_access.py
```

This manual check requires the configured Azure credentials and `AZURE_RESOURCE_GROUP` in `.env`, and contacts Azure.

## Main Scripts

### fetch_vm_skus_all_regions.py

Fetches Azure VM SKU data using the Azure REST API and exports to CSV and Parquet formats.

**Key Features:**
- Authenticates using Azure service principal
- Fetches all VM SKUs across all regions with pagination support
- Captures all capability attributes dynamically
- Outputs comprehensive CSV and Parquet files with performance metrics

### streamlit_app.py

Interactive web dashboard for exploring Azure VM SKU data.

**Key Features:**
- Reads Parquet data for fast loading
- Four navigable reports with shared family-version filters
- Multi-dimensional filtering (region, family, CPU architecture, GPUs, etc.)
- Real-time statistics and visualizations
- Customizable column display
- Distinct SKU aggregation for family and region summaries
- vCPU/memory count matrix with a filter-aware heatmap
- CSV export of filtered data and summaries

## Project Structure

```
.
├── fetch_vm_skus_all_regions.py  # Data collection script
├── fetch_azure_locations.py       # Cache official Azure location metadata
├── fetch_vm_sku_prices.py         # Enrich saved exports with hourly USD prices
├── streamlit_app.py               # Dashboard entry point and report navigation
├── app_pages/                     # Streamlit report pages
│   ├── sku_browser.py
│   ├── family_summary.py
│   ├── region_summary.py
│   ├── region_map.py
│   └── matrix_view.py
├── vm_sku_data.py                 # Shared data loading and report aggregations
├── region_map_data.py             # Official-coordinate joins and paired map bars
├── family_filters.py              # Shared family-version controls and state
├── pricing_filters.py             # Shared pricing-availability controls and row filters
├── docs/
│   └── screenshots/               # Screenshots embedded in this README
├── tests/                        # Automated tests
│   ├── __init__.py
│   ├── test_main.py               # Interactive launcher tests
│   ├── test_fetch_azure_locations.py # Location API and cache tests
│   ├── test_fetch_vm_skus_all_regions.py # Mocked refresh tests
│   ├── test_fetch_vm_sku_prices.py # Pricing matching, paging, and export tests
│   ├── test_vm_sku_data.py        # SKU browser and family report tests
│   ├── test_region_summary.py     # Region report tests
│   ├── test_pricing_filters.py    # Pricing filter semantics and summary page integration
│   ├── test_region_map.py         # Coordinate joins, paired bars, and map page tests
│   ├── test_matrix_view.py        # Matrix, heatmap, and shared-filter tests
│   └── manual/
│       └── test_access.py         # Opt-in live Azure access check
├── pyproject.toml                 # Project metadata, dependencies, and uv index
├── uv.lock                        # Locked dependency versions (commit this)
├── .python-version                # Preferred Python version for uv
├── .venv/                         # uv-managed environment (DO NOT COMMIT)
├── requirements.txt               # Legacy dependency list (not used by uv)
├── .env                           # Azure credentials (DO NOT COMMIT)
├── .gitignore                     # Git ignore rules
├── Readme.md                      # This file
└── data/                          # Output directory (created automatically)
    ├── azure_locations.json       # Official location metadata cache
    ├── vm_skus_all_regions.csv
    └── vm_skus_all_regions.parquet
```

## Troubleshooting

**Authentication errors:**
- Verify your `.env` file contains correct credentials
- Ensure the app registration has Reader role on the subscription
- Check that the client secret hasn't expired

**Missing data:**
- Confirm your subscription ID is correct
- Verify network connectivity to Azure endpoints
- Check API version compatibility (currently using 2025-04-01)

**Streamlit errors:**
- Ensure you've run `fetch_vm_skus_all_regions.py` first to generate data files
- Verify the `data/` directory exists with the parquet file
- Run `uv sync --locked` to ensure all required packages are installed, then launch with `uv run --locked streamlit run streamlit_app.py`
- If a newly added report raises an `ImportError` for a function that exists on disk, stop the running app with **Ctrl+C** and restart it. A long-running server can retain an older imported module; refreshing the browser alone may not reload it.

**uv setup errors:**
- If `uv` is not recognized, reopen your terminal after installation and verify `uv --version` works
- If the lockfile is missing or out of date after an intentional dependency change, run `uv lock`, then `uv sync --locked`, and commit the updated dependency files
- If package downloads fail, check network access and any required authentication for the configured Microsoft CFS package feed

## Future Ideas

- Implement version using azure-mgmt-compute SDK instead of raw REST API
- Add support for filtering by additional resource types beyond VMs
- Create comparison views for VM SKU availability across regions
- Add cost estimation integration
- Export to additional formats (JSON, Excel)
- Add historical tracking of SKU availability changes

## License

This project is provided as-is for educational and exploration purposes.

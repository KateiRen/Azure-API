# Azure VM SKU Explorer

This project provides tools to fetch and explore Azure Virtual Machine SKUs across all regions using the Azure REST API. It includes a data collection script and an interactive Streamlit dashboard for visualization.

## Features

- Fetch all Azure VM SKUs across all regions
- Export data to both CSV and Parquet formats
- Interactive Streamlit dashboard with filtering capabilities
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
- Multi-select filters for region, family, tier, CPU architecture, etc.
- Real-time statistics (total SKUs, unique regions, unique VM types)
- Distribution charts for top regions and VM families
- Detailed statistics for vCPUs, memory, and disk counts
- Downloadable filtered results

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
- Multi-dimensional filtering (region, family, tier, CPU arch, etc.)
- Real-time statistics and visualizations
- Customizable column display
- CSV export of filtered data

## Project Structure

```
.
├── fetch_vm_skus_all_regions.py  # Data collection script
├── streamlit_app.py               # Interactive dashboard
├── pyproject.toml                 # Project metadata, dependencies, and uv index
├── uv.lock                        # Locked dependency versions (commit this)
├── .python-version                # Preferred Python version for uv
├── .venv/                         # uv-managed environment (DO NOT COMMIT)
├── requirements.txt               # Legacy dependency list (not used by uv)
├── .env                           # Azure credentials (DO NOT COMMIT)
├── .gitignore                     # Git ignore rules
├── Readme.md                      # This file
└── data/                          # Output directory (created automatically)
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

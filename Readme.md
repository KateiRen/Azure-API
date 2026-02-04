# Azure VM SKU Explorer

This project provides tools to fetch and explore Azure Virtual Machine SKUs across all regions using the Azure REST API. It includes a data collection script and an interactive Streamlit dashboard for visualization.

## Features

- Fetch all Azure VM SKUs across all regions
- Export data to both CSV and Parquet formats
- Interactive Streamlit dashboard with filtering capabilities
- Detailed statistics and visualizations
- Support for vCPU, memory, GPU, disk, and architecture filtering

## Prerequisites

- **Python 3.8+** required
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

### 4. Create Virtual Environment and Install Dependencies

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

**Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Usage

### 1. Fetch Azure VM SKU Data

Run the data collection script to fetch all VM SKUs:

```bash
python list_vm_skus_all_regions.py
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

### 2. Launch Streamlit Dashboard

Start the interactive dashboard:

```bash
streamlit run streamlit_app.py
```

The dashboard provides:
- Multi-select filters for region, family, tier, CPU architecture, etc.
- Real-time statistics (total SKUs, unique regions, unique VM types)
- Distribution charts for top regions and VM families
- Detailed statistics for vCPUs, memory, and disk counts
- Downloadable filtered results

## Main Scripts

### list_vm_skus_all_regions.py

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
├── list_vm_skus_all_regions.py   # Data collection script
├── streamlit_app.py               # Interactive dashboard
├── requirements.txt               # Python dependencies
├── .env                           # Azure credentials (DO NOT COMMIT)
├── .gitignore                     # Git ignore rules
├── readme.md                      # This file
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
- Ensure you've run `list_vm_skus_all_regions.py` first to generate data files
- Verify the `data/` directory exists with the parquet file
- Check that all required packages are installed

## Future Ideas

- Implement version using azure-mgmt-compute SDK instead of raw REST API
- Add support for filtering by additional resource types beyond VMs
- Create comparison views for VM SKU availability across regions
- Add cost estimation integration
- Export to additional formats (JSON, Excel)
- Add historical tracking of SKU availability changes

## License

This project is provided as-is for educational and exploration purposes.


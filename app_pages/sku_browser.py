import streamlit as st
import pandas as pd
import numpy as np

from family_filters import family_version_checkboxes, filter_family_versions
from pricing_filters import filter_pricing, pricing_checkboxes
from region_map_data import geography_groups_for_regions, load_region_locations
from vm_sku_data import load_data

# Title and description
st.title("☁️ Azure VM SKU Explorer")
st.markdown("Explore Azure VM SKUs across all regions with interactive filters and statistics.")

try:
    df = load_data()
    selected_versions = family_version_checkboxes("sku_family_version")

    # Sidebar filters
    st.sidebar.divider()
    st.sidebar.header("🔍 Filters")

    geography_available = False
    try:
        df = df.assign(**{
            "Geography group": geography_groups_for_regions(
                df["Region"], load_region_locations()["value"],
            ),
        })
        geography_available = True
    except FileNotFoundError:
        st.sidebar.warning(
            "Geography Group filtering is unavailable. Run "
            "`uv run --locked python fetch_azure_locations.py` to create the location cache."
        )
    except (OSError, ValueError) as error:
        st.sidebar.error(
            "Unable to load geography metadata; Geography Group filtering is disabled. "
            "Check the error and refresh the location cache if needed."
        )
        with st.sidebar:
            st.exception(error)
    if not geography_available:
        st.session_state["sku_geography_groups"] = []
    selected_geographies = st.sidebar.multiselect(
        "Geography Group",
        options=sorted(df["Geography group"].unique()) if geography_available else [],
        key="sku_geography_groups",
        disabled=not geography_available,
        help=(
            "Select one or more official geographyGroup values. Empty means all groups. "
            "Not assigned includes regions without geography-group metadata."
        ),
    )
    geography_df = (
        df[df["Geography group"].isin(selected_geographies)] if selected_geographies else df
    )

    # Region filter (multiselect)
    regions = sorted(geography_df['Region'].dropna().unique())
    if any(region not in regions for region in st.session_state.get("sku_region", [])):
        st.session_state["sku_region"] = [
            region for region in st.session_state["sku_region"] if region in regions
        ]
    selected_regions = st.sidebar.multiselect(
        "Region",
        options=regions,
        default=None,
        key="sku_region",
        help="Select one or more regions within the selected geography groups"
    )

    required_prices = pricing_checkboxes(df, "sku")

    # Family filter (multiselect)
    families = sorted(df['Family'].dropna().unique())
    selected_families = st.sidebar.multiselect(
        "Family",
        options=families,
        default=None,
        key="sku_families",
        help="Select VM families (e.g., Dasv5, Dv5)"
    )

    # CPU Architecture filter
    cpu_archs = sorted(df['CpuArchitectureType'].dropna().unique())
    selected_cpu_arch = st.sidebar.multiselect(
        "CPU Architecture Type",
        options=cpu_archs,
        default=None,
        help="Select CPU architecture (x64, Arm64, etc.)"
    )

    if 'PremiumIO' not in df.columns:
        st.session_state["sku_premium_io"] = False
    selected_premium_io = st.sidebar.checkbox(
        "Premium IO",
        key="sku_premium_io",
        disabled='PremiumIO' not in df.columns,
        help=(
            "Show only SKUs with reported Premium IO support. "
            "Unchecked includes all support values."
            if 'PremiumIO' in df.columns else "Premium IO support is not present in the saved dataset."
        ),
    )

    constraints_available = {"vCPUsConstraintsAllowed", "vCPUs"}.issubset(df.columns)
    if not constraints_available:
        st.session_state["sku_vcpu_constraints_allowed"] = False
    selected_vcpu_constraints = st.sidebar.checkbox(
        "vCPUs Constraints Allowed",
        key="sku_vcpu_constraints_allowed",
        disabled=not constraints_available,
        help=(
            "Show only SKUs whose reported allowed positive vCPU counts include "
            "a value below the SKU's reported vCPU count. Unchecked includes all "
            "SKUs, including missing or unusable constraint data."
            if constraints_available else "vCPU constraint fields are not present in the saved dataset."
        ),
    )

    # vCPUsPerCore filter
    vcpu_per_core = sorted(df['vCPUsPerCore'].dropna().unique())
    selected_vcpu_per_core = st.sidebar.multiselect(
        "vCPUs Per Core",
        options=vcpu_per_core,
        default=None,
        help="Filter by vCPUs per core"
    )

    if 'GPUs' not in df.columns:
        st.session_state["sku_has_gpus"] = False
    has_gpus = st.sidebar.checkbox(
        "Has GPUs",
        key="sku_has_gpus",
        disabled='GPUs' not in df.columns,
        help=(
            "Show only VMs with a reported, finite GPU count greater than zero. "
            "The GPU-count filter below can narrow the results further."
            if 'GPUs' in df.columns else "GPU counts are not present in the saved dataset."
        ),
    )

    # GPUs filter - check if GPUs column exists and has values
    if 'GPUs' in df.columns:
        gpu_values = df['GPUs'].dropna().unique()
        if len(gpu_values) > 0:
            gpu_options = sorted([str(x) for x in gpu_values if pd.notna(x)])
            selected_gpus = st.sidebar.multiselect(
                "GPUs",
                options=gpu_options,
                default=None,
                help="Filter by GPU count"
            )
        else:
            selected_gpus = []
    else:
        selected_gpus = []

    # Apply filters
    filtered_df = filter_pricing(
        filter_family_versions(geography_df, selected_versions).copy(), required_prices,
    )

    if selected_regions:
        filtered_df = filtered_df[filtered_df['Region'].isin(selected_regions)]

    if selected_families:
        filtered_df = filtered_df[filtered_df['Family'].isin(selected_families)]

    if selected_cpu_arch:
        filtered_df = filtered_df[filtered_df['CpuArchitectureType'].isin(selected_cpu_arch)]

    if selected_premium_io:
        filtered_df = filtered_df[
            filtered_df['PremiumIO'].astype("string").str.strip().str.casefold().eq("true").fillna(False)
        ]

    if selected_vcpu_constraints:
        allowed_counts = pd.to_numeric(
            filtered_df['vCPUsConstraintsAllowed'].astype("string").str.split(",").explode(),
            errors="coerce",
        )
        minimum_allowed = (
            allowed_counts[allowed_counts.gt(0)]
            .groupby(level=0).min().reindex(filtered_df.index)
        )
        known_constraints = minimum_allowed.notna() & filtered_df['vCPUs'].gt(0)
        supports_constraints = minimum_allowed.lt(filtered_df['vCPUs'])
        filtered_df = filtered_df[
            known_constraints & supports_constraints
        ]

    if selected_vcpu_per_core:
        filtered_df = filtered_df[filtered_df['vCPUsPerCore'].isin(selected_vcpu_per_core)]

    if has_gpus:
        filtered_df = filtered_df[
            filtered_df['GPUs'].gt(0) & np.isfinite(filtered_df['GPUs'])
        ]

    if selected_gpus:
        filtered_df = filtered_df[filtered_df['GPUs'].astype(str).isin(selected_gpus)]

    # Display statistics
    st.header("📊 Statistics")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Total SKUs", len(filtered_df))

    with col2:
        unique_regions = filtered_df['Region'].nunique()
        st.metric("Unique Regions", unique_regions)

    with col3:
        unique_skus = filtered_df['SkuName'].nunique()
        st.metric("Unique SKU Names", unique_skus)

    with col4:
        unique_families = filtered_df['Family'].nunique()
        st.metric("Unique Families", unique_families)

    # Detailed statistics per column
    st.subheader("📈 Detailed Statistics")

    stats_col1, stats_col2, stats_col3, stats_col4 = st.columns(4)

    with stats_col1:
        st.markdown("**vCPUs**")
        if 'vCPUs' in filtered_df.columns and filtered_df['vCPUs'].notna().any():
            vcpu_stats = filtered_df['vCPUs'].describe()
            st.write(f"Min: {vcpu_stats['min']:.0f}")
            st.write(f"Max: {vcpu_stats['max']:.0f}")
        else:
            st.write("No data")

    with stats_col2:
        st.markdown("**GPUs**")
        if 'GPUs' in filtered_df.columns and filtered_df['GPUs'].notna().any():
            st.write(f"Min: {filtered_df['GPUs'].min():.0f}")
            st.write(f"Max: {filtered_df['GPUs'].max():.0f}")
        else:
            st.write("No data")

    with stats_col3:
        st.markdown("**Memory (GB)**")
        if 'MemoryGB' in filtered_df.columns and filtered_df['MemoryGB'].notna().any():
            mem_stats = filtered_df['MemoryGB'].describe()
            st.write(f"Min: {mem_stats['min']:.0f}")
            st.write(f"Max: {mem_stats['max']:.0f}")
        else:
            st.write("No data")

    with stats_col4:
        st.markdown("**Max Data Disks**")
        if 'MaxDataDiskCount' in filtered_df.columns and filtered_df['MaxDataDiskCount'].notna().any():
            disk_stats = filtered_df['MaxDataDiskCount'].describe()
            st.write(f"Min: {disk_stats['min']:.0f}")
            st.write(f"Max: {disk_stats['max']:.0f}")
        else:
            st.write("No data")

    # Display distribution charts
    st.subheader("📊 Distribution Charts")

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        # Top 10 regions by SKU count
        top_regions = filtered_df['Region'].value_counts().head(10)
        if not top_regions.empty:
            st.markdown("**Top 10 Regions by SKU Count**")
            st.bar_chart(
                top_regions, sort=False,
                alt="Top regions ordered from highest to lowest SKU count.",
            )

    with chart_col2:
        # Top 10 families by SKU count
        top_families = filtered_df['Family'].value_counts().head(10)
        if not top_families.empty:
            st.markdown("**Top 10 Families by SKU Count**")
            st.bar_chart(
                top_families, sort=False,
                alt="Top families ordered from highest to lowest SKU count.",
            )

    # Display filtered data table
    st.header("📋 Filtered Results")
    st.markdown(f"Showing **{len(filtered_df)}** SKUs")

    # Column selection for display
    all_columns = list(filtered_df.columns)
    default_columns = ['Region', 'SkuName', 'Family', 'Tier', 'vCPUs', 'MemoryGB',
                      'GPUs', 'CpuArchitectureType', 'PremiumIO', 'AvailabilityZones',
                      'retail price', '1Y RI', '3Y RI']
    default_columns = [col for col in default_columns if col in all_columns]

    selected_columns = st.multiselect(
        "Select columns to display:",
        options=all_columns,
        default=default_columns,
        help="Choose which columns to show in the table"
    )

    if selected_columns:
        display_df = filtered_df[selected_columns]
    else:
        display_df = filtered_df

    # Display table with search and sorting
    st.dataframe(
        display_df,
        height=600
    )

    # Download button
    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download filtered data as CSV",
        data=csv,
        file_name="filtered_vm_skus.csv",
        mime="text/csv"
    )

except FileNotFoundError:
    st.error("❌ Error: data/vm_skus_all_regions.parquet not found. Please make sure the file exists in the data directory.")
except Exception as e:
    st.error(f"❌ An error occurred: {str(e)}")
    st.exception(e)

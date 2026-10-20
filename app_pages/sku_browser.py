import streamlit as st
import pandas as pd
import numpy as np

from family_filters import family_version_checkboxes, filter_family_versions
from vm_sku_data import load_data

# Title and description
st.title("☁️ Azure VM SKU Explorer")
st.markdown("Explore Azure VM SKUs across all regions with interactive filters and statistics.")

try:
    df = load_data()

    # Sidebar filters
    st.sidebar.header("🔍 Filters")

    # Region filter (multiselect)
    regions = sorted(df['Region'].dropna().unique())
    selected_regions = st.sidebar.multiselect(
        "Region",
        options=regions,
        default=None,
        help="Select one or more regions"
    )

    # Family filter (multiselect)
    families = sorted(df['Family'].dropna().unique())
    selected_families = st.sidebar.multiselect(
        "Family",
        options=families,
        default=None,
        help="Select VM families (e.g., Dasv5, Dv5)"
    )

    with st.sidebar:
        selected_versions = family_version_checkboxes("sku_family_version", horizontal=False)

    # CPU Architecture filter
    cpu_archs = sorted(df['CpuArchitectureType'].dropna().unique())
    selected_cpu_arch = st.sidebar.multiselect(
        "CPU Architecture Type",
        options=cpu_archs,
        default=None,
        help="Select CPU architecture (x64, Arm64, etc.)"
    )

    # PremiumIO filter
    premium_io_options = sorted(df['PremiumIO'].dropna().unique())
    selected_premium_io = st.sidebar.multiselect(
        "Premium IO",
        options=premium_io_options,
        default=None,
        help="Filter by Premium IO support"
    )

    # vCPUsConstraintsAllowed filter
    selected_vcpu_constraints = st.sidebar.selectbox(
        "vCPUs Constraints Allowed",
        options=["All", "Yes", "No"],
        key="vcpu_constraints_support",
        help=(
            "Yes: an allowed positive vCPU count is lower than the SKU's reported "
            "vCPU count. No: reported allowed counts do not permit a reduction. "
            "Missing or unusable data is included only with All."
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
    filtered_df = filter_family_versions(df, selected_versions).copy()

    if selected_regions:
        filtered_df = filtered_df[filtered_df['Region'].isin(selected_regions)]

    if selected_families:
        filtered_df = filtered_df[filtered_df['Family'].isin(selected_families)]

    if selected_cpu_arch:
        filtered_df = filtered_df[filtered_df['CpuArchitectureType'].isin(selected_cpu_arch)]

    if selected_premium_io:
        filtered_df = filtered_df[filtered_df['PremiumIO'].isin(selected_premium_io)]

    if selected_vcpu_constraints != "All":
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
            known_constraints & (supports_constraints == (selected_vcpu_constraints == "Yes"))
        ]

    if selected_vcpu_per_core:
        filtered_df = filtered_df[filtered_df['vCPUsPerCore'].isin(selected_vcpu_per_core)]

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

    stats_col1, stats_col2, stats_col3 = st.columns(3)

    with stats_col1:
        st.markdown("**vCPUs**")
        if 'vCPUs' in filtered_df.columns and filtered_df['vCPUs'].notna().any():
            vcpu_stats = filtered_df['vCPUs'].describe()
            st.write(f"Min: {vcpu_stats['min']:.0f}")
            st.write(f"Max: {vcpu_stats['max']:.0f}")
            st.write(f"Mean: {vcpu_stats['mean']:.2f}")
            st.write(f"Median: {vcpu_stats['50%']:.0f}")
        else:
            st.write("No data")

    with stats_col2:
        st.markdown("**Memory (GB)**")
        if 'MemoryGB' in filtered_df.columns and filtered_df['MemoryGB'].notna().any():
            mem_stats = filtered_df['MemoryGB'].describe()
            st.write(f"Min: {mem_stats['min']:.0f}")
            st.write(f"Max: {mem_stats['max']:.0f}")
            st.write(f"Mean: {mem_stats['mean']:.2f}")
            st.write(f"Median: {mem_stats['50%']:.0f}")
        else:
            st.write("No data")

    with stats_col3:
        st.markdown("**Max Data Disks**")
        if 'MaxDataDiskCount' in filtered_df.columns and filtered_df['MaxDataDiskCount'].notna().any():
            disk_stats = filtered_df['MaxDataDiskCount'].describe()
            st.write(f"Min: {disk_stats['min']:.0f}")
            st.write(f"Max: {disk_stats['max']:.0f}")
            st.write(f"Mean: {disk_stats['mean']:.2f}")
            st.write(f"Median: {disk_stats['50%']:.0f}")
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
            st.bar_chart(top_regions)

    with chart_col2:
        # Top 10 families by SKU count
        top_families = filtered_df['Family'].value_counts().head(10)
        if not top_families.empty:
            st.markdown("**Top 10 Families by SKU Count**")
            st.bar_chart(top_families)

    # Display filtered data table
    st.header("📋 Filtered Results")
    st.markdown(f"Showing **{len(filtered_df)}** SKUs")

    # Column selection for display
    all_columns = list(filtered_df.columns)
    default_columns = ['Region', 'SkuName', 'Family', 'Tier', 'vCPUs', 'MemoryGB',
                      'GPUs', 'CpuArchitectureType', 'PremiumIO', 'AvailabilityZones']
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

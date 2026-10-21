import pandas as pd
import streamlit as st

from family_filters import family_version_checkboxes, filter_family_versions
from region_map_data import geography_groups_for_regions, load_region_locations
from vm_sku_data import load_data, summarize_matrix


st.title("Matrix View")
st.markdown(
    "Distinct VM types by vCPU range (rows) and memory range in GB (columns). "
    "Each cell counts unique SKU names, not repeated regional listings."
)
st.caption(
    "Ranges double at each step and include the upper boundary. For example, "
    "'>4-8' GB means greater than 4 and up to 8 GB. Overflow ranges cover larger "
    "future sizes. Missing, non-positive, or non-finite vCPU/memory values are "
    "not counted. Missing GPU data is not assumed to mean zero GPUs."
)

try:
    df = load_data()
except FileNotFoundError:
    st.error("Saved VM SKU data was not found. Fetch data before opening Matrix View.")
    st.stop()
except (OSError, ValueError) as error:
    st.error("Unable to load the saved VM SKU data.")
    st.exception(error)
    st.stop()

selected_versions = family_version_checkboxes("matrix_family_version")
st.caption("Family version selections are shared across all pages; other filters are page-specific.")
geography_available = False
try:
    df = df.assign(**{
        "Geography group": geography_groups_for_regions(
            df["Region"], load_region_locations()["value"],
        ),
    })
    geography_available = True
except FileNotFoundError:
    st.warning(
        "Geography Group filtering is unavailable. Run "
        "`uv run --locked python fetch_azure_locations.py` to create the location cache."
    )
except (OSError, ValueError) as error:
    st.error(
        "Unable to load geography metadata; Geography Group filtering is disabled. "
        "Check the error and refresh the location cache if needed."
    )
    st.exception(error)

geography_column, region_column, architecture_column, gpu_column = st.columns(4)
if not geography_available:
    st.session_state["matrix_geography_groups"] = []
selected_geographies = geography_column.multiselect(
    "Geography Group",
    options=sorted(df["Geography group"].unique()) if geography_available else [],
    key="matrix_geography_groups",
    disabled=not geography_available,
    help=(
        "Select one or more official geographyGroup values. Empty means all groups. "
        "Not assigned includes regions without geography-group metadata."
    ),
)
geography_df = (
    df[df["Geography group"].isin(selected_geographies)] if selected_geographies else df
)
region_options = sorted(geography_df["Region"].dropna().unique())
if any(region not in region_options for region in st.session_state.get("matrix_region", [])):
    st.session_state["matrix_region"] = [
        region for region in st.session_state["matrix_region"] if region in region_options
    ]
selected_regions = region_column.multiselect(
    "Region",
    options=region_options,
    key="matrix_region",
    help="Leave empty to include all regions in the selected geography groups.",
)
selected_architectures = architecture_column.multiselect(
    "CPU Architecture Type",
    options=sorted(df["CpuArchitectureType"].dropna().unique()),
    key="matrix_cpu_arch",
    help="Leave empty to include all architectures.",
)
gpu_labels = df["GPUs"].map(
    lambda value: "Not reported" if pd.isna(value) else f"{value:g}"
)
gpu_options = [f"{value:g}" for value in sorted(df["GPUs"].dropna().unique())]
if df["GPUs"].isna().any():
    gpu_options.append("Not reported")
selected_gpus = gpu_column.multiselect(
    "# GPUs",
    options=gpu_options,
    key="matrix_gpu",
    help="Leave empty to include all GPU counts. Not reported is unknown, not zero.",
)

filtered = filter_family_versions(geography_df, selected_versions)
if selected_regions:
    filtered = filtered[filtered["Region"].isin(selected_regions)]
if selected_architectures:
    filtered = filtered[filtered["CpuArchitectureType"].isin(selected_architectures)]
if selected_gpus:
    filtered = filtered[gpu_labels.reindex(filtered.index).isin(selected_gpus)]

matrix = summarize_matrix(filtered)
if not matrix.to_numpy().any():
    st.info("No VM types with usable vCPU and memory data match the selected filters.")
st.caption("Zero means no matching VM types. Empty ranges remain visible for consistent comparisons.")
st.caption("Stronger blue means more VM types, scaled to the highest count in the filtered matrix.")
max_count = int(matrix.to_numpy().max())


def heatmap_cell(value: int) -> str:
    if value == 0:
        return ""
    strength = value / max_count
    red = round(239 - 180 * strength)
    green = round(246 - 116 * strength)
    blue = round(255 - 9 * strength)
    return f"background-color: rgb({red}, {green}, {blue}); color: #000000"


styled_matrix = matrix.style.apply(
    lambda column: [heatmap_cell(value) for value in column],
).format("{:.0f}")
st.dataframe(styled_matrix, height=450)
st.download_button(
    label="Download matrix as CSV",
    data=matrix.to_csv().encode("utf-8"),
    file_name="vm_type_matrix.csv",
    mime="text/csv",
)

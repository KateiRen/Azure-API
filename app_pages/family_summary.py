import streamlit as st

from family_filters import family_version_checkboxes, filter_family_versions
from vm_sku_data import load_data, summarize_families


st.title("Family summary")
st.markdown("One row per VM family, aggregated across all SKUs and regions in the saved dataset.")
st.caption(
    "SKU and region counts are distinct counts. Memory is in GB; data disk values "
    "are the minimum and maximum supported data disk counts. Missing numeric values "
    "are excluded from ranges, not assumed to be zero. Architecture and Premium IO "
    "list every distinct reported value."
)

try:
    df = load_data()
except FileNotFoundError:
    st.error("Saved VM SKU data was not found. Fetch data before opening the family summary.")
    st.stop()
except (OSError, ValueError) as error:
    st.error("Unable to load the saved VM SKU data.")
    st.exception(error)
    st.stop()

summary = summarize_families(df)
region_column, version_column = st.columns([1, 3], vertical_alignment="bottom")
with region_column:
    min_regions = st.number_input(
        "Minimum regions",
        min_value=1,
        value=1,
        step=1,
        key="min_family_regions",
        help="Show only families present in at least this many regions.",
    )
with version_column:
    selected_versions = family_version_checkboxes("family_version")

summary = filter_family_versions(summary, selected_versions)
summary = summary[summary["Region Count"] >= min_regions]
if summary.empty:
    st.info("No families match the selected filters.")
st.markdown(f"Showing **{len(summary)}** families")
st.dataframe(summary, hide_index=True, height=600)
st.download_button(
    label="Download family summary as CSV",
    data=summary.to_csv(index=False).encode("utf-8"),
    file_name="vm_family_summary.csv",
    mime="text/csv",
)

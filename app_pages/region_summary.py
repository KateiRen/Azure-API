import streamlit as st

from family_filters import family_version_checkboxes, filter_family_versions
from pricing_filters import filter_pricing, pricing_checkboxes
from vm_sku_data import load_data, summarize_regions


st.title("Region Summary")
st.markdown("One row per region, aggregated across all VM SKUs in the saved dataset.")
st.caption(
    "SKU and family counts are distinct counts. Availability zones are the union "
    "reported across SKUs; not every SKU supports every listed zone. "
    "'Not reported' means zone availability is unknown, not that zones are absent. "
    "Hardware maxima and support values describe reported SKU capabilities, not "
    "region-wide guarantees. Missing numeric values are not assumed to be zero."
)
st.caption(
    "This is the region coverage of the saved Compute SKU response, not the complete "
    "Azure region catalog or a guarantee of deployment capacity or subscription "
    "eligibility. Geographic metadata, paired regions, and SKU restrictions are "
    "not included in the saved export."
)

try:
    df = load_data()
except FileNotFoundError:
    st.error("Saved VM SKU data was not found. Fetch data before opening the region summary.")
    st.stop()
except (OSError, ValueError) as error:
    st.error("Unable to load the saved VM SKU data.")
    st.exception(error)
    st.stop()

selected_versions = family_version_checkboxes("region_family_version")
st.sidebar.divider()
st.sidebar.header("🔍 Filters")
required_prices = pricing_checkboxes(df, "region")
st.caption(
    "Counts, zones, and capabilities reflect only SKUs matching the selected family "
    "versions and pricing filters. All checked price types must be present; zero "
    "counts as a price. Regions without matching SKUs are hidden."
)
summary = summarize_regions(filter_pricing(
    filter_family_versions(df, selected_versions), required_prices,
))
if "retail price" in df.columns:
    st.caption(
        "'SKUs with price data' counts distinct SKUs with a reported retail "
        "price, including zero. SKUs with only reservation prices do not count."
    )
if summary.empty:
    st.info("No regions match the selected filters.")
st.markdown(f"Showing **{len(summary)}** regions")
hidden_columns = {
    "Premium IO", "Accelerated Networking", "Ultra SSD",
    "Ephemeral OS Disk", "Hyper-V Generations", "Tiers",
}
st.dataframe(
    summary,
    column_order=[column for column in summary.columns if column not in hidden_columns],
    hide_index=True,
    height=600,
)
st.download_button(
    label="Download region summary as CSV",
    data=summary.to_csv(index=False).encode("utf-8"),
    file_name="vm_region_summary.csv",
    mime="text/csv",
)

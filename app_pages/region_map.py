import streamlit as st

from family_filters import family_version_checkboxes, filter_family_versions
from region_map_data import PRICE_COUNT, build_region_deck, join_region_locations, load_region_locations
from vm_sku_data import load_data, summarize_regions


st.title("Region Map")
st.markdown(
    "Compare the number of distinct VM SKUs and SKUs with retail price data "
    "at each region's official Azure location."
)

try:
    df = load_data()
except FileNotFoundError:
    st.error("Saved VM SKU data was not found. Fetch data before opening the region map.")
    st.stop()
except (OSError, ValueError) as error:
    st.error("Unable to load the saved VM SKU data.")
    st.exception(error)
    st.stop()

selected_versions = family_version_checkboxes("map_family_version")
summary = summarize_regions(filter_family_versions(df, selected_versions))
if summary.empty:
    st.info("No regions match the selected family versions.")
    st.stop()

try:
    locations = load_region_locations()
    regions = join_region_locations(summary, locations["value"])
except FileNotFoundError:
    st.error(
        "Official location metadata was not found. Run "
        "`uv run --locked python fetch_azure_locations.py` to create the cache."
    )
    st.stop()
except (OSError, ValueError) as error:
    st.error(
        "Unable to load official location metadata. Check the error and run "
        "`uv run --locked python fetch_azure_locations.py --refresh` if needed."
    )
    st.exception(error)
    st.stop()

geography_groups = sorted(regions["Geography group"].unique().tolist())
selected_groups = st.multiselect(
    "Geography groups",
    options=geography_groups,
    default=geography_groups,
    key="map_geography_groups",
    help=(
        "Select or deselect multiple official geographyGroup values. All groups start "
        "selected; clearing the selection shows no regions. Not reported includes "
        "regions without geography-group metadata."
    ),
)
regions = regions[regions["Geography group"].isin(selected_groups)]
if regions.empty:
    st.info("No regions match the selected geography groups.")
    st.stop()

mapped = regions["Map status"].eq("Mapped")
total_metric, mapped_metric, unmapped_metric = st.columns(3)
total_metric.metric("Regions", len(regions))
mapped_metric.metric("Mapped regions", int(mapped.sum()))
unmapped_metric.metric("Unmapped regions", int((~mapped).sum()))

st.caption(":blue[Blue: all VM SKUs] | :orange[Orange: SKUs with price data]")
st.caption(
    "Both bars use the same linear height scale. Bars are offset slightly west/east "
    "of the official coordinates so they appear side by side. Hover for exact counts; "
    "zoom and rotate to explore nearby regions. A zero count has a flat bar."
)
if PRICE_COUNT not in summary.columns:
    st.info(
        "The saved dataset has no retail price column. Only total SKU bars are shown. "
        "Fetch prices using launcher option 2 to add pricing coverage."
    )
else:
    st.caption(
        "Pricing counts distinct SKUs with a numeric retail price, including zero; "
        "reservation-only prices do not count. All counts reflect the selected filters."
    )

if mapped.any():
    st.pydeck_chart(
        build_region_deck(regions),
        height=700,
        alt="Azure regions with paired bars for total VM SKUs and SKUs with retail price data.",
    )
else:
    st.warning("No selected regions have usable official coordinates.")

if (~mapped).any():
    st.warning(
        f"{int((~mapped).sum())} regions cannot be mapped from the official metadata. "
        "Their counts and reasons are listed below; no coordinates are guessed."
    )
    st.dataframe(
        regions[~mapped].drop(columns=["Latitude", "Longitude"]),
        hide_index=True,
        alt="Regions omitted from the map, their SKU counts, and reasons.",
    )

st.caption(f"Official metadata snapshot: {locations['fetchedAt']}. Metadata is cached for up to 60 seconds.")
with st.expander("Region counts and map coordinates"):
    st.dataframe(
        regions, hide_index=True,
        alt="Exact per-region SKU and pricing counts with official map coordinates and mapping status.",
    )
st.download_button(
    "Download region map data as CSV",
    data=regions.to_csv(index=False).encode("utf-8"),
    file_name="vm_region_map.csv",
    mime="text/csv",
)

import math
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

from fetch_azure_locations import load_cache


LOCATIONS_PATH = Path(__file__).resolve().parent / "data" / "azure_locations.json"
TOTAL_COLOR = [0, 114, 178]
PRICED_COLOR = [230, 159, 0]
PRICE_COUNT = "SKUs with price data"


@st.cache_data(ttl=60)
def load_region_locations() -> dict:
    return load_cache(LOCATIONS_PATH)


def location_metadata(locations: list[dict]) -> pd.DataFrame:
    records = []
    for location in locations:
        metadata = location.get("metadata") or {}
        records.append({
            "_RegionKey": location["name"].strip().casefold(),
            "Display name": location.get("displayName") or location["name"],
            "Geography group": metadata.get("geographyGroup"),
            "Latitude": metadata.get("latitude"),
            "Longitude": metadata.get("longitude"),
            "Region type": metadata.get("regionType"),
        })
    coordinates = pd.DataFrame(records, columns=[
        "_RegionKey", "Display name", "Geography group", "Latitude", "Longitude", "Region type",
    ])
    if coordinates["_RegionKey"].duplicated().any():
        raise ValueError("The location cache contains duplicate region identifiers.")
    coordinates["Geography group"] = (
        coordinates["Geography group"].astype("string").str.strip().replace("", pd.NA)
    )
    return coordinates


def geography_groups_for_regions(regions: pd.Series, locations: list[dict]) -> pd.Series:
    groups = location_metadata(locations).set_index("_RegionKey")["Geography group"]
    return regions.astype("string").str.strip().str.casefold().map(groups).fillna("Not assigned")


def join_region_locations(summary: pd.DataFrame, locations: list[dict]) -> pd.DataFrame:
    columns = ["Region", "VM SKU Count"]
    if PRICE_COUNT in summary.columns:
        columns.append(PRICE_COUNT)
    counts = summary[columns].copy()
    counts["_RegionKey"] = counts["Region"].str.strip().str.casefold()
    coordinates = location_metadata(locations)
    result = counts.merge(coordinates, on="_RegionKey", how="left", validate="many_to_one")
    missing_location = result["Display name"].isna()
    missing_coordinates = result[["Latitude", "Longitude"]].isna().any(axis=1)
    for column in ("Latitude", "Longitude"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    valid_coordinates = (
        result["Latitude"].between(-85.05112878, 85.05112878)
        & result["Longitude"].between(-180, 180)
    )
    result["Map status"] = "Mapped"
    result.loc[~valid_coordinates, "Map status"] = "Invalid map coordinates"
    result.loc[missing_coordinates, "Map status"] = "Coordinates not reported"
    result.loc[result["Region type"].eq("Logical"), "Map status"] = "Logical/non-geographic location"
    result.loc[missing_location, "Map status"] = "No matching official location metadata"
    result["Display name"] = result["Display name"].fillna(result["Region"])
    result["Geography group"] = result["Geography group"].fillna("Not reported")
    return result.drop(columns=["_RegionKey", "Region type"])


def build_region_deck(regions: pd.DataFrame) -> pdk.Deck:
    mapped = regions[regions["Map status"].eq("Mapped")]
    layers = []
    measures = [("VM SKU Count", TOTAL_COLOR, -1, "total-skus")]
    if PRICE_COUNT in mapped.columns:
        measures.append((PRICE_COUNT, PRICED_COLOR, 1, "priced-skus"))
    for measure, color, direction, layer_id in measures:
        bars = mapped.copy()
        bars["Position"] = [
            [
                (
                    longitude + direction * 0.36 / math.cos(math.radians(latitude))
                    + 180
                ) % 360 - 180,
                latitude,
            ]
            for latitude, longitude in zip(bars["Latitude"], bars["Longitude"])
        ]
        bars["Count"] = bars[measure]
        bars["Measure"] = measure
        bars["Pricing"] = bars[PRICE_COUNT].astype(str) if PRICE_COUNT in bars else "Not available"
        layers.append(pdk.Layer(
            "ColumnLayer",
            id=layer_id,
            data=bars.to_dict(orient="records"),
            get_position="Position",
            get_elevation="Count",
            get_fill_color=color,
            elevation_scale=600,
            radius=30_000,
            disk_resolution=12,
            pickable=True,
            auto_highlight=True,
        ))
    return pdk.Deck(
        layers=layers,
        map_provider="carto",
        map_style=pdk.map_styles.LIGHT,
        initial_view_state=pdk.ViewState(
            latitude=20, longitude=15, zoom=0.5, pitch=45, bearing=0,
        ),
        tooltip={
            "text": "{Display name} ({Region})\nGeography group: {Geography group}\n{Measure}: {Count}\n"
            "Total SKUs: {VM SKU Count}\nSKUs with price data: {Pricing}",
        },
    )

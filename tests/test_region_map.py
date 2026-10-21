import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from region_map_data import (
    PRICE_COUNT, build_region_deck, join_region_locations, load_region_locations,
)
from tests.test_region_summary import region_data
from vm_sku_data import load_data, summarize_regions


def location_data():
    return [
        {
            "name": " EAST ", "displayName": "East Region",
            "metadata": {
                "latitude": "45", "longitude": "10", "regionType": "Physical",
                "geographyGroup": "Europe",
            },
        },
        {
            "name": "west", "displayName": "West Region",
            "metadata": {
                "latitude": "0", "longitude": "0", "regionType": "Physical",
                "geographyGroup": "US",
            },
        },
        {"name": "unused", "metadata": {"latitude": "30", "longitude": "30"}},
    ]


def priced_data():
    return region_data().assign(**{"retail price": [None, None, 0.1, 0, None]})


class RegionMapDataTests(unittest.TestCase):
    def test_case_insensitive_exact_join_preserves_counts_and_input(self):
        summary = summarize_regions(priced_data())
        before = summary.copy(deep=True)
        result = join_region_locations(summary, location_data())
        self.assertEqual(result["Region"].tolist(), ["east", "west"])
        self.assertEqual(result["VM SKU Count"].tolist(), [3, 1])
        self.assertEqual(result[PRICE_COUNT].tolist(), [2, 0])
        self.assertEqual(result["Display name"].tolist(), ["East Region", "West Region"])
        self.assertEqual(result["Geography group"].tolist(), ["Europe", "US"])
        self.assertEqual(result["Map status"].tolist(), ["Mapped", "Mapped"])
        self.assertEqual(result["Latitude"].tolist(), [45, 0])
        self.assertEqual(result["Longitude"].tolist(), [10, 0])
        pd.testing.assert_frame_equal(summary, before)

    def test_unmatched_missing_invalid_and_logical_coordinates_are_explicit(self):
        for records, reason in (
            ([], "No matching official location metadata"),
            ([{"name": "east"}], "Coordinates not reported"),
            ([{"name": "east", "metadata": {"latitude": "invalid", "longitude": "10"}}],
             "Invalid map coordinates"),
            ([{"name": "east", "metadata": {"latitude": "91", "longitude": "10"}}],
             "Invalid map coordinates"),
            ([{"name": "east", "metadata": {"latitude": "45", "longitude": "181"}}],
             "Invalid map coordinates"),
            ([{"name": "east", "metadata": {"latitude": "45", "longitude": "inf"}}],
             "Invalid map coordinates"),
            ([{"name": "east", "metadata": {
                "latitude": "45", "longitude": "10", "regionType": "Logical",
            }}], "Logical/non-geographic location"),
        ):
            with self.subTest(reason=reason, records=records):
                result = join_region_locations(summarize_regions(priced_data()), records)
                self.assertEqual(result.iloc[0]["Map status"], reason)
                self.assertEqual(result.iloc[0][PRICE_COUNT], 2)
                self.assertEqual(result.iloc[0]["Geography group"], "Not reported")
                deck = json.loads(build_region_deck(result).to_json())
                self.assertTrue(all(not layer["data"] for layer in deck["layers"]))

    def test_duplicate_metadata_identifiers_are_rejected(self):
        records = location_data()
        records.append({"name": "east", "metadata": {"latitude": "40", "longitude": "20"}})
        with self.assertRaisesRegex(ValueError, "duplicate region"):
            join_region_locations(summarize_regions(priced_data()), records)

    def test_two_bars_use_same_linear_scale_exact_counts_and_separate_positions(self):
        mapped = join_region_locations(summarize_regions(priced_data()), location_data())
        before = mapped.copy(deep=True)
        deck = json.loads(build_region_deck(mapped).to_json())
        total, priced = deck["layers"]
        self.assertEqual(total["@@type"], "ColumnLayer")
        self.assertEqual([bar["Count"] for bar in total["data"]], [3, 1])
        self.assertEqual([bar["Count"] for bar in priced["data"]], [2, 0])
        self.assertEqual(total["getElevation"], priced["getElevation"])
        self.assertEqual(total["elevationScale"], priced["elevationScale"])
        self.assertNotEqual(total["getFillColor"], priced["getFillColor"])
        self.assertTrue(total["pickable"])
        for index, row in mapped.iterrows():
            self.assertLess(total["data"][index]["Position"][0], row["Longitude"])
            self.assertGreater(priced["data"][index]["Position"][0], row["Longitude"])
            self.assertEqual(total["data"][index]["Position"][1], row["Latitude"])
        pd.testing.assert_frame_equal(mapped, before)

    def test_missing_price_column_does_not_fabricate_zero_coverage(self):
        mapped = join_region_locations(summarize_regions(region_data()), location_data())
        self.assertNotIn(PRICE_COUNT, mapped.columns)
        layers = json.loads(build_region_deck(mapped).to_json())["layers"]
        self.assertEqual(len(layers), 1)
        self.assertTrue(all(bar["Pricing"] == "Not available" for bar in layers[0]["data"]))

    def test_empty_summary_preserves_schema_and_no_records(self):
        result = join_region_locations(summarize_regions(priced_data().iloc[:0]), location_data())
        self.assertTrue(result.empty)
        self.assertIn(PRICE_COUNT, result.columns)
        self.assertIn("Map status", result.columns)

    def test_longitude_offsets_wrap_at_date_line(self):
        records = location_data()
        records[0]["metadata"]["longitude"] = "179.99"
        result = join_region_locations(summarize_regions(priced_data()), records)
        layers = json.loads(build_region_deck(result).to_json())["layers"]
        for layer in layers:
            for bar in layer["data"]:
                self.assertTrue(-180 <= bar["Position"][0] <= 180)


class RegionMapPageTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        load_region_locations.clear()
        self.addCleanup(load_data.clear)
        self.addCleanup(load_region_locations.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"

    def open_map(self, expected_exceptions=0):
        app = AppTest.from_file(str(self.app_path), default_timeout=10).run()
        app.switch_page("app_pages/region_map.py").run()
        self.assertEqual(len(app.exception), expected_exceptions)
        return app

    def test_navigation_map_counts_and_csv_download(self):
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=priced_data()),
            patch("region_map_data.load_region_locations", return_value={
                "value": location_data(), "fetchedAt": "2026-10-21T14:00:00+00:00",
            }),
            patch("streamlit.pydeck_chart", wraps=st.pydeck_chart) as chart,
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = self.open_map()
            self.assertEqual(app.title[0].value, "Region Map")
            self.assertEqual([metric.value for metric in app.metric], ["2", "2", "0"])
            self.assertEqual(len(app.checkbox), 7)
            chart.assert_called_once()
            layers = json.loads(chart.call_args.args[0].to_json())["layers"]
            self.assertEqual(len(layers), 2)
            self.assertEqual([bar["Count"] for bar in layers[1]["data"]], [2, 0])
            self.assertIn("paired bars", chart.call_args.kwargs["alt"])
            exported = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
            self.assertEqual(exported[PRICE_COUNT].tolist(), [2, 0])
            self.assertEqual(exported["Map status"].tolist(), ["Mapped", "Mapped"])
            self.assertEqual(download.call_args.kwargs["file_name"], "vm_region_map.csv")

    def test_family_and_geography_filters_update_bars_and_share_versions(self):
        data = priced_data()
        data["Family"] = data["Family"].replace({"F1": "standardDv2Family", "F2": "standardDv6Family"})
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("region_map_data.load_region_locations", return_value={
                "value": location_data(), "fetchedAt": "2026-10-21T14:00:00+00:00",
            }),
            patch("streamlit.pydeck_chart", wraps=st.pydeck_chart) as chart,
        ):
            app = self.open_map()
            self.assertEqual(app.multiselect[0].label, "Geography groups")
            self.assertEqual(app.multiselect[0].value, ["Europe", "US"])
            app.multiselect[0].set_value(["Europe"]).run()
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east"])
            for checkbox in app.checkbox:
                checkbox.set_value(checkbox.label == "v6")
            app.run()
            self.assertEqual(len(app.exception), 0)
            layers = json.loads(chart.call_args.args[0].to_json())["layers"]
            self.assertEqual([bar["Count"] for bar in layers[0]["data"]], [2])
            self.assertEqual([bar["Count"] for bar in layers[1]["data"]], [1])
            app.switch_page("app_pages/region_summary.py").run()
            self.assertEqual([c.label for c in app.checkbox if c.value], ["v6"])

    def test_multiple_groups_can_be_selected_deselected_and_cleared(self):
        data = pd.concat([
            priced_data(),
            priced_data().iloc[:1].assign(Region="asia", SkuName="A1"),
        ], ignore_index=True)
        records = location_data() + [{
            "name": "asia",
            "metadata": {"latitude": "30", "longitude": "100", "geographyGroup": "Asia Pacific"},
        }]
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("region_map_data.load_region_locations", return_value={
                "value": records, "fetchedAt": "2026-10-21T14:00:00+00:00",
            }),
            patch("streamlit.pydeck_chart", wraps=st.pydeck_chart) as chart,
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = self.open_map()
            self.assertEqual(app.multiselect[0].value, ["Asia Pacific", "Europe", "US"])
            app.multiselect[0].set_value(["Europe", "US"]).run()
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east", "west"])
            exported = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
            self.assertEqual(exported["Geography group"].tolist(), ["Europe", "US"])
            app.multiselect[0].unselect("US").run()
            app.multiselect[0].select("Asia Pacific").run()
            self.assertEqual(len(app.exception), 0)
            layers = json.loads(chart.call_args.args[0].to_json())["layers"]
            self.assertEqual([bar["Region"] for bar in layers[0]["data"]], ["asia", "east"])
            chart.reset_mock()
            app.multiselect[0].set_value([]).run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn("No regions match the selected geography groups", app.info[0].value)
            chart.assert_not_called()

    def test_no_selected_versions_renders_no_map(self):
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=priced_data()),
            patch("region_map_data.load_region_locations", return_value={
                "value": location_data(), "fetchedAt": "2026-10-21T14:00:00+00:00",
            }),
            patch("streamlit.pydeck_chart", wraps=st.pydeck_chart) as chart,
        ):
            app = self.open_map()
            chart.reset_mock()
            for checkbox in app.checkbox:
                checkbox.uncheck()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn("No regions match", app.info[0].value)
            chart.assert_not_called()

    def test_missing_price_data_has_one_layer_and_explicit_notice(self):
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=region_data()),
            patch("region_map_data.load_region_locations", return_value={
                "value": location_data(), "fetchedAt": "2026-10-21T14:00:00+00:00",
            }),
            patch("streamlit.pydeck_chart", wraps=st.pydeck_chart) as chart,
        ):
            app = self.open_map()
            self.assertIn("no retail price column", app.info[0].value)
            self.assertEqual(len(json.loads(chart.call_args.args[0].to_json())["layers"]), 1)

    def test_unmapped_regions_are_listed_without_fake_coordinates(self):
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=priced_data()),
            patch("region_map_data.load_region_locations", return_value={
                "value": location_data()[:1], "fetchedAt": "2026-10-21T14:00:00+00:00",
            }),
        ):
            app = self.open_map()
            self.assertEqual([metric.value for metric in app.metric], ["2", "1", "1"])
            self.assertIn("no coordinates are guessed", app.warning[0].value)
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["west"])
            self.assertNotIn("Latitude", app.dataframe[0].value.columns)
            self.assertIn("Not reported", app.multiselect[0].options)
            app.multiselect[0].set_value(["Not reported"]).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual([metric.value for metric in app.metric], ["1", "0", "1"])
            self.assertEqual(app.dataframe[0].value["Geography group"].tolist(), ["Not reported"])

    def test_missing_or_invalid_metadata_has_actionable_error(self):
        for error in (FileNotFoundError(), ValueError("invalid cache")):
            with (
                self.subTest(error=error),
                patch("vm_sku_data.pd.read_parquet", return_value=priced_data()),
                patch("region_map_data.load_region_locations", side_effect=error),
                patch("streamlit.pydeck_chart", wraps=st.pydeck_chart) as chart,
            ):
                app = self.open_map(expected_exceptions=int(isinstance(error, ValueError)))
                self.assertIn("fetch_azure_locations.py", app.error[0].value)
                if isinstance(error, ValueError):
                    self.assertEqual(app.exception[0].message, "invalid cache")
                chart.assert_not_called()
                load_data.clear()

    def test_missing_sku_data_has_actionable_error(self):
        with patch("vm_sku_data.pd.read_parquet", side_effect=FileNotFoundError):
            app = self.open_map()
            self.assertIn("Fetch data", app.error[0].value)


if __name__ == "__main__":
    unittest.main()

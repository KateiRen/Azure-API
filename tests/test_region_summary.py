import io
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from vm_sku_data import load_data, summarize_regions


def region_data() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Region": region, "SkuName": sku, "Family": family,
            "AvailabilityZones": zones, "CpuArchitectureType": arch,
            "vCPUs": cpu, "MemoryGB": memory, "GPUs": gpu,
            "PremiumIO": premium, "AcceleratedNetworkingEnabled": premium,
            "UltraSSDAvailable": premium, "EphemeralOSDiskSupported": premium,
            "HyperVGenerations": generations, "Tier": "Standard",
            "MaxDataDiskCount": "8", "vCPUsConstraintsAllowed": None,
            "vCPUsPerCore": "1",
        }
        for region, sku, family, zones, arch, cpu, memory, gpu, premium, generations in [
            ("west", "W1", "F1", None, "x64", "4", "8", None, "False", "V1"),
            ("east", "E1", "F1", "3,1,2", "x64", "2", "3.5", None, "False", "V1,V2"),
            ("east", "E1", "F1", "2,3,1", "x64", "2", "3.5", None, "False", "V1,V2"),
            ("east", "E2", "F2", " 4, 2, ,4 ", "Arm64", "16", "64", "2", "True", "V2"),
            ("east", "E3", "F2", "", None, "unknown", None, None, None, None),
        ]
    ])


class RegionSummaryTests(unittest.TestCase):
    def test_unique_counts_zone_union_and_reported_capabilities(self):
        data = region_data()
        before = data.copy(deep=True)
        result = summarize_regions(data)
        self.assertEqual(result["Region"].tolist(), ["east", "west"])
        self.assertEqual(list(result.columns), [
            "Region", "VM SKU Count", "Family Count", "Availability Zones",
            "Zone Count", "SKUs with Zone Data", "CPU Architecture(s)",
            "Max vCPU", "Max Memory (GB)", "Max GPU", "Premium IO",
            "Accelerated Networking", "Ultra SSD", "Ephemeral OS Disk",
            "Hyper-V Generations", "Tiers",
        ])
        east = result.iloc[0]
        self.assertEqual(east.tolist(), [
            "east", 3, 2, "1, 2, 3, 4", 4, 2, "Arm64, x64", 16, 64, 2,
            "False, True", "False, True", "False, True", "False, True", "V1, V2", "Standard",
        ])
        west = result.iloc[1]
        self.assertEqual(west["Availability Zones"], "Not reported")
        self.assertTrue(pd.isna(west["Zone Count"]))
        self.assertEqual(west["SKUs with Zone Data"], 0)
        self.assertTrue(pd.isna(west["Max GPU"]))
        pd.testing.assert_frame_equal(data, before)

    def test_blank_zone_lists_are_unknown_not_zero(self):
        data = region_data()
        data["AvailabilityZones"] = [None, "", " , ", " ", None]
        summary = summarize_regions(data)
        self.assertTrue(summary["Availability Zones"].eq("Not reported").all())
        self.assertTrue(summary["Zone Count"].isna().all())
        self.assertTrue(summary["SKUs with Zone Data"].eq(0).all())

    def test_optional_capabilities_and_boolean_values(self):
        data = region_data().drop(columns="UltraSSDAvailable")
        data["PremiumIO"] = data["PremiumIO"].map({"True": True, "False": False})
        result = summarize_regions(data)
        self.assertNotIn("Ultra SSD", result.columns)
        self.assertEqual(result.iloc[0]["Premium IO"], "False, True")

    def test_empty_dataset_keeps_table_schema(self):
        data = region_data()
        result = summarize_regions(data.iloc[:0])
        self.assertTrue(result.empty)
        self.assertEqual(list(result.columns), list(summarize_regions(data).columns))

    def test_retail_price_count_is_distinct_includes_zero_and_ignores_ri_only(self):
        data = region_data()
        data["retail price"] = [None, None, "0.1", 0, None]
        data["1Y RI"] = 1
        data["3Y RI"] = 2
        before = data.copy(deep=True)
        result = summarize_regions(data)
        column = "SKUs with price data"
        self.assertEqual(result.columns[2], column)
        self.assertEqual(result[column].tolist(), [2, 0])
        self.assertEqual(result["VM SKU Count"].tolist(), [3, 1])
        pd.testing.assert_frame_equal(data, before)
        data["retail price"] = [None, "", "not reported", None, None]
        self.assertEqual(summarize_regions(data)[column].tolist(), [0, 0])

    def test_price_column_is_optional_and_empty_summary_keeps_it(self):
        column = "SKUs with price data"
        data = region_data().assign(**{"1Y RI": 1})
        self.assertNotIn(column, summarize_regions(data).columns)
        data["retail price"] = None
        expected = summarize_regions(data)
        self.assertEqual(expected[column].tolist(), [0, 0])
        empty = summarize_regions(data.iloc[:0])
        self.assertTrue(empty.empty)
        self.assertEqual(empty.columns.tolist(), expected.columns.tolist())


class RegionPageTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        self.addCleanup(load_data.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"

    def test_navigation_full_dataset_and_csv_download(self):
        data = region_data()
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            app.sidebar.multiselect(key="sku_region").select("west").run()
            self.assertEqual(len(app.exception), 0)
            app.switch_page("app_pages/region_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.title[0].value, "Region Summary")
            self.assertEqual(len(app.sidebar.multiselect), 0)
            self.assertEqual(
                [checkbox.label for checkbox in app.main.checkbox],
                ["Other", "v2", "v3", "v4", "v5", "v6", "v7"],
            )
            self.assertTrue(all(checkbox.value for checkbox in app.main.checkbox))
            expected = summarize_regions(data)
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            self.assertEqual(list(app.dataframe[0].proto.column_order), [
                "Region", "VM SKU Count", "Family Count", "Availability Zones",
                "Zone Count", "SKUs with Zone Data", "CPU Architecture(s)",
                "Max vCPU", "Max Memory (GB)", "Max GPU",
            ])
            kwargs = download.call_args.kwargs
            self.assertEqual(kwargs["file_name"], "vm_region_summary.csv")
            csv = pd.read_csv(io.BytesIO(kwargs["data"]))
            self.assertEqual(csv["Region"].tolist(), ["east", "west"])
            self.assertEqual(csv["VM SKU Count"].tolist(), [3, 1])
            self.assertEqual(csv.iloc[0]["Availability Zones"], "1, 2, 3, 4")
            self.assertEqual(list(csv.columns), list(expected.columns))
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            app.switch_page("app_pages/sku_browser.py").run()
            self.assertEqual(len(app.exception), 0)

    def test_version_filter_recalculates_counts_zones_capabilities_and_csv(self):
        data = region_data()
        data["Family"] = data["Family"].replace({
            "F1": "standardDv2PromoFamily",
            "F2": "standardNDISRGB200V6NDRFamily",
        })
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/region_summary.py").run()
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v6")
            app.run()
            self.assertEqual(len(app.exception), 0)
            expected = summarize_regions(data[data["Family"] == "standardNDISRGB200V6NDRFamily"])
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            row = app.dataframe[0].value.iloc[0]
            self.assertEqual(row["Region"], "east")
            self.assertEqual(row["VM SKU Count"], 2)
            self.assertEqual(row["Family Count"], 1)
            self.assertEqual(row["Availability Zones"], "2, 4")
            self.assertEqual(row["CPU Architecture(s)"], "Arm64")
            self.assertEqual(row["Premium IO"], "True")
            self.assertEqual(
                download.call_args.kwargs["data"],
                expected.to_csv(index=False).encode("utf-8"),
            )
            app.checkbox(key="region_family_version_v2").check().run()
            pd.testing.assert_frame_equal(app.dataframe[0].value, summarize_regions(data))
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(
                [checkbox.label for checkbox in app.main.checkbox if checkbox.value],
                ["v2", "v6"],
            )

    def test_no_selected_or_matching_versions_shows_empty_table(self):
        with patch("vm_sku_data.pd.read_parquet", return_value=region_data()):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/region_summary.py").run()
            for checkbox in app.main.checkbox:
                checkbox.uncheck()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            self.assertEqual(len(app.dataframe[0].value.columns), 16)
            self.assertEqual(app.info[0].value, "No regions match the selected filters.")
            app.checkbox(key="region_family_version_v7").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            app.checkbox(key="region_family_version_other").check().run()
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east", "west"])

    def test_retail_price_count_is_visible_filtered_and_exported(self):
        data = region_data()
        data["Family"] = data["Family"].replace({"F1": "standardDv2Family", "F2": "standardDv6Family"})
        data["retail price"] = [0.5, 0.1, None, None, 0]
        data["1Y RI"] = 1
        column = "SKUs with price data"
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/region_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value[column].tolist(), [2, 1])
            self.assertEqual(list(app.dataframe[0].proto.column_order)[2], column)
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v6")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east"])
            self.assertEqual(app.dataframe[0].value[column].tolist(), [1])
            csv = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
            self.assertEqual(csv[column].tolist(), [1])
            for checkbox in app.main.checkbox:
                checkbox.uncheck()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            self.assertIn(column, app.dataframe[0].value.columns)

    def test_missing_file_shows_actionable_error(self):
        with patch("vm_sku_data.pd.read_parquet", side_effect=FileNotFoundError):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/region_summary.py").run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("Fetch data", app.error[0].value)
        self.assertEqual(len(app.dataframe), 0)


if __name__ == "__main__":
    unittest.main()

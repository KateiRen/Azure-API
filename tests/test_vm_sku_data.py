from pathlib import Path
import io
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from vm_sku_data import load_data, summarize_families


def sample_data() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Family": family, "SkuName": sku, "Region": region,
            "CpuArchitectureType": arch, "vCPUs": cpu, "GPUs": gpu,
            "MemoryGB": memory, "MaxDataDiskCount": disks, "PremiumIO": premium,
            "Tier": "Standard", "vCPUsConstraintsAllowed": None, "vCPUsPerCore": "1",
        }
        for family, sku, region, arch, cpu, gpu, memory, disks, premium in [
            ("B", "B1", "west", "x64", "4", None, "16", "8", "False"),
            ("A", "A1", "east", "x64", "2", "0", "3.5", "4", "False"),
            ("A", "A1", "west", "x64", "2", "0", "3.5", "4", "False"),
            ("A", "A2", "east", "Arm64", "16", "2", "64", "32", "True"),
            ("A", "A2", "east", "Arm64", "16", "2", "64", "32", "True"),
        ]
    ])


class SkuBrowserTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        self.addCleanup(load_data.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"

    def test_sidebar_versions_filter_results_metrics_and_csv_with_existing_filters(self):
        data = sample_data()
        data["Family"] = data["Family"].replace({
            "A": "standardDv5Family", "B": "standardDv6Family",
        })
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertNotIn("Tier", [widget.label for widget in app.sidebar.multiselect])
            self.assertEqual(
                [checkbox.label for checkbox in app.sidebar.checkbox],
                ["Other", "v2", "v3", "v4", "v5", "v6", "v7"],
            )
            self.assertTrue(all(checkbox.value for checkbox in app.sidebar.checkbox))
            self.assertEqual(len(app.dataframe[0].value), 5)
            for checkbox in app.sidebar.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), 4)
            self.assertEqual([metric.value for metric in app.metric], ["4", "2", "2", "1"])
            app.sidebar.multiselect[0].select("west")
            app.sidebar.multiselect[1].select("standardDv5Family")
            app.run()
            self.assertEqual(len(app.exception), 0)
            result = app.dataframe[0].value
            self.assertEqual(result["SkuName"].tolist(), ["A1"])
            self.assertEqual(result["Region"].tolist(), ["west"])
            self.assertEqual([metric.value for metric in app.metric], ["1", "1", "1", "1"])
            self.assertEqual(
                download.call_args.kwargs["data"],
                result.to_csv(index=False).encode("utf-8"),
            )
            app.sidebar.multiselect[1].unselect("standardDv5Family").select("standardDv6Family").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)

    def test_other_and_no_selected_versions(self):
        data = sample_data()
        data["Family"] = data["Family"].replace({"A": "standardDv5Family"})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            for checkbox in app.sidebar.checkbox:
                checkbox.set_value(checkbox.label == "Other")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["B1"])
            app.sidebar.checkbox(key="sku_family_version_other").uncheck().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            self.assertEqual([metric.value for metric in app.metric], ["0", "0", "0", "0"])
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(checkbox.value for checkbox in app.checkbox))


    def test_constraint_filter_detects_reduction_and_preserves_unknowns_with_all(self):
        template = sample_data().iloc[0].to_dict()
        data = pd.DataFrame([
            {
                **template, "SkuName": sku, "vCPUs": cpu,
                "vCPUsConstraintsAllowed": allowed, "Region": region,
                "Family": family, "vCPUsAvailable": available,
            }
            for sku, cpu, allowed, region, family, available in [
                ("One", "1", "1", "east", "standardDv5Family", "1"),
                ("Full", "4", "4", "east", "standardDv5Family", "4"),
                ("Reduced", "4", " 1, 2, 4 ", "east", "standardDv5Family", "4"),
                ("Already", "8", "1, 2", "west", "standardDv6Family", "2"),
                ("Zero", "4", "0, 4", "west", "standardDv5Family", "4"),
                ("Missing", "4", None, "west", "standardDv5Family", "4"),
                ("Blank", "4", " ", "west", "standardDv5Family", "4"),
                ("Invalid", "4", "unknown", "west", "standardDv5Family", "4"),
                ("UnknownCPU", "unknown", "1", "west", "standardDv5Family", None),
            ]
        ])
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            selector = app.sidebar.selectbox(key="vcpu_constraints_support")
            self.assertEqual(selector.options, ["All", "Yes", "No"])
            self.assertEqual(selector.value, "All")
            self.assertEqual(len(app.dataframe[0].value), len(data))
            selector.set_value("Yes").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["Reduced", "Already"])
            self.assertEqual([metric.value for metric in app.metric], ["2", "2", "2", "2"])
            self.assertEqual(
                download.call_args.kwargs["data"],
                app.dataframe[0].value.to_csv(index=False).encode("utf-8"),
            )
            app.sidebar.selectbox(key="vcpu_constraints_support").set_value("No").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["One", "Full", "Zero"])
            app.sidebar.selectbox(key="vcpu_constraints_support").set_value("All").run()
            self.assertEqual(len(app.dataframe[0].value), len(data))
            app.sidebar.selectbox(key="vcpu_constraints_support").set_value("Yes")
            app.sidebar.multiselect[0].select("east").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["Reduced"])
            for checkbox in app.sidebar.checkbox:
                checkbox.set_value(checkbox.label == "v6")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            app.sidebar.selectbox(key="vcpu_constraints_support").set_value("No").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)


class FamilySummaryTests(unittest.TestCase):
    def test_aggregates_unique_counts_ranges_and_all_categories(self):
        result = summarize_families(sample_data())
        self.assertEqual(list(result.columns), [
            "Family", "Unique SKU Count", "Region Count", "CPU Architecture(s)",
            "Min vCPU", "Max vCPU", "Min GPU", "Max GPU",
            "Min Memory (GB)", "Max Memory (GB)", "Min Data Disk", "Max Data Disk",
            "Premium IO",
        ])
        self.assertEqual(result["Family"].tolist(), ["A", "B"])
        self.assertEqual(result.iloc[0].tolist(), [
            "A", 2, 2, "Arm64, x64", 2, 16, 0, 2, 3.5, 64, 4, 32, "False, True",
        ])
        self.assertTrue(pd.isna(result.iloc[1]["Min GPU"]))
        self.assertTrue(pd.isna(result.iloc[1]["Max GPU"]))
        self.assertEqual(result.iloc[1]["Premium IO"], "False")

    def test_missing_values_are_not_fabricated_and_input_is_unchanged(self):
        data = sample_data()
        data.loc[1, ["vCPUs", "MemoryGB", "CpuArchitectureType", "PremiumIO"]] = None
        data.loc[2, "vCPUs"] = "unknown"
        before = data.copy(deep=True)
        result = summarize_families(data).iloc[0]
        self.assertEqual(result["Min vCPU"], 16)
        self.assertEqual(result["CPU Architecture(s)"], "Arm64, x64")
        self.assertEqual(result["Premium IO"], "False, True")
        pd.testing.assert_frame_equal(data, before)

    def test_empty_dataset_preserves_columns(self):
        result = summarize_families(sample_data().iloc[:0])
        self.assertTrue(result.empty)
        self.assertEqual(len(result.columns), 13)

    def test_boolean_premium_values(self):
        data = sample_data()
        data["PremiumIO"] = data["PremiumIO"].map({"True": True, "False": False})
        self.assertEqual(summarize_families(data).iloc[0]["Premium IO"], "False, True")


class FamilyPageTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        self.addCleanup(load_data.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"

    def test_navigation_summary_and_existing_browser_filters(self):
        data = sample_data()
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            app.sidebar.multiselect[0].select("west").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), 2)
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.title[0].value, "Family summary")
            expected = summarize_families(data)
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            self.assertEqual(len(app.sidebar.multiselect), 0)
            self.assertEqual(len(app.get("download_button")), 1)
            app.switch_page("app_pages/sku_browser.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertIn("SKU Explorer", app.title[0].value)

    def test_missing_file_shows_actionable_error(self):
        with patch("vm_sku_data.pd.read_parquet", side_effect=FileNotFoundError):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/family_summary.py").run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("Fetch data", app.error[0].value)
        self.assertEqual(len(app.dataframe), 0)

    def test_region_threshold_is_inclusive_and_download_matches(self):
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=sample_data()),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(app.number_input[0].value, 1)
            self.assertTrue(all(checkbox.value for checkbox in app.checkbox))
            app.number_input[0].set_value(2).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["Family"].tolist(), ["A"])
            csv = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
            self.assertEqual(csv["Family"].tolist(), ["A"])
            self.assertEqual(csv["Region Count"].tolist(), [2])
            self.assertEqual(list(csv.columns), list(app.dataframe[0].value.columns))
            app.number_input[0].set_value(3).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            self.assertEqual(app.info[0].value, "No families match the selected filters.")
            app.number_input[0].set_value(1).run()
            self.assertEqual(app.dataframe[0].value["Family"].tolist(), ["A", "B"])

    def test_version_checkboxes_handle_qualifiers_and_empty_selection(self):
        families = {
            "Other": ["standardBSFamily", "standardNv1Family", "standardDv8Family",
                      "StandardNGADSV620Family"],
            "v2": ["standardDv2Family", "standardDSv2PromoFamily"],
            "v3": ["Standard NCASv3_T4 Family"],
            "v4": ["Standard NDASv4_A100 Family"],
            "v5": ["StandardNVadsV710v5Family"],
            "v6": ["standardNDISRGB200V6NDRFamily", "StandardNCdsRTXPRO6000BSEv6XLFamily"],
            "v7": ["StandardDsv7Family"],
        }
        template = sample_data().iloc[0].to_dict()
        data = pd.DataFrame([
            {**template, "Family": family}
            for group in families.values()
            for family in group
        ])
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual([checkbox.label for checkbox in app.checkbox], list(families))
            self.assertEqual(len(app.dataframe[0].value), len(data))
            for version, expected in families.items():
                with self.subTest(version=version):
                    for checkbox in app.checkbox:
                        checkbox.set_value(checkbox.label == version)
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(
                        app.dataframe[0].value["Family"].tolist(), sorted(expected),
                    )
            for checkbox in app.checkbox:
                checkbox.uncheck()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)

    def test_region_and_version_filters_combine_without_changing_aggregates(self):
        data = sample_data()
        data["Family"] = data["Family"].replace({"A": "standardDv5Family", "B": "standardDv6Family"})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/family_summary.py").run()
            app.number_input[0].set_value(2)
            for checkbox in app.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            expected = summarize_families(data)
            expected = expected[expected["Family"] == "standardDv5Family"]
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            app.checkbox(key="family_version_v5").uncheck()
            app.checkbox(key="family_version_v6").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)


if __name__ == "__main__":
    unittest.main()

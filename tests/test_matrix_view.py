from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from vm_sku_data import load_data, summarize_matrix
from tests.test_vm_sku_data import browser_locations


def matrix_data() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "SkuName": sku, "Region": region, "Family": family,
            "vCPUs": cpu, "MemoryGB": memory, "GPUs": gpu,
            "CpuArchitectureType": architecture, "Tier": "Standard",
            "PremiumIO": "True", "MaxDataDiskCount": "8",
            "vCPUsConstraintsAllowed": "1", "vCPUsPerCore": "1",
            "AvailabilityZones": "1,2,3",
        }
        for sku, region, family, cpu, memory, gpu, architecture in [
            ("A", "east", "standardDv5Family", "2", "4", None, "x64"),
            ("A", "west", "standardDv5Family", "2", "4", None, "x64"),
            ("B", "east", "standardDv5Family", "4", "8", "1", "Arm64"),
            ("C", "west", "standardDsv6Family", "8", "16", "2", "x64"),
            ("D", "east", "standardDsv7Family", "3", "4.5", "0", "x64"),
            ("E", "west", "standardBSFamily", "16", "32", None, "Arm64"),
            ("F", "west", "standardDv5Family", "32", "64", "4", "x64"),
            ("Missing", "east", "standardDv5Family", "8", None, None, "x64"),
        ]
    ])


class MatrixAggregationTests(unittest.TestCase):
    def test_counts_distinct_skus_and_keeps_zero_cells_in_fixed_order(self):
        data = matrix_data()
        before = data.copy(deep=True)
        result = summarize_matrix(data)
        self.assertEqual(result.shape, (11, 14))
        self.assertEqual(result.index.name, "vCPU range")
        self.assertEqual(result.columns.name, "Memory range (GB)")
        self.assertEqual(result.index[:4].tolist(), ["1-2", "3-4", "5-8", "9-16"])
        self.assertEqual(result.columns[:4].tolist(), ["<=4", ">4-8", ">8-16", ">16-32"])
        self.assertEqual(result.loc["1-2", "<=4"], 1)
        self.assertEqual(result.loc["3-4", ">4-8"], 2)
        self.assertEqual(result.loc["5-8", ">8-16"], 1)
        self.assertEqual(result.loc["9-16", ">16-32"], 1)
        self.assertEqual(result.loc["17-32", ">32-64"], 1)
        self.assertEqual(result.loc["1-2", ">16384"], 0)
        self.assertEqual(result.to_numpy().sum(), 6)
        self.assertTrue(all(pd.api.types.is_integer_dtype(dtype) for dtype in result.dtypes))
        pd.testing.assert_frame_equal(data, before)

    def test_exact_boundaries_fractional_memory_and_overflow(self):
        data = pd.DataFrame({
            "SkuName": [f"SKU{i}" for i in range(11)],
            "vCPUs": [1, 2, 3, 4, 5, 8, 9, 16, 17, 1024, 1025],
            "MemoryGB": [0.5, 4, 4.5, 8, 8.5, 16, 16.5, 32, 32.5, 16384, 16385],
        })
        result = summarize_matrix(data)
        for cpu_range, memory_range, count in [
            ("1-2", "<=4", 2),
            ("3-4", ">4-8", 2),
            ("5-8", ">8-16", 2),
            ("9-16", ">16-32", 2),
            ("17-32", ">32-64", 1),
            ("513-1024", ">8192-16384", 1),
            (">1024", ">16384", 1),
        ]:
            with self.subTest(cpu=cpu_range, memory=memory_range):
                self.assertEqual(result.loc[cpu_range, memory_range], count)
        self.assertEqual(result.to_numpy().sum(), 11)

    def test_invalid_values_and_empty_data_produce_zero_counts(self):
        data = pd.DataFrame({
            "SkuName": ["A", "B", "C", "D", "E", "F", None],
            "vCPUs": [None, 0, -1, "invalid", 2, float("inf"), 2],
            "MemoryGB": [4, 4, 4, 4, float("inf"), 4, 4],
        })
        result = summarize_matrix(data)
        self.assertEqual(result.to_numpy().sum(), 0)
        pd.testing.assert_frame_equal(result, summarize_matrix(data.iloc[:0]))


class MatrixPageTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        self.addCleanup(load_data.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
        metadata = patch("region_map_data.load_region_locations", return_value=browser_locations())
        metadata.start()
        self.addCleanup(metadata.stop)

    def test_geography_filter_is_left_of_region_and_updates_region_options_matrix_and_csv(self):
        data = pd.concat([
            matrix_data(),
            matrix_data().iloc[:1].assign(Region="unknown", SkuName="Unassigned"),
        ], ignore_index=True)
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/matrix_view.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                [widget.label for widget in app.multiselect],
                ["Geography Group", "Region", "CPU Architecture Type", "# GPUs"],
            )
            self.assertEqual(
                app.multiselect(key="matrix_geography_groups").options,
                ["Europe", "Not assigned", "US"],
            )
            self.assertEqual(app.multiselect(key="matrix_geography_groups").value, [])
            app.multiselect(key="matrix_region").select("west").run()
            app.multiselect(key="matrix_geography_groups").select("Europe").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.multiselect(key="matrix_region").options, ["east"])
            self.assertEqual(app.multiselect(key="matrix_region").value, [])
            expected = summarize_matrix(data[data["Region"] == "east"])
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            self.assertEqual(download.call_args.kwargs["data"], expected.to_csv().encode("utf-8"))
            app.multiselect(key="matrix_geography_groups").select("Not assigned").run()
            self.assertEqual(len(app.exception), 0)
            expected = summarize_matrix(data[data["Region"].isin(["east", "unknown"])])
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            self.assertEqual(download.call_args.kwargs["data"], expected.to_csv().encode("utf-8"))
            app.multiselect(key="matrix_geography_groups").set_value(["Not assigned"]).run()
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 1)
            app.multiselect(key="matrix_geography_groups").set_value([]).run()
            pd.testing.assert_frame_equal(app.dataframe[0].value, summarize_matrix(data))

    def test_geography_combines_with_family_architecture_gpu_and_region_filters(self):
        data = matrix_data()
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/matrix_view.py").run()
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.multiselect(key="matrix_geography_groups").select("Europe")
            app.multiselect(key="matrix_region").select("east")
            app.multiselect(key="matrix_cpu_arch").select("Arm64")
            app.multiselect(key="matrix_gpu").select("1").run()
            self.assertEqual(len(app.exception), 0)
            pd.testing.assert_frame_equal(
                app.dataframe[0].value, summarize_matrix(data[data["SkuName"] == "B"]),
            )
            self.assertIn(
                "background-color: rgb(59, 130, 246)",
                app.dataframe[0].proto.arrow_data.styler.styles,
            )

    def test_missing_or_invalid_geography_cache_does_not_block_matrix(self):
        for error in (FileNotFoundError(), ValueError("invalid metadata")):
            with (
                self.subTest(error=error),
                patch("vm_sku_data.pd.read_parquet", return_value=matrix_data()),
                patch("region_map_data.load_region_locations", side_effect=error),
            ):
                load_data.clear()
                app = AppTest.from_file(str(self.app_path)).run()
                app.switch_page("app_pages/matrix_view.py").run()
                self.assertEqual(len(app.exception), int(isinstance(error, ValueError)))
                self.assertTrue(app.multiselect(key="matrix_geography_groups").disabled)
                pd.testing.assert_frame_equal(app.dataframe[0].value, summarize_matrix(matrix_data()))
                if isinstance(error, FileNotFoundError):
                    self.assertIn("fetch_azure_locations.py", app.warning[0].value)
                else:
                    self.assertIn("Geography Group filtering is disabled", app.error[0].value)

    def test_filters_combine_and_csv_contains_the_matrix(self):
        data = matrix_data()
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/matrix_view.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.title[0].value, "Matrix View")
            pd.testing.assert_frame_equal(app.dataframe[0].value, summarize_matrix(data))
            for checkbox in app.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 3)
            app.multiselect(key="matrix_region").select("east")
            app.multiselect(key="matrix_cpu_arch").select("x64")
            app.multiselect(key="matrix_gpu").select("Not reported").run()
            self.assertEqual(len(app.exception), 0)
            expected = summarize_matrix(data[(data["SkuName"] == "A") & (data["Region"] == "east")])
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            self.assertEqual(download.call_args.kwargs["file_name"], "vm_type_matrix.csv")
            self.assertEqual(download.call_args.kwargs["data"], expected.to_csv().encode("utf-8"))
            app.multiselect(key="matrix_gpu").unselect("Not reported").select("1").run()
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 0)
            self.assertIn("No VM types", app.info[0].value)
            app.multiselect(key="matrix_cpu_arch").unselect("x64").run()
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 1)
            self.assertEqual(app.dataframe[0].value.loc["3-4", ">4-8"], 1)

    def test_zero_gpu_is_distinct_from_unreported_gpu(self):
        with patch("vm_sku_data.pd.read_parquet", return_value=matrix_data()):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/matrix_view.py").run()
            self.assertEqual(
                app.multiselect(key="matrix_gpu").options, ["0", "1", "2", "4", "Not reported"],
            )
            app.multiselect(key="matrix_gpu").select("0").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 1)
            self.assertEqual(app.dataframe[0].value.loc["3-4", ">4-8"], 1)
            app.multiselect(key="matrix_gpu").unselect("0").select("Not reported").run()
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 2)

    def test_heatmap_colors_scale_to_filtered_counts_and_leave_zero_cells_unstyled(self):
        with patch("vm_sku_data.pd.read_parquet", return_value=matrix_data()):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/matrix_view.py").run()
            self.assertEqual(len(app.exception), 0)
            styles = app.dataframe[0].proto.arrow_data.styler.styles
            self.assertIn("background-color: rgb(59, 130, 246)", styles)
            self.assertIn("background-color: rgb(149, 188, 250)", styles)
            self.assertIn("color: #000000", styles)
            self.assertNotIn("row0_col13", styles)
            app.multiselect(key="matrix_gpu").select("0").run()
            self.assertEqual(len(app.exception), 0)
            styles = app.dataframe[0].proto.arrow_data.styler.styles
            self.assertIn("background-color: rgb(59, 130, 246)", styles)
            self.assertNotIn("background-color: rgb(149, 188, 250)", styles)
            for checkbox in app.checkbox:
                checkbox.uncheck()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 0)
            self.assertNotIn("background-color", app.dataframe[0].proto.arrow_data.styler.styles)

    def test_family_versions_are_shared_across_all_pages_and_survive_return_navigation(self):
        with patch("vm_sku_data.pd.read_parquet", return_value=matrix_data()):
            app = AppTest.from_file(str(self.app_path)).run()
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            app.switch_page("app_pages/matrix_view.py").run()
            self.assertEqual([c.label for c in app.checkbox if c.value], ["v5"])
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 3)
            app.checkbox(key="matrix_family_version_v6").check().run()
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual([c.label for c in app.main.checkbox if c.value], ["v5", "v6"])
            app.checkbox(key="family_version_v5").uncheck().run()
            app.switch_page("app_pages/region_summary.py").run()
            self.assertEqual([c.label for c in app.main.checkbox if c.value], ["v6"])
            self.assertEqual(app.dataframe[0].value["VM SKU Count"].tolist(), [1])
            app.checkbox(key="region_family_version_other").check().run()
            app.switch_page("app_pages/sku_browser.py").run()
            self.assertEqual([c.label for c in app.main.checkbox if c.value], ["Other", "v6"])
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["C", "E"])
            app.switch_page("app_pages/matrix_view.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual([c.label for c in app.checkbox if c.value], ["Other", "v6"])
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 2)
            for checkbox in app.checkbox:
                checkbox.uncheck()
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value.to_numpy().sum(), 0)

    def test_missing_file_shows_actionable_error(self):
        with patch("vm_sku_data.pd.read_parquet", side_effect=FileNotFoundError):
            app = AppTest.from_file(str(self.app_path)).run()
            app.switch_page("app_pages/matrix_view.py").run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("Fetch data", app.error[0].value)
        self.assertEqual(len(app.dataframe), 0)


if __name__ == "__main__":
    unittest.main()

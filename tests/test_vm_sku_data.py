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


def browser_locations():
    return {"value": [
        {"name": " EAST ", "metadata": {"geographyGroup": "Europe"}},
        {"name": "west", "metadata": {"geographyGroup": "US"}},
    ]}


class SkuBrowserTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        self.addCleanup(load_data.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"
        metadata = patch("region_map_data.load_region_locations", return_value=browser_locations())
        metadata.start()
        self.addCleanup(metadata.stop)

    def test_main_versions_filter_results_metrics_and_csv_with_existing_filters(self):
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
                [checkbox.label for checkbox in app.main.checkbox],
                ["Other", "v2", "v3", "v4", "v5", "v6", "v7"],
            )
            self.assertTrue(all(checkbox.value for checkbox in app.main.checkbox))
            self.assertEqual(
                [checkbox.label for checkbox in app.sidebar.checkbox],
                [
                    "With pricing", "With 1Y RI", "With 3Y RI",
                    "Premium IO", "vCPUs Constraints Allowed", "Has GPUs",
                ],
            )
            self.assertEqual(len(app.dataframe[0].value), 5)
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), 4)
            self.assertEqual([metric.value for metric in app.metric], ["4", "2", "2", "1"])
            app.sidebar.multiselect(key="sku_region").select("west")
            app.sidebar.multiselect(key="sku_families").select("standardDv5Family")
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
            app.sidebar.multiselect(key="sku_families").unselect("standardDv5Family").select("standardDv6Family").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)

    def test_other_and_no_selected_versions(self):
        data = sample_data()
        data["Family"] = data["Family"].replace({"A": "standardDv5Family"})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "Other")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["B1"])
            app.main.checkbox(key="sku_family_version_other").uncheck().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            self.assertEqual([metric.value for metric in app.metric], ["0", "0", "0", "0"])
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(any(checkbox.value for checkbox in app.main.checkbox))

    def test_sidebar_branding_is_shared_across_pages(self):
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=sample_data()),
            patch("streamlit.navigation", wraps=st.navigation) as navigation,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.sidebar.header[0].value, "☁️ Azure VM SKU Explorer")
            self.assertEqual(app.sidebar[0].value, "☁️ Azure VM SKU Explorer")
            self.assertEqual(app.sidebar[1].proto.label, "SKU browser")
            self.assertEqual(navigation.call_args.kwargs["position"], "hidden")
            self.assertEqual(
                [link.proto.label for link in app.sidebar.get("page_link")],
                ["SKU browser", "Family summary", "Region Summary", "Region Map", "Matrix View"],
            )
            app.switch_page("app_pages/family_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.sidebar.header[0].value, "☁️ Azure VM SKU Explorer")

    def test_has_gpus_includes_all_positive_counts_and_combines_with_exact_count(self):
        data = sample_data().assign(GPUs=[None, 0, 1, 2, 8])
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(app.sidebar.checkbox(key="sku_has_gpus").value)
            self.assertEqual(len(app.dataframe[0].value), 5)
            app.sidebar.checkbox(key="sku_has_gpus").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["GPUs"].tolist(), [1, 2, 8])
            self.assertEqual([metric.value for metric in app.metric], ["3", "2", "2", "1"])
            self.assertEqual(
                pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))["GPUs"].tolist(),
                [1, 2, 8],
            )
            exact_count = next(widget for widget in app.sidebar.multiselect if widget.label == "GPUs")
            exact_count.select("2.0").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["GPUs"].tolist(), [2])
            app.sidebar.multiselect(key="sku_region").select("west").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)

    def test_has_gpus_excludes_unknown_zero_negative_and_nonfinite_counts(self):
        for values in (
            [None, "unknown", "", -1, float("inf")],
            [0, 0, None, "0", "bad"],
        ):
            with self.subTest(values=values), patch(
                "vm_sku_data.pd.read_parquet", return_value=sample_data().assign(GPUs=values),
            ):
                load_data.clear()
                app = AppTest.from_file(str(self.app_path)).run()
                app.sidebar.checkbox(key="sku_has_gpus").check().run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(app.dataframe[0].value.empty)
                self.assertEqual([metric.value for metric in app.metric], ["0", "0", "0", "0"])

    def test_has_gpus_is_disabled_when_gpu_column_is_missing(self):
        with patch("vm_sku_data.pd.read_parquet", return_value=sample_data().drop(columns="GPUs")):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.sidebar.checkbox(key="sku_has_gpus").disabled)
            self.assertFalse(app.sidebar.checkbox(key="sku_has_gpus").value)
            self.assertEqual(len(app.dataframe[0].value), 5)

    def test_available_price_columns_are_displayed_by_default_and_exported(self):
        for prices in (
            {"retail price": 0.2, "1Y RI": 0.1, "3Y RI": 0.05},
            {"1Y RI": 0},
            {},
        ):
            with (
                self.subTest(columns=list(prices)),
                patch("vm_sku_data.pd.read_parquet", return_value=sample_data().assign(**prices)),
                patch("streamlit.download_button", wraps=st.download_button) as download,
            ):
                load_data.clear()
                app = AppTest.from_file(str(self.app_path)).run()
                self.assertEqual(len(app.exception), 0)
                expected = [column for column in ("retail price", "1Y RI", "3Y RI") if column in prices]
                shown = app.dataframe[0].value
                self.assertEqual([column for column in shown if column in prices], expected)
                self.assertEqual(
                    [column for column in app.main.multiselect[0].value if column in prices],
                    expected,
                )
                exported = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
                self.assertEqual([column for column in exported if column in prices], expected)
                for column, value in prices.items():
                    self.assertTrue(shown[column].eq(value).all())
                    self.assertTrue(exported[column].eq(value).all())
                if expected:
                    app.main.multiselect[0].unselect(expected[0]).run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertNotIn(expected[0], app.dataframe[0].value.columns)
                    self.assertNotIn(
                        expected[0], pd.read_csv(io.BytesIO(download.call_args.kwargs["data"])).columns,
                    )

    def test_gpu_min_max_are_next_to_cpu_and_follow_filters(self):
        with patch("vm_sku_data.pd.read_parquet", return_value=sample_data()):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            labels = {"**vCPUs**", "**GPUs**", "**Memory (GB)**", "**Max Data Disks**"}
            statistics = [
                column for column in app.get("column")
                if column.markdown and column.markdown[0].value in labels
            ]
            self.assertEqual(
                [column.markdown[0].value for column in statistics],
                ["**vCPUs**", "**GPUs**", "**Memory (GB)**", "**Max Data Disks**"],
            )
            for column in statistics:
                self.assertEqual(len(column.markdown), 3)
                self.assertTrue(column.markdown[1].value.startswith("Min: "))
                self.assertTrue(column.markdown[2].value.startswith("Max: "))
            self.assertFalse(any(
                item.value.startswith(("Mean:", "Median:")) for item in app.main.markdown
            ))
            self.assertEqual(
                [item.value for item in statistics[1].markdown],
                ["**GPUs**", "Min: 0", "Max: 2"],
            )
            app.sidebar.multiselect(key="sku_region").select("west").run()
            gpu = next(
                column for column in app.get("column")
                if column.markdown and column.markdown[0].value == "**GPUs**"
            )
            self.assertEqual([item.value for item in gpu.markdown], ["**GPUs**", "Min: 0", "Max: 0"])
            app.sidebar.multiselect(key="sku_families").select("B").run()
            gpu = next(
                column for column in app.get("column")
                if column.markdown and column.markdown[0].value == "**GPUs**"
            )
            self.assertEqual([item.value for item in gpu.markdown], ["**GPUs**", "No data"])

    def test_gpu_statistics_show_no_data_for_missing_column_or_invalid_values(self):
        for data in (
            sample_data().drop(columns="GPUs"),
            sample_data().assign(GPUs=["unknown", None, "", None, "not reported"]),
        ):
            with self.subTest(columns=data.columns.tolist()), patch(
                "vm_sku_data.pd.read_parquet", return_value=data,
            ):
                load_data.clear()
                app = AppTest.from_file(str(self.app_path)).run()
                self.assertEqual(len(app.exception), 0)
                gpu = next(
                    column for column in app.get("column")
                    if column.markdown and column.markdown[0].value == "**GPUs**"
                )
                self.assertEqual([item.value for item in gpu.markdown], ["**GPUs**", "No data"])

    def test_distribution_charts_preserve_descending_sku_count_not_alphabetic_order(self):
        data = sample_data()
        data["Region"] = data["Region"].replace({"east": "zeta", "west": "alpha"})
        data["Family"] = data["Family"].replace({"A": "Z family", "B": "A family"})
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.bar_chart", wraps=st.bar_chart) as charts,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(charts.call_count, 2)
            regions, families = charts.call_args_list
            self.assertEqual(regions.args[0].to_dict(), {"zeta": 3, "alpha": 2})
            self.assertEqual(families.args[0].to_dict(), {"Z family": 4, "A family": 1})
            self.assertEqual(regions.args[0].index.tolist(), ["zeta", "alpha"])
            self.assertEqual(families.args[0].index.tolist(), ["Z family", "A family"])
            self.assertFalse(regions.kwargs["sort"])
            self.assertFalse(families.kwargs["sort"])
            charts.reset_mock()
            app.sidebar.multiselect(key="sku_region").select("alpha").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(charts.call_count, 2)
            self.assertEqual(charts.call_args_list[0].args[0].to_dict(), {"alpha": 2})
            self.assertTrue(all(not call.kwargs["sort"] for call in charts.call_args_list))

    def test_geography_groups_include_unassigned_and_constrain_region_options(self):
        data = pd.concat([
            sample_data(),
            sample_data().iloc[:1].assign(Region="unknown", SkuName="U1"),
        ], ignore_index=True)
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(
                [widget.label for widget in app.sidebar.multiselect][:2],
                ["Geography Group", "Region"],
            )
            self.assertEqual(
                app.sidebar.multiselect(key="sku_geography_groups").options,
                ["Europe", "Not assigned", "US"],
            )
            app.sidebar.multiselect(key="sku_region").select("west").run()
            app.sidebar.multiselect(key="sku_geography_groups").select("Europe").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.sidebar.multiselect(key="sku_region").options, ["east"])
            self.assertEqual(app.sidebar.multiselect(key="sku_region").value, [])
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east"] * 3)
            app.sidebar.multiselect(key="sku_geography_groups").set_value(["Not assigned"]).run()
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["U1"])
            app.main.multiselect[0].select("Geography group").run()
            self.assertEqual(app.dataframe[0].value["Geography group"].tolist(), ["Not assigned"])
            app.sidebar.multiselect(key="sku_geography_groups").select("Europe").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east"] * 3 + ["unknown"])
            csv = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
            self.assertEqual(csv["Geography group"].tolist(), ["Europe"] * 3 + ["Not assigned"])
            app.sidebar.multiselect(key="sku_geography_groups").set_value([]).run()
            self.assertEqual(len(app.dataframe[0].value), len(data))

    def test_pricing_filters_require_each_selected_numeric_price_and_include_zero(self):
        data = sample_data().assign(**{
            "retail price": [None, "0", 0.2, "", "bad"],
            "1Y RI": [1, None, "0", "", 3],
            "3Y RI": [None, 0.02, None, None, 0],
        })
        cases = [
            ((False, False, False), [0, 1, 2, 3, 4]),
            ((True, False, False), [1, 2]),
            ((False, True, False), [0, 2, 4]),
            ((False, False, True), [1, 4]),
            ((True, True, False), [2]),
            ((True, False, True), [1]),
            ((False, True, True), [4]),
            ((True, True, True), []),
        ]
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertTrue(all(not checkbox.value for checkbox in app.sidebar.checkbox))
            self.assertTrue(all(not checkbox.disabled for checkbox in app.sidebar.checkbox))
            for selected, expected in cases:
                with self.subTest(selected=selected):
                    for key, checked in zip(
                        ("sku_with_pricing", "sku_with_1y_ri", "sku_with_3y_ri"), selected,
                    ):
                        app.sidebar.checkbox(key=key).set_value(checked)
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    result = app.dataframe[0].value
                    self.assertEqual(
                        result[["Region", "SkuName"]].to_dict("records"),
                        data.iloc[expected][["Region", "SkuName"]].to_dict("records"),
                    )
                    self.assertEqual(app.metric[0].value, str(len(expected)))
                    pd.testing.assert_frame_equal(
                        pd.read_csv(io.BytesIO(download.call_args.kwargs["data"])),
                        pd.read_csv(io.StringIO(result.to_csv(index=False))),
                    )

    def test_pricing_combines_with_geography_family_versions_and_family_filter(self):
        data = sample_data().assign(**{"retail price": [None, 0, 0.2, 0.3, 0.3]})
        data["Family"] = data["Family"].replace({"A": "standardDv5Family", "B": "standardDv6Family"})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            app.sidebar.multiselect(key="sku_geography_groups").select("US")
            app.sidebar.multiselect(key="sku_families").select("standardDv5Family")
            app.sidebar.checkbox(key="sku_with_pricing").check()
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["A1"])
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["west"])
            self.assertEqual([metric.value for metric in app.metric], ["1", "1", "1", "1"])

    def test_missing_price_columns_disable_only_the_unavailable_filters(self):
        data = sample_data().assign(**{"1Y RI": [None, 0, None, None, None]})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertTrue(app.sidebar.checkbox(key="sku_with_pricing").disabled)
            self.assertFalse(app.sidebar.checkbox(key="sku_with_1y_ri").disabled)
            self.assertTrue(app.sidebar.checkbox(key="sku_with_3y_ri").disabled)
            self.assertTrue(all(not checkbox.value for checkbox in app.sidebar.checkbox))
            self.assertEqual(len(app.dataframe[0].value), len(data))
            app.sidebar.checkbox(key="sku_with_1y_ri").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["A1"])
            self.assertEqual(app.dataframe[0].value["Region"].tolist(), ["east"])

    def test_unavailable_location_cache_is_explicit_without_breaking_browser(self):
        for error in (FileNotFoundError(), ValueError("invalid metadata")):
            with (
                self.subTest(error=error),
                patch("vm_sku_data.pd.read_parquet", return_value=sample_data()),
                patch("region_map_data.load_region_locations", side_effect=error),
            ):
                app = AppTest.from_file(str(self.app_path)).run()
                self.assertTrue(app.sidebar.multiselect(key="sku_geography_groups").disabled)
                self.assertEqual(len(app.dataframe[0].value), 5)
                if isinstance(error, FileNotFoundError):
                    self.assertEqual(len(app.exception), 0)
                    self.assertIn("fetch_azure_locations.py", app.sidebar.warning[0].value)
                else:
                    self.assertEqual(len(app.exception), 1)
                    self.assertIn("Geography Group filtering is disabled", app.sidebar.error[0].value)
                load_data.clear()


    def test_constraint_checkbox_detects_reduction_and_includes_all_when_unchecked(self):
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
            selector = app.sidebar.checkbox(key="sku_vcpu_constraints_allowed")
            self.assertFalse(selector.value)
            self.assertEqual(len(app.dataframe[0].value), len(data))
            selector.check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["Reduced", "Already"])
            self.assertEqual([metric.value for metric in app.metric], ["2", "2", "2", "2"])
            self.assertEqual(
                download.call_args.kwargs["data"],
                app.dataframe[0].value.to_csv(index=False).encode("utf-8"),
            )
            app.sidebar.checkbox(key="sku_vcpu_constraints_allowed").uncheck().run()
            self.assertEqual(len(app.dataframe[0].value), len(data))
            app.sidebar.checkbox(key="sku_vcpu_constraints_allowed").check()
            app.sidebar.multiselect(key="sku_region").select("east").run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["Reduced"])
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v6")
            app.run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            app.sidebar.checkbox(key="sku_vcpu_constraints_allowed").uncheck().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)

    def test_premium_io_checkbox_accepts_reported_true_only_and_combines_with_constraints(self):
        data = sample_data().assign(
            PremiumIO=[True, " true ", "False", None, "unknown"],
            vCPUsConstraintsAllowed=[None, "1,2", None, None, None],
        )
        with (
            patch("vm_sku_data.pd.read_parquet", return_value=data),
            patch("streamlit.download_button", wraps=st.download_button) as download,
        ):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            self.assertFalse(app.sidebar.checkbox(key="sku_premium_io").value)
            self.assertNotIn("Premium IO", [widget.label for widget in app.sidebar.multiselect])
            self.assertEqual(len(app.sidebar.selectbox), 0)
            app.sidebar.checkbox(key="sku_premium_io").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["B1", "A1"])
            self.assertEqual([metric.value for metric in app.metric], ["2", "2", "2", "2"])
            exported = pd.read_csv(io.BytesIO(download.call_args.kwargs["data"]))
            self.assertEqual(exported["SkuName"].tolist(), ["B1", "A1"])
            app.sidebar.checkbox(key="sku_vcpu_constraints_allowed").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.dataframe[0].value["SkuName"].tolist(), ["A1"])
            app.sidebar.checkbox(key="sku_premium_io").uncheck()
            app.sidebar.checkbox(key="sku_vcpu_constraints_allowed").uncheck().run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.dataframe[0].value), len(data))

    def test_capability_checkboxes_are_disabled_if_required_columns_are_absent(self):
        data = sample_data().drop(columns=["PremiumIO", "vCPUsConstraintsAllowed"])
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path)).run()
            self.assertEqual(len(app.exception), 0)
            for key in ("sku_premium_io", "sku_vcpu_constraints_allowed"):
                self.assertTrue(app.sidebar.checkbox(key=key).disabled)
                self.assertFalse(app.sidebar.checkbox(key=key).value)
            self.assertEqual(len(app.dataframe[0].value), len(data))


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
            app.sidebar.multiselect(key="sku_region").select("west").run()
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
            self.assertTrue(all(checkbox.value for checkbox in app.main.checkbox))
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
            self.assertEqual([checkbox.label for checkbox in app.main.checkbox], list(families))
            self.assertEqual(len(app.dataframe[0].value), len(data))
            for version, expected in families.items():
                with self.subTest(version=version):
                    for checkbox in app.main.checkbox:
                        checkbox.set_value(checkbox.label == version)
                    app.run()
                    self.assertEqual(len(app.exception), 0)
                    self.assertEqual(
                        app.dataframe[0].value["Family"].tolist(), sorted(expected),
                    )
            for checkbox in app.main.checkbox:
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
            for checkbox in app.main.checkbox:
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

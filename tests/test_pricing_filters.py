from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from pricing_filters import filter_pricing
from tests.test_region_summary import region_data
from vm_sku_data import NUMERIC_COLUMNS, load_data, summarize_families, summarize_regions


def priced_summary_data():
    data = region_data().assign(**{
        "retail price": [None, 0, None, 0.5, None],
        "1Y RI": [0.1, None, 0.02, 0.3, None],
        "3Y RI": [None, 0.01, 0.01, None, None],
    })
    for column in NUMERIC_COLUMNS:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data


CASES = (
    ([], [0, 1, 2, 3, 4]),
    (["retail price"], [1, 3]),
    (["1Y RI"], [0, 2, 3]),
    (["3Y RI"], [1, 2]),
    (["retail price", "1Y RI"], [3]),
    (["retail price", "3Y RI"], [1]),
    (["1Y RI", "3Y RI"], [2]),
    (["retail price", "1Y RI", "3Y RI"], []),
)
PAGE_CASES = (
    ("family", "app_pages/family_summary.py", summarize_families),
    ("region", "app_pages/region_summary.py", summarize_regions),
)


class PricingFilterTests(unittest.TestCase):
    def test_all_combinations_require_each_price_on_the_same_row(self):
        data = priced_summary_data()
        before = data.copy(deep=True)
        for columns, indices in CASES:
            with self.subTest(columns=columns):
                pd.testing.assert_frame_equal(filter_pricing(data, columns), data.iloc[indices])
        pd.testing.assert_frame_equal(data, before)

    def test_zero_and_numeric_strings_count_but_blanks_invalid_and_null_do_not(self):
        data = pd.DataFrame({"retail price": [0, "0", "0.1", "", "bad", None]})
        self.assertEqual(filter_pricing(data, ["retail price"]).index.tolist(), [0, 1, 2])

    def test_empty_input_preserves_columns(self):
        data = priced_summary_data().iloc[:0]
        pd.testing.assert_frame_equal(filter_pricing(data, ["retail price", "1Y RI"]), data)


class SummaryPricingPageTests(unittest.TestCase):
    def setUp(self):
        load_data.clear()
        self.addCleanup(load_data.clear)
        self.app_path = Path(__file__).resolve().parents[1] / "streamlit_app.py"

    def test_summary_checkboxes_filter_before_aggregation_and_update_csv(self):
        data = priced_summary_data()
        for prefix, page, summarize in PAGE_CASES:
            with (
                self.subTest(page=page),
                patch("vm_sku_data.pd.read_parquet", return_value=data),
                patch("streamlit.download_button", wraps=st.download_button) as download,
            ):
                load_data.clear()
                app = AppTest.from_file(str(self.app_path), default_timeout=10).run()
                app.switch_page(page).run()
                self.assertEqual(len(app.exception), 0)
                self.assertEqual(
                    [checkbox.label for checkbox in app.sidebar.checkbox],
                    ["With pricing", "With 1Y RI", "With 3Y RI"],
                )
                self.assertTrue(all(not checkbox.value for checkbox in app.sidebar.checkbox))
                for required, indices in CASES:
                    with self.subTest(page=page, required=required):
                        for column, suffix in (
                            ("retail price", "with_pricing"),
                            ("1Y RI", "with_1y_ri"),
                            ("3Y RI", "with_3y_ri"),
                        ):
                            app.sidebar.checkbox(key=f"{prefix}_{suffix}").set_value(column in required)
                        app.run()
                        self.assertEqual(len(app.exception), 0)
                        expected = summarize(data.iloc[indices])
                        pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
                        self.assertEqual(
                            download.call_args.kwargs["data"],
                            expected.to_csv(index=False).encode("utf-8"),
                        )
                        if not indices:
                            self.assertIn("match the selected filters", app.info[0].value)

    def test_missing_price_columns_disable_only_unavailable_filters(self):
        data = priced_summary_data().drop(columns=["retail price", "3Y RI"])
        for prefix, page, summarize in PAGE_CASES:
            with self.subTest(page=page), patch("vm_sku_data.pd.read_parquet", return_value=data):
                load_data.clear()
                app = AppTest.from_file(str(self.app_path), default_timeout=10).run()
                app.switch_page(page).run()
                self.assertEqual(len(app.exception), 0)
                self.assertTrue(app.sidebar.checkbox(key=f"{prefix}_with_pricing").disabled)
                self.assertFalse(app.sidebar.checkbox(key=f"{prefix}_with_1y_ri").disabled)
                self.assertTrue(app.sidebar.checkbox(key=f"{prefix}_with_3y_ri").disabled)
                pd.testing.assert_frame_equal(app.dataframe[0].value, summarize(data))
                app.sidebar.checkbox(key=f"{prefix}_with_1y_ri").check().run()
                self.assertEqual(len(app.exception), 0)
                pd.testing.assert_frame_equal(app.dataframe[0].value, summarize(data.iloc[[0, 2, 3]]))

    def test_pricing_recalculates_family_region_threshold_and_capability_ranges(self):
        data = priced_summary_data()
        data["Family"] = data["Family"].replace({"F1": "standardDv5Family", "F2": "standardDv6Family"})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path), default_timeout=10).run()
            app.switch_page("app_pages/family_summary.py").run()
            app.number_input(key="min_family_regions").set_value(2).run()
            self.assertEqual(app.dataframe[0].value["Family"].tolist(), ["standardDv5Family"])
            app.sidebar.checkbox(key="family_with_pricing").check().run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(app.dataframe[0].value.empty)
            app.number_input(key="min_family_regions").set_value(1)
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v6")
            app.run()
            self.assertEqual(len(app.exception), 0)
            expected = summarize_families(data.iloc[[3]])
            pd.testing.assert_frame_equal(app.dataframe[0].value.reset_index(drop=True), expected)
            self.assertEqual(app.dataframe[0].value["Unique SKU Count"].tolist(), [1])
            self.assertEqual(app.dataframe[0].value["Min vCPU"].tolist(), [16])

    def test_summary_pricing_selections_are_page_specific_and_versions_stay_shared(self):
        data = priced_summary_data()
        data["Family"] = data["Family"].replace({"F1": "standardDv5Family", "F2": "standardDv6Family"})
        with patch("vm_sku_data.pd.read_parquet", return_value=data):
            app = AppTest.from_file(str(self.app_path), default_timeout=10).run()
            app.switch_page("app_pages/family_summary.py").run()
            app.sidebar.checkbox(key="family_with_pricing").check()
            for checkbox in app.main.checkbox:
                checkbox.set_value(checkbox.label == "v5")
            app.run()
            app.switch_page("app_pages/region_summary.py").run()
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(all(not checkbox.value for checkbox in app.sidebar.checkbox))
            self.assertEqual([checkbox.label for checkbox in app.main.checkbox if checkbox.value], ["v5"])
            expected = summarize_regions(data[data["Family"] == "standardDv5Family"])
            pd.testing.assert_frame_equal(app.dataframe[0].value, expected)
            app.sidebar.checkbox(key="region_with_1y_ri").check().run()
            self.assertEqual(len(app.exception), 0)
            pd.testing.assert_frame_equal(app.dataframe[0].value, summarize_regions(data.iloc[[0, 2]]))


if __name__ == "__main__":
    unittest.main()

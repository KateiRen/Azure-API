import contextlib
from datetime import datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

import fetch_vm_sku_prices as pricing


def meter(**changes):
    item = {
        "serviceName": "Virtual Machines", "currencyCode": "USD",
        "armRegionName": "eastus", "armSkuName": "Standard_D2s_v5",
        "productName": "Virtual Machines Dsv5 Series", "meterName": "D2s v5",
        "skuName": "D2s v5", "isPrimaryMeterRegion": True, "unitOfMeasure": "1 Hour",
        "type": "Consumption", "retailPrice": 0.096, "tierMinimumUnits": 0,
        "effectiveStartDate": "2021-11-01T00:00:00Z",
    }
    return item | changes


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.data = pd.DataFrame({
            "Region": ["eastus", "eastus", "westus"],
            "SkuName": ["Standard_D2s_v5"] * 3, "Family": ["Dsv5"] * 3,
        })
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def select(self, items):
        return pricing.select_prices(items, "eastus", {"standard_d2s_v5"}, self.now)

    def test_hourly_conversion_and_no_savings_plan_substitution(self):
        prices, conflicts = self.select([
            meter(savingsPlan=[{"term": "1 Year", "retailPrice": 0.001}]),
            meter(type="Reservation", reservationTerm="1 Year", retailPrice=519),
            meter(type="Reservation", reservationTerm="3 Years", retailPrice=997),
        ])
        self.assertEqual(prices[("standard_d2s_v5", "retail price")], 0.096)
        self.assertAlmostEqual(prices[("standard_d2s_v5", "1Y RI")], 519 / 8760)
        self.assertAlmostEqual(prices[("standard_d2s_v5", "3Y RI")], 997 / 26280)
        self.assertEqual(conflicts, 0)

    def test_excludes_nonmatching_and_nonstandard_meters(self):
        changes = [
            {"productName": "Virtual Machines Dsv5 Series Windows"},
            {"meterName": "D2s v5 Spot"}, {"skuName": "D2s v5 Low Priority"},
            {"type": "DevTestConsumption"}, {"type": "Reservation", "reservationTerm": "5 Years"},
            {"serviceName": "SQL Database"}, {"currencyCode": "EUR"},
            {"isPrimaryMeterRegion": False}, {"unitOfMeasure": "1 Month"},
            {"tierMinimumUnits": 1}, {"armRegionName": "westus"},
            {"armSkuName": "Standard_D2_v5"}, {"effectiveStartDate": "2099-01-01T00:00:00Z"},
        ]
        self.assertEqual(self.select([meter(**change) for change in changes]), ({}, 0))

    def test_newest_effective_price_and_conflicts(self):
        latest = meter(retailPrice=0.1, effectiveStartDate="2026-01-01T00:00:00Z")
        self.assertEqual(self.select([meter(), latest, latest])[0][
            ("standard_d2s_v5", "retail price")
        ], 0.1)
        self.assertEqual(self.select([latest, latest | {"retailPrice": 0.2}]), ({}, 1))
        self.assertIn("WARNING", self.output.getvalue())

    def test_cloud_services_and_abbreviated_windows_are_not_vm_base_prices(self):
        for product in ("Basv2 Series Cloud Services", "Easv5 Series CloudServices",
                        "Virtual Machines NCCadsv5 Srs Win"):
            with self.subTest(product=product):
                prices, conflicts = self.select([
                    meter(retailPrice=0.0556),
                    meter(productName=product, retailPrice=0.148,
                          effectiveStartDate="2026-01-01T00:00:00Z"),
                ])
                self.assertEqual(prices[("standard_d2s_v5", "retail price")], 0.0556)
                self.assertEqual(conflicts, 0)
        prices, _ = self.select([meter(productName="Lasv3 Series Linux")])
        self.assertEqual(prices[("standard_d2s_v5", "retail price")], 0.096)

    def test_update_reprices_populated_wrong_product_values(self):
        data = self.data.assign(**{"retail price": 0.148})
        with patch.object(pricing, "fetch_region_prices", side_effect=[[
            meter(retailPrice=0.0556),
            meter(productName="Dasv5 Series Cloud Services", retailPrice=0.148,
                  effectiveStartDate="2026-01-01T00:00:00Z"),
        ], []]):
            result, _ = pricing.enrich_prices(data, Mock(), preserve_existing=True)
        self.assertEqual(result["retail price"].tolist(), [0.0556, 0.0556, 0.148])

    def test_bad_metadata_fails_explicitly(self):
        for change in ({"retailPrice": -1}, {"retailPrice": float("nan")},
                       {"effectiveStartDate": "bad"}, {"productName": None}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.select([meter(**change)])

    def test_paging_parameters_and_progress(self):
        session = Mock()
        next_url = pricing.API_URL + "?$skip=1000"
        first, second = Mock(), Mock()
        first.json.return_value = {"Items": [meter()], "NextPageLink": next_url}
        second.json.return_value = {"Items": [], "NextPageLink": None}
        session.get.side_effect = [first, second]
        self.assertEqual(pricing.fetch_region_prices(session, "eastus"), [meter()])
        self.assertIn("armRegionName eq 'eastus'", session.get.call_args_list[0].kwargs["params"]["$filter"])
        self.assertIsNone(session.get.call_args_list[1].kwargs["params"])
        self.assertIn("Page 2", self.output.getvalue())
        first.raise_for_status.assert_called_once()

    def test_invalid_paging_and_http_failure(self):
        for payload in ({}, {"Items": []}, {"Items": [None], "NextPageLink": None},
                        {"Items": [], "NextPageLink": 3},
                        {"Items": [], "NextPageLink": "https://example.com/prices"},
                        {"Items": [], "NextPageLink": pricing.API_URL}):
            with self.subTest(payload=payload):
                session = Mock()
                session.get.return_value.json.return_value = payload
                with self.assertRaises(ValueError):
                    pricing.fetch_region_prices(session, "eastus")
        session = Mock()
        session.get.return_value.raise_for_status.side_effect = requests.HTTPError("503")
        with self.assertRaises(requests.HTTPError):
            pricing.fetch_region_prices(session, "eastus")

    def test_pagination_accepts_explicit_https_port(self):
        session = Mock()
        next_url = "https://prices.azure.com:443/api/retail/prices?$skip=1000"
        first, second = Mock(), Mock()
        first.json.return_value = {"Items": [meter()], "NextPageLink": next_url}
        second.json.return_value = {"Items": [meter()], "NextPageLink": None}
        session.get.side_effect = [first, second]
        self.assertEqual(len(pricing.fetch_region_prices(session, "australiacentral")), 2)
        self.assertEqual(session.get.call_args_list[1].args[0], next_url)
        self.assertIsNone(session.get.call_args_list[1].kwargs["params"])

    def test_pagination_rejects_other_hosts_ports_and_credentials(self):
        for url in (
            "http://prices.azure.com/api/retail/prices",
            "https://prices.azure.com:444/api/retail/prices",
            "https://prices.azure.com.example.com/api/retail/prices",
            "https://user:password@prices.azure.com/api/retail/prices",
        ):
            with self.subTest(url=url):
                session = Mock()
                session.get.return_value.json.return_value = {"Items": [], "NextPageLink": url}
                with self.assertRaises(ValueError):
                    pricing.fetch_region_prices(session, "eastus")
                self.assertEqual(session.get.call_count, 1)

    def test_enrichment_preserves_rows_and_missing_is_not_zero(self):
        before = self.data.copy(deep=True)
        with patch.object(pricing, "fetch_region_prices", side_effect=[[meter(retailPrice=0)], []]) as fetch:
            enriched, conflicts = pricing.enrich_prices(self.data, Mock())
        pd.testing.assert_frame_equal(before, self.data)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(enriched["retail price"].iloc[:2].tolist(), [0, 0])
        self.assertTrue(pd.isna(enriched["retail price"].iloc[2]))
        self.assertTrue(enriched["1Y RI"].isna().all())
        self.assertEqual(list(enriched.columns), list(self.data.columns) + list(pricing.PRICE_COLUMNS))
        pricing.print_summary(enriched, conflicts, 1)
        text = self.output.getvalue()
        self.assertIn("retail price: 1/2 (50.00%)", text)
        self.assertIn("1Y RI among retail-matched regional SKUs: 0/1 (0.00%)", text)

    def test_invalid_input(self):
        for data in (pd.DataFrame(), self.data.assign(Region=None), self.data.iloc[:0]):
            with self.subTest(), self.assertRaises(ValueError):
                pricing.enrich_prices(data, Mock())

    def test_no_prices_summary_and_case_insensitive_exact_keys(self):
        with patch.object(pricing, "fetch_region_prices", return_value=[]):
            enriched, conflicts = pricing.enrich_prices(self.data, Mock())
        pricing.print_summary(enriched, conflicts, 1)
        self.assertIn("0/2 (0.00%)", self.output.getvalue())
        self.assertIn("0/0 (N/A)", self.output.getvalue())
        prices, _ = self.select([meter(armSkuName="STANDARD_D2S_V5", armRegionName="EASTUS")])
        self.assertIn(("standard_d2s_v5", "retail price"), prices)

    def test_nonparquet_input_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            pricing.main(["--input", "skus.csv"])
        self.assertEqual(error.exception.code, 2)

    def test_main_updates_both_files_and_rerun_replaces_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            csv = parquet.with_suffix(".csv")
            self.data.assign(**{"retail price": 999}).to_parquet(parquet)
            csv.write_text("old", encoding="utf-8")
            with (
                patch("builtins.input", return_value="1"),
                patch.object(pricing, "create_session"),
                patch.object(pricing, "fetch_region_prices", side_effect=[[meter()], []]),
            ):
                self.assertEqual(pricing.main(["--input", str(parquet)]), 0)
            actual = pd.read_parquet(parquet)
            self.assertEqual(actual.columns.tolist().count("retail price"), 1)
            self.assertEqual(actual["retail price"].iloc[0], 0.096)
            self.assertTrue(pd.isna(actual["retail price"].iloc[2]))
            self.assertTrue(actual["1Y RI"].isna().all())
            self.assertTrue(actual["3Y RI"].isna().all())
            self.assertEqual(pd.read_csv(csv).shape, actual.shape)
            report = pd.read_csv(parquet.with_name("skus_pricing_diagnostics.csv"))
            self.assertEqual(report.columns.tolist(), list(pricing.DIAGNOSTIC_COLUMNS))
            self.assertEqual(set(report["Status"]), {"no_api_match", "no_eligible_price"})
            audit = pd.read_csv(parquet.with_name("skus_pricing_provenance.csv"))
            self.assertEqual(audit.columns.tolist(), list(pricing.PROVENANCE_COLUMNS))
            self.assertEqual(audit["Status"].tolist(), ["fresh_primary"])
            self.assertEqual(len(list(Path(directory).iterdir())), 4)

    def test_prompt_handles_partial_columns_invalid_answers_and_default(self):
        with patch("builtins.input") as prompt:
            self.assertFalse(pricing.choose_preserve_existing(self.data))
            prompt.assert_not_called()
        for answer, expected in (("1", False), ("2", True), ("", True)):
            with self.subTest(answer=answer), patch(
                "builtins.input", side_effect=["invalid", answer],
            ) as prompt:
                self.assertEqual(
                    pricing.choose_preserve_existing(self.data.assign(**{"1Y RI": 1})), expected,
                )
                self.assertEqual(prompt.call_count, 2)
        self.assertIn("Please choose 1 or 2.", self.output.getvalue())

    def test_update_preserves_missing_and_conflicting_prices_and_accepts_zero(self):
        data = self.data.assign(**{"retail price": [8, 9, 10], "1Y RI": [1, 2, 3]})
        before = data.copy(deep=True)
        items = [
            meter(retailPrice=0),
            meter(type="Reservation", reservationTerm="1 Year", retailPrice=100),
            meter(type="Reservation", reservationTerm="1 Year", retailPrice=200),
            meter(type="Reservation", reservationTerm="3 Years", retailPrice=997),
        ]
        diagnostics = []
        with patch.object(pricing, "fetch_region_prices", side_effect=[items, []]):
            result, conflicts = pricing.enrich_prices(
                data, Mock(), preserve_existing=True, diagnostics=diagnostics,
            )
        pd.testing.assert_frame_equal(data, before)
        self.assertEqual(result["retail price"].tolist(), [0, 0, 10])
        self.assertEqual(result["1Y RI"].tolist(), [1, 2, 3])
        self.assertAlmostEqual(result["3Y RI"].iloc[0], 997 / 26280)
        self.assertTrue(pd.isna(result["3Y RI"].iloc[2]))
        self.assertEqual(conflicts, 1)
        self.assertIn("Matched retail prices: 0/1 rows", self.output.getvalue())
        conflict_rows = [row for row in diagnostics if row["Status"] == "conflict"]
        self.assertEqual(len(conflict_rows), 2)
        self.assertEqual({row["retailPrice"] for row in conflict_rows}, {100, 200})
        self.assertTrue(all(row["ExistingPriceRetained"] for row in conflict_rows))
        self.assertTrue(all(row["SkuName"] == "Standard_D2s_v5" for row in diagnostics))

    def test_main_update_preserves_unmatched_prices_in_both_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            self.data.assign(**{"retail price": 999, "1Y RI": 111}).to_parquet(parquet)
            with (
                patch("builtins.input", return_value="2") as prompt,
                patch.object(pricing, "create_session"),
                patch.object(pricing, "fetch_region_prices", side_effect=[[meter()], []]),
            ):
                self.assertEqual(pricing.main(["--input", str(parquet)]), 0)
            prompt.assert_called_once()
            for actual in (pd.read_parquet(parquet), pd.read_csv(parquet.with_suffix(".csv"))):
                self.assertEqual(actual["retail price"].tolist(), [0.096, 0.096, 999])
                self.assertEqual(actual["1Y RI"].tolist(), [111, 111, 111])
                self.assertTrue(actual["3Y RI"].isna().all())

    def test_unavailable_prompt_preserves_files_without_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            csv = parquet.with_suffix(".csv")
            self.data.assign(**{"retail price": 999}).to_parquet(parquet)
            original = parquet.read_bytes()
            csv.write_bytes(b"original csv")
            with (
                patch("builtins.input", side_effect=EOFError),
                patch.object(pricing, "create_session") as session,
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(pricing.main(["--input", str(parquet)]), 1)
            session.assert_not_called()
            self.assertIn("No pricing mode received", errors.getvalue())
            self.assertEqual(parquet.read_bytes(), original)
            self.assertEqual(csv.read_bytes(), b"original csv")

    def test_fetch_or_serialization_failure_keeps_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            csv = parquet.with_suffix(".csv")
            self.data.to_parquet(parquet)
            csv.write_bytes(b"original")
            original = parquet.read_bytes()
            with (
                patch.object(pricing, "create_session"),
                patch.object(pricing, "fetch_region_prices", side_effect=requests.Timeout("timed out")),
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(pricing.main(["--input", str(parquet)]), 1)
            self.assertIn("timed out", errors.getvalue())
            with patch.object(pd.DataFrame, "to_csv", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    pricing.write_exports(self.data, parquet, csv)
            self.assertEqual(parquet.read_bytes(), original)
            self.assertEqual(csv.read_bytes(), b"original")
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_csv_replace_failure_rolls_back_parquet(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            csv = parquet.with_suffix(".csv")
            self.data.to_parquet(parquet)
            csv.write_bytes(b"original")
            original = parquet.read_bytes()
            replace = pricing.os.replace

            def replace_except_csv(source, target):
                if target == csv:
                    raise PermissionError("CSV is open")
                replace(source, target)

            with patch.object(pricing.os, "replace", side_effect=replace_except_csv):
                with self.assertRaisesRegex(PermissionError, "CSV is open"):
                    pricing.write_exports(self.data.assign(**{"retail price": 1}), parquet, csv)
            self.assertEqual(parquet.read_bytes(), original)
            self.assertEqual(csv.read_bytes(), b"original")
            self.assertEqual(len(list(Path(directory).iterdir())), 2)

    def test_diagnostics_include_only_latest_conflicting_candidates(self):
        diagnostics = []
        latest = "2026-01-01T00:00:00Z"
        first = meter(retailPrice=0.1, effectiveStartDate=latest, meterId="first", skuId="offer1")
        second = meter(retailPrice=0.2, effectiveStartDate=latest, meterId="second", skuId="offer2")
        pricing.select_prices(
            [meter(), first, second, meter(meterName="D2s v5 Spot")],
            "eastus", {"standard_d2s_v5"}, self.now, diagnostics=diagnostics,
        )
        conflicts = [row for row in diagnostics if row["Status"] == "conflict"]
        self.assertEqual(len(conflicts), 2)
        self.assertEqual({row["meterId"] for row in conflicts}, {"first", "second"})
        self.assertEqual({row["skuId"] for row in conflicts}, {"offer1", "offer2"})
        self.assertEqual({row["CandidateHourlyPrice"] for row in conflicts}, {0.1, 0.2})
        self.assertTrue(all(row["effectiveStartDate"] == latest for row in conflicts))

    def test_diagnostics_explain_exclusions_and_absent_terms(self):
        diagnostics = []
        pricing.select_prices(
            [
                meter(productName="Virtual Machines Windows"),
                meter(meterName="D2s v5 Spot"),
                meter(type="DevTestConsumption"),
                meter(currencyCode="EUR"),
                meter(effectiveStartDate="2099-01-01T00:00:00Z"),
                meter(tierMinimumUnits=1),
                meter(unitOfMeasure="1 Month"),
                meter(isPrimaryMeterRegion=False),
                meter(armSkuName="unrelated"),
            ],
            "eastus", {"standard_d2s_v5", "missing"}, self.now, diagnostics=diagnostics,
        )
        missing = [row for row in diagnostics if row["SkuName"] == "missing"]
        self.assertEqual(len(missing), 3)
        self.assertTrue(all(row["Status"] == "no_api_match" for row in missing))
        reasons = {row["Reason"] for row in diagnostics if row["PriceColumn"] == "retail price"}
        for reason in (
            "Excluded offer: windows", "Excluded offer: spot", "Currency is not USD",
            "Unsupported price type or reservation term", "Price is not yet effective",
            "Nonzero tierMinimumUnits", "Unit is not 1 Hour", "Not a primary meter",
        ):
            self.assertIn(reason, reasons)
        term_diagnostics = []
        pricing.select_prices(
            [meter()], "eastus", {"standard_d2s_v5"}, self.now, diagnostics=term_diagnostics,
        )
        self.assertEqual({row["PriceColumn"] for row in term_diagnostics}, {"1Y RI", "3Y RI"})
        self.assertTrue(all("Eligible only for retail price" in row["Reason"] for row in term_diagnostics))

    def test_diagnostics_empty_report_has_headers_and_duplicate_rows_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.csv"
            row = {"Region": "eastus", "SkuName": "example", "Status": "no_api_match"}
            self.assertEqual(pricing.write_diagnostics([row, row], path), 1)
            self.assertEqual(len(pd.read_csv(path)), 1)
            self.assertEqual(pricing.write_diagnostics([], path), 0)
            report = pd.read_csv(path)
            self.assertTrue(report.empty)
            self.assertEqual(report.columns.tolist(), list(pricing.DIAGNOSTIC_COLUMNS))
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_diagnostic_write_failure_keeps_existing_report_and_data(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            csv = parquet.with_suffix(".csv")
            self.data.to_parquet(parquet)
            csv.write_bytes(b"original csv")
            original = parquet.read_bytes()
            report = parquet.with_name("skus_pricing_diagnostics.csv")
            report.write_bytes(b"previous report")
            with (
                patch.object(pricing, "create_session"),
                patch.object(pricing, "fetch_region_prices", return_value=[]),
                patch.object(pd.DataFrame, "to_csv", side_effect=PermissionError("report locked")),
                contextlib.redirect_stderr(io.StringIO()) as errors,
            ):
                self.assertEqual(pricing.main(["--input", str(parquet)]), 1)
            self.assertIn("report locked", errors.getvalue())
            self.assertEqual(parquet.read_bytes(), original)
            self.assertEqual(csv.read_bytes(), b"original csv")
            self.assertEqual(report.read_bytes(), b"previous report")
            self.assertEqual(len(list(Path(directory).iterdir())), 3)

    def test_zero_price_is_retained_but_warned_in_diagnostics_and_provenance(self):
        diagnostics, provenance = [], []
        with patch.object(pricing, "fetch_region_prices", side_effect=[[
            meter(retailPrice=0, productId="product", skuId="offer", meterId="meter"),
        ], []]):
            result, _ = pricing.enrich_prices(
                self.data.assign(**{"retail price": 10}), Mock(), preserve_existing=True,
                diagnostics=diagnostics, provenance=provenance,
            )
        self.assertEqual(result["retail price"].tolist(), [0, 0, 10])
        zero = [row for row in diagnostics if row["Status"] == "zero_price_verify"]
        self.assertEqual(len(zero), 1)
        self.assertFalse(zero[0]["ExistingPriceRetained"])
        selected = [row for row in provenance if row["Status"] == "fresh_primary"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["meterId"], "meter")
        self.assertEqual(selected[0]["skuId"], "offer")
        self.assertEqual(selected[0]["RuleVersion"], pricing.PRICING_RULE_VERSION)
        self.assertIn("Zero price", selected[0]["Warning"])
        retained = [row for row in provenance if row["Status"] == "retained"]
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0]["SourceStatus"], "unknown")
        self.assertNotIn("meterId", retained[0])
        self.assertIn("WARNING", self.output.getvalue())

    def test_retained_provenance_keeps_original_source_and_fetch_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            self.data.to_parquet(parquet)
            provenance = []
            with patch.object(pricing, "fetch_region_prices", side_effect=[[
                meter(meterId="original-meter", effectiveStartDate="2021-01-01T00:00:00Z"),
            ], []]):
                first, _ = pricing.enrich_prices(self.data, Mock(), provenance=provenance)
            path = parquet.with_name("provenance.csv")
            pricing.write_exports(first, parquet, parquet.with_suffix(".csv"),
                                  provenance=provenance, provenance_path=path)
            previous = pricing.load_provenance(path)
            audit = []
            with patch.object(pricing, "fetch_region_prices", return_value=[]):
                result, _ = pricing.enrich_prices(
                    first, Mock(), preserve_existing=True, provenance=audit,
                    previous_provenance=previous,
                )
            self.assertEqual(len(audit), 1)
            self.assertEqual(audit[0]["Status"], "retained")
            self.assertEqual(audit[0]["SourceStatus"], "fresh_primary")
            self.assertEqual(audit[0]["FetchedAt"], provenance[0]["FetchedAt"])
            self.assertEqual(audit[0]["meterId"], "original-meter")
            self.assertEqual(audit[0]["RuleVersion"], pricing.PRICING_RULE_VERSION)
            self.assertTrue(audit[0]["LastCheckedAt"] >= audit[0]["FetchedAt"])
            pd.testing.assert_frame_equal(first, result)
            # An externally changed value must not inherit the old source metadata.
            audit = []
            with patch.object(pricing, "fetch_region_prices", return_value=[]):
                pricing.enrich_prices(
                    first.assign(**{"retail price": 1}), Mock(), preserve_existing=True,
                    provenance=audit, previous_provenance=previous,
                )
            self.assertTrue(all(row["SourceStatus"] == "unknown" for row in audit))

    def test_ri_provenance_round_trip_does_not_lose_exact_price_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            self.data.to_parquet(parquet)
            provenance = []
            with patch.object(pricing, "fetch_region_prices", side_effect=[[
                meter(type="Reservation", reservationTerm="1 Year", retailPrice=519, meterId="RI"),
            ], []]):
                first, _ = pricing.enrich_prices(self.data, Mock(), provenance=provenance)
            path = parquet.with_name("provenance.csv")
            pricing.write_exports(first, parquet, parquet.with_suffix(".csv"),
                                  provenance=provenance, provenance_path=path)
            previous = pricing.load_provenance(path)
            price = first["1Y RI"].iloc[0]
            self.assertIn(("eastus", "standard_d2s_v5", "1Y RI", price), previous)
            audit = []
            with patch.object(pricing, "fetch_region_prices", return_value=[]):
                pricing.enrich_prices(first, Mock(), preserve_existing=True,
                                      provenance=audit, previous_provenance=previous)
            self.assertEqual(audit[0]["meterId"], "RI")
            self.assertEqual(audit[0]["SourceStatus"], "fresh_primary")

    def test_identical_prices_have_deterministic_source_selection(self):
        records = [meter(meterId="second", skuId="b"), meter(meterId="first", skuId="a")]
        for items in (records, records[::-1]):
            provenance = []
            pricing.select_prices(items, "eastus", {"standard_d2s_v5"}, self.now,
                                  provenance=provenance)
            self.assertEqual(provenance[0]["meterId"], "first")
            self.assertEqual(provenance[0]["EquivalentOfferCount"], 2)

    def test_provenance_replacement_failure_rolls_back_both_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            csv = parquet.with_suffix(".csv")
            path = parquet.with_name("provenance.csv")
            self.data.to_parquet(parquet)
            original = parquet.read_bytes()
            csv.write_bytes(b"original csv")
            path.write_bytes(b"original provenance")
            replace = pricing.os.replace

            def replace_except_provenance(source, target):
                if target == path:
                    raise PermissionError("provenance locked")
                replace(source, target)

            with patch.object(pricing.os, "replace", side_effect=replace_except_provenance):
                with self.assertRaisesRegex(PermissionError, "provenance locked"):
                    pricing.write_exports(self.data.assign(**{"retail price": 1}), parquet, csv,
                                          provenance=[], provenance_path=path)
            self.assertEqual(parquet.read_bytes(), original)
            self.assertEqual(csv.read_bytes(), b"original csv")
            self.assertEqual(path.read_bytes(), b"original provenance")
            self.assertEqual(len(list(Path(directory).iterdir())), 3)

    def test_empty_provenance_and_invalid_prior_report(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            self.data.to_parquet(parquet)
            path = parquet.with_name("provenance.csv")
            pricing.write_exports(self.data, parquet, parquet.with_suffix(".csv"),
                                  provenance=[], provenance_path=path)
            self.assertEqual(pricing.load_provenance(path), {})
            path.write_text("invalid\nvalue\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing columns"):
                pricing.load_provenance(path)

    def test_nonprimary_is_opt_in_and_primary_always_has_priority(self):
        secondary = meter(isPrimaryMeterRegion=False, retailPrice=0.01,
                          effectiveStartDate="2026-01-01T00:00:00Z")
        self.assertEqual(self.select([secondary]), ({}, 0))
        provenance = []
        prices, _ = pricing.select_prices(
            [secondary, meter()], "eastus", {"standard_d2s_v5"}, self.now,
            allow_nonprimary=True, provenance=provenance,
        )
        self.assertEqual(prices[("standard_d2s_v5", "retail price")], 0.096)
        self.assertEqual(provenance[0]["Status"], "fresh_primary")
        self.assertTrue(provenance[0]["isPrimaryMeterRegion"])
        provenance = []
        prices, _ = pricing.select_prices(
            [secondary], "eastus", {"standard_d2s_v5"}, self.now,
            allow_nonprimary=True, provenance=provenance,
        )
        self.assertEqual(prices[("standard_d2s_v5", "retail price")], 0.01)
        self.assertEqual(provenance[0]["Status"], "fresh_nonprimary")
        self.assertFalse(provenance[0]["isPrimaryMeterRegion"])

    def test_primary_conflict_is_not_bypassed_by_nonprimary_price(self):
        diagnostics = []
        prices, conflicts = pricing.select_prices(
            [meter(), meter(retailPrice=0.2), meter(isPrimaryMeterRegion=False, retailPrice=0.01)],
            "eastus", {"standard_d2s_v5"}, self.now,
            allow_nonprimary=True, diagnostics=diagnostics,
        )
        self.assertEqual(prices, {})
        self.assertEqual(conflicts, 1)
        rows = [row for row in diagnostics if row["Status"] == "conflict"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["isPrimaryMeterRegion"] for row in rows))

    def test_nonprimary_conflicts_are_not_resolved_by_minimum_price(self):
        diagnostics = []
        prices, conflicts = pricing.select_prices(
            [meter(isPrimaryMeterRegion=False, retailPrice=0.1),
             meter(isPrimaryMeterRegion=False, retailPrice=0.2)],
            "eastus", {"standard_d2s_v5"}, self.now,
            allow_nonprimary=True, diagnostics=diagnostics,
        )
        self.assertEqual(prices, {})
        self.assertEqual(conflicts, 1)
        self.assertEqual(len([row for row in diagnostics if row["Status"] == "conflict"]), 2)

    def test_nonprimary_fallback_keeps_offer_and_exact_identity_restrictions(self):
        records = [
            meter(isPrimaryMeterRegion=False, productName="Dasv5 Series CloudServices"),
            meter(isPrimaryMeterRegion=False, productName="Virtual Machines Srs Win"),
            meter(isPrimaryMeterRegion=False, meterName="D2s v5 Spot"),
            meter(isPrimaryMeterRegion=False, type="DevTestConsumption"),
            meter(isPrimaryMeterRegion=False, armRegionName="westus"),
            meter(isPrimaryMeterRegion=False, armSkuName="Standard_D2_v5"),
            meter(isPrimaryMeterRegion=False, effectiveStartDate="2099-01-01T00:00:00Z"),
            meter(isPrimaryMeterRegion=None),
        ]
        self.assertEqual(pricing.select_prices(
            records, "eastus", {"standard_d2s_v5"}, self.now, allow_nonprimary=True,
        ), ({}, 0))

    def test_fallback_policy_applies_independently_to_each_price_column(self):
        provenance = []
        prices, _ = pricing.select_prices(
            [
                meter(),
                meter(isPrimaryMeterRegion=False, type="Reservation",
                      reservationTerm="1 Year", retailPrice=519),
                meter(isPrimaryMeterRegion=False, type="Reservation",
                      reservationTerm="3 Years", retailPrice=997),
            ], "eastus", {"standard_d2s_v5"}, self.now,
            allow_nonprimary=True, provenance=provenance,
        )
        self.assertEqual(len(prices), 3)
        statuses = {row["PriceColumn"]: row["Status"] for row in provenance}
        self.assertEqual(statuses, {
            "retail price": "fresh_primary", "1Y RI": "fresh_nonprimary", "3Y RI": "fresh_nonprimary",
        })
        self.assertAlmostEqual(prices[("standard_d2s_v5", "1Y RI")], 519 / 8760)

    def test_fallback_fetch_requests_all_meters(self):
        session = Mock()
        session.get.return_value.json.return_value = {"Items": [], "NextPageLink": None}
        pricing.fetch_region_prices(session, "eastus", allow_nonprimary=True)
        params = session.get.call_args.kwargs["params"]
        self.assertNotIn("meterRegion", params)
        self.assertIn("armRegionName eq 'eastus'", params["$filter"])

    def test_main_fallback_option_is_wired_through_to_stored_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            parquet = Path(directory) / "skus.parquet"
            self.data.to_parquet(parquet)
            with (
                patch.object(pricing, "create_session"),
                patch.object(pricing, "fetch_region_prices", side_effect=[[
                    meter(isPrimaryMeterRegion=False),
                ], []]) as fetch,
            ):
                self.assertEqual(pricing.main(["--input", str(parquet), "--allow-nonprimary"]), 0)
            self.assertTrue(all(call.kwargs["allow_nonprimary"] for call in fetch.call_args_list))
            self.assertEqual(pd.read_parquet(parquet)["retail price"].iloc[0], 0.096)
            report = pd.read_csv(parquet.with_name("skus_pricing_provenance.csv"))
            self.assertEqual(report["Status"].tolist(), ["fresh_nonprimary"])
            self.assertFalse(report["isPrimaryMeterRegion"].iloc[0])


if __name__ == "__main__":
    unittest.main()

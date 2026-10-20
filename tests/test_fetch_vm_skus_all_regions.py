import contextlib
import io
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "fetch_vm_skus_all_regions.py"


class ExistingDataTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.temp_dir.name)
        self.addCleanup(os.chdir, self.cwd)
        self.csv = Path("data") / "vm_skus_all_regions.csv"
        self.parquet = Path("data") / "vm_skus_all_regions.parquet"
        self.csv.parent.mkdir()
        self.csv.write_text("old csv", encoding="utf-8")
        self.parquet.write_bytes(b"old parquet")
        self.now = time.time()
        for path in (self.csv, self.parquet):
            os.utime(path, (self.now - 172800, self.now - 172800))

        self.msal = Mock()
        self.msal.ConfidentialClientApplication.return_value.acquire_token_for_client.return_value = {
            "access_token": "test-token"
        }
        self.requests = Mock()
        self.requests.get.return_value.json.return_value = {
            "value": [{
                "resourceType": "virtualMachines",
                "name": "Standard_Test",
                "locations": ["eastus"],
                "locationInfo": [{"location": "eastus", "zones": ["1"]}],
            }]
        }
        self.pandas = Mock()
        self.pandas.DataFrame.return_value.to_parquet.side_effect = (
            lambda path, **kwargs: Path(path).write_bytes(b"new parquet")
        )
        self.dotenv = Mock()
        self.output = io.StringIO()

    def run_script(self, answers=(), args=()):
        with (
            patch.dict(sys.modules, {
                "msal": self.msal,
                "requests": self.requests,
                "pandas": self.pandas,
                "dotenv": self.dotenv,
            }),
            patch.dict(os.environ, {
                "AZURE_TENANT_ID": "test-tenant",
                "AZURE_CLIENT_ID": "test-client",
                "AZURE_CLIENT_SECRET": "test-secret",
                "AZURE_SUBSCRIPTION_ID": "test-subscription",
            }, clear=True),
            patch.object(sys, "argv", [str(SCRIPT), *args]),
            patch("builtins.input", side_effect=answers) as prompt,
            contextlib.redirect_stdout(self.output),
        ):
            try:
                runpy.run_path(str(SCRIPT), run_name="__main__")
            except SystemExit as error:
                return error.code, prompt
        return 0, prompt

    def assert_original_files(self):
        self.assertEqual(self.csv.read_text(encoding="utf-8"), "old csv")
        self.assertEqual(self.parquet.read_bytes(), b"old parquet")

    def test_declining_or_default_preserves_files_without_authentication(self):
        for answer in ("n", "no", ""):
            with self.subTest(answer=answer):
                code, prompt = self.run_script([answer])
                self.assertEqual(code, 0)
                prompt.assert_called_once()
                self.assert_original_files()
                self.msal.ConfidentialClientApplication.assert_not_called()
                self.requests.get.assert_not_called()
        text = self.output.getvalue()
        self.assertIn("current time:", text)
        self.assertIn(str(self.csv), text)
        self.assertIn(str(self.parquet), text)
        self.assertIn("last modified", text)
        self.assertIn("2 days, 0:00:", text)
        self.assertIn("+00:00", text)

    def test_confirming_replaces_both_files(self):
        code, prompt = self.run_script([" YES "])
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        self.assertIn("Standard_Test", self.csv.read_text(encoding="utf-8"))
        self.assertEqual(self.parquet.read_bytes(), b"new parquet")
        self.requests.get.assert_called_once()

    def test_invalid_answer_prompts_again(self):
        code, prompt = self.run_script(["maybe", "no"])
        self.assertEqual(code, 0)
        self.assertEqual(prompt.call_count, 2)
        self.assertIn("Please answer yes or no.", self.output.getvalue())
        self.assert_original_files()

    def test_unavailable_input_keeps_files_and_reports_failure(self):
        code, _ = self.run_script([EOFError()])
        self.assertIn("No confirmation received", code)
        self.assert_original_files()
        self.requests.get.assert_not_called()

    def test_single_existing_file_still_requires_confirmation(self):
        self.csv.unlink()
        code, prompt = self.run_script(["no"])
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        self.assertFalse(self.csv.exists())
        self.assertEqual(self.parquet.read_bytes(), b"old parquet")

    def test_missing_files_fetch_without_prompt(self):
        self.csv.unlink()
        self.parquet.unlink()
        code, prompt = self.run_script()
        self.assertEqual(code, 0)
        prompt.assert_not_called()
        self.assertIn("Standard_Test", self.csv.read_text(encoding="utf-8"))
        self.assertTrue(self.parquet.exists())

    def test_listing_modes_leave_files_untouched_without_prompt(self):
        for args in (("--listRegions",), ("--listResourceTypes",),
                     ("--listRegions", "--listResourceTypes")):
            with self.subTest(args=args):
                code, prompt = self.run_script(args=args)
                self.assertEqual(code, 0)
                prompt.assert_not_called()
                self.assert_original_files()
                self.pandas.DataFrame.assert_not_called()

    def test_failed_authentication_preserves_confirmed_files(self):
        self.msal.ConfidentialClientApplication.return_value.acquire_token_for_client.return_value = {
            "error_description": "test authentication failure"
        }
        code, _ = self.run_script(["yes"])
        self.assertIn("Token acquisition failed", code)
        self.assert_original_files()

    def test_failed_fetch_preserves_confirmed_files(self):
        self.requests.get.side_effect = RuntimeError("test fetch failure")
        with self.assertRaisesRegex(RuntimeError, "test fetch failure"):
            self.run_script(["yes"])
        self.assert_original_files()

    def test_future_timestamp_is_identified(self):
        os.utime(self.csv, (self.now + 86400, self.now + 86400))
        self.run_script(["no"])
        self.assertIn("in the future", self.output.getvalue())


if __name__ == "__main__":
    unittest.main()

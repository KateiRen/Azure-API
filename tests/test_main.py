import contextlib
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import main as launcher


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.csv = self.data / "vm_skus_all_regions.csv"
        self.parquet = self.data / "vm_skus_all_regions.parquet"
        self.now = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

    def create_file(self, path, age_seconds=172800):
        path.write_bytes(b"test data")
        timestamp = self.now.timestamp() - age_seconds
        os.utime(path, (timestamp, timestamp))

    def run_launcher(self, answers, returncode=0):
        with (
            patch.object(launcher, "__file__", str(self.root / "main.py")),
            patch.object(launcher, "datetime", wraps=datetime) as clock,
            patch("builtins.input", side_effect=answers) as prompt,
            patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess(
                args=[], returncode=returncode,
            )) as run,
            contextlib.redirect_stdout(self.stdout),
            contextlib.redirect_stderr(self.stderr),
        ):
            clock.now.return_value = self.now
            code = launcher.main()
        return code, prompt, run

    def test_existing_files_show_sizes_timestamps_and_exact_age(self):
        self.create_file(self.csv)
        self.create_file(self.parquet, age_seconds=3600)
        code, _, run = self.run_launcher(["q"])
        self.assertEqual(code, 0)
        run.assert_not_called()
        text = self.stdout.getvalue()
        self.assertIn("Current time: 2026-10-19T09:00:00+00:00", text)
        self.assertIn("vm_skus_all_regions.csv: 9 bytes", text)
        self.assertIn("last modified 2026-10-17T09:00:00+00:00; age: 2 days, 0:00:00 old", text)
        self.assertIn("vm_skus_all_regions.parquet: 9 bytes", text)
        self.assertIn("age: 1:00:00 old", text)
        self.assertIn("Saved data is available", text)

    def test_missing_files_are_reported(self):
        self.run_launcher(["q"])
        text = self.stdout.getvalue()
        self.assertIn("vm_skus_all_regions.csv: not present", text)
        self.assertIn("vm_skus_all_regions.parquet: not present", text)
        self.assertIn("Fetch new data first", text)

    def test_fetch_uses_current_interpreter_and_project_directory(self):
        code, _, run = self.run_launcher(["1"])
        self.assertEqual(code, 0)
        run.assert_called_once_with(
            [sys.executable, str(self.root / "fetch_vm_skus_all_regions.py")],
            cwd=self.root,
        )

    def test_fetch_does_not_delete_existing_files(self):
        self.create_file(self.csv)
        self.create_file(self.parquet)
        self.run_launcher(["1"])
        self.assertEqual(self.csv.read_bytes(), b"test data")
        self.assertEqual(self.parquet.read_bytes(), b"test data")

    def test_browser_starts_with_parquet_even_without_csv(self):
        self.create_file(self.parquet)
        code, _, run = self.run_launcher(["3"])
        self.assertEqual(code, 0)
        run.assert_called_once_with(
            [sys.executable, "-m", "streamlit", "run", str(self.root / "streamlit_app.py")],
            cwd=self.root,
        )
        self.assertIn("3. Open the SKU browser (Streamlit)", self.stdout.getvalue())

    def test_csv_alone_cannot_start_browser(self):
        self.create_file(self.csv)
        code, prompt, run = self.run_launcher(["3", "q"])
        self.assertEqual(code, 0)
        self.assertEqual(prompt.call_count, 2)
        run.assert_not_called()
        self.assertIn("Cannot open the SKU browser", self.stdout.getvalue())

    def test_prices_use_current_interpreter_and_project_directory(self):
        self.create_file(self.parquet)
        code, prompt, run = self.run_launcher(["2"])
        self.assertEqual(code, 0)
        prompt.assert_called_once()
        run.assert_called_once_with(
            [sys.executable, str(self.root / "fetch_vm_sku_prices.py")],
            cwd=self.root,
        )
        self.assertIn("2. Fetch prices for saved VM SKUs", self.stdout.getvalue())
        self.assertEqual(self.parquet.read_bytes(), b"test data")

    def test_prices_require_parquet_even_if_csv_exists(self):
        for csv_exists in (False, True):
            with self.subTest(csv_exists=csv_exists):
                if csv_exists:
                    self.create_file(self.csv)
                code, prompt, run = self.run_launcher(["2", "q"])
                self.assertEqual(code, 0)
                self.assertEqual(prompt.call_count, 2)
                run.assert_not_called()
                self.assertIn("Cannot fetch VM prices", self.stdout.getvalue())
                self.assertIn("Choose 1 to fetch it.", self.stdout.getvalue())

    def test_price_fetch_failure_is_reported_and_propagated(self):
        self.create_file(self.parquet)
        code, _, run = self.run_launcher(["2"], returncode=2)
        self.assertEqual(code, 2)
        run.assert_called_once()
        self.assertIn("exited with code 2", self.stderr.getvalue())

    def test_invalid_choice_prompts_again(self):
        code, prompt, run = self.run_launcher(["invalid", "", "Q"])
        self.assertEqual(code, 0)
        self.assertEqual(prompt.call_count, 3)
        run.assert_not_called()
        self.assertIn("Please choose 1, 2, 3, or Q.", self.stdout.getvalue())

    def test_unavailable_input_reports_failure_without_starting_command(self):
        code, _, run = self.run_launcher([EOFError()])
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("No selection received", self.stderr.getvalue())

    def test_command_failure_is_reported_and_propagated(self):
        code, _, _ = self.run_launcher(["1"], returncode=3)
        self.assertEqual(code, 3)
        self.assertIn("exited with code 3", self.stderr.getvalue())

    def test_future_timestamp_is_identified(self):
        self.create_file(self.csv, age_seconds=-3600)
        self.run_launcher(["q"])
        self.assertIn("age: 1:00:00 in the future", self.stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

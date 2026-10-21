import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import fetch_azure_locations as locations


ENVIRONMENT = {
    "AZURE_TENANT_ID": "test-tenant",
    "AZURE_CLIENT_ID": "test-client",
    "AZURE_CLIENT_SECRET": "test-secret",
    "AZURE_SUBSCRIPTION_ID": "test-subscription",
}
LOCATION = {
    "name": "eastus", "type": "Region", "displayName": "East US",
    "metadata": {
        "geography": "United States", "geographyGroup": "US",
        "physicalLocation": "Virginia", "latitude": "37.3719", "longitude": "-79.8164",
        "regionType": "Physical", "pairedRegion": [{"name": "westus"}],
    },
}


def cache_payload():
    return {
        "fetchedAt": "2026-10-21T14:00:00+00:00",
        "apiVersion": locations.API_VERSION, "subscriptionId": "test-subscription",
        "sourceUrl": "https://management.azure.com/subscriptions/test-subscription/locations",
        "includeExtendedLocations": False, "value": [LOCATION],
    }


class LocationMetadataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "azure_locations.json"
        self.environment = patch.dict(os.environ, ENVIRONMENT, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        dotenv = patch.object(locations, "load_dotenv")
        dotenv.start()
        self.addCleanup(dotenv.stop)
        output = contextlib.redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)

    def test_fetch_preserves_all_metadata_and_follows_same_origin_pages(self):
        first, second = Mock(status_code=200), Mock(status_code=200)
        next_link = "https://management.azure.com:443/subscriptions/test-subscription/locations?next=1"
        logical = {"name": "global", "type": "Region", "metadata": {"regionType": "Logical"}}
        first.json.return_value = {"value": [LOCATION], "nextLink": next_link}
        second.json.return_value = {"value": [logical]}
        with patch.object(locations.requests, "Session") as factory:
            session = factory.return_value.__enter__.return_value
            session.get.side_effect = [first, second]
            result = locations.fetch_locations(locations.get_configuration(), "test-token")
        self.assertEqual(result["value"], [LOCATION, logical])
        self.assertEqual(result["apiVersion"], locations.API_VERSION)
        first_call, second_call = session.get.call_args_list
        self.assertEqual(first_call.kwargs["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(first_call.kwargs["params"]["includeExtendedLocations"], "false")
        self.assertFalse(first_call.kwargs["allow_redirects"])
        self.assertEqual(first_call.kwargs["timeout"], (15, 90))
        self.assertEqual(second_call.args[0], next_link)
        self.assertIsNone(second_call.kwargs["params"])

    def test_authentication_respects_configuration(self):
        with patch.dict(os.environ, {
            "AZURE_AUTH_AUTHORITY": "https://login.example.test/",
            "AZURE_ARM_SCOPE": "https://arm.example.test/.default",
        }), patch.object(locations.msal, "ConfidentialClientApplication") as client:
            client.return_value.acquire_token_for_client.return_value = {"access_token": "test-token"}
            self.assertEqual(locations.acquire_token(locations.get_configuration()), "test-token")
        self.assertEqual(client.call_args.kwargs["authority"], "https://login.example.test/test-tenant")
        client.return_value.acquire_token_for_client.assert_called_once_with(
            scopes=["https://arm.example.test/.default"],
        )

    def test_main_fetches_and_then_reuses_cache_without_authentication(self):
        with patch.object(locations, "acquire_token", return_value="test-token"), patch.object(
            locations, "fetch_locations", return_value=cache_payload(),
        ):
            self.assertEqual(locations.main(["--output", str(self.path)]), 0)
        self.assertEqual(json.loads(self.path.read_text())["value"], [LOCATION])
        with patch.dict(os.environ, {}, clear=True), patch.object(
            locations, "acquire_token",
        ) as auth, patch.object(locations, "fetch_locations") as fetch:
            self.assertEqual(locations.main(["--output", str(self.path)]), 0)
        auth.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)

    def test_refresh_replaces_valid_cache(self):
        self.path.write_text("old", encoding="utf-8")
        with patch.object(locations, "acquire_token", return_value="test-token"), patch.object(
            locations, "fetch_locations", return_value=cache_payload(),
        ) as fetch:
            self.assertEqual(locations.main(["--output", str(self.path), "--refresh"]), 0)
        fetch.assert_called_once()
        self.assertEqual(json.loads(self.path.read_text())["value"], [LOCATION])

    def test_missing_configuration_does_not_authenticate(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(
            locations, "acquire_token",
        ) as auth, contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(locations.main(["--output", str(self.path)]), 1)
        auth.assert_not_called()
        self.assertIn("AZURE_TENANT_ID", errors.getvalue())
        self.assertFalse(self.path.exists())

    def test_auth_or_fetch_failure_preserves_previous_cache(self):
        for auth_failure in (True, False):
            self.path.write_bytes(b"old cache")
            with (
                self.subTest(auth_failure=auth_failure),
                patch.object(locations, "acquire_token", side_effect=ValueError("denied"))
                if auth_failure else patch.object(locations, "acquire_token", return_value="test-token"),
                patch.object(locations, "fetch_locations", side_effect=requests.Timeout("timeout")),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(locations.main(["--output", str(self.path), "--refresh"]), 1)
            self.assertEqual(self.path.read_bytes(), b"old cache")

    def test_invalid_cache_and_subscription_mismatch_are_explicit(self):
        for payload in ("not json", json.dumps(cache_payload() | {"subscriptionId": "other"})):
            with self.subTest(payload=payload):
                self.path.write_text(payload, encoding="utf-8")
                with patch.object(locations, "acquire_token") as auth, contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(locations.main(["--output", str(self.path)]), 1)
                auth.assert_not_called()

    def test_invalid_responses_and_pagination_are_rejected(self):
        for payload in (
            {}, {"value": []}, {"value": [None]},
            {"value": [{"name": "eastus", "metadata": "invalid"}]},
            {"value": [LOCATION], "nextLink": 1},
            {"value": [LOCATION], "nextLink": "https://example.com/locations"},
            {"value": [LOCATION], "nextLink": cache_payload()["sourceUrl"]},
        ):
            with self.subTest(payload=payload), patch.object(locations.requests, "Session") as factory:
                response = Mock(status_code=200)
                response.json.return_value = payload
                session = factory.return_value.__enter__.return_value
                session.get.return_value = response
                with self.assertRaises(ValueError):
                    locations.fetch_locations(locations.get_configuration(), "test-token")
                self.assertEqual(session.get.call_count, 1)

    def test_http_errors_and_redirects_are_not_treated_as_success(self):
        for status in (302, 403):
            with self.subTest(status=status), patch.object(locations.requests, "Session") as factory:
                response = Mock(status_code=status)
                if status == 403:
                    response.raise_for_status.side_effect = requests.HTTPError("Forbidden")
                factory.return_value.__enter__.return_value.get.return_value = response
                with self.assertRaises((ValueError, requests.HTTPError)):
                    locations.fetch_locations(locations.get_configuration(), "test-token")

    def test_atomic_write_failure_keeps_original_cache(self):
        self.path.write_bytes(b"old cache")
        with patch.object(locations.os, "replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                locations.write_cache(cache_payload(), self.path)
        self.assertEqual(self.path.read_bytes(), b"old cache")
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()

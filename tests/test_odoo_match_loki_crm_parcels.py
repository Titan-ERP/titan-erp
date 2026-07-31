from __future__ import annotations

import argparse
import json
import tempfile
import unittest
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.odoo_match_loki_crm_parcels import (
    ArcGISParcelAdapter,
    ArcGISRequestError,
    LeadPoint,
    MatchResult,
    bounded_limit,
    normalized_county,
    run,
    upsert_link,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class ParcelAdapterTests(unittest.TestCase):
    def setUp(self):
        self.point = LeadPoint(10, 20, 32.8, -96.8, "Example")

    def test_point_query_does_not_request_geometry(self):
        seen = []

        def opener(request, timeout):
            seen.append((request.full_url, timeout))
            return FakeResponse({"features": []})

        result = ArcGISParcelAdapter(opener=opener, minimum_interval_seconds=0).match(
            self.point, company_id=3
        )
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0][0]).query)
        self.assertEqual(result.status, "unmatched")
        self.assertEqual(params["geometryType"], ["esriGeometryPoint"])
        self.assertEqual(params["spatialRel"], ["esriSpatialRelIntersects"])
        self.assertEqual(params["returnGeometry"], ["false"])

    def test_single_dallas_parcel_builds_contract_values_without_geometry(self):
        payload = {
            "features": [{"attributes": {
                "OBJECTID": 88,
                "ACCT": "A-1",
                "GIS_ACCT": "G-1",
                "COUNTY": "Dallas County",
                "APPRAISALYEAR": 2026,
            }}]
        }
        adapter = ArcGISParcelAdapter(
            opener=lambda *_args, **_kwargs: FakeResponse(payload),
            minimum_interval_seconds=0,
        )
        result = adapter.match(
            self.point,
            company_id=3,
            matched_at=datetime(2026, 7, 31, 12, tzinfo=UTC),
        )
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.values["parcel_account"], "G-1")
        self.assertEqual(result.values["parcel_external_id"], "88")
        self.assertEqual(result.values["crm_lead_id"], 10)
        self.assertNotIn("geometry", result.values)
        self.assertFalse(result.values["evidence_json"]["geometry_requested"])

    def test_other_county_is_unsupported_and_has_no_write_values(self):
        payload = {"features": [{"attributes": {"OBJECTID": 1, "COUNTY": "Collin"}}]}
        adapter = ArcGISParcelAdapter(
            opener=lambda *_args, **_kwargs: FakeResponse(payload),
            minimum_interval_seconds=0,
        )
        result = adapter.match(self.point, company_id=3)
        self.assertEqual(result.status, "unsupported")
        self.assertIsNone(result.values)

    def test_outside_extent_does_not_call_service(self):
        adapter = ArcGISParcelAdapter(opener=lambda *_a, **_k: self.fail("network called"))
        result = adapter.match(LeadPoint(1, 2, 35.0, -100.0), company_id=3)
        self.assertEqual(result.status, "unsupported")

    def test_multiple_intersections_require_review(self):
        payload = {"features": [
            {"attributes": {"OBJECTID": 1, "COUNTY": "Dallas"}},
            {"attributes": {"OBJECTID": 2, "COUNTY": "Dallas"}},
        ]}
        result = ArcGISParcelAdapter(
            opener=lambda *_a, **_k: FakeResponse(payload), minimum_interval_seconds=0
        ).match(self.point, company_id=3)
        self.assertEqual(result.status, "review")
        self.assertIsNone(result.values)

    def test_transient_failures_retry_with_timeout(self):
        attempts = []

        def opener(_request, timeout):
            attempts.append(timeout)
            if len(attempts) < 3:
                raise TimeoutError("slow")
            return FakeResponse({"features": []})

        adapter = ArcGISParcelAdapter(
            opener=opener, attempts=3, timeout_seconds=7, minimum_interval_seconds=0, sleep=lambda _: None
        )
        self.assertEqual(adapter.match(self.point, company_id=3).status, "unmatched")
        self.assertEqual(attempts, [7, 7, 7])

    def test_terminal_arcgis_error_is_wrapped(self):
        adapter = ArcGISParcelAdapter(
            opener=lambda *_a, **_k: FakeResponse({"error": {"message": "bad"}}),
            attempts=1,
            minimum_interval_seconds=0,
        )
        with self.assertRaises(ArcGISRequestError):
            adapter.match(self.point, company_id=3)


class FakeOdoo:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.calls = []

    def execute(self, model, method, args=None, kwargs=None):
        self.calls.append((model, method, args, kwargs))
        if method == "search":
            return self.existing
        if method == "create":
            return 42
        if method == "write":
            return True
        raise AssertionError(method)


class UpsertTests(unittest.TestCase):
    def values(self):
        return {"company_id": 1, "crm_lead_id": 2, "source_key": "dallas_cad"}

    def test_create_when_identity_is_new(self):
        client = FakeOdoo()
        self.assertEqual(upsert_link(client, self.values()), ("created", 42))
        self.assertEqual([call[1] for call in client.calls], ["search", "create"])

    def test_update_when_identity_exists(self):
        client = FakeOdoo([9])
        self.assertEqual(upsert_link(client, self.values()), ("updated", 9))
        self.assertEqual([call[1] for call in client.calls], ["search", "write"])

    def test_existing_duplicate_blocks_write(self):
        client = FakeOdoo([9, 10])
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            upsert_link(client, self.values())
        self.assertEqual([call[1] for call in client.calls], ["search"])


class HelperTests(unittest.TestCase):
    def test_county_normalization(self):
        self.assertEqual(normalized_county(" DALLAS County "), "dallas")

    def test_limit_is_bounded(self):
        self.assertEqual(bounded_limit("500"), 500)
        with self.assertRaises(argparse.ArgumentTypeError):
            bounded_limit("501")


class RunSafetyTests(unittest.TestCase):
    def test_dry_run_never_calls_odoo_write_methods(self):
        point = LeadPoint(10, 20, 32.8, -96.8)
        values = {
            "company_id": 3,
            "crm_lead_id": 10,
            "partner_id": 20,
            "source_key": "dallas_cad",
            "parcel_account": "G-1",
        }

        class ReadOnlyClient:
            def execute(self, *_args, **_kwargs):
                raise AssertionError("dry run attempted an unexpected Odoo call")

        class Adapter:
            def match(self, received, *, company_id):
                self.assertEqual(received, point)
                self.assertEqual(company_id, 3)
                return MatchResult("matched", point, "single", values, 1)

            assertEqual = self.assertEqual

        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(
                audit=Path(directory) / "audit.jsonl",
                env_file=Path("unused"),
                company="LOKI",
                team="LOKI CRM",
                limit=10,
                layer_url="unused",
                timeout=1,
                attempts=1,
                request_interval=0,
                apply=False,
                confirm="",
                reason="",
            )
            with patch(
                "scripts.odoo_match_loki_crm_parcels.fetch_loki_lead_points",
                return_value=(3, [point], []),
            ):
                counts = run(args, client=ReadOnlyClient(), adapter=Adapter())
        self.assertEqual(counts["matched"], 1)
        self.assertEqual(counts["created"], 0)
        self.assertEqual(counts["updated"], 0)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import sys
from unittest.mock import MagicMock, patch

from scripts.sparex_catalog_agents import publication_worker


def test_read_only_mode_only_previews_candidates(tmp_path, capsys):
    client = MagicMock()
    client.call.return_value = [{"product_id": 12, "sku": "S.12"}]
    config = MagicMock(company_id=1)
    argv = [
        "publication-worker",
        "--odoo-env-file",
        str(tmp_path / "odoo.env"),
        "--artifact-root",
        str(tmp_path / "artifacts"),
        "--s3-bucket",
        "test-bucket",
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.object(publication_worker.OdooConfig, "from_env", return_value=config),
        patch.object(publication_worker, "require_company_context"),
        patch.object(publication_worker.OdooClient, "connect", return_value=client),
    ):
        assert publication_worker.main() == 0

    result = json.loads(capsys.readouterr().out)
    assert result["candidate_count"] == 1
    assert result["portal_requests"] == 0
    client.call.assert_called_once_with(
        "southern.catalog.agent.task", "preview_ready_candidates", limit=25
    )


def test_apply_publishes_and_confirms_without_portal_calls(tmp_path, capsys):
    client = MagicMock()
    prepared = [{"task_id": 21, "product_id": 31, "sku": "S.31", "public_path": "/shop/s-31"}]

    def call(model, method, **params):
        if method == "preview_ready_candidates":
            return [{"product_id": 31, "sku": "S.31"}]
        if method == "seed_ready_candidates":
            return [{"id": 11}]
        if method == "prepare_publication_plan":
            return prepared
        if method == "publish_prepared_tasks":
            return prepared
        if method in {"confirm_publications", "record_external_result"}:
            return True
        if method == "claim_tasks":
            return []
        raise AssertionError((model, method, params))

    client.call.side_effect = call
    config = MagicMock(company_id=1, url="https://odoo.example")
    verification = [
        {
            "task_id": 21,
            "product_id": 31,
            "sku": "S.31",
            "public_url": "https://odoo.example/shop/s-31",
            "http_status": 200,
            "exact_sku_present": True,
            "attempts": 1,
        }
    ]
    records = iter(
        [
            {"sha256": "a" * 64, "artifact_uri": "s3://test/plan.json"},
            {"sha256": "b" * 64, "artifact_uri": "s3://test/result.json"},
        ]
    )
    argv = [
        "publication-worker",
        "--odoo-env-file",
        str(tmp_path / "odoo.env"),
        "--artifact-root",
        str(tmp_path / "artifacts"),
        "--s3-bucket",
        "test-bucket",
        "--apply",
        "--publish",
        "--confirm",
        publication_worker.WORKFLOW,
        "--reason",
        "test publication",
    ]
    with (
        patch.object(sys, "argv", argv),
        patch.dict("os.environ", {"ODOO_WRITE_ENABLED": "true"}),
        patch.object(publication_worker.OdooConfig, "from_env", return_value=config),
        patch.object(publication_worker, "require_company_context"),
        patch.object(publication_worker.OdooClient, "connect", return_value=client),
        patch.object(publication_worker, "_archive", side_effect=lambda *args: next(records)),
        patch.object(publication_worker, "verify_public_pages", return_value=verification),
    ):
        assert publication_worker.main() == 0

    result = json.loads(capsys.readouterr().out)
    assert result["published_count"] == 1
    assert result["portal_requests"] == 0
    expected_sha = hashlib.sha256(
        json.dumps(verification, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert any(
        call.args[:2] == ("southern.catalog.agent.task", "confirm_publications")
        and call.kwargs["verification_sha256"] == expected_sha
        for call in client.call.mock_calls
    )

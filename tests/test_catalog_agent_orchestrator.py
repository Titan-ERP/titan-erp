from pathlib import Path

from scripts.sparex_catalog_agents import orchestrator
from scripts.sparex_catalog_agents.agent import build_agent, deterministic_agent_decision, requires_ai_review
from scripts.sparex_catalog_agents.orchestrator import (
    AGENT_SEQUENCE,
    MAX_AI_CALLS,
    MAX_BATCH,
    _public_url,
    _run_agent_tasks,
    run_s3_prefix,
)

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_chain_and_batch_limit():
    assert MAX_BATCH == 50
    assert MAX_AI_CALLS == 5
    assert AGENT_SEQUENCE == (
        "coordinator",
        "sparex_discovery",
        "odoo_match",
        "product_verification",
        "website_release",
    )


def test_public_url_is_scoped_to_odoo_base():
    assert _public_url("https://example.odoo.com/", "/shop/example") == "https://example.odoo.com/shop/example"
    assert _public_url("https://example.odoo.com", "https://public.example/item") == "https://public.example/item"


def test_public_url_percent_encodes_unicode_product_paths():
    assert (
        _public_url("https://example.odoo.com", "/shop/king-pin-–-heavy-duty-42")
        == "https://example.odoo.com/shop/king-pin-%E2%80%93-heavy-duty-42"
    )


def test_rate_limit_fallback_keeps_the_exact_ready_chain_bounded():
    snapshot = {
        "task_id": 10,
        "sku": "S.42",
        "product_id": 42,
        "odoo_match_state": "matched",
        "has_positive_supplier_cost": True,
        "has_positive_sales_price": True,
        "has_exact_sparex_url": True,
        "has_image": True,
        "product_is_hidden": True,
        "ready_to_publish": True,
        "blockers": [],
    }
    assert deterministic_agent_decision("coordinator", snapshot).decision == "continue"
    assert deterministic_agent_decision("sparex_discovery", snapshot).decision == "continue"
    assert deterministic_agent_decision("odoo_match", snapshot).decision == "continue"
    assert deterministic_agent_decision("product_verification", snapshot).decision == "ready_for_release"
    release = deterministic_agent_decision("website_release", snapshot)
    assert release.decision == "ready_for_release"
    assert release.next_agent is None


def test_ai_review_is_only_for_explicit_ambiguity():
    assert not requires_ai_review({"blockers": []})
    assert not requires_ai_review({"blockers": ["missing_image"]})
    assert requires_ai_review({"blockers": ["ambiguous_identity"]})


def test_optional_agents_default_to_efficient_model():
    assert build_agent("coordinator").model == "gpt-5.6-luna"


def test_normal_task_path_never_calls_openai(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.recorded = []

        def call(self, model, method, **kwargs):
            if method == "claim_tasks":
                return [
                    {
                        "id": 10,
                        "input_json": '{"task_id":10,"sku":"S.42","ready_to_publish":true}',
                    }
                ]
            if method == "record_external_result":
                self.recorded.append(kwargs)
                return True
            raise AssertionError((model, method))

    monkeypatch.setattr(orchestrator, "run_agent", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()))
    client = FakeClient()
    state = {"calls": 0, "failures": 0, "disabled": False, "max_calls": 1}
    result = _run_agent_tasks(
        client,
        "coordinator",
        worker_id="test",
        limit=1,
        throttle_seconds=3.0,
        throttle_state={},
        run_ai=False,
        ai_state=state,
    )
    assert result["completed"] == 1
    assert result["ai_calls"] == 0
    assert result["deterministic_decisions"] == 1
    assert state["calls"] == 0
    assert client.recorded[0]["state"] == "completed"


def test_each_run_gets_an_immutable_s3_prefix():
    assert (
        run_s3_prefix("sparex-product-catalog/catalog-agent-automation/production/", "20260801T043236Z")
        == "sparex-product-catalog/catalog-agent-automation/production/20260801T043236Z"
    )


def test_service_uses_non_overlapping_secure_runtime():
    launcher = (ROOT / "scripts" / "run_catalog_agent_automation.sh").read_text(encoding="utf-8")
    service = (ROOT / "cloud" / "aws" / "titan-catalog-agent.service").read_text(encoding="utf-8")
    timer = (ROOT / "cloud" / "aws" / "titan-catalog-agent.timer").read_text(encoding="utf-8")
    assert "flock -n" in launcher
    assert "aws ssm get-parameter" not in launcher
    assert "--run-ai" not in launcher
    assert "export ODOO_API_MODE=json2" in launcher
    assert "--limit 50" in launcher
    assert "--publish" in launcher
    assert "OPENAI_API_KEY=" not in service
    assert "Environment=ODOO_COMPANY_ID=1" in service
    assert "OnUnitInactiveSec=2min" in timer
    assert "Persistent=false" in timer


def test_successful_discovery_triggers_publication_handoff():
    discovery_service = (ROOT / "cloud" / "aws" / "titan-sparex-discovery.service").read_text(
        encoding="utf-8"
    )
    discovery_launcher = (ROOT / "scripts" / "run_sparex_catalog_discovery.sh").read_text(encoding="utf-8")
    assert "OnSuccess=titan-catalog-agent.service" in discovery_service
    assert "SPAREX_DISCOVERY_RUN_KEY=sparex-full-catalog-inventory-v3" in discovery_service
    assert "sparex-full-catalog-inventory-v3" in discovery_launcher

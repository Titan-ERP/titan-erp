from pathlib import Path

from scripts.sparex_catalog_agents.orchestrator import AGENT_SEQUENCE, MAX_BATCH, _public_url

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_chain_and_batch_limit():
    assert MAX_BATCH == 5
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


def test_service_uses_non_overlapping_secure_runtime():
    launcher = (ROOT / "scripts" / "run_catalog_agent_automation.sh").read_text(encoding="utf-8")
    service = (ROOT / "cloud" / "aws" / "titan-catalog-agent.service").read_text(encoding="utf-8")
    timer = (ROOT / "cloud" / "aws" / "titan-catalog-agent.timer").read_text(encoding="utf-8")
    assert "flock -n" in launcher
    assert "aws ssm get-parameter" in launcher
    assert "--limit 5" in launcher
    assert "--publish" in launcher
    assert "OPENAI_API_KEY=" not in service
    assert "OnUnitInactiveSec=2min" in timer
    assert "Persistent=false" in timer

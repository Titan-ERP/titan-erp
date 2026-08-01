from scripts.sparex_catalog_agents.agent import AGENT_TOOL_NAMES, build_agent, evaluate_agent_tool

READY_SNAPSHOT = {
    "schema_version": "1.0",
    "task_id": 42,
    "sku": "S.146",
    "odoo_match_state": "matched",
    "product_id": 146,
    "has_positive_supplier_cost": True,
    "has_positive_sales_price": True,
    "has_exact_sparex_url": True,
    "has_image": True,
    "product_is_hidden": True,
    "ready_to_publish": True,
    "blockers": [],
}


def test_agents_receive_exactly_one_role_specific_function_tool():
    for agent_code, expected_tool_name in AGENT_TOOL_NAMES.items():
        agent = build_agent(agent_code)
        assert [tool.name for tool in agent.tools] == [expected_tool_name]
        assert agent.tools[0].params_json_schema["properties"] == {}
        assert agent.tools[0].params_json_schema["additionalProperties"] is False
        assert agent.model_settings.tool_choice == "required"
        assert agent.model_settings.parallel_tool_calls is False


def test_coordinator_routes_ready_product_to_release_agent():
    result = evaluate_agent_tool("coordinator", READY_SNAPSHOT)
    assert result["next_agent"] == "website_release"
    assert result["ready_to_publish"] is True


def test_discovery_exposes_presence_only_not_private_urls():
    result = evaluate_agent_tool("sparex_discovery", READY_SNAPSHOT)
    assert result["discovery_evidence_complete"] is True
    assert "supplier_url" not in result
    assert "supplier_cost" not in result


def test_odoo_match_never_creates_or_selects_a_missing_product():
    snapshot = {**READY_SNAPSHOT, "odoo_match_state": "missing", "product_id": None}
    result = evaluate_agent_tool("odoo_match", snapshot)
    assert result["exact_single_match"] is False
    assert result["product_id"] is None


def test_product_verification_uses_exactly_four_requirements():
    result = evaluate_agent_tool("product_verification", READY_SNAPSHOT)
    assert set(result["requirements"]) == {
        "positive_existing_sparex_supplier_cost",
        "positive_existing_sales_price",
        "exact_same_sku_sparex_url",
        "image_present",
    }
    assert result["all_four_requirements_met"] is True


def test_release_tool_has_no_publication_write():
    result = evaluate_agent_tool("website_release", READY_SNAPSHOT)
    assert result["supervised_release_eligible"] is True
    assert result["publication_write_available"] is False

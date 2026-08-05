from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool
from pydantic import BaseModel, Field

AgentCode = Literal[
    "coordinator",
    "sparex_discovery",
    "odoo_match",
    "product_verification",
    "website_release",
]

AGENT_NAMES: dict[AgentCode, str] = {
    "coordinator": "Catalog Coordinator",
    "sparex_discovery": "Sparex Discovery Agent",
    "odoo_match": "Odoo Match Agent",
    "product_verification": "Product Verification Agent",
    "website_release": "Website Release Agent",
}

AGENT_TOOL_NAMES: dict[AgentCode, str] = {
    "coordinator": "route_catalog_task",
    "sparex_discovery": "verify_sparex_listing",
    "odoo_match": "inspect_odoo_match",
    "product_verification": "evaluate_product_readiness",
    "website_release": "evaluate_release_gate",
}

AI_REVIEW_BLOCKERS = frozenset(
    {
        "ambiguous_identity",
        "duplicate_match",
        "incomplete_listing_identity",
        "conflicting_evidence",
    }
)

COMMON_BOUNDARY = """
Call your assigned function tool exactly once before deciding. Use only the
tool result and supplied task identity. Never invent a SKU, URL, image, price, cost,
product match, or approval. Never request or reveal credentials, supplier URLs,
supplier costs, customer data, or private evidence. You cannot create products,
change prices, change standard cost, or publish products. Return a compact,
structured decision for the Odoo task record.
""".strip()

AGENT_INSTRUCTIONS: dict[AgentCode, str] = {
    "coordinator": (
        "Confirm the bounded catalog task is ready for the fixed specialist chain. "
        "Return continue with sparex_discovery as next_agent when there are no blockers."
    ),
    "sparex_discovery": (
        "Review facts captured from an authenticated Sparex search or listing page. "
        "Accept identity only when the listing SKU is exact; product-detail navigation is forbidden. "
        "Return continue with odoo_match as next_agent when URL and image facts are complete."
    ),
    "odoo_match": (
        "Interpret the deterministic Odoo match state. Matched, missing, and duplicate are distinct. "
        "Never create a missing product and never choose between duplicates. Return continue with "
        "product_verification as next_agent only for one exact match."
    ),
    "product_verification": (
        "Assess exactly four required facts: positive existing Sparex supplier cost, positive existing "
        "sales price, exact Sparex URL, and image presence. No other business gate may be added. "
        "Return ready_for_release with website_release as next_agent only when all four are true."
    ),
    "website_release": (
        "Recommend supervised release only when ready_to_publish is true and the product is hidden. "
        "Return ready_for_release with no next_agent only when eligible. Do not perform or claim a write."
    ),
}


class CatalogAgentDecision(BaseModel):
    decision: Literal[
        "continue",
        "hold",
        "missing_in_odoo",
        "duplicate_in_odoo",
        "ready_for_release",
        "needs_review",
    ]
    summary: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    blocking_reasons: list[str] = Field(default_factory=list)
    next_agent: AgentCode | None = None


@dataclass(frozen=True)
class CatalogRunContext:
    snapshot: dict[str, Any]


def _canonical_tool_result(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def evaluate_agent_tool(agent_code: AgentCode, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return the deterministic, non-sensitive facts exposed to one agent role."""

    blockers = sorted({str(value) for value in snapshot.get("blockers", []) if value})
    match_state = str(snapshot.get("odoo_match_state") or "missing")
    matched = match_state == "matched" and bool(snapshot.get("product_id"))
    has_url = snapshot.get("has_exact_sparex_url") is True
    has_image = snapshot.get("has_image") is True
    has_cost = snapshot.get("has_positive_supplier_cost") is True
    has_sales_price = snapshot.get("has_positive_sales_price") is True
    is_hidden = snapshot.get("product_is_hidden") is True
    ready = snapshot.get("ready_to_publish") is True
    common = {
        "schema_version": "1.0",
        "tool": AGENT_TOOL_NAMES[agent_code],
        "task_id": snapshot.get("task_id"),
        "sku": snapshot.get("sku"),
        "blockers": blockers,
    }

    if agent_code == "coordinator":
        return {**common, "next_agent": "sparex_discovery", "ready_to_publish": ready}

    if agent_code == "sparex_discovery":
        return {
            **common,
            "exact_same_sku_url_present": has_url,
            "listing_image_present": has_image,
            "discovery_evidence_complete": has_url and has_image,
            "next_agent": "odoo_match",
        }

    if agent_code == "odoo_match":
        return {
            **common,
            "odoo_match_state": match_state,
            "product_id": snapshot.get("product_id"),
            "exact_single_match": matched,
            "next_agent": "product_verification" if matched else None,
        }

    if agent_code == "product_verification":
        requirements = {
            "positive_existing_sparex_supplier_cost": has_cost,
            "positive_existing_sales_price": has_sales_price,
            "exact_same_sku_sparex_url": has_url,
            "image_present": has_image,
        }
        return {
            **common,
            "requirements": requirements,
            "all_four_requirements_met": all(requirements.values()),
            "ready_to_publish": ready,
            "next_agent": "website_release" if ready else None,
        }

    if agent_code == "website_release":
        return {
            **common,
            "exact_single_match": matched,
            "product_is_hidden": is_hidden,
            "ready_to_publish": ready,
            "supervised_release_eligible": matched and is_hidden and ready,
            "publication_write_available": False,
            "next_agent": None,
        }

    raise ValueError(f"Unknown catalog agent: {agent_code}")


def deterministic_agent_decision(agent_code: AgentCode, snapshot: dict[str, Any]) -> CatalogAgentDecision:
    """Return the bounded decision implied by the deterministic Odoo facts.

    This is the normal production decision path. It does not add facts, relax
    readiness, or expose a write capability.
    """

    facts = evaluate_agent_tool(agent_code, snapshot)
    blockers = list(facts.get("blockers") or [])
    decision = "hold"
    next_agent = None

    if agent_code == "coordinator" and facts.get("ready_to_publish") and not blockers:
        decision = "continue"
        next_agent = "sparex_discovery"
    elif agent_code == "sparex_discovery" and facts.get("discovery_evidence_complete") and not blockers:
        decision = "continue"
        next_agent = "odoo_match"
    elif agent_code == "odoo_match":
        match_state = facts.get("odoo_match_state")
        if facts.get("exact_single_match") and not blockers:
            decision = "continue"
            next_agent = "product_verification"
        elif match_state == "missing":
            decision = "missing_in_odoo"
        elif match_state == "duplicate":
            decision = "duplicate_in_odoo"
    elif agent_code == "product_verification" and facts.get("ready_to_publish") and not blockers:
        decision = "ready_for_release"
        next_agent = "website_release"
    elif agent_code == "website_release" and facts.get("supervised_release_eligible") and not blockers:
        decision = "ready_for_release"

    return CatalogAgentDecision(
        decision=decision,
        summary=f"Deterministic {AGENT_NAMES[agent_code]} decision from the verified Odoo snapshot.",
        confidence=1.0,
        blocking_reasons=blockers,
        next_agent=next_agent,
    )


def requires_ai_review(snapshot: dict[str, Any]) -> bool:
    """Return whether a bounded snapshot contains an explicit ambiguity.

    Normal catalog readiness is completely deterministic. AI is reserved for
    an operator-enabled review of known ambiguity markers and cannot relax the
    underlying Odoo blockers or perform a write.
    """

    blockers = {str(value) for value in snapshot.get("blockers", []) if value}
    return bool(blockers & AI_REVIEW_BLOCKERS)


@function_tool
def route_catalog_task(context: RunContextWrapper[CatalogRunContext]) -> str:
    """Route the current Odoo-owned task to the next bounded specialist."""

    return _canonical_tool_result(evaluate_agent_tool("coordinator", context.context.snapshot))


@function_tool
def verify_sparex_listing(context: RunContextWrapper[CatalogRunContext]) -> str:
    """Check exact-SKU URL and listing-image evidence already verified by deterministic code."""

    return _canonical_tool_result(evaluate_agent_tool("sparex_discovery", context.context.snapshot))


@function_tool
def inspect_odoo_match(context: RunContextWrapper[CatalogRunContext]) -> str:
    """Read the deterministic exact-SKU Odoo match result without creating or changing products."""

    return _canonical_tool_result(evaluate_agent_tool("odoo_match", context.context.snapshot))


@function_tool
def evaluate_product_readiness(context: RunContextWrapper[CatalogRunContext]) -> str:
    """Evaluate only cost presence, sales-price presence, exact URL, and image presence."""

    return _canonical_tool_result(evaluate_agent_tool("product_verification", context.context.snapshot))


@function_tool
def evaluate_release_gate(context: RunContextWrapper[CatalogRunContext]) -> str:
    """Check supervised website-release eligibility without exposing a publication write."""

    return _canonical_tool_result(evaluate_agent_tool("website_release", context.context.snapshot))


AGENT_TOOLS = {
    "coordinator": [route_catalog_task],
    "sparex_discovery": [verify_sparex_listing],
    "odoo_match": [inspect_odoo_match],
    "product_verification": [evaluate_product_readiness],
    "website_release": [evaluate_release_gate],
}


def build_agent(agent_code: AgentCode, *, model_name: str | None = None) -> Agent:
    if agent_code not in AGENT_NAMES:
        raise ValueError(f"Unknown catalog agent: {agent_code}")
    model = model_name or os.environ.get("OPENAI_CATALOG_AGENT_MODEL", "gpt-5.6-luna").strip()
    instructions = (
        f"{COMMON_BOUNDARY}\n\n{AGENT_INSTRUCTIONS[agent_code]}\n\nYour only tool is {AGENT_TOOL_NAMES[agent_code]}."
    )
    return Agent(
        name=AGENT_NAMES[agent_code],
        instructions=instructions,
        model=model,
        tools=list(AGENT_TOOLS[agent_code]),
        model_settings=ModelSettings(tool_choice="required", parallel_tool_calls=False),
        output_type=CatalogAgentDecision,
    )


def run_agent(agent_code: AgentCode, snapshot: dict, *, model_name: str | None = None) -> CatalogAgentDecision:
    agent = build_agent(agent_code, model_name=model_name)
    prompt = (
        "Review the Odoo-owned catalog task for "
        f"task_id={snapshot.get('task_id')} sku={snapshot.get('sku')}. "
        "Call your assigned function tool before returning the structured decision."
    )
    result = Runner.run_sync(
        agent,
        prompt,
        context=CatalogRunContext(snapshot=dict(snapshot)),
        max_turns=3,
    )
    output = result.final_output
    if not isinstance(output, CatalogAgentDecision):
        raise TypeError("Catalog agent returned an unexpected output type.")
    return output

from __future__ import annotations

import json
import os
from typing import Literal

from agents import Agent, Runner
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

COMMON_BOUNDARY = """
Use only the supplied JSON facts. Never invent a SKU, URL, image, price, cost,
product match, or approval. Never request or reveal credentials, supplier URLs,
supplier costs, customer data, or private evidence. You cannot create products,
change prices, change standard cost, or publish products. Return a compact,
structured decision for the Odoo task record.
""".strip()

AGENT_INSTRUCTIONS: dict[AgentCode, str] = {
    "coordinator": (
        "Coordinate one bounded catalog task and select the next specialist. "
        "Respect the deterministic blockers and never override a safety gate."
    ),
    "sparex_discovery": (
        "Review facts captured from an authenticated Sparex search or listing page. "
        "Accept identity only when the listing SKU is exact; product-detail navigation is forbidden."
    ),
    "odoo_match": (
        "Interpret the deterministic Odoo match state. Matched, missing, and duplicate are distinct. "
        "Never create a missing product and never choose between duplicates."
    ),
    "product_verification": (
        "Assess exactly four required facts: positive existing Sparex supplier cost, positive existing "
        "sales price, exact Sparex URL, and image presence. No other business gate may be added."
    ),
    "website_release": (
        "Recommend supervised release only when ready_to_publish is true and the product is hidden. "
        "Do not perform or claim any publication write."
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


def build_agent(agent_code: AgentCode, *, model_name: str | None = None) -> Agent:
    if agent_code not in AGENT_NAMES:
        raise ValueError(f"Unknown catalog agent: {agent_code}")
    model = model_name or os.environ.get("OPENAI_CATALOG_AGENT_MODEL", "gpt-5.6").strip()
    instructions = f"{COMMON_BOUNDARY}\n\n{AGENT_INSTRUCTIONS[agent_code]}"
    return Agent(
        name=AGENT_NAMES[agent_code],
        instructions=instructions,
        model=model,
        output_type=CatalogAgentDecision,
    )


def run_agent(agent_code: AgentCode, snapshot: dict, *, model_name: str | None = None) -> CatalogAgentDecision:
    agent = build_agent(agent_code, model_name=model_name)
    prompt = "Review this Odoo-owned catalog snapshot:\n" + json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
    )
    result = Runner.run_sync(agent, prompt)
    output = result.final_output
    if not isinstance(output, CatalogAgentDecision):
        raise RuntimeError("Catalog agent returned an unexpected output type.")
    return output

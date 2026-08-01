"""OpenAI Agents SDK worker for Odoo-owned Sparex catalog tasks."""

from .agent import AGENT_NAMES, CatalogAgentDecision, build_agent, run_agent

__all__ = ["AGENT_NAMES", "CatalogAgentDecision", "build_agent", "run_agent"]

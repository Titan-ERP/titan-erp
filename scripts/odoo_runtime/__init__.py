"""Shared, safe runtime for Titan Odoo automation."""

from .artifacts import ArtifactStore, MIN_FREE_BYTES, MIN_FREE_GB
from .client import OdooClient, OdooConfig, connect_legacy
from .crm import classify_crm_rows
from .matching import ContactCandidate, ContactIdentity, MatchDecision, choose_contact_match
from .safety import ApplyGate, WriteBlocked

__all__ = [
    "ApplyGate",
    "ArtifactStore",
    "ContactCandidate",
    "ContactIdentity",
    "MIN_FREE_BYTES",
    "MIN_FREE_GB",
    "MatchDecision",
    "OdooClient",
    "OdooConfig",
    "WriteBlocked",
    "choose_contact_match",
    "classify_crm_rows",
    "connect_legacy",
]

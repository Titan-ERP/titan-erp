"""Shared, safe runtime for Titan Odoo automation."""

from .artifacts import MIN_FREE_BYTES, MIN_FREE_GB, ArtifactStore, sha256_file
from .client import OdooClient, OdooConfig, OdooError, connect_legacy
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
    "OdooError",
    "WriteBlocked",
    "choose_contact_match",
    "classify_crm_rows",
    "connect_legacy",
    "sha256_file",
]

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def normalize_name(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


@dataclass(frozen=True)
class ContactIdentity:
    name: str
    email: str = ""
    phone: str = ""


@dataclass(frozen=True)
class ContactCandidate:
    partner_id: int
    name: str
    email: str = ""
    phone: str = ""
    mobile: str = ""


@dataclass(frozen=True)
class MatchDecision:
    status: str
    partner_id: int | None
    score: int
    reasons: tuple[str, ...]


def _score(source: ContactIdentity, candidate: ContactCandidate) -> tuple[int, tuple[str, ...]]:
    score = 0
    reasons: list[str] = []
    source_email = normalize_email(source.email)
    if source_email and source_email == normalize_email(candidate.email):
        score += 100
        reasons.append("exact_email")
    source_phone = normalize_phone(source.phone)
    candidate_phones = {normalize_phone(candidate.phone), normalize_phone(candidate.mobile)}
    if source_phone and source_phone in candidate_phones:
        score += 80
        reasons.append("exact_phone")
    if normalize_name(source.name) and normalize_name(source.name) == normalize_name(candidate.name):
        score += 40
        reasons.append("normalized_name")
    return score, tuple(reasons)


def choose_contact_match(
    source: ContactIdentity,
    candidates: Iterable[ContactCandidate],
    *,
    minimum_score: int = 40,
) -> MatchDecision:
    ranked = sorted(
        ((_score(source, candidate), candidate) for candidate in candidates),
        key=lambda item: (-item[0][0], item[1].partner_id),
    )
    if not ranked or ranked[0][0][0] < minimum_score:
        return MatchDecision("new", None, 0, ("no_confident_match",))
    best_score, best_reasons = ranked[0][0]
    tied = [candidate for (score, _), candidate in ranked if score == best_score]
    if len(tied) > 1:
        return MatchDecision("review", None, best_score, ("ambiguous_top_score",) + best_reasons)
    return MatchDecision("matched", tied[0].partner_id, best_score, best_reasons)

"""Deterministic Product Master Quality classification.

Kept free of Odoo imports so standalone tests can exercise the same rules the
queue refresh uses in production.
"""

from __future__ import annotations

from dataclasses import dataclass

ISSUE_TYPES = [
    ("placeholder_price", "Placeholder Price"),
    ("price_not_above_cost", "Price Not Above Cost"),
    ("missing_verified_supplier_cost", "Missing Verified Supplier Cost"),
    ("missing_evidence", "Missing Evidence"),
    ("taxonomy_review", "Taxonomy Review"),
    ("duplicate_reference", "Duplicate Internal Reference"),
    ("published_missing_image", "Published Without Image"),
    ("published_missing_description", "Published Without Description"),
    ("publication_gate_blocked", "Published but Sourcing Gate Blocked"),
    ("publication_ready", "Publication Ready"),
]

WORK_LANES = [
    ("live_fix", "Live Website Fix"),
    ("enrich", "Unpublished Enrichment"),
    ("release", "Ready to Publish"),
]

BLOCKER_WHEN_PUBLISHED = frozenset(
    {
        "placeholder_price",
        "price_not_above_cost",
        "missing_verified_supplier_cost",
        "publication_gate_blocked",
    }
)

MIN_CUSTOMER_READY_PRICE = 1.49


@dataclass(frozen=True)
class QualityFinding:
    issue_type: str
    details: str
    severity: str
    work_lane: str


def severity_for(issue_type: str, published: bool) -> str:
    if issue_type == "publication_ready":
        return "1_low"
    if issue_type in BLOCKER_WHEN_PUBLISHED and published:
        return "4_blocker"
    if issue_type.startswith("published_"):
        return "3_high"
    return "2_medium"


def work_lane_for(issue_type: str, published: bool) -> str:
    if issue_type == "publication_ready":
        return "release"
    if published:
        return "live_fix"
    return "enrich"


def _money(value: float) -> str:
    return f"${float(value):.2f}"


def classify_product_quality(
    *,
    price: float,
    cost: float,
    verified_supplier_cost: float,
    is_sparex: bool,
    published: bool,
    source_url: str,
    evidence_count: int,
    has_website_category: bool,
    has_image: bool,
    description_ready: bool,
    sparex_publication_eligible: bool,
    reference: str,
    duplicate_count: int,
) -> list[QualityFinding]:
    """Return the current quality findings for one product snapshot."""
    findings: list[QualityFinding] = []
    price = float(price or 0.0)
    cost = float(cost or 0.0)
    verified_supplier_cost = float(verified_supplier_cost or 0.0)
    evidence_count = int(evidence_count or 0)
    duplicate_count = int(duplicate_count or 0)
    reference = (reference or "").strip()
    comparison_cost = verified_supplier_cost if is_sparex else cost

    def add(issue_type: str, details: str) -> None:
        findings.append(
            QualityFinding(
                issue_type=issue_type,
                details=details,
                severity=severity_for(issue_type, published),
                work_lane=work_lane_for(issue_type, published),
            )
        )

    if price <= MIN_CUSTOMER_READY_PRICE:
        add(
            "placeholder_price",
            f"Sale price is {_money(price)}; customer-ready retail must be above "
            f"{_money(MIN_CUSTOMER_READY_PRICE)}.",
        )
    elif is_sparex and verified_supplier_cost > 0 and price <= verified_supplier_cost:
        add(
            "price_not_above_cost",
            f"Sale price {_money(price)} is not above verified Sparex supplier cost "
            f"{_money(verified_supplier_cost)}.",
        )
    elif not is_sparex and cost > 0 and price <= cost:
        add(
            "price_not_above_cost",
            f"Sale price {_money(price)} is not above standard cost {_money(cost)}.",
        )
    if is_sparex and verified_supplier_cost <= 0:
        add(
            "missing_verified_supplier_cost",
            "No approved Sparex supplier cost is on the sourcing queue.",
        )
    if not source_url and evidence_count <= 0:
        add(
            "missing_evidence",
            "No source URL and no Parts Intelligence evidence rows.",
        )
    if published and not has_website_category:
        add("taxonomy_review", "Published without a website category.")
    if reference and duplicate_count > 1:
        add(
            "duplicate_reference",
            f"Internal reference {reference} is used on {duplicate_count} products.",
        )
    if published and not has_image:
        add("published_missing_image", "Published without a product image.")
    if published and not description_ready:
        add(
            "published_missing_description",
            "Published without a customer-facing description.",
        )
    if is_sparex and published and not sparex_publication_eligible:
        add(
            "publication_gate_blocked",
            "Published but the Sparex sourcing publication gate is blocked.",
        )
    sourcing_ready = (not is_sparex) or sparex_publication_eligible
    if (
        not published
        and price > max(comparison_cost, MIN_CUSTOMER_READY_PRICE)
        and has_website_category
        and has_image
        and (source_url or evidence_count > 0)
        and description_ready
        and sourcing_ready
    ):
        add(
            "publication_ready",
            "Unpublished and currently satisfies the Product Master Quality publication checks.",
        )
    return findings

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
QUALITY_BATCH_LIMIT = 500
QUALITY_PRIORITY_PUBLISHED_LIMIT = 200
QUALITY_PRIORITY_OPEN_LIMIT = 150

NEXT_ACTIONS = {
    "placeholder_price": "Correct the sale price on the product. Do not publish from this queue.",
    "price_not_above_cost": "Raise retail above verified cost, or return the cost row to review.",
    "missing_verified_supplier_cost": "Open Sparex Sourcing and complete dealer-cost approval.",
    "missing_evidence": "Add an exact source URL or Parts Intelligence evidence.",
    "taxonomy_review": "Assign a public website category. Leave publication to the approved release workflow.",
    "duplicate_reference": "Keep one canonical internal reference and archive or recode the extras.",
    "published_missing_image": "Add a product image before the next public check.",
    "published_missing_description": "Add a customer-facing description without placeholder or script copy.",
    "publication_gate_blocked": "Open Sparex Sourcing and clear the publication blockers.",
    "publication_ready": "Queue an approved Sparex release batch. This row does not publish the product.",
}


@dataclass(frozen=True)
class QualityFinding:
    issue_type: str
    details: str
    severity: str
    work_lane: str
    next_action: str


def next_action_for(issue_type: str) -> str:
    return NEXT_ACTIONS.get(issue_type, "Review the product facts and keep writes on the approved workflow.")


def merge_quality_refresh_ids(published_ids, open_ids, cursor_ids, limit=QUALITY_BATCH_LIMIT):
    """Dedupe product ids in live-first order without exceeding the batch bound."""
    ordered = []
    seen = set()
    for product_id in (*published_ids, *open_ids, *cursor_ids):
        if not product_id or product_id in seen:
            continue
        seen.add(product_id)
        ordered.append(product_id)
        if len(ordered) >= int(limit):
            break
    return ordered


def fact_key(issue_type, details, severity, work_lane):
    return "\n".join(
        [
            issue_type or "",
            details or "",
            severity or "",
            work_lane or "",
        ]
    )


def finding_fact_key(finding):
    return fact_key(finding.issue_type, finding.details, finding.severity, finding.work_lane)


def dismissed_should_reopen(previous, finding):
    """Reopen a dismissed row only when stored facts changed.

    Do not trust computed work_lane or severity on the issue. Those fields
    update as soon as the product is published, so a missing accepted fact
    key plus a live finding must reopen instead of seeding the live snapshot.
    Unpublished rows with no snapshot are seeded and stay dismissed.
    """
    current = finding_fact_key(finding)
    accepted = (previous.get("accepted_fact_key") or "").strip()
    if accepted:
        return accepted != current
    if finding.work_lane == "live_fix":
        return True
    previous_details = (previous.get("details") or "").strip()
    if not previous_details:
        return False
    return previous_details != finding.details


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
                next_action=next_action_for(issue_type),
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

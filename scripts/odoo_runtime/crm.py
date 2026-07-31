from __future__ import annotations

from collections import Counter
from typing import Any


def classify_crm_rows(rows: list[dict[str, Any]], *, mass_import_threshold: int = 50) -> list[dict[str, Any]]:
    """Classify imported prospect references separately from worked opportunities.

    A large same-day cohort is treated as reference data only when each row also
    lacks commercial evidence. Any partner, email, description, activity, revenue,
    assigned non-administrator owner, or progression beyond the first stage makes
    the row an actual opportunity.
    """

    cohort_counts = Counter(str(row.get("create_date") or "")[:10] for row in rows)
    classified = []
    for original in rows:
        row = dict(original)
        create_day = str(row.get("create_date") or "")[:10]
        stage = row.get("stage_id")
        stage_id = stage[0] if isinstance(stage, list) and stage else None
        user = row.get("user_id")
        user_name = user[1] if isinstance(user, list) and len(user) > 1 else ""
        commercial_signals = [
            bool(row.get("partner_id")),
            bool(row.get("email_from")),
            bool(row.get("description")),
            bool(row.get("activity_state")),
            float(row.get("expected_revenue") or 0) > 0,
            float(row.get("probability") or 0) >= 100,
            stage_id not in (None, 1),
            bool(user_name and user_name.casefold() != "administrator"),
        ]
        is_mass_reference = cohort_counts[create_day] >= mass_import_threshold and not any(commercial_signals)
        row["record_class"] = "imported_reference" if is_mass_reference else "actual_opportunity"
        classified.append(row)
    return classified

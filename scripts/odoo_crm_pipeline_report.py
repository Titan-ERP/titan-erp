"""Build a full CRM report that excludes imported prospect references from pipeline."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from odoo_runtime import ArtifactStore, OdooClient, OdooConfig, classify_crm_rows


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / "odoo_connection.env")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "crm_control")
    parser.add_argument("--mass-import-threshold", type=int, default=50)
    args = parser.parse_args()

    client = OdooClient(OdooConfig.from_env(args.env_file)).connect()
    available = client.fields("crm.lead")
    desired = [
        "id",
        "name",
        "active",
        "type",
        "stage_id",
        "user_id",
        "team_id",
        "partner_id",
        "email_from",
        "phone",
        "description",
        "activity_state",
        "activity_date_deadline",
        "probability",
        "expected_revenue",
        "create_date",
        "write_date",
        "date_closed",
    ]
    fields = [field for field in desired if field == "id" or field in available]
    rows = client.search_read_all("crm.lead", [], fields, context={"active_test": False})
    rows = classify_crm_rows(rows, mass_import_threshold=args.mass_import_threshold)
    counts = Counter(row["record_class"] for row in rows)
    actual = [row for row in rows if row["record_class"] == "actual_opportunity"]
    reference = [row for row in rows if row["record_class"] == "imported_reference"]

    export_fields = ["record_class"] + fields
    store = ArtifactStore(args.output_dir.resolve(), schema_version="1.0")
    actual_manifest = store.write_csv("actual_opportunities.csv", actual, export_fields)
    reference_manifest = store.write_csv("imported_reference_records.csv", reference, export_fields)
    summary_manifest = store.write_json(
        "crm_pipeline_summary.json",
        {
            "odoo_uid": client.uid,
            "records_scanned": len(rows),
            "actual_opportunities": counts["actual_opportunity"],
            "imported_reference_records": counts["imported_reference"],
            "actual_expected_revenue": round(sum(float(row.get("expected_revenue") or 0) for row in actual), 2),
            "actual_open": sum(
                bool(row.get("active")) and float(row.get("probability") or 0) < 100 for row in actual
            ),
        },
    )
    print(
        {
            "mode": "read_only",
            "records_scanned": len(rows),
            "actual_opportunities": counts["actual_opportunity"],
            "imported_reference_records": counts["imported_reference"],
            "actual_sha256": actual_manifest["sha256"],
            "reference_sha256": reference_manifest["sha256"],
            "summary_sha256": summary_manifest["sha256"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

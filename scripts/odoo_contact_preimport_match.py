"""Match an import CSV to live Odoo contacts without creating or updating records."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from odoo_runtime import (
    ArtifactStore,
    ContactCandidate,
    ContactIdentity,
    OdooClient,
    OdooConfig,
    choose_contact_match,
)


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_csv", type=Path)
    parser.add_argument("--name-column", default="name")
    parser.add_argument("--email-column", default="email")
    parser.add_argument("--phone-column", default="phone")
    parser.add_argument("--env-file", type=Path, default=ROOT / "odoo_connection.env")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs" / "contact_matching")
    args = parser.parse_args()

    with args.source_csv.open("r", newline="", encoding="utf-8-sig") as handle:
        source_rows = list(csv.DictReader(handle))

    client = OdooClient(OdooConfig.from_env(args.env_file)).connect()
    partners = client.search_read_all(
        "res.partner",
        [("active", "=", True)],
        ["id", "name", "email", "phone", "mobile"],
    )
    candidates = [
        ContactCandidate(
            partner_id=row["id"],
            name=row.get("name") or "",
            email=row.get("email") or "",
            phone=row.get("phone") or "",
            mobile=row.get("mobile") or "",
        )
        for row in partners
    ]
    results = []
    for index, row in enumerate(source_rows, start=2):
        identity = ContactIdentity(
            name=row.get(args.name_column, ""),
            email=row.get(args.email_column, ""),
            phone=row.get(args.phone_column, ""),
        )
        decision = choose_contact_match(identity, candidates)
        results.append(
            {
                "source_row": index,
                "source_name": identity.name,
                "source_email": identity.email,
                "source_phone": identity.phone,
                "decision": decision.status,
                "odoo_partner_id": decision.partner_id or "",
                "score": decision.score,
                "reasons": ";".join(decision.reasons),
            }
        )

    manifest = ArtifactStore(args.output_dir.resolve(), schema_version="1.0").write_csv(
        "contact_preimport_match.csv",
        results,
        [
            "source_row",
            "source_name",
            "source_email",
            "source_phone",
            "decision",
            "odoo_partner_id",
            "score",
            "reasons",
        ],
    )
    print(
        {
            "mode": "read_only",
            "source_rows": len(source_rows),
            "odoo_contacts_scanned": len(partners),
            "sha256": manifest["sha256"],
            "output": manifest["path"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

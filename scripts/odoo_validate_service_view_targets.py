from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree

from scripts.odoo_runtime import OdooClient, OdooConfig


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
VIEW_DIR = ROOT / "southern_service_operations" / "views"


def resolve_view(client: OdooClient, xml_id: str) -> int:
    module, name = xml_id.split(".", 1)
    rows = client.search_read_all(
        "ir.model.data",
        [
            ("module", "=", module),
            ("name", "=", name),
            ("model", "=", "ir.ui.view"),
        ],
        ["res_id"],
    )
    if len(rows) != 1:
        raise RuntimeError(f"{xml_id}: expected one view, found {len(rows)}")
    return int(rows[0]["res_id"])


def validate_file(client: OdooClient, path: Path) -> list[str]:
    failures: list[str] = []
    document = etree.parse(str(path))
    for record in document.xpath("//record[@model='ir.ui.view'][field[@name='inherit_id']]"):
        local_id = record.get("id") or "<unknown>"
        model_nodes = record.xpath("./field[@name='model']")
        inherit_nodes = record.xpath("./field[@name='inherit_id']")
        if not model_nodes or not inherit_nodes:
            failures.append(f"{path.name}:{local_id}: incomplete inherited view")
            continue
        model = (model_nodes[0].text or "").strip()
        parent_xml_id = inherit_nodes[0].get("ref") or ""
        try:
            view_id = resolve_view(client, parent_xml_id)
            view_rows = client.execute(
                "ir.ui.view",
                "read",
                [[view_id]],
                {"fields": ["type"]},
            )
            view_type = view_rows[0]["type"]
            compiled = client.execute(
                model,
                "get_view",
                [],
                {"view_id": view_id, "view_type": view_type},
            )
            parent_arch = etree.fromstring(compiled["arch"].encode())
        except Exception as exc:  # report every target in one run
            failures.append(
                f"{path.name}:{local_id}: cannot load {parent_xml_id}: {exc}"
            )
            continue

        for target in record.xpath("./field[@name='arch']/xpath"):
            expression = target.get("expr") or ""
            try:
                matches = parent_arch.xpath(expression)
            except etree.XPathError as exc:
                failures.append(
                    f"{path.name}:{local_id}: invalid XPath {expression!r}: {exc}"
                )
                continue
            if not matches:
                failures.append(
                    f"{path.name}:{local_id}: XPath matched nothing in "
                    f"{parent_xml_id}: {expression}"
                )
            else:
                print(
                    f"PASS {path.name}:{local_id} -> {parent_xml_id} "
                    f"[{len(matches)}] {expression}"
                )
    return failures


def main() -> int:
    client = OdooClient(OdooConfig.from_env(ENV_PATH)).connect()
    failures: list[str] = []
    for path in sorted(VIEW_DIR.glob("*.xml")):
        failures.extend(validate_file(client, path))
    if failures:
        print("\nFAILURES")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nAll inherited view targets match the live Odoo 19 architecture.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

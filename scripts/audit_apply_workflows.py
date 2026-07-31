"""Inventory every apply-capable workflow and its safety controls."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def inspect_workflow(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8", errors="replace")
    explicit_opt_in = bool(
        re.search(r"""add_argument\(\s*["']--apply(?:-[\w-]+)?["'][\s\S]{0,250}?action\s*=\s*["']store_true["']""", source)
        or re.search(r"""["']--apply["']\s+(?:not\s+)?in\s+""", source)
    )
    return {
        "workflow": path.name,
        "explicit_apply_opt_in": explicit_opt_in,
        "shared_apply_gate": "ApplyGate(" in source,
        "retry_aware": "connect_legacy(" in source or "OdooClient(" in source,
        "direct_xmlrpc": "xmlrpc.client" in source,
        "writes_odoo": bool(re.search(r"""["'](?:write|create|unlink|action_post)["']""", source)),
        "test_file": f"test_{path.stem}.py",
    }


def inventory() -> list[dict[str, object]]:
    paths = [
        path
        for path in sorted(SCRIPTS.glob("*.py"))
        if path.name != Path(__file__).name and "--apply" in path.read_text(encoding="utf-8", errors="replace")
    ]
    return [inspect_workflow(path) for path in paths]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "outputs" / "controls" / "apply_workflows.json")
    args = parser.parse_args()
    rows = inventory()
    summary = {
        "schema_version": "1.0",
        "apply_capable_workflows": len(rows),
        "explicit_opt_in": sum(bool(row["explicit_apply_opt_in"]) for row in rows),
        "shared_apply_gate": sum(bool(row["shared_apply_gate"]) for row in rows),
        "retry_aware": sum(bool(row["retry_aware"]) for row in rows),
        "workflows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print({key: value for key, value in summary.items() if key != "workflows"} | {"output": str(args.output)})
    return 1 if summary["explicit_opt_in"] != summary["apply_capable_workflows"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

import ast
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]


def test_operations_control_loads_order_reset_components():
    manifest = ast.literal_eval(
        (ROOT / "southern_operations_control" / "__manifest__.py").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["version"] == "19.0.1.2.0"
    assert "purchase" in manifest["depends"]
    assert "sale" in manifest["depends"]
    assert "views/commercial_order_views.xml" in manifest["data"]


def test_reset_actions_use_native_cancel_and_draft_without_sudo():
    source = (
        ROOT / "southern_operations_control" / "models" / "commercial_order.py"
    ).read_text(encoding="utf-8")

    assert source.count("def action_southern_reset_to_draft") == 2
    assert "order.button_cancel()" in source
    assert "order.button_draft()" in source
    assert "order.action_cancel()" in source
    assert "order.action_draft()" in source
    assert ".sudo(" not in source
    assert "purchase.group_purchase_user" in source
    assert "sales_team.group_sale_salesman" in source


def test_reset_buttons_are_limited_to_authorized_internal_order_users():
    view_path = (
        ROOT / "southern_operations_control" / "views" / "commercial_order_views.xml"
    )
    tree = etree.parse(str(view_path))
    buttons = tree.xpath("//button[@name='action_southern_reset_to_draft']")

    assert len(buttons) == 2
    assert all(button.get("confirm") for button in buttons)
    groups = " ".join(button.get("groups", "") for button in buttons)
    assert "purchase.group_purchase_user" in groups
    assert "sales_team.group_sale_salesman" in groups
    assert "base.group_public" not in groups
    assert "base.group_portal" not in groups

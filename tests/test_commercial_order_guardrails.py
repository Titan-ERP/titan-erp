from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "southern_accounting_guardrails"
MODEL = (MODULE / "models" / "commercial_order.py").read_text(encoding="utf-8")
MANIFEST = (MODULE / "__manifest__.py").read_text(encoding="utf-8")


def test_sales_and_purchase_guardrails_are_loaded():
    assert '"southern_service_operations"' in MANIFEST
    assert '"purchase"' in MANIFEST
    assert '"views/commercial_order_views.xml"' in MANIFEST
    assert "from . import commercial_order" in (MODULE / "models" / "__init__.py").read_text(encoding="utf-8")


def test_sales_confirmation_and_invoice_account_are_guarded():
    assert "class SaleOrder(models.Model):" in MODEL
    assert "self._validate_southern_accounting_confirmation()" in MODEL
    assert "def _prepare_invoice_line(self, **optional_values):" in MODEL
    assert 'values["account_id"] = account.id' in MODEL
    assert "require_sale_tax_selection" in MODEL


def test_purchase_confirmation_and_vendor_bill_account_are_guarded():
    purchase_start = MODEL.index("class PurchaseOrder(models.Model):")
    purchase_source = MODEL[purchase_start:]
    assert "def button_confirm(self):" in purchase_source
    assert "self._validate_southern_accounting_confirmation()" in purchase_source
    assert "def _prepare_account_move_line(self, *args, **kwargs):" in purchase_source
    assert 'values["account_id"] = account.id' in purchase_source
    assert "property_stock_valuation_account_id" in purchase_source
    assert "require_purchase_tax_selection" in purchase_source


def test_commercial_views_are_valid_xml_and_show_line_routing():
    view_path = MODULE / "views" / "commercial_order_views.xml"
    etree.parse(str(view_path))
    source = view_path.read_text(encoding="utf-8")
    assert "southern_revenue_bucket" in source
    assert "southern_revenue_account_id" in source
    assert "southern_purchase_purpose" in source
    assert "southern_expense_account_id" in source


def test_legacy_orders_can_be_warned_before_enforcement():
    policy = (MODULE / "models" / "accounting_policy.py").read_text(encoding="utf-8")
    assert "commercial_order_guardrail_mode = fields.Selection(" in policy
    assert "commercial_order_guardrail_effective_at = fields.Datetime(" in policy
    assert 'default="warn"' in policy

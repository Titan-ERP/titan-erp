from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "southern_accounting_guardrails"
MODEL = (MODULE / "models" / "commercial_order.py").read_text(encoding="utf-8")
MANIFEST = (MODULE / "__manifest__.py").read_text(encoding="utf-8")
ACCESS = (MODULE / "security" / "ir.model.access.csv").read_text(encoding="utf-8")


def test_sales_and_purchase_guardrails_are_loaded():
    assert '"southern_service_operations"' in MANIFEST
    assert '"purchase"' in MANIFEST
    assert '"views/commercial_order_views.xml"' in MANIFEST
    assert "from . import commercial_order" in (MODULE / "models" / "__init__.py").read_text(encoding="utf-8")


def test_accounting_policy_access_is_available_to_internal_users():
    assert "model_southern_accounting_policy,base.group_user,1,0,0,0" in ACCESS
    assert "model_southern_accounting_policy,account.group_account_manager,1,1,1,1" in ACCESS


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


def test_commercial_views_are_valid_xml_and_inactive():
    view_path = MODULE / "views" / "commercial_order_views.xml"
    tree = etree.parse(str(view_path))
    source = view_path.read_text(encoding="utf-8")
    records = tree.xpath("//record")
    assert len(records) == 4
    assert all(record.xpath("./field[@name='active'][@eval='False']") for record in records)
    assert "southern_revenue_bucket" in source
    assert "southern_revenue_account_id" in source
    assert "southern_purchase_purpose" in source
    assert "southern_expense_account_id" in source


def test_commercial_guardrails_default_off_and_are_hidden_from_policy_form():
    policy = (MODULE / "models" / "accounting_policy.py").read_text(encoding="utf-8")
    policy_view = (MODULE / "views" / "accounting_policy_views.xml").read_text(encoding="utf-8")
    assert "commercial_order_guardrail_mode = fields.Selection(" in policy
    assert "commercial_order_guardrail_effective_at = fields.Datetime(" in policy
    assert 'default="off"' in policy
    assert 'field name="commercial_order_guardrail_mode"' not in policy_view
    assert 'field name="commercial_order_guardrail_effective_at"' not in policy_view


def test_guardrail_retirement_migration_is_present():
    migration = MODULE / "migrations" / "19.0.1.8.0" / "post-migrate.py"
    source = migration.read_text(encoding="utf-8")
    assert "commercial_order_guardrail_mode = 'off'" in source
    assert "commercial_order_guardrail_effective_at = NULL" in source
    assert "SET active = FALSE" in source

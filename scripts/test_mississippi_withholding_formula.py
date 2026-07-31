import importlib.util
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "custom_addons" / "l10n_us_hr_payroll_ms_status" / "models" / "mississippi_withholding.py"


spec = importlib.util.spec_from_file_location("mississippi_withholding", MODULE)
mississippi_withholding = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mississippi_withholding)


def check(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected}, got {actual}")


check(
    mississippi_withholding.calculate_ms_withholding(Decimal("1000"), "ms_single", 12),
    Decimal("0.00"),
    "single monthly under threshold",
)
check(
    mississippi_withholding.calculate_ms_withholding(Decimal("3000"), "ms_single", 12),
    Decimal("59"),
    "single monthly over threshold",
)
check(
    mississippi_withholding.calculate_ms_withholding(Decimal("2000"), "ms_head_of_family", 26),
    Decimal("45"),
    "head-of-family biweekly",
)

print("Mississippi withholding formula checks passed.")

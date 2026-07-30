import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
READINESS = ROOT / "odoo_imports" / "accounting" / "payroll_employee_readiness.csv"
OUT = ROOT / "odoo_imports" / "accounting" / "payroll_employee_setup_template.csv"


PLACEHOLDER_NAMES = {"administrator", "service", "southern equipment co"}


def main():
    with READINESS.open("r", newline="", encoding="utf-8-sig") as f:
        employees = list(csv.DictReader(f))

    rows = []
    for employee in employees:
        name = employee["Employee"]
        is_placeholder = name.lower() in PLACEHOLDER_NAMES or "likely non-payroll" in employee.get("Missing/Review", "")
        rows.append(
            {
                "Employee ID": employee["Employee ID"],
                "Employee": name,
                "Company": employee["Company"],
                "Include in Payroll? (yes/no)": "no" if is_placeholder else "",
                "Pay Type (salary/hourly)": employee.get("Wage Type", ""),
                "Pay Schedule": employee.get("Pay Schedule", ""),
                "Payroll Structure": employee.get("Payroll Structure", ""),
                "Gross Wage or Hourly Rate": "",
                "Federal Filing Status": employee.get("Federal Filing Status", ""),
                "State Filing Status": employee.get("State Filing Status", ""),
                "SSN": "",
                "Date of Birth": "",
                "Home Street": "",
                "Home Street 2": "",
                "Home City": "",
                "Home State": "",
                "Home ZIP": "",
                "Personal Email": "",
                "Bank Routing Number": "",
                "Bank Account Number": "",
                "Bank Account Type (checking/savings)": "",
                "Worker Comp Code": "",
                "Deduction Notes": "",
                "Review Notes": employee.get("Missing/Review", ""),
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Template rows: {len(rows)}")
    print(f"Template: {OUT}")


if __name__ == "__main__":
    main()

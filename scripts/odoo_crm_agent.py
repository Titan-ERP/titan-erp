import argparse
import csv
import os
import sys
import xmlrpc.client
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
CRM_DIR = ROOT / "odoo_imports" / "crm"
SUMMARY_PATH = CRM_DIR / "crm_agent_summary.md"


def load_env(path):
    if not path.exists():
        raise SystemExit(f"Missing {path}. Copy odoo_connection.env.example and fill it in.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    load_env(ENV_PATH)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def model_exists(models, db, uid, api_key, model):
    return bool(execute(models, db, uid, api_key, "ir.model", "search_count", [[("model", "=", model)]]))


def fields(models, db, uid, api_key, model):
    if not model_exists(models, db, uid, api_key, model):
        return {}
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type"]})


def available(field_map, names):
    return [name for name in names if name in field_map]


def read(models, db, uid, api_key, model, domain, field_names, limit=500, order=None, context=None):
    kwargs = {"fields": field_names, "limit": limit}
    if order:
        kwargs["order"] = order
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def count(models, db, uid, api_key, model, domain, context=None):
    kwargs = {}
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_count", [domain], kwargs)


def month_bounds(month):
    year, month_number = [int(part) for part in month.split("-", 1)]
    start = date(year, month_number, 1)
    if month_number == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month_number + 1, 1)
    return start.isoformat(), end.isoformat()


def month_label(month):
    year, month_number = month.split("-", 1)
    names = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }
    return f"{names[month_number]} {year}"


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def flatten_value(value):
    if isinstance(value, list):
        if len(value) == 2 and isinstance(value[0], int):
            return rel(value)
        return "; ".join(map(str, value))
    if value is False:
        return ""
    return value


def flatten(rows):
    flattened = []
    for row in rows:
        flattened.append({key: flatten_value(value) for key, value in row.items()})
    return flattened


def write_csv(name, rows, field_names):
    CRM_DIR.mkdir(parents=True, exist_ok=True)
    path = CRM_DIR / name
    rows = flatten(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=field_names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows)


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def format_money(value):
    value = money(value)
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def sum_field(rows, field_name):
    total = Decimal("0.00")
    for row in rows:
        total += money(row.get(field_name))
    return total


def bucket(rows, field_name):
    totals = defaultdict(lambda: [0, Decimal("0.00")])
    for row in rows:
        key = flatten_value(row.get(field_name)) or "Unassigned"
        totals[key][0] += 1
        totals[key][1] += money(row.get("expected_revenue"))
    return dict(sorted(totals.items(), key=lambda item: (-item[1][1], item[0])))


def line_items(items):
    if not items:
        return "- None found."
    return "\n".join(f"- {name}: {count} records, expected revenue `{format_money(total)}`" for name, (count, total) in items.items())


def write_summary(summary):
    SUMMARY_PATH.write_text(
        f"""# Odoo CRM Agent Report

Generated from the latest live Odoo refresh.

Focus period: {summary['period']}.

## Agent Run

- Odoo write performed: no
- Open CRM pipeline records: {summary['open_count']}, expected revenue `{format_money(summary['open_revenue'])}`
- Stale open pipeline records: {summary['stale_count']}
- Open records without a planned activity: {summary['no_activity_count']}
- Overdue CRM activities: {summary['overdue_activity_count']}
- Won this period: {summary['won_count']}, expected revenue `{format_money(summary['won_revenue'])}`
- Lost this period: {summary['lost_count']}, expected revenue `{format_money(summary['lost_revenue'])}`

## Open Pipeline By Salesperson

{line_items(summary['by_salesperson'])}

## Open Pipeline By Stage

{line_items(summary['by_stage'])}

## Working Files

- Open pipeline: `odoo_imports/crm/crm_open_pipeline.csv`
- Stale pipeline: `odoo_imports/crm/crm_stale_pipeline.csv`
- No next activity: `odoo_imports/crm/crm_no_next_activity.csv`
- Overdue activities: `odoo_imports/crm/crm_overdue_activities.csv`
- Won this period: `odoo_imports/crm/crm_won_period.csv`
- Lost this period: `odoo_imports/crm/crm_lost_period.csv`

## Human Decisions

- Review stale opportunities and either schedule the next activity, update the stage, or mark lost.
- Confirm ownership for unassigned pipeline items.
- Work overdue CRM activities before adding new follow-up volume.
- Check won/lost period records for missing expected revenue or incorrect close dates.
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Run the local Odoo CRM agent workflow.")
    parser.add_argument("--month", default=datetime.now().strftime("%Y-%m"), help="Focus close-date reporting on one month in YYYY-MM format.")
    parser.add_argument("--stale-days", type=int, default=14, help="Flag open records not updated in this many days. Default: 14.")
    parser.add_argument("--limit", type=int, default=500, help="Maximum rows per detail report. Default: 500.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    crm_fields = fields(models, db, uid, api_key, "crm.lead")
    if not crm_fields:
        raise SystemExit("The crm.lead model is not available in this Odoo database.")

    today = date.today().isoformat()
    stale_before = (date.today() - timedelta(days=args.stale_days)).isoformat()
    month_start, month_end = month_bounds(args.month)

    base_fields = available(
        crm_fields,
        [
            "name",
            "type",
            "partner_id",
            "partner_name",
            "contact_name",
            "email_from",
            "phone",
            "user_id",
            "team_id",
            "stage_id",
            "priority",
            "probability",
            "expected_revenue",
            "date_deadline",
            "create_date",
            "write_date",
            "date_open",
            "date_closed",
            "activity_state",
            "activity_date_deadline",
            "activity_summary",
            "lost_reason_id",
        ],
    )
    if "name" not in base_fields:
        base_fields.insert(0, "name")

    open_domain = [("active", "=", True)]
    if "probability" in crm_fields:
        open_domain.append(("probability", "<", 100))
    if "type" in crm_fields:
        open_domain.append(("type", "in", ["lead", "opportunity"]))

    open_rows = read(models, db, uid, api_key, "crm.lead", open_domain, base_fields, limit=args.limit, order="write_date asc")
    write_csv("crm_open_pipeline.csv", open_rows, base_fields)

    stale_domain = open_domain + [("write_date", "<", stale_before)]
    stale_rows = read(models, db, uid, api_key, "crm.lead", stale_domain, base_fields, limit=args.limit, order="write_date asc")
    write_csv("crm_stale_pipeline.csv", stale_rows, base_fields)

    no_activity_rows = []
    if "activity_state" in crm_fields:
        no_activity_rows = read(
            models,
            db,
            uid,
            api_key,
            "crm.lead",
            open_domain + [("activity_state", "=", False)],
            base_fields,
            limit=args.limit,
            order="write_date asc",
        )
    write_csv("crm_no_next_activity.csv", no_activity_rows, base_fields)

    activity_rows = []
    activity_fields = fields(models, db, uid, api_key, "mail.activity")
    activity_field_list = available(
        activity_fields,
        ["res_name", "activity_type_id", "summary", "date_deadline", "user_id", "create_date", "res_model", "res_id"],
    )
    if activity_fields and activity_field_list:
        activity_rows = read(
            models,
            db,
            uid,
            api_key,
            "mail.activity",
            [("res_model", "=", "crm.lead"), ("date_deadline", "<", today)],
            activity_field_list,
            limit=args.limit,
            order="date_deadline asc",
        )
    write_csv("crm_overdue_activities.csv", activity_rows, activity_field_list)

    won_rows = []
    if "date_closed" in crm_fields and "probability" in crm_fields:
        won_rows = read(
            models,
            db,
            uid,
            api_key,
            "crm.lead",
            [("date_closed", ">=", month_start), ("date_closed", "<", month_end), ("probability", "=", 100)],
            base_fields,
            limit=args.limit,
            order="date_closed desc",
            context={"active_test": False},
        )
    write_csv("crm_won_period.csv", won_rows, base_fields)

    lost_domain = [("date_closed", ">=", month_start), ("date_closed", "<", month_end), ("active", "=", False)]
    lost_rows = read(
        models,
        db,
        uid,
        api_key,
        "crm.lead",
        lost_domain,
        base_fields,
        limit=args.limit,
        order="date_closed desc",
        context={"active_test": False},
    )
    write_csv("crm_lost_period.csv", lost_rows, base_fields)

    summary = {
        "period": month_label(args.month),
        "open_count": count(models, db, uid, api_key, "crm.lead", open_domain),
        "open_revenue": sum_field(open_rows, "expected_revenue"),
        "stale_count": count(models, db, uid, api_key, "crm.lead", stale_domain),
        "no_activity_count": len(no_activity_rows),
        "overdue_activity_count": len(activity_rows),
        "won_count": len(won_rows),
        "won_revenue": sum_field(won_rows, "expected_revenue"),
        "lost_count": len(lost_rows),
        "lost_revenue": sum_field(lost_rows, "expected_revenue"),
        "by_salesperson": bucket(open_rows, "user_id"),
        "by_stage": bucket(open_rows, "stage_id"),
    }
    write_summary(summary)

    print(f"Connected uid: {uid}")
    print(f"Focus: {summary['period']}")
    print("Odoo write performed: no")
    print(f"Open CRM pipeline records: {summary['open_count']} ({format_money(summary['open_revenue'])} in first {len(open_rows)} exported rows)")
    print(f"Stale open pipeline records: {summary['stale_count']}")
    print(f"Open records without a planned activity: {summary['no_activity_count']}")
    print(f"Overdue CRM activities: {summary['overdue_activity_count']}")
    print(f"Won this period: {summary['won_count']} ({format_money(summary['won_revenue'])})")
    print(f"Lost this period: {summary['lost_count']} ({format_money(summary['lost_revenue'])})")
    print(f"Report: {SUMMARY_PATH}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)

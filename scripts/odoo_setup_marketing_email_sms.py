import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env():
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}.")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
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


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def get_or_create(models, db, uid, api_key, model, domain, values, fields=None):
    rows = execute(
        models,
        db,
        uid,
        api_key,
        model,
        "search_read",
        [domain],
        {"fields": fields or ["name"], "limit": 1},
    )
    if rows:
        return rows[0]["id"], False
    return execute(models, db, uid, api_key, model, "create", [values]), True


def model_id(models, db, uid, api_key, model_name):
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model",
        "search_read",
        [[("model", "=", model_name)]],
        {"fields": ["name", "model"], "limit": 1},
    )
    if not rows:
        raise SystemExit(f"Could not find ir.model for {model_name!r}.")
    return rows[0]["id"]


def main():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")

    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    created = []
    existing = []

    list_specs = [
        ("Newsletter", True),
        ("Parts & Service Promotions", True),
        ("Equipment Buyers", True),
        ("Rental & Field Service Customers", True),
        ("SMS Opt-In Customers", False),
    ]
    list_ids = {}
    for name, is_public in list_specs:
        rec_id, was_created = get_or_create(
            models,
            db,
            uid,
            api_key,
            "mailing.list",
            [("name", "=", name)],
            {"name": name, "is_public": is_public, "active": True},
            ["name", "is_public"],
        )
        list_ids[name] = rec_id
        (created if was_created else existing).append(f"mailing.list: {name}")

    campaign_specs = [
        "Email Marketing - General",
        "SMS Marketing - General",
        "Parts & Service Promotions",
    ]
    campaign_ids = {}
    for name in campaign_specs:
        rec_id, was_created = get_or_create(
            models,
            db,
            uid,
            api_key,
            "utm.campaign",
            [("name", "=", name)],
            {"name": name, "is_auto_campaign": False},
            ["name"],
        )
        campaign_ids[name] = rec_id
        (created if was_created else existing).append(f"utm.campaign: {name}")

    source_ids = {}
    for name in ["Odoo Email Marketing", "Odoo SMS Marketing"]:
        rec_id, was_created = get_or_create(
            models,
            db,
            uid,
            api_key,
            "utm.source",
            [("name", "=", name)],
            {"name": name},
            ["name"],
        )
        source_ids[name] = rec_id
        (created if was_created else existing).append(f"utm.source: {name}")

    medium_ids = {}
    for name in ["Email", "SMS"]:
        rec_id, was_created = get_or_create(
            models,
            db,
            uid,
            api_key,
            "utm.medium",
            [("name", "=", name)],
            {"name": name, "active": True},
            ["name"],
        )
        medium_ids[name] = rec_id
        (created if was_created else existing).append(f"utm.medium: {name}")

    mailing_contact_model_id = model_id(models, db, uid, api_key, "mailing.contact")

    draft_specs = [
        {
            "name": "Draft - Parts & Service Seasonal Email",
            "mailing_type": "mail",
            "subject": "Keep your equipment ready for the next job",
            "preview": "Parts, service, and support from Southern Equipment.",
            "body_arch": """
<div>
  <p>Hello,</p>
  <p>Keep your machines working when the schedule gets tight. Southern Equipment can help with parts, service, repairs, and equipment support for the next job on your calendar.</p>
  <p><strong>Need something this week?</strong> Reply to this email or contact our team and we will help track it down.</p>
  <p>Southern Equipment</p>
</div>
""",
            "email_from": "Southern Equipment <info@southernequipment.co>",
            "reply_to": "info@southernequipment.co",
            "contact_list_ids": [(6, 0, [list_ids["Newsletter"], list_ids["Parts & Service Promotions"]])],
            "campaign_id": campaign_ids["Parts & Service Promotions"],
            "source_id": source_ids["Odoo Email Marketing"],
            "medium_id": medium_ids["Email"],
        },
        {
            "name": "Draft - Parts & Service Seasonal SMS",
            "mailing_type": "sms",
            "subject": "Parts & service reminder",
            "sms_subject": "Parts & service reminder",
            "body_plaintext": "Southern Equipment: Need parts, service, or repair support this week? Reply or call 601-651-4555. Opt out: STOP",
            "sms_allow_unsubscribe": True,
            "contact_list_ids": [(6, 0, [list_ids["SMS Opt-In Customers"]])],
            "campaign_id": campaign_ids["SMS Marketing - General"],
            "source_id": source_ids["Odoo SMS Marketing"],
            "medium_id": medium_ids["SMS"],
        },
    ]

    for spec in draft_specs:
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "mailing.mailing",
            "search_read",
            [[("name", "=", spec["name"])]],
            {"fields": ["name", "state"], "limit": 1},
        )
        if rows:
            existing.append(f"mailing.mailing: {spec['name']}")
            continue
        values = {
            "state": "draft",
            "mailing_model_id": mailing_contact_model_id,
            **spec,
        }
        execute(models, db, uid, api_key, "mailing.mailing", "create", [values])
        created.append(f"mailing.mailing: {spec['name']}")

    print(f"Connected uid: {uid}")
    print("Created:")
    for item in created:
        print(f"  - {item}")
    print("Existing:")
    for item in existing:
        print(f"  - {item}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)

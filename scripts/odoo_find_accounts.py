import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_env()
    terms = sys.argv[1:] or ["freight", "shipping", "fuel", "vehicle", "auto", "gas"]
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    domain = []
    for index, term in enumerate(terms):
        if index:
            domain.insert(0, "|")
        domain.append(("name", "ilike", term))
    field_names = ["id", "code", "name"]
    account_fields = models.execute_kw(
        db,
        uid,
        api_key,
        "account.account",
        "fields_get",
        [],
        {"attributes": ["string", "type"]},
    )
    for optional in ["company_id", "company_ids", "account_type"]:
        if optional in account_fields:
            field_names.append(optional)
    rows = models.execute_kw(
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [domain],
        {"fields": field_names, "limit": 300, "order": "code asc"},
    )
    for row in rows:
        company = ""
        if row.get("company_id"):
            company = row["company_id"][1]
        elif row.get("company_ids"):
            company = ",".join(map(str, row["company_ids"]))
        print(f"{row['id']}\t{row.get('code', '')}\t{row['name']}\t{company}\t{row.get('account_type', '')}")


if __name__ == "__main__":
    main()

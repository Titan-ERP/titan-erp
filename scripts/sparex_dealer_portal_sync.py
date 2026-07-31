from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import sys
import time
import xmlrpc.client
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import requests


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "dealer_portal"
FAILURE_JOURNAL = OUT_DIR / "sparex_dealer_failure_journal.jsonl"
TRANSIENT_ODOO_STATUSES = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "<!doctype html",
    "<html",
    "bad gateway",
    "gateway timeout",
    "service unavailable",
    "temporarily unavailable",
    "timed out",
    "timeout",
)


class ExhaustedTransientOdooError(RuntimeError):
    pass


@dataclass
class OdooConnection:
    models: Any
    db: str
    uid: int
    api_key: str


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def execute(conn: OdooConnection, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return execute_with_retry(conn, model, method, args, kwargs)


def is_transient_odoo_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(status in text for status in TRANSIENT_ODOO_STATUSES)


def describe_error(exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    if "<!doctype html" in lowered or "<html" in lowered:
        if "service unavailable" in lowered or "503" in lowered:
            return "transient_odoo_html_503"
        if "bad gateway" in lowered or "502" in lowered:
            return "transient_odoo_html_502"
        if "gateway timeout" in lowered or "504" in lowered:
            return "transient_odoo_html_504"
        return "transient_html_error"
    return re.sub(r"\s+", " ", text)[:500]


def classify_failure(text: str) -> str:
    lowered = (text or "").lower()
    if "login" in lowered or "authentication" in lowered:
        return "login_or_auth"
    if "transient_odoo_html_503" in lowered or "service unavailable" in lowered or " 503" in lowered:
        return "odoo_503_html"
    if "transient_odoo_html_502" in lowered or "bad gateway" in lowered or " 502" in lowered:
        return "odoo_502_html"
    if "transient_odoo_html_504" in lowered or "gateway timeout" in lowered or " 504" in lowered:
        return "odoo_504_html"
    if "transient_html_error" in lowered or "<html" in lowered or "<!doctype html" in lowered:
        return "html_proxy_error"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "not_found" in lowered:
        return "not_found"
    return "other"


def read_recent_failure_journal(limit: int = 20) -> list[dict[str, Any]]:
    if not FAILURE_JOURNAL.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in FAILURE_JOURNAL.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def apply_failure_learning(args: argparse.Namespace) -> None:
    if args.disable_failure_learning or args.sku:
        return
    recent = read_recent_failure_journal()
    if not recent:
        return
    transient_kinds = {"odoo_503_html", "odoo_502_html", "odoo_504_html", "html_proxy_error", "timeout"}
    recent_transients = [row for row in recent[-5:] if row.get("failure_kind") in transient_kinds]
    if not recent_transients:
        return
    old_limit, old_delay, old_max_errors = args.limit, args.delay, args.max_errors
    args.limit = min(args.limit, 10)
    args.delay = max(args.delay, 1.0)
    args.max_errors = min(args.max_errors, 1)
    if (old_limit, old_delay, old_max_errors) != (args.limit, args.delay, args.max_errors):
        latest = recent_transients[-1]
        print(
            "Failure learning: recent "
            f"{latest.get('failure_kind')} on {latest.get('sku', 'unknown SKU')}; "
            f"using --limit {args.limit} --delay {args.delay} --max-errors {args.max_errors}.",
            flush=True,
        )


def append_failure_journal(
    *,
    sku: str,
    product_id: Any,
    status: str,
    error_text: str,
    source_url: str,
    report_csv: Path,
    recommended_next: str,
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    row = {
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "sku": sku,
        "product_id": product_id,
        "status": status,
        "error": error_text,
        "failure_kind": classify_failure(error_text),
        "source_url": source_url,
        "report_csv": str(report_csv),
        "recommended_next": recommended_next,
    }
    with FAILURE_JOURNAL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def execute_with_retry(
    conn: OdooConnection,
    model: str,
    method: str,
    args: list[Any],
    kwargs: dict[str, Any] | None = None,
    retries: int = 3,
    base_delay: float = 2.0,
):
    for attempt in range(retries + 1):
        try:
            return conn.models.execute_kw(conn.db, conn.uid, conn.api_key, model, method, args, kwargs or {})
        except (xmlrpc.client.ProtocolError, xmlrpc.client.Fault, socket.timeout, TimeoutError, OSError) as exc:
            if not is_transient_odoo_error(exc) or attempt >= retries:
                if is_transient_odoo_error(exc):
                    raise ExhaustedTransientOdooError(describe_error(exc)) from exc
                raise
            time.sleep(base_delay * (attempt + 1))
    raise RuntimeError(f"Odoo {model}.{method} failed after retry budget.")


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def money(value: Any) -> float:
    if value in (None, False, ""):
        return 0.0
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if not text:
        return 0.0
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def clean_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"<script\b.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_sku(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "").upper())
    match = re.search(r"S\.?(\d+)", text)
    return f"S.{match.group(1)}" if match else text


def slug_from_name(name: str, sku: str) -> str:
    base = re.sub(r"\s*-\s*Sparex\s+S\.\d+\s*$", "", name or "", flags=re.I)
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    number = re.sub(r"\D", "", sku or "")
    return f"{slug}-{number}.html" if slug and number else ""


def title_from_html(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    return clean_text(match.group(1)) if match else ""


def form_value(html: str, name: str) -> str:
    pattern = rf'name=["\']{re.escape(name)}["\'][^>]*value=["\']([^"\']+)'
    match = re.search(pattern, html, flags=re.I)
    return unescape(match.group(1)) if match else ""


def connect_odoo() -> OdooConnection:
    socket.setdefaulttimeout(90)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    return OdooConnection(xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object"), db, uid, api_key)


def login_sparex() -> requests.Session:
    login_url = required("SPAREX_DEALER_LOGIN_URL")
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 Southern Equipment Sparex dealer portal sync",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    response = session.get(login_url, timeout=45)
    response.raise_for_status()
    action_match = re.search(r'<form[^>]+id=["\']login-form["\'][^>]+action=["\']([^"\']+)', response.text, flags=re.I | re.S)
    post_url = unescape(action_match.group(1)) if action_match else urljoin(login_url, "/customer/account/loginPost/")
    payload = {
        "form_key": form_value(response.text, "form_key"),
        "login[username]": required("SPAREX_DEALER_USERNAME"),
        "login[password]": required("SPAREX_DEALER_PASSWORD"),
    }
    post = session.post(post_url, data=payload, timeout=45, allow_redirects=True)
    post.raise_for_status()
    probe = session.get(urljoin(login_url, "/customer/account/"), timeout=45)
    probe.raise_for_status()
    text = probe.text.lower()
    if "customer/account/logout" not in text and "logout" not in text:
        raise SystemExit("Sparex dealer login did not verify; credentials or site flow may need review.")
    return session


def polite_get(session: requests.Session, url: str, timeout: int = 45, retries: int = 2) -> requests.Response:
    last_response: requests.Response | None = None
    for attempt in range(retries + 1):
        response = session.get(url, timeout=timeout)
        last_response = response
        if response.status_code in {429, 500, 502, 503, 504}:
            if attempt >= retries:
                raise RuntimeError(f"Sparex returned HTTP {response.status_code}; stopping to avoid stressing the site.")
            time.sleep(2 * (attempt + 1))
            continue
        if response.status_code == 404:
            return response
        response.raise_for_status()
        return response
    if last_response is not None:
        last_response.raise_for_status()
    raise RuntimeError("Sparex request failed without a response.")


def load_odoo_sparex_products(
    conn: OdooConnection,
    limit: int,
    offset: int,
    only_needing_cost: bool,
    skus: list[str] | None = None,
) -> list[dict[str, Any]]:
    available = execute(conn, "product.template", "fields_get", [], {"attributes": ["string"]})
    fields = ["id", "default_code", "name", "standard_price", "description_sale", "active", "sale_ok"]
    for optional in ["southern_source_url", "southern_source_name", "image_1920", "website_published", "is_published", "website_url"]:
        if optional in available:
            fields.append(optional)
    if skus:
        domain: list[tuple[str, str, Any]] = [("default_code", "in", [clean_sku(sku) for sku in skus])]
    else:
        domain = [("default_code", "=like", "S.%")]
    if only_needing_cost:
        domain.append(("standard_price", "<=", 0))
    ids = execute(
        conn,
        "product.template",
        "search",
        [domain],
        {"offset": offset, "limit": limit, "order": "id", "context": {"active_test": False}},
    )
    if not ids:
        return []
    return execute(conn, "product.template", "read", [ids], {"fields": fields, "context": {"active_test": False}})


def verify_website_publication(conn: OdooConnection, product_id: Any, available: dict[str, Any]) -> dict[str, Any]:
    fields = ["id", "default_code", "name"]
    for optional in ["website_published", "is_published", "website_url", "image_1920", "list_price", "standard_price"]:
        if optional in available:
            fields.append(optional)
    rows = execute(conn, "product.template", "read", [[int(product_id)]], {"fields": fields, "context": {"active_test": False}})
    if not rows:
        return {
            "website_published": "",
            "is_published": "",
            "website_url": "",
            "publication_verification": "not_verified_missing_product",
        }
    product = rows[0]
    website_published = product.get("website_published")
    is_published = product.get("is_published")
    published = bool(website_published) or bool(is_published)
    image_ok = bool(product.get("image_1920"))
    price_ok = money(product.get("list_price")) > 0
    if published:
        verification = "published_verified"
    elif not price_ok:
        verification = "unpublished_price_missing_or_zero"
    elif not image_ok:
        verification = "unpublished_image_missing"
    else:
        verification = "unpublished_verified"
    return {
        "website_published": "Yes" if bool(website_published) else "No" if "website_published" in product else "",
        "is_published": "Yes" if bool(is_published) else "No" if "is_published" in product else "",
        "website_url": product.get("website_url") or "",
        "publication_verification": verification,
    }


def source_from_product(product: dict[str, Any], products_url: str) -> str:
    existing = str(product.get("southern_source_url") or "").strip()
    if existing.startswith("https://us.sparex.com/"):
        return existing
    match = re.search(r"Sparex source:\s*(https?://\S+)", product.get("description_sale") or "")
    if match:
        return match.group(1).rstrip(".,)")
    slug = slug_from_name(product.get("name", ""), product.get("default_code", ""))
    return urljoin(products_url, slug) if slug else ""


def page_matches_sku(html: str, sku: str, url: str) -> bool:
    number = re.sub(r"\D", "", sku)
    plain = clean_text(html).upper().replace(" ", "")
    return sku.upper().replace(" ", "") in plain or f"-{number}.HTML" in url.upper() or f"S{number}" in plain


def search_exact_product(session: requests.Session, products_url: str, sku: str) -> str:
    search_url = urljoin(products_url, f"catalogsearch/result/?q={quote_plus(sku)}")
    response = polite_get(session, search_url)
    number = re.sub(r"\D", "", sku)
    links = re.findall(r'href=["\']([^"\']*-(?:%s)\.html[^"\']*)["\']' % re.escape(number), response.text, flags=re.I)
    for link in links:
        return urljoin(products_url, unescape(link))
    return ""


def fetch_exact_product_page(session: requests.Session, products_url: str, product: dict[str, Any]) -> tuple[str, str, str]:
    sku = clean_sku(product.get("default_code"))
    candidates = [source_from_product(product, products_url)]
    searched = ""
    for candidate in [value for value in candidates if value]:
        response = polite_get(session, candidate)
        if response.status_code == 200 and page_matches_sku(response.text, sku, response.url):
            return response.url, response.text, "direct"
    searched = search_exact_product(session, products_url, sku)
    if searched:
        response = polite_get(session, searched)
        if response.status_code == 200 and page_matches_sku(response.text, sku, response.url):
            return response.url, response.text, "search"
    return "", "", "not_found"


def parse_dealer_page(html: str, url: str, sku: str) -> dict[str, Any]:
    plain = clean_text(html)
    prices = [money(match) for match in re.findall(r'"final_price"\s*:\s*([0-9][0-9,]*(?:\.\d+)?)', html)]
    prices.extend(money(match) for match in re.findall(r"\$\s*[0-9][0-9,]*(?:\.\d{2})?", plain))
    prices.extend(money(match) for match in re.findall(r'\$\\?\s*([0-9][0-9,]*(?:\.\d{2})?)', html))
    prices = [price for price in prices if price > 0]
    image_match = re.search(r"https?[^\"'\\ ]*imagelibrary_(?:med|sml|org)[^\"'\\ ]+?\.(?:jpg|jpeg|png)", html, flags=re.I)
    sku_text = ""
    sku_match = re.search(r'class=["\'][^"\']*thesku[^"\']*["\'][^>]*>(.*?)<', html, flags=re.I | re.S)
    if sku_match:
        sku_text = clean_sku(clean_text(sku_match.group(1)))
    title = title_from_html(html)
    pairs: list[dict[str, str]] = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", html, flags=re.I | re.S):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, flags=re.I | re.S)
        if len(cells) >= 2:
            key = clean_text(cells[0])
            value = clean_text(cells[1])
            if key and value and len(key) < 120 and len(value) < 500:
                pairs.append({"name": key, "value": value})
    return {
        "url": url,
        "sku_on_page": sku_text or sku,
        "title": title,
        "dealer_price": min(prices) if prices else 0.0,
        "image_url": image_match.group(0) if image_match else "",
        "specifications": pairs[:80],
    }


def ensure_sparex_supplier(conn: OdooConnection) -> int:
    rows = execute(conn, "res.partner", "search_read", [[("name", "=", "Sparex")]], {"fields": ["id"], "limit": 1})
    if rows:
        return int(rows[0]["id"])
    return execute(conn, "res.partner", "create", [{"name": "Sparex", "is_company": True, "supplier_rank": 1}])


def upsert_supplier_cost(conn: OdooConnection, product_id: int, partner_id: int, sku: str, cost: float) -> str:
    rows = execute(
        conn,
        "product.supplierinfo",
        "search_read",
        [[("product_tmpl_id", "=", product_id), ("partner_id", "=", partner_id)]],
        {"fields": ["id", "price"], "limit": 1},
    )
    values = {"partner_id": partner_id, "product_tmpl_id": product_id, "product_code": sku, "price": cost, "min_qty": 1}
    if rows:
        execute(conn, "product.supplierinfo", "write", [[int(rows[0]["id"])], values])
        return "supplier_cost_updated"
    execute(conn, "product.supplierinfo", "create", [values])
    return "supplier_cost_created"


def apply_odoo_updates(
    conn: OdooConnection,
    product: dict[str, Any],
    evidence: dict[str, Any],
    apply_cost: bool,
    apply_source_url: bool,
    apply_supplierinfo: bool,
    partner_id: int | None,
) -> list[str]:
    statuses: list[str] = []
    values: dict[str, Any] = {}
    if apply_cost and evidence["dealer_price"] > 0:
        values["standard_price"] = evidence["dealer_price"]
        statuses.append("standard_price_updated")
    if apply_source_url and evidence["url"]:
        values["southern_source_url"] = evidence["url"]
        values["southern_source_name"] = "Sparex Dealer Portal"
        statuses.append("source_url_updated")
    if values:
        execute(conn, "product.template", "write", [[int(product["id"])], values])
    if apply_supplierinfo and partner_id and evidence["dealer_price"] > 0:
        statuses.append(upsert_supplier_cost(conn, int(product["id"]), partner_id, clean_sku(product.get("default_code")), evidence["dealer_price"]))
    return statuses or ["evidence_only"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Log into Sparex dealer portal, harvest exact product evidence, and optionally update Odoo Sparex cost/source fields.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sku", action="append", default=[], help="Specific Sparex SKU to process. Can be repeated.")
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--max-errors", type=int, default=3, help="Stop after this many page-level errors.")
    parser.add_argument("--only-needing-cost", action="store_true", default=True)
    parser.add_argument("--all-sparex", action="store_true", help="Ignore the default standard_price <= 0 queue filter.")
    parser.add_argument("--apply-cost", action="store_true", help="Write exact dealer price to product.template standard_price.")
    parser.add_argument("--apply-source-url", action="store_true", help="Write exact Sparex URL to product southern_source_url.")
    parser.add_argument("--apply-supplierinfo", action="store_true", help="Create/update Sparex supplierinfo price lines from exact dealer price.")
    parser.add_argument("--disable-failure-learning", action="store_true", help="Do not auto-adjust limit/delay/max-errors from recent transient failures.")
    args = parser.parse_args()

    load_env()
    products_url = required("SPAREX_DEALER_PRODUCTS_URL").rstrip("/") + "/"
    conn = connect_odoo()
    session = login_sparex()
    apply_failure_learning(args)
    product_fields = execute(conn, "product.template", "fields_get", [], {"attributes": ["string"]})
    products = load_odoo_sparex_products(conn, args.limit, args.offset, only_needing_cost=not args.all_sparex, skus=args.sku)
    partner_id = ensure_sparex_supplier(conn) if args.apply_supplierinfo else None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUT_DIR / f"sparex_dealer_portal_evidence_{timestamp}.json"
    csv_path = OUT_DIR / f"sparex_dealer_portal_sync_report_{timestamp}.csv"

    evidence_rows: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    publication_counts: dict[str, int] = {}
    for index, product in enumerate(products, start=1):
        sku = clean_sku(product.get("default_code"))
        status = "not_found"
        evidence: dict[str, Any] = {"url": "", "dealer_price": 0.0, "image_url": "", "specifications": []}
        try:
            url, html, resolve_method = fetch_exact_product_page(session, products_url, product)
            if url and html:
                evidence = parse_dealer_page(html, url, sku)
                status = "harvested" if evidence["dealer_price"] > 0 else "harvested_no_price"
                update_statuses = apply_odoo_updates(
                    conn,
                    product,
                    evidence,
                    apply_cost=args.apply_cost,
                    apply_source_url=args.apply_source_url,
                    apply_supplierinfo=args.apply_supplierinfo,
                    partner_id=partner_id,
                )
            else:
                resolve_method = "not_found"
                update_statuses = ["not_found"]
        except Exception as exc:
            resolve_method = "error"
            error_text = describe_error(exc)
            update_statuses = [f"error: {error_text}"]
            status = "error"
            should_stop_for_cooldown = isinstance(exc, ExhaustedTransientOdooError)
            recommended_next = "retry targeted SKU gently" if args.sku else "cooldown: --limit 10 --delay 1.0 --max-errors 1"
            append_failure_journal(
                sku=sku,
                product_id=product.get("id"),
                status=status,
                error_text=error_text,
                source_url=evidence.get("url", ""),
                report_csv=csv_path,
                recommended_next=recommended_next,
            )
            if should_stop_for_cooldown or sum(1 for row in report_rows if row.get("Status") == "error") + 1 >= args.max_errors:
                report_rows.append(
                    {
                        "SKU": sku,
                        "Product ID": product.get("id"),
                        "Name": product.get("name", ""),
                        "Status": status,
                        "Resolve Method": resolve_method,
                        "Dealer Cost": f"{money(evidence.get('dealer_price')):.2f}" if evidence.get("dealer_price") else "",
                        "Source URL": evidence.get("url", ""),
                        "Image URL": evidence.get("image_url", ""),
                        "Odoo Updates": "; ".join(update_statuses),
                    }
                )
                print(f"{index}/{len(products)} {sku}: {status} {'; '.join(update_statuses)}", flush=True)
                if should_stop_for_cooldown:
                    print("Stopping early after exhausted transient Odoo/HTML error; cooldown recommended.", flush=True)
                else:
                    print("Stopping early after repeated Sparex/Odoo errors.", flush=True)
                break
        publication = verify_website_publication(conn, product.get("id"), product_fields)
        publication_status = publication.get("publication_verification", "not_verified")
        publication_counts[publication_status] = publication_counts.get(publication_status, 0) + 1
        evidence_row = {
            "sku": sku,
            "product_id": product.get("id"),
            "name": product.get("name", ""),
            "status": status,
            "resolve_method": resolve_method,
            "source_url": evidence.get("url", ""),
            "dealer_price": evidence.get("dealer_price", 0.0),
            "image_url": evidence.get("image_url", ""),
            "title": evidence.get("title", ""),
            "specifications": evidence.get("specifications", []),
            "website_published": publication.get("website_published", ""),
            "is_published": publication.get("is_published", ""),
            "website_url": publication.get("website_url", ""),
            "publication_verification": publication_status,
            "harvested_at": datetime.now().isoformat(timespec="seconds"),
        }
        evidence_rows.append(evidence_row)
        report_rows.append(
            {
                "SKU": sku,
                "Product ID": product.get("id"),
                "Name": product.get("name", ""),
                "Status": status,
                "Resolve Method": resolve_method,
                "Dealer Cost": f"{money(evidence.get('dealer_price')):.2f}" if evidence.get("dealer_price") else "",
                "Source URL": evidence.get("url", ""),
                "Image URL": evidence.get("image_url", ""),
                "Odoo Updates": "; ".join(update_statuses),
                "Website Published": publication.get("website_published", ""),
                "Is Published": publication.get("is_published", ""),
                "Website URL": publication.get("website_url", ""),
                "Publication Verification": publication_status,
            }
        )
        print(f"{index}/{len(products)} {sku}: {status} {'; '.join(update_statuses)}; {publication_status}", flush=True)
        time.sleep(args.delay)

    json_path.write_text(json.dumps({"records": evidence_rows}, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        fields = [
            "SKU",
            "Product ID",
            "Name",
            "Status",
            "Resolve Method",
            "Dealer Cost",
            "Source URL",
            "Image URL",
            "Odoo Updates",
            "Website Published",
            "Is Published",
            "Website URL",
            "Publication Verification",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report_rows)

    priced = sum(1 for row in evidence_rows if money(row.get("dealer_price")) > 0)
    print(f"Evidence JSON: {json_path}")
    print(f"Report CSV: {csv_path}")
    print(f"Mode: {'apply' if (args.apply_cost or args.apply_source_url or args.apply_supplierinfo) else 'dry_run'}")
    print(f"Products checked: {len(products)}")
    print(f"Dealer costs found: {priced}")
    print(f"Cost writes enabled: {args.apply_cost}")
    print(f"Source URL writes enabled: {args.apply_source_url}")
    print(f"Supplierinfo writes enabled: {args.apply_supplierinfo}")
    for key, count in sorted(publication_counts.items()):
        print(f"Publication verification {key}: {count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except requests.RequestException as exc:
        print(f"Sparex HTTP error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)

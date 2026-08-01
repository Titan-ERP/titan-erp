"""Throttled, resumable inventory of authenticated Sparex listing pages.

This worker never opens a product-detail page and never creates or updates an
Odoo product. It captures exact SKU links and listing images, archives each
page, and writes only the dedicated Odoo discovery run/page/item models.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from lxml import html as lxml_html

from scripts.odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig
from scripts.odoo_runtime.client import load_env_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODOO_ENV = ROOT / "odoo_connection.env"
DEFAULT_ARTIFACT_ROOT = ROOT / "outputs" / "sparex-catalog-discovery"
WORKFLOW = "sparex-discovery-queue"
CONFIRMATION = "sparex-discovery-queue"
PARSER_VERSION = "sparex-listing-v1"
SCHEMA_VERSION = "1.0"
SPAREX_HOST = "us.sparex.com"
MAX_PAGE_ITEMS = 100
PORTAL_COOLDOWN_STATUSES = {429, 500, 502, 503, 504}
SKU_FROM_URL = re.compile(r"-(?P<digits>\d+)\.html(?:$|[?#])", re.IGNORECASE)
SKU_IN_TEXT = re.compile(r"(?<![A-Z0-9])S[.\s-]?0*(?P<digits>\d+)(?!\d)", re.IGNORECASE)


class PortalCooldownError(RuntimeError):
    """A real Sparex-origin signal that requires a cooldown."""


@dataclass
class RequestThrottle:
    seconds: float
    last_request: float = 0.0

    def wait(self) -> None:
        remaining = self.seconds - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request = time.monotonic()


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_sku(digits: str) -> str:
    return f"S.{int(digits)}"


def exact_sparex_product_url(url: str, sku: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold().rstrip(".") != SPAREX_HOST:
        return False
    match = SKU_FROM_URL.search(parsed.path)
    return bool(match and normalized_sku(match.group("digits")) == sku)


def _listing_container(anchor):
    current = anchor
    for _depth in range(7):
        if current is None:
            break
        classes = f" {current.get('class', '').casefold()} "
        if any(
            token in classes for token in (" product-item ", " product-item-info ", " item product ", " product-card ")
        ):
            return current
        current = current.getparent()
    return anchor.getparent() or anchor


def _image_url(container, page_url: str) -> str:
    for image in container.xpath(".//img"):
        for attribute in ("data-src", "data-original", "data-lazy", "src"):
            value = (image.get(attribute) or "").strip()
            if value and not value.startswith("data:"):
                candidate = urljoin(page_url, unescape(value))
                if urlsplit(candidate).scheme.casefold() == "https" and urlsplit(candidate).hostname:
                    return candidate
        srcset = (image.get("srcset") or "").strip()
        if srcset:
            candidate = urljoin(page_url, unescape(srcset.split(",", 1)[0].strip().split(" ", 1)[0]))
            if urlsplit(candidate).scheme.casefold() == "https" and urlsplit(candidate).hostname:
                return candidate
    return ""


def parse_listing_page(content: bytes | str, page_url: str) -> dict[str, Any]:
    """Extract exact product links and listing images without navigating them."""
    document = lxml_html.fromstring(content, base_url=page_url)
    by_sku: dict[str, list[dict[str, str]]] = {}
    for anchor in document.xpath("//a[@href]"):
        source_url = urljoin(page_url, unescape((anchor.get("href") or "").strip()))
        url_match = SKU_FROM_URL.search(urlsplit(source_url).path)
        if not url_match:
            continue
        sku = normalized_sku(url_match.group("digits"))
        if not exact_sparex_product_url(source_url, sku):
            continue
        container = _listing_container(anchor)
        visible_text = " ".join(" ".join(container.itertext()).split())
        text_skus = {normalized_sku(match.group("digits")) for match in SKU_IN_TEXT.finditer(visible_text)}
        attribute_skus = set()
        for node in [container, *container.xpath(".//*[@data-product-sku or @data-sku]")]:
            for attribute in ("data-product-sku", "data-sku"):
                value = (node.get(attribute) or "").strip()
                match = SKU_IN_TEXT.fullmatch(value)
                if match:
                    attribute_skus.add(normalized_sku(match.group("digits")))
        # URL digits are the identity anchor. Conflicting explicit card SKUs are ambiguous.
        explicit_skus = text_skus | attribute_skus
        source_state = "ambiguous" if explicit_skus and explicit_skus != {sku} else "verified"
        image_url = _image_url(container, page_url)
        if not image_url and source_state == "verified":
            source_state = "missing_image"
        by_sku.setdefault(sku, []).append(
            {
                "sku": sku,
                "source_url": source_url,
                "image_url": image_url,
                "source_state": source_state,
            }
        )

    items: list[dict[str, str]] = []
    for _sku, candidates in sorted(by_sku.items(), key=lambda row: int(row[0].split(".", 1)[1])):
        unique = {(row["source_url"], row["image_url"], row["source_state"]) for row in candidates}
        selected = dict(candidates[0])
        if len(unique) > 1:
            selected["source_state"] = "ambiguous"
        items.append(selected)
    if len(items) > MAX_PAGE_ITEMS:
        raise RuntimeError(f"listing_page_exceeded_{MAX_PAGE_ITEMS}_items")
    return {"items": items, "next_url": find_next_listing_url(document, page_url)}


def find_next_listing_url(document, page_url: str) -> str:
    candidates = []
    xpaths = (
        "//a[translate(@rel,'NEXT','next')='next']/@href",
        "//li[contains(concat(' ', normalize-space(@class), ' '), ' pages-item-next ')]//a/@href",
        "//a[contains(concat(' ', normalize-space(@class), ' '), ' action ') and contains(concat(' ', normalize-space(@class), ' '), ' next ')]/@href",
        "//a[translate(@aria-label,'NEXT','next')='next']/@href",
        "//a[translate(@title,'NEXT','next')='next']/@href",
    )
    for xpath in xpaths:
        for value in document.xpath(xpath):
            candidate = urljoin(page_url, unescape(str(value).strip()))
            current = urlsplit(page_url)
            parsed = urlsplit(candidate)
            query = parse_qs(parsed.query)
            same_host = (parsed.hostname or "").casefold().rstrip(".") == SPAREX_HOST
            listing_shape = parsed.path == current.path or bool({"p", "page"} & set(query))
            if parsed.scheme.casefold() == "https" and same_host and listing_shape and candidate != page_url:
                candidates.append(candidate)
    unique = list(dict.fromkeys(candidates))
    if len(unique) > 1:
        raise RuntimeError("ambiguous_listing_pagination")
    return unique[0] if unique else ""


def _checked_request(session: requests.Session, throttle: RequestThrottle, method: str, url: str, **kwargs):
    throttle.wait()
    response = session.request(method, url, timeout=45, allow_redirects=True, **kwargs)
    if response.status_code in PORTAL_COOLDOWN_STATUSES:
        raise PortalCooldownError(f"portal_http_{response.status_code}")
    response.raise_for_status()
    return response


def authenticated_session(env_file: Path, throttle_seconds: float) -> tuple[requests.Session, RequestThrottle, str]:
    load_env_file(env_file.resolve())
    login_url = os.environ.get("SPAREX_DEALER_LOGIN_URL", "").strip()
    products_url = os.environ.get("SPAREX_DEALER_PRODUCTS_URL", "").strip()
    username = os.environ.get("SPAREX_DEALER_USERNAME", "").strip()
    password = os.environ.get("SPAREX_DEALER_PASSWORD", "").strip()
    if not all((login_url, products_url, username, password)):
        raise RuntimeError("sparex_dealer_environment_incomplete")
    session = requests.Session()
    session.headers.update({"User-Agent": "Titan-Sparex-Listing-Discovery/1.0"})
    throttle = RequestThrottle(max(3.0, throttle_seconds))
    first = _checked_request(session, throttle, "GET", login_url)
    form_key_match = re.search(r'name=["\']form_key["\'][^>]*value=["\']([^"\']+)', first.text, flags=re.IGNORECASE)
    form_action_match = re.search(r'<form\b[^>]*action=["\']([^"\']+)["\'][^>]*>', first.text, flags=re.IGNORECASE)
    payload = {"login[username]": username, "login[password]": password}
    if form_key_match:
        payload["form_key"] = form_key_match.group(1)
    post_url = urljoin(login_url, unescape(form_action_match.group(1))) if form_action_match else login_url
    logged_in = _checked_request(session, throttle, "POST", post_url, data=payload)
    login_path = urlsplit(login_url).path.casefold().rstrip("/")
    if urlsplit(logged_in.url).path.casefold().rstrip("/") == login_path:
        raise PortalCooldownError("dealer_login_failed")
    return session, throttle, products_url


def _archive(store: ArtifactStore, name: str, payload: Any, bucket: str, prefix: str) -> dict[str, Any]:
    record = store.write_json(
        name, payload, record_count=len(payload.get("items") or []) if isinstance(payload, dict) else 1
    )
    if not bucket:
        raise RuntimeError("SOUTHERN_PRODUCT_ARTIFACT_BUCKET or --s3-bucket is required.")
    return store.archive_s3(record, bucket=bucket, prefix=prefix)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ODOO_ENV)
    parser.add_argument("--dealer-env-file", type=Path, default=DEFAULT_ODOO_ENV)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--s3-bucket", default=os.environ.get("SOUTHERN_PRODUCT_ARTIFACT_BUCKET", ""))
    parser.add_argument("--s3-prefix", default="sparex-product-catalog/sparex-discovery/production")
    parser.add_argument("--run-key", required=True)
    parser.add_argument("--seed-url", default="")
    parser.add_argument("--throttle-seconds", type=float, default=3.0)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    return parser


def _existing_run(client: OdooClient, run_key: str) -> dict[str, Any] | None:
    rows = client.call(
        "southern.sparex.discovery.run",
        "search_read",
        domain=[("idempotency_key", "=", run_key)],
        fields=[
            "id",
            "idempotency_key",
            "state",
            "cursor_url",
            "cursor_url_sha256",
            "seed_url",
            "seed_url_sha256",
            "plan_artifact_uri",
            "plan_sha256",
            "throttle_seconds",
            "page_count",
        ],
        limit=1,
    )
    return rows[0] if rows else None


def main() -> int:
    args = build_parser().parse_args()
    throttle_seconds = max(3.0, float(args.throttle_seconds))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    if not client.count("ir.model", [("model", "=", "southern.sparex.discovery.run")]):
        raise RuntimeError("Upgrade Southern Parts Intelligence before running Sparex discovery.")
    existing = _existing_run(client, args.run_key)
    seed_url = (args.seed_url or (existing or {}).get("seed_url") or "").strip()
    if not seed_url:
        load_env_file(args.dealer_env_file.resolve())
        seed_url = os.environ.get("SPAREX_DEALER_PRODUCTS_URL", "").strip()
    if not seed_url:
        raise RuntimeError("An explicit Sparex listing seed URL is required.")

    run_stamp = utc_stamp()
    run_key_hash = sha256_text(args.run_key)[:16]
    store = ArtifactStore(args.artifact_root / run_key_hash / run_stamp, schema_version=SCHEMA_VERSION)
    archive_prefix = f"{args.s3_prefix.rstrip('/')}/{run_key_hash}/{run_stamp}"
    if not args.apply:
        session, throttle, _products_url = authenticated_session(args.dealer_env_file, throttle_seconds)
        response = _checked_request(session, throttle, "GET", seed_url)
        if "customer/account/login" in response.url.casefold():
            raise PortalCooldownError("dealer_session_lost")
        parsed = parse_listing_page(response.content, seed_url)
        print(
            json.dumps(
                {
                    "mode": "read_only",
                    "run_key_sha256": sha256_text(args.run_key),
                    "page_url_sha256": sha256_text(seed_url),
                    "item_count": len(parsed["items"]),
                    "next_cursor_present": bool(parsed["next_url"]),
                },
                sort_keys=True,
            )
        )
        return 0

    gate = ApplyGate(WORKFLOW, True, args.confirm, args.reason, 1)
    gate.authorize(1)
    if not args.s3_bucket:
        raise RuntimeError("An S3 artifact bucket is required for discovery apply mode.")
    if existing:
        run = existing
    else:
        plan = {
            "schema_version": SCHEMA_VERSION,
            "workflow": WORKFLOW,
            "run_key": args.run_key,
            "seed_url": seed_url,
            "seed_url_sha256": sha256_text(seed_url),
            "parser_version": PARSER_VERSION,
            "throttle_seconds": throttle_seconds,
            "max_pages_per_checkpoint": 1,
            "product_creation_authorized": False,
        }
        plan_record = _archive(store, "plan.json", plan, args.s3_bucket, archive_prefix)
        run = client.call(
            "southern.sparex.discovery.run",
            "start_discovery_run",
            values={
                "idempotency_key": args.run_key,
                "seed_url": seed_url,
                "seed_url_sha256": sha256_text(seed_url),
                "plan_artifact_uri": plan_record["artifact_uri"],
                "plan_sha256": plan_record["sha256"],
                "parser_version": PARSER_VERSION,
                "schema_version": SCHEMA_VERSION,
                "throttle_seconds": throttle_seconds,
                "max_pages_per_checkpoint": 1,
                "max_items_per_page": MAX_PAGE_ITEMS,
            },
        )
    claim = client.call(
        "southern.sparex.discovery.run",
        "claim_discovery_checkpoint",
        run_id=int(run["id"]),
        worker_id=args.worker_id,
        lease_seconds=180,
    )
    if not claim.get("claimed"):
        print(json.dumps({"mode": "apply", "claimed": False, "state": claim.get("state")}, sort_keys=True))
        return 0

    cursor_url = str(claim["cursor_url"])
    try:
        session, throttle, _products_url = authenticated_session(args.dealer_env_file, throttle_seconds)
        response = _checked_request(session, throttle, "GET", cursor_url)
        if "customer/account/login" in response.url.casefold():
            raise PortalCooldownError("dealer_session_lost")
        parsed = parse_listing_page(response.content, cursor_url)
        if not parsed["items"]:
            raise RuntimeError("empty_or_unrecognized_listing_page")
        page_payload = {
            "schema_version": SCHEMA_VERSION,
            "workflow": WORKFLOW,
            "run_id": int(run["id"]),
            "run_key_sha256": sha256_text(args.run_key),
            "page_number": int(claim.get("page_count") or 0) + 1,
            "page_url": cursor_url,
            "page_url_sha256": sha256_text(cursor_url),
            "effective_url_sha256": sha256_text(response.url),
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "parser_version": PARSER_VERSION,
            "items": parsed["items"],
            "next_url": parsed["next_url"],
        }
        page_record = _archive(store, "listing-page.json", page_payload, args.s3_bucket, archive_prefix)
        recorded = client.call(
            "southern.sparex.discovery.run",
            "record_discovery_page",
            run_id=int(run["id"]),
            worker_id=args.worker_id,
            page={
                "page_url": cursor_url,
                "page_sha256": page_payload["response_sha256"],
                "artifact_uri": page_record["artifact_uri"],
                "artifact_sha256": page_record["sha256"],
                "items": parsed["items"],
                "next_url": parsed["next_url"],
            },
        )
        result = {
            "schema_version": SCHEMA_VERSION,
            "workflow": WORKFLOW,
            "run_id": int(run["id"]),
            "run_key_sha256": sha256_text(args.run_key),
            "page_artifact_uri": page_record["artifact_uri"],
            "page_artifact_sha256": page_record["sha256"],
            "recorded": recorded,
            "product_creation_authorized": False,
        }
        result_record = _archive(store, "result.json", result, args.s3_bucket, archive_prefix)
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "run_id": int(run["id"]),
                    "state": recorded.get("state"),
                    "observed": recorded.get("observed", 0),
                    "counts": recorded.get("counts", {}),
                    "result_sha256": result_record["sha256"],
                    "result_uri": result_record["artifact_uri"],
                },
                sort_keys=True,
            )
        )
        return 0
    except PortalCooldownError as exc:
        client.call(
            "southern.sparex.discovery.run",
            "record_discovery_failure",
            run_id=int(run["id"]),
            worker_id=args.worker_id,
            error_code=str(exc),
            cooldown=True,
        )
        raise
    except Exception as exc:
        client.call(
            "southern.sparex.discovery.run",
            "record_discovery_failure",
            run_id=int(run["id"]),
            worker_id=args.worker_id,
            error_code=type(exc).__name__,
            cooldown=False,
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())

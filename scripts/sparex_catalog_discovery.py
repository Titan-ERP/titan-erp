"""Throttled, resumable reconciliation of authenticated Sparex listing pages.

The worker never opens a product-detail page or uses Sparex search. It captures
exact SKU links, listing titles, and images, archives each page, and can invoke
Odoo's separately gated draft-product creation contract for exact missing SKUs.
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
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from lxml import html as lxml_html

from scripts.odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig
from scripts.odoo_runtime.client import load_env_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODOO_ENV = ROOT / "odoo_connection.env"
DEFAULT_ARTIFACT_ROOT = ROOT / "outputs" / "sparex-catalog-discovery"
WORKFLOW = "sparex-discovery-queue"
CONFIRMATION = "sparex-discovery-queue"
PARSER_VERSION = "sparex-listing-frontier-v4"
SCHEMA_VERSION = "1.1"
SPAREX_HOST = "us.sparex.com"
MAX_PAGE_ITEMS = 100
MAX_CHECKPOINT_PAGES = 10
MAX_PRODUCT_CREATION_BATCH = 5
PORTAL_COOLDOWN_STATUSES = {429, 500, 502, 503, 504}
SKU_FROM_URL = re.compile(r"-(?P<digits>\d+)\.html(?:$|[?#])", re.IGNORECASE)
SKU_IN_TEXT = re.compile(r"(?<![A-Z0-9])S[.\s-]?0*(?P<digits>\d+)(?!\d)", re.IGNORECASE)
PRODUCT_DETAIL_PATH = re.compile(r"(?:^|[-/])\d+\.html$", re.IGNORECASE)
LISTING_PATH_DENY_PREFIXES = (
    "/about",
    "/account",
    "/catalogue",
    "/checkout",
    "/contact",
    "/cookie",
    "/customer",
    "/help",
    "/login",
    "/media",
    "/privacy",
    "/sales",
    "/search",
    "/wishlist",
)
MAX_LISTING_LINKS_PER_PAGE = 5_000


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
    parent = anchor.getparent()
    return parent if parent is not None else anchor


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


def _listing_title(container, page_url: str, source_url: str) -> str:
    candidates: list[str] = []
    for anchor in container.xpath(".//a[@href]"):
        candidate_url = urljoin(page_url, unescape((anchor.get("href") or "").strip()))
        if candidate_url != source_url:
            continue
        value = " ".join(" ".join(anchor.itertext()).split()).strip()
        value = SKU_IN_TEXT.sub("", value).strip(" -|:/")
        if value and re.search(r"[A-Za-z]", value):
            candidates.append(value[:255])
    return max(candidates, key=lambda value: (len(value.split()), len(value)), default="")


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
        listing_title = _listing_title(container, page_url, source_url)
        if not image_url and source_state == "verified":
            source_state = "missing_image"
        by_sku.setdefault(sku, []).append(
            {
                "sku": sku,
                "listing_title": listing_title,
                "source_url": source_url,
                "image_url": image_url,
                "source_state": source_state,
            }
        )

    items: list[dict[str, str]] = []
    for _sku, candidates in sorted(by_sku.items(), key=lambda row: int(row[0].split(".", 1)[1])):
        # Sparex listing cards can expose the same product link more than once
        # (for example, the card wrapper and its title).  A title-only anchor
        # has no image of its own, but that must not downgrade the image-backed
        # card for the same exact product URL.
        source_urls = {row["source_url"] for row in candidates}
        image_urls = {row["image_url"] for row in candidates if row["image_url"]}
        selected = dict(next((row for row in candidates if row["image_url"]), candidates[0]))
        selected["listing_title"] = max(
            (row["listing_title"] for row in candidates if row["listing_title"]),
            key=lambda value: (len(value.split()), len(value)),
            default="",
        )
        if (
            len(source_urls) > 1
            or len(image_urls) > 1
            or any(row["source_state"] == "ambiguous" for row in candidates)
        ):
            selected["source_state"] = "ambiguous"
        elif selected["image_url"]:
            selected["source_state"] = "verified"
        else:
            selected["source_state"] = "missing_image"
        items.append(selected)
    if len(items) > MAX_PAGE_ITEMS:
        raise RuntimeError(f"listing_page_exceeded_{MAX_PAGE_ITEMS}_items")
    return {
        "items": items,
        "next_url": find_next_listing_url(document, page_url),
        "listing_urls": find_listing_frontier_urls(document, page_url),
    }


def canonical_listing_url(value: str, page_url: str) -> str:
    candidate = urljoin(page_url, unescape((value or "").strip()))
    parsed = urlsplit(candidate)
    if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold().rstrip(".") != SPAREX_HOST:
        return ""
    path = parsed.path.rstrip("/") or "/"
    if PRODUCT_DETAIL_PATH.search(path) or path.casefold().startswith(LISTING_PATH_DENY_PREFIXES):
        return ""
    query = parsed.query if {"p", "page"} & set(parse_qs(parsed.query)) else ""
    category_shape = path.endswith(".html") or path == "/" or bool(query)
    if not category_shape:
        return ""
    return urlunsplit(("https", SPAREX_HOST, path, query, ""))


def find_listing_frontier_urls(document, page_url: str) -> list[str]:
    current = canonical_listing_url(page_url, page_url) or page_url
    candidates = []
    for value in document.xpath("//a[@href]/@href"):
        candidate = canonical_listing_url(str(value), page_url)
        if candidate and candidate != current:
            candidates.append(candidate)
    unique = list(dict.fromkeys(candidates))
    if len(unique) > MAX_LISTING_LINKS_PER_PAGE:
        raise RuntimeError(f"listing_frontier_exceeded_{MAX_LISTING_LINKS_PER_PAGE}_links")
    return unique


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
    parser.add_argument("--max-pages-per-checkpoint", type=int, default=5)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--create-missing-products", action="store_true")
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
            "max_pages_per_checkpoint",
            "page_count",
            "recovery_state",
            "consecutive_failure_count",
        ],
        limit=1,
    )
    return rows[0] if rows else None


def adaptive_checkpoint_pages(requested: int, existing: dict[str, Any] | None) -> int:
    bounded = max(1, min(int(requested or 5), MAX_CHECKPOINT_PAGES))
    if not existing:
        return min(5, bounded)
    if existing.get("recovery_state") not in {None, "healthy"} or int(existing.get("consecutive_failure_count") or 0):
        return min(2, bounded)
    if int(existing.get("page_count") or 0) < 10:
        return min(5, bounded)
    return bounded


def main() -> int:
    args = build_parser().parse_args()
    throttle_seconds = max(3.0, float(args.throttle_seconds))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    if not client.count("ir.model", [("model", "=", "southern.sparex.discovery.run")]):
        raise RuntimeError("Upgrade Southern Parts Intelligence before running Sparex discovery.")
    existing = _existing_run(client, args.run_key)
    checkpoint_pages = adaptive_checkpoint_pages(args.max_pages_per_checkpoint, existing)
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
                    "frontier_url_count": len(parsed["listing_urls"]),
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
            "max_pages_per_checkpoint": checkpoint_pages,
            "max_pages_total": 10000,
            "product_creation_authorized": bool(args.create_missing_products),
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
                "max_pages_per_checkpoint": checkpoint_pages,
                "max_items_per_page": MAX_PAGE_ITEMS,
                "max_pages_total": 10000,
            },
        )
    run = client.call(
        "southern.sparex.discovery.run",
        "configure_discovery_checkpoint",
        run_id=int(run["id"]),
        max_pages_per_checkpoint=checkpoint_pages,
    )
    run = client.call(
        "southern.sparex.discovery.run",
        "prepare_reconciliation_run",
        run_id=int(run["id"]),
    )
    current_claim: dict[str, Any] | None = None
    try:
        session = throttle = None
        pages = []
        aggregate_counts = {"matched": 0, "missing": 0, "duplicate": 0, "review": 0}
        observed = corrected = stale = created_count = creation_operations = 0
        created_products: list[dict[str, Any]] = []
        terminal_state = str(run.get("state") or "ready")
        for _index in range(checkpoint_pages):
            current_claim = client.call(
                "southern.sparex.discovery.run",
                "claim_discovery_checkpoint",
                run_id=int(run["id"]),
                worker_id=args.worker_id,
                lease_seconds=180,
            )
            if not current_claim.get("claimed"):
                terminal_state = str(current_claim.get("state") or terminal_state)
                break
            if session is None or throttle is None:
                session, throttle, _products_url = authenticated_session(args.dealer_env_file, throttle_seconds)
            cursor_url = str(current_claim["cursor_url"])
            response = _checked_request(session, throttle, "GET", cursor_url)
            if "customer/account/login" in response.url.casefold():
                raise PortalCooldownError("dealer_session_lost")
            parsed = parse_listing_page(response.content, cursor_url)
            page_number = int(current_claim.get("page_count") or 0) + 1
            page_payload = {
                "schema_version": SCHEMA_VERSION,
                "workflow": WORKFLOW,
                "run_id": int(run["id"]),
                "run_key_sha256": sha256_text(args.run_key),
                "page_number": page_number,
                "page_url": cursor_url,
                "page_url_sha256": sha256_text(cursor_url),
                "effective_url_sha256": sha256_text(response.url),
                "response_sha256": hashlib.sha256(response.content).hexdigest(),
                "parser_version": PARSER_VERSION,
                "items": parsed["items"],
                "next_url": parsed["next_url"],
                "listing_urls": parsed["listing_urls"],
            }
            page_record = _archive(
                store,
                f"listing-page-{page_number:05d}.json",
                page_payload,
                args.s3_bucket,
                archive_prefix,
            )
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
                    "listing_urls": parsed["listing_urls"],
                },
            )
            if args.create_missing_products and creation_operations < MAX_PRODUCT_CREATION_BATCH:
                creation_records = client.call(
                    "southern.sparex.discovery.item",
                    "prepare_product_creation_plan",
                    item_ids=recorded.get("item_ids") or [],
                    limit=MAX_PRODUCT_CREATION_BATCH - creation_operations,
                )
                if creation_records:
                    creation_plan = {
                        "schema_version": SCHEMA_VERSION,
                        "workflow": WORKFLOW,
                        "operation": "page_driven_draft_product_creation",
                        "run_id": int(run["id"]),
                        "page_number": page_number,
                        "page_artifact_uri": page_record["artifact_uri"],
                        "page_artifact_sha256": page_record["sha256"],
                        "records": creation_records,
                    }
                    creation_plan_record = _archive(
                        store,
                        f"product-creation-plan-{page_number:05d}.json",
                        creation_plan,
                        args.s3_bucket,
                        archive_prefix,
                    )
                    created = client.call(
                        "southern.sparex.discovery.item",
                        "apply_product_creation_plan",
                        records=creation_records,
                        plan_artifact_uri=creation_plan_record["artifact_uri"],
                        plan_sha256=creation_plan_record["sha256"],
                        confirmation="sparex-page-driven-draft-creation",
                        reason="Odoo-approved exact listing-page draft product creation",
                    )
                    created_products.extend(created)
                    creation_operations += len(created)
                    created_count += sum(1 for row in created if row.get("created"))
            current_claim = None
            terminal_state = str(recorded.get("state") or terminal_state)
            observed += int(recorded.get("observed") or 0)
            corrected += int(recorded.get("corrected") or 0)
            stale = max(stale, int(recorded.get("stale") or 0))
            for key in aggregate_counts:
                aggregate_counts[key] += int((recorded.get("counts") or {}).get(key) or 0)
            pages.append(
                {
                    "page_number": page_number,
                    "page_url_sha256": page_payload["page_url_sha256"],
                    "page_artifact_uri": page_record["artifact_uri"],
                    "page_artifact_sha256": page_record["sha256"],
                    "recorded": recorded,
                }
            )
            if terminal_state == "completed":
                break
        if not pages:
            print(json.dumps({"mode": "apply", "claimed": False, "state": terminal_state}, sort_keys=True))
            return 0
        result = {
            "schema_version": SCHEMA_VERSION,
            "workflow": WORKFLOW,
            "run_id": int(run["id"]),
            "run_key_sha256": sha256_text(args.run_key),
            "checkpoint_page_limit": checkpoint_pages,
            "pages": pages,
            "aggregate_counts": aggregate_counts,
            "observed": observed,
            "corrected": corrected,
            "stale": stale,
            "terminal_state": terminal_state,
            "product_creation_authorized": bool(args.create_missing_products),
            "created_count": created_count,
            "created_products": created_products,
        }
        result_record = _archive(store, "result.json", result, args.s3_bucket, archive_prefix)
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "run_id": int(run["id"]),
                    "state": terminal_state,
                    "pages_processed": len(pages),
                    "observed": observed,
                    "corrected": corrected,
                    "stale": stale,
                    "created_count": created_count,
                    "counts": aggregate_counts,
                    "result_sha256": result_record["sha256"],
                    "result_uri": result_record["artifact_uri"],
                },
                sort_keys=True,
            )
        )
        return 0
    except PortalCooldownError as exc:
        if current_claim and current_claim.get("claimed"):
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
        if current_claim and current_claim.get("claimed"):
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

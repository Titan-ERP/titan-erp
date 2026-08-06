"""Throttled, resumable reconciliation of authenticated Sparex listing pages.

The worker never opens a product-detail page or uses Sparex search. It captures
exact SKU links, listing titles, and images, archives each page, and can publish
immutable checkpoint manifests for decoupled Odoo ingestion.
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
from scripts.sparex_catalog_manifest import MAX_RECORDS as MAX_MANIFEST_RECORDS
from scripts.sparex_catalog_manifest import publish_manifest

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ODOO_ENV = ROOT / "odoo_connection.env"
DEFAULT_ARTIFACT_ROOT = ROOT / "outputs" / "sparex-catalog-discovery"
WORKFLOW = "sparex-discovery-queue"
CONFIRMATION = "sparex-discovery-queue"
PARSER_VERSION = "sparex-listing-frontier-v6"
SCHEMA_VERSION = "1.1"
SPAREX_HOST = "us.sparex.com"
MAX_PAGE_ITEMS = 100
MAX_CHECKPOINT_PAGES = 50
MAX_TOTAL_PAGES = 10_000_000
MAX_PRODUCT_CREATION_BATCH = 100
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
NON_CONTENT_XPATH = ".//script | .//style | .//noscript | .//template"
PLACEHOLDER_IMAGE_PATTERN = re.compile(
    r"(?:/placeholder/|/default/(?:placeholder|no[_-]?image)|no[_-]?image|coming[_-]?soon)",
    re.IGNORECASE,
)


class PortalCooldownError(RuntimeError):
    """A real Sparex-origin signal that requires a cooldown."""


@dataclass
class RequestThrottle:
    seconds: float
    last_request: float = 0.0
    request_count: int = 0
    slow_request_count: int = 0
    max_request_seconds: float = 0.0
    slow_request_seconds: float = 8.0

    def wait(self) -> None:
        remaining = self.seconds - (time.monotonic() - self.last_request)
        if remaining > 0:
            time.sleep(remaining)
        self.last_request = time.monotonic()

    def record_request(self, elapsed_seconds: float) -> None:
        elapsed = max(0.0, float(elapsed_seconds))
        self.request_count += 1
        self.max_request_seconds = max(self.max_request_seconds, elapsed)
        if elapsed >= self.slow_request_seconds:
            self.slow_request_count += 1

    def telemetry(self) -> dict[str, Any]:
        return {
            "http_requests": self.request_count,
            "slow_pages": self.slow_request_count,
            "http_backoffs": 0,
            "max_page_seconds": round(self.max_request_seconds, 3),
        }


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
            token in classes
            for token in (" product-item ", " product-item-info ", " item product ", " product-card ", " pm-listitem ")
        ):
            return current
        current = current.getparent()
    parent = anchor.getparent()
    return parent if parent is not None else anchor


def _image_url(container, page_url: str) -> str:
    candidates: list[str] = []
    for node in [container, *container.xpath(".//*[@data-cdnimg or @data-image]")]:
        for attribute in ("data-cdnimg", "data-image"):
            value = (node.get(attribute) or "").strip()
            if value:
                candidate = urljoin(page_url, unescape(value))
                if urlsplit(candidate).scheme.casefold() == "https" and urlsplit(candidate).hostname:
                    candidates.append(candidate)
    for image in container.xpath(".//source | .//img"):
        for attribute in ("data-src", "data-original", "data-lazy", "data-srcset", "srcset", "src"):
            value = (image.get(attribute) or "").strip()
            if value and not value.startswith("data:"):
                source = value.split(",", 1)[0].strip().split(" ", 1)[0]
                candidate = urljoin(page_url, unescape(source))
                if urlsplit(candidate).scheme.casefold() == "https" and urlsplit(candidate).hostname:
                    candidates.append(candidate)
    return next((candidate for candidate in candidates if not PLACEHOLDER_IMAGE_PATTERN.search(candidate)), "")


def _content_text(node) -> str:
    clone = lxml_html.fromstring(lxml_html.tostring(node, encoding="unicode"))
    for excluded in clone.xpath(NON_CONTENT_XPATH):
        excluded.drop_tree()
    return " ".join(" ".join(clone.itertext()).split()).strip()


def _listing_title(container, page_url: str, source_url: str) -> str:
    candidates: list[str] = []
    for anchor in container.xpath(".//a[@href]"):
        candidate_url = urljoin(page_url, unescape((anchor.get("href") or "").strip()))
        if candidate_url != source_url:
            continue
        value = _content_text(anchor)
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
        attribute_skus = set()
        for node in [container, *container.xpath(".//*[@data-product-sku or @data-sku]")]:
            for attribute in ("data-product-sku", "data-sku"):
                value = (node.get(attribute) or "").strip()
                match = SKU_IN_TEXT.fullmatch(value)
                if match:
                    attribute_skus.add(normalized_sku(match.group("digits")))
        # URL digits are the identity anchor. Conflicting explicit card SKUs are ambiguous.
        explicit_skus = attribute_skus
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
    started = time.monotonic()
    try:
        response = session.request(method, url, timeout=45, allow_redirects=True, **kwargs)
    finally:
        throttle.record_request(time.monotonic() - started)
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


def _archive_raw_page(store: ArtifactStore, name: str, content: bytes, bucket: str, prefix: str) -> dict[str, Any]:
    record = store.write_bytes(name, content, kind="html")
    return store.archive_s3(record, bucket=bucket, prefix=prefix)


def _read_archived_json(artifact_uri: str, expected_sha256: str, s3_client: Any) -> dict[str, Any]:
    parsed = urlsplit(artifact_uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ValueError("Archived discovery evidence must use an S3 URI.")
    content = s3_client.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))["Body"].read()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise RuntimeError("Archived discovery evidence checksum did not verify.")
    envelope = json.loads(content)
    payload = envelope.get("data") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        raise TypeError("Archived discovery evidence does not contain one JSON object.")
    return payload


def backfill_legacy_page_urls(client: OdooClient, run_id: int, limit: int = 50) -> dict[str, int]:
    prepared = client.call(
        "southern.sparex.discovery.run",
        "prepare_legacy_page_url_backfill",
        run_id=run_id,
        limit=limit,
    )
    if not prepared:
        return {"prepared": 0, "updated": 0, "failed": 0}
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    s3_client = boto3.client("s3")
    records = []
    failed = 0
    for row in prepared:
        try:
            payload = _read_archived_json(
                str(row["artifact_uri"]), str(row["artifact_sha256"]), s3_client
            )
            page_url = str(payload.get("page_url") or "").strip()
            if sha256_text(page_url) != row["page_url_sha256"]:
                raise ValueError("Archived page URL does not match the Odoo page checksum.")
            records.append({**row, "page_url": page_url})
        except (BotoCoreError, ClientError, KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError):
            failed += 1
    updated = 0
    if records:
        result = client.call(
            "southern.sparex.discovery.run",
            "apply_legacy_page_url_backfill",
            run_id=run_id,
            records=records,
        )
        updated = int(result.get("updated") or 0)
    return {"prepared": len(prepared), "updated": updated, "failed": failed}


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
    parser.add_argument("--manifest-queue-url", default=os.environ.get("SPAREX_CATALOG_QUEUE_URL", ""))
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
    if args.create_missing_products and args.manifest_queue_url:
        raise RuntimeError("Direct product creation and durable manifest delivery cannot run together.")
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
            "max_pages_total": MAX_TOTAL_PAGES,
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
                "max_pages_total": MAX_TOTAL_PAGES,
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
    legacy_backfill = backfill_legacy_page_urls(client, int(run["id"]))
    repair_queue = client.call(
        "southern.sparex.discovery.run",
        "queue_due_discovery_page_repairs",
        run_id=int(run["id"]),
        limit=checkpoint_pages,
        min_age_hours=24,
    )
    current_claim: dict[str, Any] | None = None
    try:
        session = throttle = None
        pages = []
        manifest_items: list[dict[str, Any]] = []
        manifest_source_artifacts: list[dict[str, str]] = []
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
            page_kind = str(current_claim.get("cursor_kind") or "frontier")
            artifact_stem = (
                f"listing-page-repair-{len(pages) + 1:02d}-{sha256_text(cursor_url)[:8]}"
                if page_kind == "repair"
                else f"listing-page-{page_number:05d}"
            )
            raw_page_record = _archive_raw_page(
                store,
                f"{artifact_stem}.html",
                response.content,
                args.s3_bucket,
                archive_prefix,
            )
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
                "raw_artifact_uri": raw_page_record["artifact_uri"],
                "raw_artifact_sha256": raw_page_record["sha256"],
                "parser_version": PARSER_VERSION,
                "items": parsed["items"],
                "next_url": parsed["next_url"],
                "listing_urls": parsed["listing_urls"],
            }
            page_record = _archive(
                store,
                f"{artifact_stem}.json",
                page_payload,
                args.s3_bucket,
                archive_prefix,
            )
            manifest_source_artifacts.append(
                {"uri": page_record["artifact_uri"], "sha256": page_record["sha256"], "role": "listing_page"}
            )
            for item in parsed["items"]:
                card_payload = {
                    "vendor_sku": item["sku"],
                    "listing_title": item.get("listing_title") or "",
                    "source_url": item["source_url"],
                    "image_url": item.get("image_url") or "",
                    "availability": "unknown",
                    "currency_code": "USD",
                    "page_sha256": page_payload["response_sha256"],
                    "image_source_sha256": sha256_text(item.get("image_url") or "") if item.get("image_url") else "",
                }
                card_payload["card_sha256"] = sha256_text(
                    json.dumps(card_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                )
                manifest_items.append(card_payload)
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
                    limit=MAX_PRODUCT_CREATION_BATCH - creation_operations,
                )
                if creation_records:
                    creation_plan = {
                        "schema_version": SCHEMA_VERSION,
                        "workflow": WORKFLOW,
                        "operation": "page_driven_draft_product_creation",
                        "selection_scope": "current_catalog_backlog",
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
        queued_manifests = []
        if args.manifest_queue_url:
            import boto3

            s3 = boto3.client("s3")
            sqs = boto3.client("sqs")
            page_range = f"{min(page['page_number'] for page in pages)}-{max(page['page_number'] for page in pages)}"
            for offset in range(0, len(manifest_items), MAX_MANIFEST_RECORDS):
                batch = manifest_items[offset : offset + MAX_MANIFEST_RECORDS]
                batch_number = offset // MAX_MANIFEST_RECORDS + 1
                key_base = f"{archive_prefix}/manifests/{page_range}-{batch_number:03d}"
                queued_manifests.append(
                    publish_manifest(
                        s3=s3,
                        sqs=sqs,
                        queue_url=args.manifest_queue_url,
                        payload_uri=f"s3://{args.s3_bucket}/{key_base}-payload.json",
                        manifest_uri=f"s3://{args.s3_bucket}/{key_base}-manifest.json",
                        payload=batch,
                        parser_version=PARSER_VERSION,
                        run_id=str(run["id"]),
                        sweep_id=f"sparex-discovery-{run['id']}",
                        page_range=page_range,
                        source_artifacts=manifest_source_artifacts,
                    )
                )
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
            "legacy_page_url_backfill": legacy_backfill,
            "repair_queue": repair_queue,
            "created_count": created_count,
            "created_products": created_products,
            "queued_manifests": queued_manifests,
            **(throttle.telemetry() if throttle else RequestThrottle(throttle_seconds).telemetry()),
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
                    "http_requests": result["http_requests"],
                    "slow_pages": result["slow_pages"],
                    "http_backoffs": result["http_backoffs"],
                    "max_page_seconds": result["max_page_seconds"],
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

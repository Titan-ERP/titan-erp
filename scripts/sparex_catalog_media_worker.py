"""Validate, archive, and apply one bounded batch of staged Sparex listing images."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from scripts.odoo_runtime import OdooClient, OdooConfig, OdooError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MIN_DIMENSION = 64
MEDIA_CONFIRMATION = "sparex-media-batch-write"
MEDIA_OUTCOME_CONFIRMATION = "sparex-media-outcome-write"
MEDIA_TRANSIENT_EXIT = 76
MEDIA_UNKNOWN_EXIT = 2
PERMANENT_VALUE_ERRORS = {
    "image_url_not_https": "image_url_not_https",
    "unsupported_or_invalid_image": "image_invalid_encoding",
    "unsupported_webp_header": "image_invalid_encoding",
    "image_too_large": "image_too_large",
    "image_mime_mismatch": "image_mime_mismatch",
    "image_dimensions_out_of_bounds": "image_dimensions_out_of_bounds",
    "image_source_hash_mismatch": "image_source_hash_mismatch",
}
TRANSIENT_HTTP_STATUSES = {429, 500, 502, 503, 504}
PERMANENT_HTTP_STATUSES = {404, 410}


def s3_image_metadata(row: dict, width: int, height: int) -> dict[str, str]:
    return {
        "source-url-sha256": str(row.get("image_source_sha256") or ""),
        "width": str(width),
        "height": str(height),
    }


def image_metadata(content: bytes) -> tuple[str, int, int]:
    if content.startswith(b"\x89PNG\r\n\x1a\n") and len(content) >= 24:
        width, height = struct.unpack(">II", content[16:24])
        return "image/png", width, height
    if content.startswith((b"GIF87a", b"GIF89a")) and len(content) >= 10:
        width, height = struct.unpack("<HH", content[6:10])
        return "image/gif", width, height
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP" and len(content) >= 30:
        if content[12:16] == b"VP8X":
            width = 1 + int.from_bytes(content[24:27], "little")
            height = 1 + int.from_bytes(content[27:30], "little")
            return "image/webp", width, height
        raise ValueError("unsupported_webp_header")
    if content.startswith(b"\xff\xd8\xff"):
        offset = 2
        while offset + 9 < len(content):
            if content[offset] != 0xFF:
                offset += 1
                continue
            marker = content[offset + 1]
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                height, width = struct.unpack(">HH", content[offset + 5 : offset + 9])
                return "image/jpeg", width, height
            if offset + 4 > len(content):
                break
            length = int.from_bytes(content[offset + 2 : offset + 4], "big")
            if length < 2:
                break
            offset += length + 2
    raise ValueError("unsupported_or_invalid_image")


def download_image(url: str) -> tuple[bytes, str, int, int]:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("image_url_not_https")
    with requests.get(url, timeout=(10, 30), stream=True, allow_redirects=True) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise ValueError("image_too_large")
            chunks.append(chunk)
    content = b"".join(chunks)
    mime_type, width, height = image_metadata(content)
    response_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().casefold()
    if response_type and response_type != mime_type:
        raise ValueError("image_mime_mismatch")
    if min(width, height) < MIN_DIMENSION or width * height > MAX_IMAGE_PIXELS:
        raise ValueError("image_dimensions_out_of_bounds")
    return content, mime_type, width, height


def expected_image_source_sha256(url: str) -> str:
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def classify_media_failure(error: BaseException) -> tuple[str, str]:
    if isinstance(error, ValueError):
        code = PERMANENT_VALUE_ERRORS.get(str(error))
        if code:
            return "permanent", code
        return "unknown", "unexpected_value_error"
    status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, int):
        if status in PERMANENT_HTTP_STATUSES:
            return "permanent", f"image_http_{status}"
        if status in TRANSIENT_HTTP_STATUSES:
            return "transient", f"image_http_{status}"
        return "unknown", f"image_http_{status}"
    if isinstance(
        error,
        (
            requests.Timeout,
            requests.ConnectTimeout,
            requests.ReadTimeout,
            requests.ConnectionError,
        ),
    ):
        return "transient", "image_network_timeout"
    error_name = type(error).__name__
    if error_name in {"EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError", "ClientError"}:
        return "transient", "media_infrastructure_transient"
    if isinstance(error, OdooError):
        message = str(error).casefold()
        if any(
            token in message
            for token in (
                "http 404",
                "http 429",
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "json-2 404",
                "json-2 429",
                "json-2 500",
                "json-2 502",
                "json-2 503",
                "json-2 504",
                "timed out",
                "timeout",
                "timeouterror",
            )
        ):
            return "transient", "odoo_transient"
        return "unknown", "odoo_contract_failure"
    return "unknown", "unexpected_media_failure"


def safe_media_error(code: str) -> str:
    return (code or "unexpected_media_failure")[:80]


def media_candidate_domain(now: datetime | None = None) -> list:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S")
    return [
        ("source_id.code", "=", "sparex"),
        ("active", "=", True),
        ("vendor_cost", ">", 0),
        ("dealer_cost_evidence_sha256", "!=", False),
        ("image_write_verified", "=", False),
        ("image_url", "!=", False),
        ("media_state", "not in", ["manual_review"]),
        "|",
        ("media_state", "!=", "retry_wait"),
        "|",
        ("media_next_attempt_at", "=", False),
        ("media_next_attempt_at", "<=", stamp),
    ]


def _outcome(item_id: int, kind: str, code: str) -> dict:
    return {
        "item_id": int(item_id),
        "kind": kind,
        "failure_class": code,
        "error_safe": safe_media_error(code),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, required=True)
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--s3-prefix", default="sparex-product-catalog/media")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--throttle-seconds", type=float, default=3.0)
    args = parser.parse_args()
    limit = max(1, min(args.limit, 25))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    try:
        rows = client.call(
            "southern.vendor.catalog.item",
            "search_read",
            domain=media_candidate_domain(),
            fields=["id", "image_url", "image_source_sha256", "content_sha256"],
            order="promotion_requested desc, demand_count desc, last_seen_at, id",
            limit=limit,
        )
    except OdooError as error:
        kind, code = classify_media_failure(error)
        payload = {
            "state": "transient" if kind == "transient" else "failed",
            "processed": 0,
            "permanent_failures": 0,
            "transient_failures": 1 if kind == "transient" else 0,
            "unknown_failures": 0 if kind == "transient" else 1,
            "outcomes": [],
            "failure_class": code,
        }
        print(json.dumps(payload, sort_keys=True))
        return MEDIA_TRANSIENT_EXIT if kind == "transient" else MEDIA_UNKNOWN_EXIT
    if not rows:
        print(
            json.dumps(
                {
                    "state": "idle",
                    "processed": 0,
                    "permanent_failures": 0,
                    "transient_failures": 0,
                    "unknown_failures": 0,
                    "outcomes": [],
                },
                sort_keys=True,
            )
        )
        return 0
    import boto3

    s3 = boto3.client("s3")
    prepared = []
    outcomes = []
    last_request = 0.0
    infrastructure_failure = None
    for row in rows:
        item_id = int(row["id"])
        try:
            source_url = str(row.get("image_url") or "")
            if expected_image_source_sha256(source_url) != str(row.get("image_source_sha256") or ""):
                raise ValueError("image_source_hash_mismatch")
            wait_seconds = max(3.0, args.throttle_seconds) - (time.monotonic() - last_request)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            content, mime_type, width, height = download_image(source_url)
            last_request = time.monotonic()
            image_sha = hashlib.sha256(content).hexdigest()
            key = f"{args.s3_prefix.rstrip('/')}/{image_sha[:2]}/{image_sha}"
            s3.put_object(
                Bucket=args.s3_bucket,
                Key=key,
                Body=content,
                ContentType=mime_type,
                Metadata=s3_image_metadata(row, width, height),
            )
            prepared.append(
                {
                    "item_id": item_id,
                    "source_image_sha256": row["image_source_sha256"],
                    "image_sha256": image_sha,
                    "image_artifact_sha256": image_sha,
                    "image_base64": base64.b64encode(content).decode("ascii"),
                    "image_artifact_uri": f"s3://{args.s3_bucket}/{key}",
                }
            )
        except Exception as error:  # noqa: BLE001 - classify, then fail closed only for unknown
            last_request = time.monotonic()
            kind, code = classify_media_failure(error)
            if code == "media_infrastructure_transient":
                infrastructure_failure = code
                break
            outcomes.append(_outcome(item_id, kind, code))
    applied = []
    try:
        if prepared:
            plan_bytes = json.dumps(
                [{key: value for key, value in row.items() if key != "image_base64"} for row in prepared],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            plan_sha = hashlib.sha256(plan_bytes).hexdigest()
            plan_key = f"{args.s3_prefix.rstrip('/')}/plans/{plan_sha}.json"
            s3.put_object(Bucket=args.s3_bucket, Key=plan_key, Body=plan_bytes, ContentType="application/json")
            applied = client.call(
                "southern.vendor.catalog.item",
                "apply_media_batch",
                records=prepared,
                artifact_uri=f"s3://{args.s3_bucket}/{plan_key}",
                artifact_sha256=plan_sha,
                confirmation=MEDIA_CONFIRMATION,
            )
        classified = [row for row in outcomes if row["kind"] in {"permanent", "transient"}]
        if classified:
            client.call(
                "southern.vendor.catalog.item",
                "record_media_outcomes",
                records=classified,
                confirmation=MEDIA_OUTCOME_CONFIRMATION,
            )
    except OdooError as error:
        kind, code = classify_media_failure(error)
        print(
            json.dumps(
                {
                    "state": "transient" if kind == "transient" else "failed",
                    "processed": len(applied),
                    "permanent_failures": sum(1 for row in outcomes if row["kind"] == "permanent"),
                    "transient_failures": sum(1 for row in outcomes if row["kind"] == "transient"),
                    "unknown_failures": 1 if kind != "transient" else 0,
                    "outcomes": outcomes,
                    "failure_class": code,
                },
                sort_keys=True,
            )
        )
        return MEDIA_TRANSIENT_EXIT if kind == "transient" else MEDIA_UNKNOWN_EXIT
    except Exception as error:  # noqa: BLE001 - S3/runtime infrastructure
        kind, code = classify_media_failure(error)
        print(
            json.dumps(
                {
                    "state": "transient" if kind == "transient" else "failed",
                    "processed": len(applied),
                    "permanent_failures": sum(1 for row in outcomes if row["kind"] == "permanent"),
                    "transient_failures": sum(1 for row in outcomes if row["kind"] == "transient"),
                    "unknown_failures": 0 if kind == "transient" else 1,
                    "outcomes": outcomes,
                    "failure_class": code,
                },
                sort_keys=True,
            )
        )
        return MEDIA_TRANSIENT_EXIT if kind == "transient" else MEDIA_UNKNOWN_EXIT
    unknown = sum(1 for row in outcomes if row["kind"] == "unknown")
    payload = {
        "state": "complete" if unknown == 0 else "failed",
        "processed": len(applied),
        "permanent_failures": sum(1 for row in outcomes if row["kind"] == "permanent"),
        "transient_failures": sum(1 for row in outcomes if row["kind"] == "transient"),
        "unknown_failures": unknown,
        "outcomes": outcomes,
    }
    print(json.dumps(payload, sort_keys=True))
    if unknown:
        return MEDIA_UNKNOWN_EXIT
    if infrastructure_failure:
        return MEDIA_TRANSIENT_EXIT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

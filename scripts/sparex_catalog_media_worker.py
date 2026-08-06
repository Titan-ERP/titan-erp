"""Validate, archive, and apply one bounded batch of staged Sparex listing images."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import struct
from pathlib import Path
from urllib.parse import urlsplit

import requests

from scripts.odoo_runtime import OdooClient, OdooConfig

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MIN_DIMENSION = 64
MEDIA_CONFIRMATION = "sparex-media-batch-write"


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, required=True)
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--s3-prefix", default="sparex-product-catalog/media")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    limit = max(1, min(args.limit, 25))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    rows = client.call(
        "southern.vendor.catalog.item",
        "search_read",
        domain=[
            ("source_id.code", "=", "sparex"),
            ("active", "=", True),
            ("image_write_verified", "=", False),
            ("image_url", "!=", False),
        ],
        fields=["id", "image_url", "image_source_sha256", "content_sha256"],
        order="promotion_requested desc, demand_count desc, last_seen_at, id",
        limit=limit,
    )
    if not rows:
        print(json.dumps({"state": "idle", "processed": 0}))
        return 0
    import boto3

    s3 = boto3.client("s3")
    prepared = []
    failures = []
    for row in rows:
        try:
            content, mime_type, width, height = download_image(row["image_url"])
            image_sha = hashlib.sha256(content).hexdigest()
            key = f"{args.s3_prefix.rstrip('/')}/{image_sha[:2]}/{image_sha}"
            s3.put_object(
                Bucket=args.s3_bucket,
                Key=key,
                Body=content,
                ContentType=mime_type,
                Metadata={
                    "source-url-sha256": row["image_source_sha256"],
                    "width": str(width),
                    "height": str(height),
                },
            )
            prepared.append(
                {
                    "item_id": row["id"],
                    "source_image_sha256": row["image_source_sha256"],
                    "image_sha256": image_sha,
                    "image_artifact_sha256": image_sha,
                    "image_base64": base64.b64encode(content).decode("ascii"),
                    "image_artifact_uri": f"s3://{args.s3_bucket}/{key}",
                }
            )
        except (requests.RequestException, ValueError) as error:
            failures.append({"item_id": row["id"], "failure": str(error)[:200]})
    applied = []
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
    print(json.dumps({"state": "complete", "processed": len(applied), "failures": failures}, sort_keys=True))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

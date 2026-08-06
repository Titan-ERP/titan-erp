"""Create immutable Sparex catalog manifests and publish their S3 pointers to FIFO SQS."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "sparex-manifest-v1"
MESSAGE_GROUP_ID = "vendor:sparex:catalog"
MAX_RECORDS = 500


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def reject_binary_floats(value: Any, path: str = "payload") -> None:
    if isinstance(value, float):
        raise TypeError(f"{path} contains a binary floating-point value; encode decimals as strings.")
    if isinstance(value, dict):
        for key, child in value.items():
            reject_binary_floats(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_binary_floats(child, f"{path}[{index}]")


def build_manifest(
    payload: list[dict[str, Any]],
    *,
    parser_version: str,
    run_id: str,
    sweep_id: str,
    page_range: str,
    source_artifacts: list[dict[str, str]],
) -> tuple[dict[str, Any], str, bytes]:
    if not 1 <= len(payload) <= MAX_RECORDS:
        raise ValueError("A Sparex manifest must contain between 1 and 500 records.")
    reject_binary_floats(payload)
    if not source_artifacts:
        raise ValueError("At least one immutable source artifact is required.")
    for artifact in source_artifacts:
        if not str(artifact.get("uri") or "").startswith("s3://"):
            raise ValueError("Source artifacts must use S3 URIs.")
        if len(str(artifact.get("sha256") or "")) != 64:
            raise ValueError("Source artifacts require SHA-256 checksums.")
    payload_bytes = canonical_bytes(payload)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "parser_version": parser_version,
        "run_id": run_id,
        "sweep_id": sweep_id,
        "page_range": page_range,
        "record_count": len(payload),
        "payload_sha256": sha256_bytes(payload_bytes),
        "source_artifacts": source_artifacts,
    }
    manifest_bytes = canonical_bytes(manifest)
    return manifest, sha256_bytes(manifest_bytes), manifest_bytes


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://") or "/" not in uri[5:]:
        raise ValueError("Expected an s3://bucket/key URI.")
    bucket, key = uri[5:].split("/", 1)
    if not bucket or not key:
        raise ValueError("Expected an s3://bucket/key URI.")
    return bucket, key


def publish_manifest(
    *,
    s3: Any,
    sqs: Any,
    queue_url: str,
    payload_uri: str,
    manifest_uri: str,
    payload: list[dict[str, Any]],
    parser_version: str,
    run_id: str,
    sweep_id: str,
    page_range: str,
    source_artifacts: list[dict[str, str]],
) -> dict[str, str]:
    manifest, manifest_sha256, manifest_bytes = build_manifest(
        payload,
        parser_version=parser_version,
        run_id=run_id,
        sweep_id=sweep_id,
        page_range=page_range,
        source_artifacts=source_artifacts,
    )
    payload_bytes = canonical_bytes(payload)
    payload_bucket, payload_key = parse_s3_uri(payload_uri)
    manifest_bucket, manifest_key = parse_s3_uri(manifest_uri)
    s3.put_object(Bucket=payload_bucket, Key=payload_key, Body=payload_bytes, ContentType="application/json")
    s3.put_object(Bucket=manifest_bucket, Key=manifest_key, Body=manifest_bytes, ContentType="application/json")
    message = {
        "schema_version": SCHEMA_VERSION,
        "manifest_uri": manifest_uri,
        "manifest_sha256": manifest_sha256,
        "payload_uri": payload_uri,
        "payload_sha256": manifest["payload_sha256"],
    }
    response = sqs.send_message(
        QueueUrl=queue_url,
        MessageBody=canonical_bytes(message).decode("utf-8"),
        MessageGroupId=MESSAGE_GROUP_ID,
        MessageDeduplicationId=manifest_sha256,
    )
    return {"manifest_sha256": manifest_sha256, "message_id": response["MessageId"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path, help="Canonicalizable JSON array containing no more than 500 records.")
    parser.add_argument("--parser-version", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--sweep-id", required=True)
    parser.add_argument("--page-range", required=True)
    parser.add_argument("--source-artifacts", type=Path, required=True, help="JSON array of immutable S3 artifact descriptors.")
    parser.add_argument("--payload-uri", required=True)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--queue-url", required=True)
    args = parser.parse_args()

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    source_artifacts = json.loads(args.source_artifacts.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(record, dict) for record in payload):
        raise TypeError("Payload must be a JSON array of objects.")
    import boto3

    result = publish_manifest(
        s3=boto3.client("s3"),
        sqs=boto3.client("sqs"),
        queue_url=args.queue_url,
        payload_uri=args.payload_uri,
        manifest_uri=args.manifest_uri,
        payload=payload,
        parser_version=args.parser_version,
        run_id=args.run_id,
        sweep_id=args.sweep_id,
        page_range=args.page_range,
        source_artifacts=source_artifacts,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

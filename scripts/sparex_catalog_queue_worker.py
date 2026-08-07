"""Consume one durable Sparex catalog manifest from SQS and ingest it into Odoo."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from scripts.odoo_runtime import OdooClient, OdooConfig
from scripts.sparex_catalog_manifest import SCHEMA_VERSION, canonical_bytes, parse_s3_uri

VISIBILITY_HEARTBEAT_SECONDS = 60
VISIBILITY_TIMEOUT_SECONDS = 180


def verified_s3_json(s3: Any, uri: str, expected_sha256: str) -> Any:
    bucket, key = parse_s3_uri(uri)
    content = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise RuntimeError("s3_artifact_checksum_mismatch")
    value = json.loads(content)
    if canonical_bytes(value) != content:
        raise RuntimeError("s3_artifact_not_canonical")
    return value


def verify_source_artifacts(s3: Any, artifacts: list[dict[str, str]]) -> None:
    for artifact in artifacts:
        bucket, key = parse_s3_uri(artifact["uri"])
        content = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
            raise RuntimeError("source_artifact_checksum_mismatch")


@contextmanager
def visibility_heartbeat(sqs: Any, queue_url: str, receipt_handle: str) -> Iterator[None]:
    stopped = threading.Event()

    def extend() -> None:
        while not stopped.wait(VISIBILITY_HEARTBEAT_SECONDS):
            sqs.change_message_visibility(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=VISIBILITY_TIMEOUT_SECONDS,
            )

    thread = threading.Thread(target=extend, name="sparex-sqs-visibility", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=5)


def process_message(s3: Any, client: OdooClient, body: str) -> dict[str, Any]:
    pointer = json.loads(body)
    if pointer.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("unknown_pointer_schema")
    manifest = verified_s3_json(s3, pointer["manifest_uri"], pointer["manifest_sha256"])
    payload = verified_s3_json(s3, pointer["payload_uri"], pointer["payload_sha256"])
    if manifest.get("payload_sha256") != pointer["payload_sha256"]:
        raise RuntimeError("manifest_payload_pointer_conflict")
    verify_source_artifacts(s3, manifest.get("source_artifacts") or [])
    result = client.call(
        "southern.sparex.catalog.ingestion",
        "ingest_manifest",
        manifest=manifest,
        payload=payload,
        manifest_sha256=pointer["manifest_sha256"],
    )
    if result.get("state") != "complete" or result.get("manifest_sha256") != pointer["manifest_sha256"]:
        raise RuntimeError("odoo_ingestion_not_committed")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-url", required=True)
    parser.add_argument("--odoo-env-file", required=True, type=Path)
    parser.add_argument("--wait-seconds", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    import boto3

    sqs = boto3.client("sqs")
    s3 = boto3.client("s3")
    response = sqs.receive_message(
        QueueUrl=args.queue_url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=max(0, min(args.wait_seconds, 20)),
        VisibilityTimeout=VISIBILITY_TIMEOUT_SECONDS,
        AttributeNames=["ApproximateReceiveCount"],
    )
    messages = response.get("Messages") or []
    if not messages:
        print(json.dumps({"state": "idle"}))
        return 0
    message = messages[0]
    with visibility_heartbeat(sqs, args.queue_url, message["ReceiptHandle"]):
        client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
        result = process_message(s3, client, message["Body"])
    sqs.delete_message(QueueUrl=args.queue_url, ReceiptHandle=message["ReceiptHandle"])
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

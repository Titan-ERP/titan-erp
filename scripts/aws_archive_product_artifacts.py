from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOTS = [
    ROOT / "odoo_imports" / "product_master" / "automation_reports",
    ROOT / "odoo_imports" / "product_master" / "order_refresh",
    ROOT / "odoo_imports" / "product_master" / "pricing",
    ROOT / "odoo_imports" / "product_master" / "sparex",
    ROOT / "odoo_imports" / "product_master" / "parts_intelligence",
]
DEFAULT_ENV_FILES = [ROOT / "cloud" / "aws" / ".env", ROOT / "odoo_connection.env"]
MANIFEST_SCHEMA_VERSION = "1.0"


def import_boto3():
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "boto3 is required for S3 uploads. Install it on the worker with: "
            "python -m pip install boto3"
        ) from exc
    return boto3


def load_env_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive product catalog evidence and reports to S3.")
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        help="Environment file to load before resolving bucket/prefix. Defaults to cloud/aws/.env and odoo_connection.env.",
    )
    parser.add_argument("--bucket", default=os.environ.get("SOUTHERN_PARTS_S3_BUCKET", ""))
    parser.add_argument("--prefix", default=os.environ.get("SOUTHERN_PARTS_S3_PREFIX", "sparex-product-catalog"))
    parser.add_argument(
        "--root",
        action="append",
        default=[],
        help="Local directory to archive. Can be passed multiple times. Defaults to product_master evidence roots.",
    )
    parser.add_argument(
        "--since-hours",
        type=float,
        default=0,
        help="Only upload files modified in the last N hours. 0 uploads all files under selected roots.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--delete-local-after-upload",
        action="store_true",
        help="Delete local artifacts only after upload and S3 head-object verification.",
    )
    parser.add_argument(
        "--retain-local-hours",
        type=float,
        default=24,
        help="When deleting after upload, keep files modified within this many hours.",
    )
    parser.add_argument(
        "--manifest",
        default="",
        help="Optional JSONL manifest path. Defaults to automation_reports/aws_archive_manifest_<timestamp>.jsonl.",
    )
    return parser.parse_args()


def iter_files(roots: list[Path], since_hours: float):
    cutoff = None
    if since_hours > 0:
        cutoff = datetime.now().timestamp() - timedelta(hours=since_hours).total_seconds()
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in {"odoo_connection.env", ".env"}:
                continue
            if cutoff is not None and path.stat().st_mtime < cutoff:
                continue
            yield path


def key_for(path: Path, prefix: str) -> str:
    rel = path.resolve().relative_to(ROOT.resolve()).as_posix()
    return f"{prefix.strip('/')}/{rel}"


def removable(path: Path, roots: list[Path], retain_local_hours: float) -> bool:
    resolved = path.resolve()
    if not any(resolved.is_relative_to(root.resolve()) for root in roots):
        return False
    cutoff = datetime.now().timestamp() - timedelta(hours=retain_local_hours).total_seconds()
    return path.stat().st_mtime < cutoff


def write_manifest_row(handle, row: dict[str, object]) -> None:
    handle.write(json.dumps(row, sort_keys=True) + "\n")
    handle.flush()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    env_files = [Path(p).resolve() for p in args.env_file] if args.env_file else DEFAULT_ENV_FILES
    load_env_files(env_files)
    args.bucket = args.bucket or os.environ.get("SOUTHERN_PARTS_S3_BUCKET", "")
    args.prefix = args.prefix or os.environ.get("SOUTHERN_PARTS_S3_PREFIX", "sparex-product-catalog")
    if not args.bucket:
        raise SystemExit("Missing S3 bucket. Set SOUTHERN_PARTS_S3_BUCKET or pass --bucket.")

    roots = [Path(p).resolve() for p in args.root] if args.root else DEFAULT_ROOTS
    files = list(iter_files(roots, args.since_hours))
    usage = shutil.disk_usage(ROOT)
    print(
        f"archive_candidates={len(files)} bucket={args.bucket} prefix={args.prefix} "
        f"free_gb_before={usage.free / (1024**3):.2f}"
    )

    if args.dry_run:
        for path in files[:50]:
            print(f"DRY RUN {path} -> s3://{args.bucket}/{key_for(path, args.prefix)}")
        if len(files) > 50:
            print(f"DRY RUN omitted {len(files) - 50} additional files")
        return 0

    boto3 = import_boto3()
    s3 = boto3.client("s3")
    manifest_path = (
        Path(args.manifest).resolve()
        if args.manifest
        else ROOT
        / "odoo_imports"
        / "product_master"
        / "automation_reports"
        / f"aws_archive_manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    uploaded = 0
    deleted = 0
    bytes_uploaded = 0
    bytes_deleted = 0
    with manifest_path.open("a", encoding="utf-8") as manifest:
        for path in files:
            stat = path.stat()
            sha256 = sha256_file(path)
            content_type, _ = mimetypes.guess_type(path.name)
            extra_args = {"ContentType": content_type} if content_type else None
            key = key_for(path, args.prefix)
            if extra_args:
                s3.upload_file(str(path), args.bucket, key, ExtraArgs=extra_args)
            else:
                s3.upload_file(str(path), args.bucket, key)
            head = s3.head_object(Bucket=args.bucket, Key=key)
            verified = int(head.get("ContentLength", -1)) == stat.st_size
            row = {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "bucket": args.bucket,
                "key": key,
                "local_path": str(path),
                "sha256": sha256,
                "size": stat.st_size,
                "modified_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "verified": verified,
                "deleted_local": False,
            }
            if not verified:
                write_manifest_row(manifest, row)
                raise SystemExit(f"S3 verification failed for {path}")
            uploaded += 1
            bytes_uploaded += stat.st_size
            if args.delete_local_after_upload and removable(path, roots, args.retain_local_hours):
                path.unlink()
                deleted += 1
                bytes_deleted += stat.st_size
                row["deleted_local"] = True
            write_manifest_row(manifest, row)
    usage_after = shutil.disk_usage(ROOT)
    print(
        f"uploaded={uploaded} bytes_uploaded={bytes_uploaded} deleted_local={deleted} "
        f"bytes_deleted={bytes_deleted} free_gb_after={usage_after.free / (1024**3):.2f} "
        f"manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

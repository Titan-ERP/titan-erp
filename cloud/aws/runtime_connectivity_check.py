from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3


ROOT = Path(os.environ.get("SOUTHERN_WORKER_ROOT", "/opt/southern-parts/Odoo"))
REGION = os.environ.get("AWS_REGION", "us-east-1")
sys.path.insert(0, str(ROOT / "scripts"))

import sparex_dealer_portal_sync as dealer_sync  # noqa: E402


def main() -> int:
    os.chdir(ROOT)
    dealer_sync.load_env()

    parameters = boto3.client("ssm", region_name=REGION).get_parameters_by_path(
        Path="/southern-parts/sparex-odoo",
        Recursive=True,
        WithDecryption=True,
    )["Parameters"]
    if len(parameters) != 8 or any(not item["Value"] for item in parameters):
        raise SystemExit("SSM parameter validation failed")
    print("ssm_secure_parameters=8")

    boto3.client("s3", region_name=REGION).head_object(
        Bucket=dealer_sync.required("SOUTHERN_PARTS_S3_BUCKET"),
        Key="deploy/southern-parts-worker.zip",
    )
    print("s3_read=passed")

    dealer_sync.connect_odoo()
    print("odoo_authentication=passed")

    session = dealer_sync.login_sparex()
    products_url = dealer_sync.required("SPAREX_DEALER_PRODUCTS_URL")
    response = session.get(products_url, timeout=45, allow_redirects=True)
    response.raise_for_status()
    print(f"sparex_dealer_authentication=passed products_status={response.status_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

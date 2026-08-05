import json

import pytest

from scripts.vendor_catalog_stage_import import batches, file_sha256, read_records


def test_reads_csv_and_batches_records(tmp_path):
    path = tmp_path / "catalog.csv"
    path.write_text(
        "vendor_sku,title,source_url,image_url,vendor_cost,sales_price\n"
        "A-1,Part One,https://vendor.test/a-1,https://vendor.test/a-1.jpg,10,20\n"
        "A-2,Part Two,https://vendor.test/a-2,https://vendor.test/a-2.jpg,12,24\n",
        encoding="utf-8",
    )
    records = list(read_records(path))
    assert [row["vendor_sku"] for row in records] == ["A-1", "A-2"]
    assert list(batches(records, 1)) == [[records[0]], [records[1]]]
    assert len(file_sha256(path)) == 64


def test_reads_jsonl_and_rejects_non_objects(tmp_path):
    path = tmp_path / "catalog.jsonl"
    path.write_text(json.dumps({"vendor_sku": "B-1"}) + "\n", encoding="utf-8")
    assert list(read_records(path)) == [{"vendor_sku": "B-1"}]
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(TypeError, match="one JSON object"):
        list(read_records(path))

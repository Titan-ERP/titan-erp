import json
from pathlib import Path

import pdfplumber


FILES = [
    Path.home() / "Downloads" / "2026-04 (1).pdf",
    Path.home() / "Downloads" / "2026-03 (1).pdf",
    Path.home() / "Downloads" / "2026 05-document (1).pdf",
    Path.home() / "Downloads" / "2026-06 (2).pdf",
]

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "odoo_imports" / "bank_reconciliation"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = []
    for path in FILES:
        item = {"file": str(path), "exists": path.exists(), "pages": 0, "sample": ""}
        if path.exists():
            with pdfplumber.open(path) as pdf:
                item["pages"] = len(pdf.pages)
                page_texts = []
                for page_index, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
                    page_texts.append(f"--- PAGE {page_index} ---\n{text}")
                all_text = "\n\n".join(page_texts)
                item["sample"] = all_text[:2500]
                text_path = OUT / f"{path.stem}_extracted.txt"
                text_path.write_text(all_text, encoding="utf-8")
                item["text_output"] = str(text_path)
        summary.append(item)

    (OUT / "pdf_extract_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    for item in summary:
        print(f"{Path(item['file']).name}: exists={item['exists']} pages={item['pages']}")
        if item.get("text_output"):
            print(f"  text={item['text_output']}")
            print("  sample:")
            print(item["sample"].replace("\n", "\n    ")[:1200])


if __name__ == "__main__":
    main()

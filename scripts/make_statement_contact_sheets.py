from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PAGE_DIR = ROOT / "odoo_imports" / "bank_reconciliation" / "statement_pages"


def make_sheet(prefix):
    paths = sorted(PAGE_DIR.glob(f"{prefix}-*.png"))
    if not paths:
        return None
    thumbs = []
    for path in paths:
        img = Image.open(path).convert("RGB")
        img.thumbnail((260, 340))
        canvas = Image.new("RGB", (280, 380), "white")
        canvas.paste(img, ((280 - img.width) // 2, 20))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 350), path.name, fill="black")
        thumbs.append(canvas)
    cols = 4
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 280, rows * 380), "white")
    for idx, thumb in enumerate(thumbs):
        sheet.paste(thumb, ((idx % cols) * 280, (idx // cols) * 380))
    out = PAGE_DIR / f"{prefix}_contact_sheet.png"
    sheet.save(out)
    return out


def main():
    for prefix in ["2026-03", "2026-04", "2026-05", "2026-06"]:
        out = make_sheet(prefix)
        if out:
            print(out)


if __name__ == "__main__":
    main()

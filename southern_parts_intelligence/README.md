# Southern Parts Intelligence

Adds Sparex-style parts catalog functionality to Odoo products.

## What It Adds

Backend product tabs:

- Product Specifications
- Fits Make/Model
- OEM Part Numbers
- Catalog Pages
- Related Parts
- Alternate Barcodes
- Source tracking

Website product detail sections:

- Product Specifications
- Suitable For Make/Model
- OEM Part Numbers
- Catalog Pages
- Related Parts

Search support:

- Adds `Parts Search Text` to products.
- Aggregates SKU, barcode, manufacturer, OEM references, fitment, catalog pages, specs, and alternate barcodes.
- Extends the product search view with a `Parts Intelligence` search field.
- Extends website shop search so the eCommerce search box can match enriched parts data.

## Deployment

Install on staging before production.

1. Copy this addon folder into the Odoo.sh repository.
2. Commit and push to a staging branch.
3. Update the app list.
4. Install `Southern Parts Intelligence`.
5. Open a Sparex or Blumaq product and verify the new `Parts Intelligence` backend tab.
6. Import a small detail JSON through `scripts/odoo_import_parts_intelligence_json.py` in dry-run mode first.
7. Apply detail import only after the dry run is clean.
8. Import the first live ecommerce detail batch:

```powershell
py -3 scripts\odoo_import_parts_intelligence_json.py outputs\southern_parts_sparex_style_detail_batch_001.json --apply
```

9. Check the website product pages. The product page should use Sparex-style anchored sections: product specifications, suitable make/model, OEM part numbers, catalog pages, and related parts.

## Importer

Detail records are imported with:

```powershell
py -3 scripts\odoo_import_parts_intelligence_json.py path\to\detail.json
```

Apply after review:

```powershell
py -3 scripts\odoo_import_parts_intelligence_json.py path\to\detail.json --apply
```

## Important Rule

Make/model fitment is relationship data, not product variant data. Do not create product variants for every compatible machine.

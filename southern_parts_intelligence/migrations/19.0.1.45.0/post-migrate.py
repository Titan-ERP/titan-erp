import json


def migrate(cr, version):
    del version
    cr.execute(
        """
        SELECT item.id,
               item.title,
               item.source_url,
               item.vendor_cost,
               item.sales_price,
               item.match_state,
               source.code,
               source.partner_id,
               source.default_category_id,
               COALESCE(product.is_published, FALSE)
          FROM southern_vendor_catalog_item item
          JOIN southern_vendor_catalog_source source ON source.id = item.source_id
          LEFT JOIN product_template product ON product.id = item.product_id
        """
    )
    updates = []
    for row in cr.fetchall():
        (
            item_id,
            title,
            source_url,
            vendor_cost,
            sales_price,
            match_state,
            source_code,
            partner_id,
            category_id,
            website_published,
        ) = row
        blockers = []
        if match_state == "duplicate":
            blockers.append("identity_conflict")
        if not partner_id:
            blockers.append("supplier_conflict")
        if not title:
            blockers.append("missing_name")
        if not source_url:
            blockers.append("missing_exact_url")
        if not vendor_cost or vendor_cost <= 0:
            blockers.append("missing_cost")
        if not category_id:
            blockers.append("category_unmapped")
        if vendor_cost and vendor_cost > 0 and (not sales_price or sales_price <= vendor_cost):
            blockers.append("pricing_error")
        blockers.extend(["missing_image_artifact", "image_write_unverified"])
        updates.append(
            (
                "blocked" if blockers else "ready_for_promotion",
                "published" if website_published else "not_ready",
                json.dumps(list(dict.fromkeys(blockers)), separators=(",", ":")),
                "USD" if source_code == "sparex" else "USD",
                item_id,
            )
        )
    if updates:
        cr.executemany(
            """
            UPDATE southern_vendor_catalog_item
               SET catalog_state = %s,
                   website_state = %s,
                   readiness_blockers_json = %s,
                   dealer_currency_code = %s,
                   image_write_verified = FALSE
             WHERE id = %s
            """,
            updates,
        )

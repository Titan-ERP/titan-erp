def migrate(cr, version):
    cr.execute(
        """
        UPDATE southern_vendor_catalog_source AS source
           SET partner_id = candidate.partner_id,
               write_date = NOW()
          FROM (
                SELECT MIN(partner.id) AS partner_id
                  FROM res_partner AS partner
                 WHERE LOWER(partner.name) = 'sparex'
                   AND partner.supplier_rank > 0
                HAVING COUNT(*) = 1
               ) AS candidate,
               ir_model_data AS data
         WHERE data.module = 'southern_parts_intelligence'
           AND data.name = 'vendor_catalog_source_sparex'
           AND data.model = 'southern.vendor.catalog.source'
           AND source.id = data.res_id
           AND source.partner_id IS NULL
        """
    )
    cr.execute(
        """
        INSERT INTO southern_vendor_catalog_item (
            active,
            company_id,
            source_id,
            vendor_sku,
            normalized_sku,
            internal_reference,
            title,
            customer_description,
            source_url,
            image_url,
            vendor_cost,
            sales_price,
            currency_id,
            availability,
            content_sha256,
            source_artifact_uri,
            source_artifact_sha256,
            schema_version,
            first_seen_at,
            last_seen_at,
            observation_count,
            demand_count,
            promotion_requested,
            product_id,
            match_state,
            promotion_state,
            blocker_code,
            promoted_at,
            create_uid,
            create_date,
            write_uid,
            write_date
        )
        SELECT discovery.active,
               discovery.company_id,
               source.id,
               COALESCE(NULLIF(discovery.raw_sku, ''), discovery.normalized_sku),
               discovery.normalized_sku,
               discovery.normalized_sku,
               COALESCE(NULLIF(discovery.listing_title, ''), discovery.normalized_sku),
               COALESCE(NULLIF(discovery.listing_title, ''), discovery.normalized_sku),
               discovery.source_url,
               discovery.image_url,
               0.0,
               COALESCE(product.list_price, 0.0),
               company.currency_id,
               'available',
               discovery.source_url_sha256,
               discovery.source_artifact_uri,
               discovery.source_artifact_sha256,
               '1.0',
               discovery.first_seen_at,
               discovery.last_seen_at,
               discovery.observation_count,
               0,
               FALSE,
               discovery.matched_product_id,
               CASE
                   WHEN discovery.odoo_match_state IN ('matched_active', 'matched_archived') THEN 'matched'
                   WHEN discovery.odoo_match_state = 'duplicate' THEN 'duplicate'
                   ELSE 'missing'
               END,
               CASE
                   WHEN discovery.odoo_match_state IN ('matched_active', 'matched_archived') THEN 'promoted'
                   WHEN discovery.odoo_match_state = 'duplicate' THEN 'blocked'
                   ELSE 'staged'
               END,
               CASE
                   WHEN discovery.odoo_match_state = 'duplicate' THEN 'duplicate_product'
                   WHEN source.partner_id IS NULL THEN 'missing_vendor'
                   WHEN discovery.image_url IS NULL OR discovery.image_url = '' THEN 'missing_image'
                   ELSE 'missing_cost'
               END,
               CASE
                   WHEN discovery.odoo_match_state IN ('matched_active', 'matched_archived') THEN discovery.last_seen_at
                   ELSE NULL
               END,
               discovery.create_uid,
               discovery.create_date,
               discovery.write_uid,
               discovery.write_date
          FROM southern_sparex_discovery_item AS discovery
          JOIN res_company AS company ON company.id = discovery.company_id
          JOIN ir_model_data AS data
            ON data.module = 'southern_parts_intelligence'
           AND data.name = 'vendor_catalog_source_sparex'
           AND data.model = 'southern.vendor.catalog.source'
          JOIN southern_vendor_catalog_source AS source ON source.id = data.res_id
          LEFT JOIN product_template AS product ON product.id = discovery.matched_product_id
         WHERE source.company_id = discovery.company_id
        ON CONFLICT (source_id, normalized_sku) DO NOTHING
        """
    )

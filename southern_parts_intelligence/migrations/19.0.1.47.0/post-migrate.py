def migrate(cr, version):
    del version
    cr.execute(
        """
        UPDATE southern_product_quality_issue issue
           SET work_lane = CASE
                WHEN issue.issue_type = 'publication_ready' THEN 'release'
                WHEN COALESCE(product.website_published, FALSE) THEN 'live_fix'
                ELSE 'enrich'
           END
          FROM product_template product
         WHERE product.id = issue.product_tmpl_id
           AND issue.work_lane IS DISTINCT FROM CASE
                WHEN issue.issue_type = 'publication_ready' THEN 'release'
                WHEN COALESCE(product.website_published, FALSE) THEN 'live_fix'
                ELSE 'enrich'
           END
        """
    )

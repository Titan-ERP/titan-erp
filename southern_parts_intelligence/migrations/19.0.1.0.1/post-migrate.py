def migrate(cr, version):
    cr.execute("DROP INDEX IF EXISTS product_template__southern_parts_search_text_index")

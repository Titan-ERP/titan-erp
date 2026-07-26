from odoo.addons.website_sale.controllers.main import WebsiteSale


class SouthernPartsWebsiteSale(WebsiteSale):
    def _search_get_detail(self, *args, **kwargs):
        detail = super()._search_get_detail(*args, **kwargs)
        search_fields = detail.get("search_fields")
        if isinstance(search_fields, list) and "southern_parts_search_text" not in search_fields:
            search_fields.append("southern_parts_search_text")
        fetch_fields = detail.get("fetch_fields")
        if isinstance(fetch_fields, list) and "southern_parts_search_text" not in fetch_fields:
            fetch_fields.append("southern_parts_search_text")
        return detail

from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.fields import Domain


class SouthernPartsWebsiteSale(WebsiteSale):
    def _add_search_subdomains_hook(self, search):
        subdomains = super()._add_search_subdomains_hook(search)
        subdomains.append(Domain("southern_parts_search_text", "ilike", search))
        return subdomains

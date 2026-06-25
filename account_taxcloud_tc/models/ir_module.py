from odoo import models


class IRModule(models.Model):
    _inherit = "ir.module.module"

    def write(self, vals):
        res = super().write(vals)
        for module in self:
            if (
                module.name == "account_taxcloud_tc"
                and vals.get("state", False) == "installed"
            ):
                taxcloud_module = module.search(
                    [("name", "=", "account_taxcloud"), ("state", "=", "installed")]
                )
                if taxcloud_module:
                    taxcloud_module.button_uninstall()
        return res

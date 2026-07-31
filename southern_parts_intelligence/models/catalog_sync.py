from odoo import api, fields, models


class SouthernPartsCatalogSync(models.Model):
    _name = "southern.parts.catalog.sync"
    _description = "Southern Parts Catalog Sync"
    _order = "sequence, id"

    name = fields.Char(required=True, default="Parts Website Snapshot Refresh")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("idle", "Idle"),
            ("running", "Running"),
            ("paused", "Paused"),
            ("error", "Error"),
        ],
        default="idle",
        required=True,
        index=True,
    )
    mode = fields.Selection(
        [
            ("snapshot_refresh", "Refresh Website Snapshots"),
            ("evidence_review", "Evidence Review Only"),
            ("sparex_dealer_sync", "Sparex Dealer Website Sync"),
        ],
        default="snapshot_refresh",
        required=True,
        help="Sparex dealer sync uses configured Sparex website credentials and keeps non-Sparex products out of scope.",
    )
    batch_size = fields.Integer(default=500, required=True)
    sparex_dealer_configured = fields.Boolean(
        compute="_compute_sparex_dealer_config",
        string="Sparex Dealer Configured",
    )
    sparex_dealer_login_url = fields.Char(
        compute="_compute_sparex_dealer_config",
        string="Sparex Login URL",
    )
    sparex_dealer_products_url = fields.Char(
        compute="_compute_sparex_dealer_config",
        string="Sparex Products URL",
    )
    sparex_dealer_username = fields.Char(
        compute="_compute_sparex_dealer_config",
        string="Sparex Username",
    )
    last_product_id = fields.Integer(default=0, readonly=True)
    last_run_at = fields.Datetime(readonly=True)
    run_count = fields.Integer(default=0, readonly=True)
    processed_count = fields.Integer(default=0, readonly=True)
    error_count = fields.Integer(default=0, readonly=True)
    last_message = fields.Text(readonly=True)

    @api.model
    def _cron_run_active_syncs(self):
        syncs = self.sudo().search(
            [("active", "=", True), ("state", "in", ["idle", "running"])],
            order="sequence, id",
            limit=3,
        )
        for sync in syncs:
            sync._run_one_batch()

    def action_run_now(self):
        for sync in self:
            sync.sudo()._run_one_batch()
        return True

    def action_pause(self):
        self.write({"state": "paused"})

    def action_resume(self):
        self.write({"state": "idle"})

    def action_reset_cursor(self):
        self.write({"last_product_id": 0, "state": "idle", "last_message": "Cursor reset."})

    def action_check_sparex_dealer_config(self):
        for sync in self:
            config = sync._get_sparex_dealer_config()
            message = (
                "Sparex dealer source configured for %s; products source %s."
                % (config["login_url"], config["products_url"])
                if config["configured"]
                else "Sparex dealer source is missing login URL, products URL, username, or password."
            )
            sync.write({"last_message": message, "last_run_at": fields.Datetime.now()})
        return True

    @api.depends_context("uid")
    def _compute_sparex_dealer_config(self):
        config = self._get_sparex_dealer_config()
        for sync in self:
            sync.sparex_dealer_configured = config["configured"]
            sync.sparex_dealer_login_url = config["login_url"]
            sync.sparex_dealer_products_url = config["products_url"]
            sync.sparex_dealer_username = config["username"]

    def _get_sparex_dealer_config(self):
        params = self.env["ir.config_parameter"].sudo()
        login_url = params.get_param(
            "southern_parts_intelligence.sparex_dealer_login_url",
            "https://us.sparex.com/customer/account/",
        )
        products_url = params.get_param(
            "southern_parts_intelligence.sparex_dealer_products_url",
            "https://us.sparex.com/",
        )
        username = params.get_param("southern_parts_intelligence.sparex_dealer_username", "")
        password = params.get_param("southern_parts_intelligence.sparex_dealer_password", "")
        return {
            "configured": bool(login_url and products_url and username and password),
            "login_url": login_url,
            "products_url": products_url,
            "username": username,
            "password": password,
        }

    def _run_one_batch(self):
        self.ensure_one()
        if self.mode == "sparex_dealer_sync":
            config = self._get_sparex_dealer_config()
            if not config["configured"]:
                self.write(
                    {
                        "state": "error",
                        "last_run_at": fields.Datetime.now(),
                        "run_count": self.run_count + 1,
                        "error_count": self.error_count + 1,
                        "last_message": "Sparex dealer sync is missing login URL, products URL, username, or password.",
                    }
                )
                return
            self.write(
                {
                    "state": "idle",
                    "last_run_at": fields.Datetime.now(),
                    "run_count": self.run_count + 1,
                    "last_message": (
                        "Sparex dealer source is configured for %s. The recurring backend job keeps this source "
                        "linked for Sparex-only harvest workers while preserving S.%% references. Cost updates "
                        "must use exact dealer/vendor cost evidence; Sales Price, images, publication, taxonomy, "
                        "and procurement remain separately delegated workflows."
                        % config["products_url"]
                    ),
                }
            )
            return
        if self.mode != "snapshot_refresh":
            self.write(
                {
                    "state": "idle",
                    "last_run_at": fields.Datetime.now(),
                    "run_count": self.run_count + 1,
                    "last_message": "Evidence-review mode is tracked here but does not perform live catalog writes.",
                }
            )
            return
        batch_size = max(min(self.batch_size or 500, 2000), 50)
        Product = self.env["product.template"].sudo()
        products = Product.search(
            [("active", "=", True), ("id", ">", self.last_product_id)],
            order="id",
            limit=batch_size,
        )
        if not products:
            self.write(
                {
                    "state": "idle",
                    "last_product_id": 0,
                    "last_run_at": fields.Datetime.now(),
                    "run_count": self.run_count + 1,
                    "last_message": "Reached the end of the product catalog; cursor reset for the next pass.",
                }
            )
            return
        try:
            products._compute_southern_parts_catalog_snapshot()
            products.write({"southern_parts_snapshot_refreshed_at": fields.Datetime.now()})
            self.write(
                {
                    "state": "idle",
                    "last_product_id": products[-1].id,
                    "last_run_at": fields.Datetime.now(),
                    "run_count": self.run_count + 1,
                    "processed_count": self.processed_count + len(products),
                    "last_message": "Refreshed website parts snapshots for %s products through product ID %s."
                    % (len(products), products[-1].id),
                }
            )
        except Exception as error:
            self.write(
                {
                    "state": "error",
                    "last_run_at": fields.Datetime.now(),
                    "run_count": self.run_count + 1,
                    "error_count": self.error_count + 1,
                    "last_message": str(error),
                }
            )

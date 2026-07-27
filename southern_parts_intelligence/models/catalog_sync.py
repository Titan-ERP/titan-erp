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
        ],
        default="snapshot_refresh",
        required=True,
        help="Live price, image, publication, and taxonomy writes are intentionally not part of this recurring job.",
    )
    batch_size = fields.Integer(default=500, required=True)
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

    def _run_one_batch(self):
        self.ensure_one()
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

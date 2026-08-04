import json
import uuid
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


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
            ("sparex_discovery", "Sparex Product Updates"),
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
        self.env["southern.parts.automation.run"].sudo().recover_expired_claims()
        syncs = self.sudo().search(
            [
                ("active", "=", True),
                ("state", "=", "idle"),
                ("internal_cron_enabled", "=", True),
            ],
            order="sequence, id",
            limit=3,
        )
        for sync in syncs:
            sync._run_one_batch()

    def action_run_now(self):
        for sync in self:
            sync.sudo()._run_one_batch()
        return True

    def action_queue_approved_apply(self):
        for sync in self:
            sync = sync.sudo()
            if sync.mode != "sparex_discovery" or sync.approval_state != "approved":
                raise UserError("Sparex apply and publication require an approved workflow.")
            schedule_was_enabled = sync.internal_cron_enabled
            if not schedule_was_enabled:
                sync.write({"internal_cron_enabled": True, "state": "idle"})
            try:
                sync._queue_sparex_dispatch("apply")
            finally:
                if not schedule_was_enabled:
                    sync.write(
                        {
                            "internal_cron_enabled": False,
                            "state": "paused",
                            "last_message": "Approved product release queued; recurring scheduling remains disabled.",
                        }
                    )
        return True

    def action_enable_dispatch_schedule(self):
        cron = self.env.ref(
            "southern_parts_intelligence.ir_cron_southern_parts_catalog_sync",
            raise_if_not_found=False,
        )
        for sync in self:
            if sync.mode != "sparex_discovery" or sync.approval_state != "approved":
                raise UserError("Enabling Sparex scheduling requires an approved workflow.")
            sync.write(
                {
                    "internal_cron_enabled": True,
                    "state": "idle",
                    "approval_state": "not_required",
                    "approved_by_id": False,
                    "approved_at": False,
                    "last_message": "Odoo scheduling enabled; awaiting the next bounded dispatch window.",
                }
            )
        if cron:
            cron.sudo().write({"active": True})
        return True

    def action_enable_continuous_release(self):
        cron = self.env.ref(
            "southern_parts_intelligence.ir_cron_southern_parts_catalog_sync",
            raise_if_not_found=False,
        )
        for sync in self.sudo():
            if sync.mode != "sparex_discovery" or sync.approval_state != "approved":
                raise UserError("Continuous Sparex release requires an approved workflow.")
            sync.write(
                {
                    "continuous_release_enabled": True,
                    "continuous_release_started_at": fields.Datetime.now(),
                    "continuous_release_completed_at": False,
                    "continuous_release_batch_count": 0,
                    "continuous_release_failure_streak": 0,
                    "internal_cron_enabled": True,
                    "state": "idle",
                    "last_message": (
                        "Continuous Sparex release enabled; Odoo will queue one bounded "
                        "approved batch at each safe dispatch window."
                    ),
                }
            )
        if cron:
            cron.sudo().write({"active": True, "interval_number": 5, "interval_type": "minutes"})
        return True

    def action_disable_continuous_release(self):
        self.filtered(lambda sync: sync.mode == "sparex_discovery").write(
            {
                "continuous_release_enabled": False,
                "internal_cron_enabled": False,
                "state": "paused",
                "approval_state": "not_required",
                "approved_by_id": False,
                "approved_at": False,
                "last_message": "Continuous Sparex release stopped by an operator.",
            }
        )
        return True

    def action_disable_dispatch_schedule(self):
        self.filtered(lambda sync: sync.mode == "sparex_discovery").write(
            {
                "continuous_release_enabled": False,
                "internal_cron_enabled": False,
                "state": "paused",
                "last_message": "Odoo product dispatch scheduling disabled.",
            }
        )
        return True

    def action_pause(self):
        self.write({"state": "paused"})

    def action_resume(self):
        self.write({"state": "idle"})

    def action_reset_cursor(self):
        self.write({"last_product_id": 0, "state": "idle", "last_message": "Cursor reset."})

    def _run_one_batch(self):
        self.ensure_one()
        if not self.internal_cron_enabled:
            self.write(
                {
                    "state": "paused",
                    "last_message": (
                        "Controlled maintenance is disabled. Review the workflow and enable "
                        "Internal Cron Enabled before running it."
                    ),
                }
            )
            return
        if self.mode == "sparex_discovery":
            mode = "evidence_only"
            if self.continuous_release_enabled:
                discovery = (
                    self.env["southern.sparex.discovery.run"]
                    .sudo()
                    .search([("company_id", "=", self.company_id.id)], order="id desc", limit=1)
                )
                if discovery and discovery.state in {"failed", "cancelled"}:
                    self.write(
                        {
                            "continuous_release_enabled": False,
                            "internal_cron_enabled": False,
                            "state": "paused",
                            "last_message": (
                                "Continuous Sparex release stopped because catalog discovery requires manual review."
                            ),
                        }
                    )
                    return False
                if discovery and discovery.state == "completed":
                    mode = "apply"
            return self._queue_sparex_dispatch(mode)
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
        batch_size = max(min(self.batch_size or 200, 200), 1)
        now = fields.Datetime.now()
        stale_before = now - timedelta(hours=24)
        run_id = self.env["southern.parts.automation.run"].begin_internal_run(
            self.id,
            {
                "name": "Catalog snapshot maintenance",
                "idempotency_key": "catalog:%s:%s:%s" % (self.id, self.last_product_id, uuid.uuid4().hex),
                "mode": "maintenance",
                "requested_count": batch_size,
            },
        )
        run = self.env["southern.parts.automation.run"].browse(run_id)
        Product = self.env["product.template"].sudo()
        try:
            with self.env.cr.savepoint():
                products = Product.search(
                    [
                        ("active", "=", True),
                        ("company_id", "in", [False, self.company_id.id]),
                        ("id", ">", self.last_product_id),
                        "|",
                        ("southern_parts_snapshot_refreshed_at", "=", False),
                        ("southern_parts_snapshot_refreshed_at", "<", stale_before),
                    ],
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
                            "last_message": ("Reached the end of the product catalog; cursor reset for the next pass."),
                        }
                    )
                    run.finish_run(
                        "succeeded",
                        {
                            "processed_count": 0,
                            "evidence_summary": ("No stale product snapshots were eligible; cursor reset."),
                        },
                    )
                    return True
                products._compute_southern_parts_catalog_snapshot()
                products.write({"southern_parts_snapshot_refreshed_at": now})
                self.write(
                    {
                        "state": "idle",
                        "last_product_id": products[-1].id,
                        "last_run_at": fields.Datetime.now(),
                        "run_count": self.run_count + 1,
                        "processed_count": self.processed_count + len(products),
                        "last_message": ("Refreshed website parts snapshots for %s products through product ID %s.")
                        % (len(products), products[-1].id),
                    }
                )
                run.finish_run(
                    "succeeded",
                    {
                        "processed_count": len(products),
                        "changed_count": len(products),
                        "evidence_summary": "Refreshed %s stale product snapshots." % len(products),
                    },
                )
                return True
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
            run.finish_run(
                "failed",
                {
                    "error_count": 1,
                    "error_message": str(error)[:2000],
                },
            )
            return False

    def _queue_sparex_dispatch(self, mode):
        self.ensure_one()
        if self.mode != "sparex_discovery":
            return False
        if mode not in {"evidence_only", "apply"}:
            raise ValueError("Unsupported Sparex dispatch mode.")
        if mode == "apply" and self.approval_state != "approved":
            raise UserError("Sparex apply and publication require an approved workflow.")
        now = fields.Datetime.now()
        gate = max(
            [value for value in (self.cooldown_until, self.next_allowed_run_at) if value],
            default=False,
        )
        if gate and gate > now:
            self.write({"last_message": "The next Sparex dispatch is not allowed before %s." % gate})
            return False
        Run = self.env["southern.parts.automation.run"].sudo()
        job_type = "sparex_discovery" if mode == "evidence_only" else "catalog_release"
        existing = Run.search_count(
            [
                ("sync_id", "=", self.id),
                ("job_type", "=", job_type),
                ("state", "in", ["queued", "running"]),
            ]
        )
        if existing:
            self.write({"last_message": "A %s dispatch is already queued or running." % job_type})
            return False
        if mode == "apply" and self.continuous_release_enabled:
            backlog = self.env["southern.sparex.discovery.item"].continuous_release_status()
            backlog_state = backlog.get("state")
            if backlog_state == "waiting":
                next_attempt_at = backlog.get("next_attempt_at")
                values = {
                    "last_message": (
                        "Continuous Sparex release is waiting for %s retry item(s)." % backlog.get("waiting_count", 0)
                    )
                }
                if next_attempt_at:
                    values["next_allowed_run_at"] = next_attempt_at
                self.write(values)
                return False
            if backlog_state in {"complete", "needs_review"}:
                complete = backlog_state == "complete"
                self.write(
                    {
                        "continuous_release_enabled": False,
                        "internal_cron_enabled": False,
                        "state": "paused",
                        "continuous_release_completed_at": (
                            fields.Datetime.now() if complete else False
                        ),
                        "approval_state": "not_required",
                        "approved_by_id": False,
                        "approved_at": False,
                        "last_message": (
                            "Continuous Sparex release completed; no unpublished actionable products remain."
                            if complete
                            else (
                                "Continuous Sparex release paused with %(manual)s manual-review "
                                "and %(blocked)s other blocked product(s)."
                            )
                            % {
                                "manual": backlog.get("manual_review_count", 0),
                                "blocked": backlog.get("blocked_count", 0)
                                + backlog.get("unlinked_count", 0)
                                + backlog.get("untracked_product_count", 0),
                            }
                        ),
                    }
                )
                return False
        requested_count = max(1, min(int(self.batch_size or 5), 5))
        request = {
            "schema_version": "1.0",
            "job_type": job_type,
            "limit": requested_count,
            "throttle_seconds": 3.0,
            "http_retries": 0,
            "publish": mode == "apply",
        }
        run_id = Run.queue_external_run(
            self.id,
            {
                "name": (
                    "Sparex evidence checkpoint"
                    if mode == "evidence_only"
                    else "Approved Sparex product update and release"
                ),
                "idempotency_key": "%s:%s:%s" % (job_type, self.id, uuid.uuid4().hex),
                "job_type": job_type,
                "mode": mode,
                "requested_count": requested_count,
                "request_json": json.dumps(request, sort_keys=True),
            },
        )
        self.write(
            {
                "state": "idle",
                "last_run_at": now,
                "run_count": self.run_count + 1,
                "last_message": "Queued Odoo-owned %s dispatch %s for AWS." % (job_type, run_id),
            }
        )
        return run_id

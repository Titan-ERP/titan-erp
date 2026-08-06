import hashlib
import json
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


SPAREX_CATALOG_LOCK_KEY = 0x535041524558
SPAREX_MANIFEST_SCHEMA = "sparex-manifest-v1"
MAX_MANIFEST_RECORDS = 500
SAVEPOINT_CHUNK_SIZE = 50
MAX_BISECTION_DEPTH = 6
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PERMANENT_FAILURES = {"identity", "schema", "uniqueness"}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def canonical_sha256(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _contains_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_float(child) for child in value)
    return False


def acquire_sparex_catalog_lock(env):
    env.cr.execute("SELECT pg_advisory_xact_lock(%s::bigint)", [SPAREX_CATALOG_LOCK_KEY])


class SouthernSparexCatalogIngestion(models.Model):
    _name = "southern.sparex.catalog.ingestion"
    _description = "Sparex Catalog Manifest Ingestion"
    _order = "create_date desc, id desc"

    manifest_sha256 = fields.Char(required=True, readonly=True, index=True)
    payload_sha256 = fields.Char(required=True, readonly=True, index=True)
    schema_version = fields.Char(required=True, readonly=True)
    parser_version = fields.Char(required=True, readonly=True)
    run_key = fields.Char(required=True, readonly=True, index=True)
    sweep_key = fields.Char(required=True, readonly=True, index=True)
    page_range = fields.Char(required=True, readonly=True)
    source_artifacts_json = fields.Text(required=True, readonly=True)
    state = fields.Selection(
        [("processing", "Processing"), ("complete", "Complete"), ("failed", "Failed")],
        required=True,
        default="processing",
        readonly=True,
        index=True,
    )
    record_count = fields.Integer(required=True, readonly=True)
    created_count = fields.Integer(readonly=True)
    updated_count = fields.Integer(readonly=True)
    unchanged_count = fields.Integer(readonly=True)
    ready_count = fields.Integer(readonly=True)
    rejected_count = fields.Integer(readonly=True)
    result_json = fields.Text(readonly=True)
    failure_class = fields.Char(readonly=True)
    failure_summary = fields.Char(readonly=True)
    completed_at = fields.Datetime(readonly=True, index=True)

    _manifest_unique = models.Constraint(
        "unique(manifest_sha256)", "A Sparex manifest can be ingested only once."
    )

    @api.model
    def _validate_contract(self, manifest, payload, supplied_manifest_sha256):
        manifest = dict(manifest or {})
        payload = list(payload or [])
        if _contains_float(payload):
            raise UserError(_("Sparex manifest decimal values must be encoded as strings."))
        required = {
            "schema_version",
            "parser_version",
            "run_id",
            "sweep_id",
            "page_range",
            "record_count",
            "payload_sha256",
            "source_artifacts",
        }
        if set(manifest) != required or manifest.get("schema_version") != SPAREX_MANIFEST_SCHEMA:
            raise UserError(_("The Sparex manifest schema is unknown or incomplete."))
        if not 1 <= len(payload) <= MAX_MANIFEST_RECORDS or int(manifest.get("record_count") or 0) != len(payload):
            raise UserError(_("Sparex manifests must contain between 1 and 500 records and an exact count."))
        payload_sha256 = canonical_sha256(payload)
        if payload_sha256 != str(manifest.get("payload_sha256") or "").casefold():
            raise UserError(_("The Sparex manifest payload checksum does not verify."))
        manifest_sha256 = canonical_sha256(manifest)
        if manifest_sha256 != str(supplied_manifest_sha256 or "").casefold():
            raise UserError(_("The Sparex manifest checksum does not verify."))
        artifacts = manifest.get("source_artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise UserError(_("The Sparex manifest must reference immutable source artifacts."))
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not str(artifact.get("uri") or "").startswith("s3://"):
                raise UserError(_("Every Sparex source artifact must use an S3 URI."))
            if not SHA256_PATTERN.fullmatch(str(artifact.get("sha256") or "").casefold()):
                raise UserError(_("Every Sparex source artifact must include a SHA-256 checksum."))
        return manifest, payload, manifest_sha256, payload_sha256

    @api.model
    def _failure_class(self, error):
        summary = str(error or "").casefold()
        if "schema" in summary or "manifest" in summary or "checksum" in summary:
            return "schema"
        if "unique" in summary or "duplicate" in summary:
            return "uniqueness"
        if "sku" in summary or "identity" in summary or "source url" in summary:
            return "identity"
        return "transient"

    @api.model
    def _persist_rejection(self, ingestion, record, index, error):
        original_sku = str(record.get("vendor_sku") or record.get("sku") or "").strip()[:128]
        normalized_sku = re.sub(r"\s+", "", str(record.get("normalized_sku") or original_sku)).upper()[:128]
        failure_class = self._failure_class(error)
        Rejection = self.env["southern.sparex.catalog.rejection"].sudo()
        existing = Rejection.search(
            [("manifest_sha256", "=", ingestion.manifest_sha256), ("record_index", "=", index)], limit=1
        )
        values = {
            "ingestion_id": ingestion.id,
            "manifest_sha256": ingestion.manifest_sha256,
            "payload_sha256": ingestion.payload_sha256,
            "record_index": index,
            "original_sku": original_sku,
            "normalized_sku": normalized_sku,
            "payload_record_sha256": canonical_sha256(record),
            "failure_class": failure_class,
            "exception_summary": str(error or "")[:500],
            "last_failure_at": fields.Datetime.now(),
            "permanent": failure_class in PERMANENT_FAILURES,
        }
        if existing:
            values["retry_count"] = existing.retry_count + 1
            existing.write(values)
        else:
            values.update({"first_failure_at": fields.Datetime.now(), "retry_count": 1})
            Rejection.create(values)

    @api.model
    def _ingest_slice(self, ingestion, indexed_records, artifact_uri, artifact_sha256, parser_version, depth=0):
        Item = self.env["southern.vendor.catalog.item"].sudo()
        try:
            with self.env.cr.savepoint():
                result = Item.upsert_catalog_items(
                    # The sweep marker is written atomically with each accepted observation.
                    "sparex",
                    [record for _index, record in indexed_records],
                    artifact_uri,
                    artifact_sha256,
                    schema_version=parser_version,
                )
            return {**result, "rejected": 0}
        except Exception as error:
            if len(indexed_records) == 1 or depth >= MAX_BISECTION_DEPTH:
                for index, record in indexed_records:
                    self._persist_rejection(ingestion, record, index, error)
                return {"created": 0, "updated": 0, "unchanged": 0, "ready": 0, "observed": 0, "rejected": len(indexed_records)}
            midpoint = len(indexed_records) // 2
            left = self._ingest_slice(
                ingestion, indexed_records[:midpoint], artifact_uri, artifact_sha256, parser_version, depth + 1
            )
            right = self._ingest_slice(
                ingestion, indexed_records[midpoint:], artifact_uri, artifact_sha256, parser_version, depth + 1
            )
            return {key: int(left.get(key) or 0) + int(right.get(key) or 0) for key in set(left) | set(right)}

    @api.model
    def ingest_manifest(self, manifest, payload, manifest_sha256):
        manifest, payload, manifest_sha256, payload_sha256 = self._validate_contract(
            manifest, payload, manifest_sha256
        )
        acquire_sparex_catalog_lock(self.env)
        existing = self.sudo().search([("manifest_sha256", "=", manifest_sha256)], limit=1)
        if existing:
            if existing.payload_sha256 != payload_sha256:
                raise UserError(_("A conflicting payload was supplied for an existing manifest hash."))
            if existing.state == "complete":
                return json.loads(existing.result_json or "{}")
            if existing.failure_class in PERMANENT_FAILURES:
                raise UserError(_("This manifest has a permanent failure and requires review."))
            ingestion = existing
            ingestion.write({"state": "processing", "failure_class": False, "failure_summary": False})
        else:
            ingestion = self.sudo().create(
                {
                    "manifest_sha256": manifest_sha256,
                    "payload_sha256": payload_sha256,
                    "schema_version": manifest["schema_version"],
                    "parser_version": str(manifest["parser_version"])[:64],
                    "run_key": str(manifest["run_id"])[:128],
                    "sweep_key": str(manifest["sweep_id"])[:128],
                    "page_range": str(manifest["page_range"])[:128],
                    "source_artifacts_json": canonical_json(manifest["source_artifacts"]),
                    "record_count": len(payload),
                    "state": "processing",
                }
            )
        primary_artifact = manifest["source_artifacts"][0]
        totals = {"created": 0, "updated": 0, "unchanged": 0, "ready": 0, "observed": 0, "rejected": 0}
        indexed = list(enumerate(payload))
        for start in range(0, len(indexed), SAVEPOINT_CHUNK_SIZE):
            result = self.with_context(sparex_sweep_key=str(manifest["sweep_id"])[:128])._ingest_slice(
                ingestion,
                indexed[start : start + SAVEPOINT_CHUNK_SIZE],
                primary_artifact["uri"],
                primary_artifact["sha256"],
                manifest["parser_version"],
            )
            for key in totals:
                totals[key] += int(result.get(key) or 0)
        response = {
            "manifest_sha256": manifest_sha256,
            "payload_sha256": payload_sha256,
            "ingestion_id": ingestion.id,
            **totals,
            "state": "complete",
        }
        ingestion.write(
            {
                "state": "complete",
                "created_count": totals["created"],
                "updated_count": totals["updated"],
                "unchanged_count": totals["unchanged"],
                "ready_count": totals["ready"],
                "rejected_count": totals["rejected"],
                "result_json": canonical_json(response),
                "completed_at": fields.Datetime.now(),
            }
        )
        return response

    @api.model
    def conflict_preflight(self, limit=200):
        """Return a bounded, read-only report; callers archive it before constraints are added."""
        bounded = max(1, min(int(limit or 200), 1_000))
        company_id = self.env.company.id
        queries = {
            "duplicate_normalized_references": (
                """
                SELECT UPPER(REGEXP_REPLACE(default_code, '\\s+', '', 'g')) AS key, ARRAY_AGG(id ORDER BY id)
                  FROM product_template
                 WHERE active IS NOT FALSE AND default_code ILIKE 'S.%%'
                 GROUP BY key HAVING COUNT(*) > 1 ORDER BY key LIMIT %s
                """,
                [bounded],
            ),
            "duplicate_staging_identities": (
                """
                SELECT source_id::text || ':' || normalized_sku, ARRAY_AGG(id ORDER BY id)
                  FROM southern_vendor_catalog_item
                 GROUP BY source_id, normalized_sku HAVING COUNT(*) > 1
                 ORDER BY 1 LIMIT %s
                """,
                [bounded],
            ),
            "duplicate_supplierinfo": (
                """
                SELECT partner_id::text || ':' || product_tmpl_id::text || ':' || COALESCE(product_code, '') || ':' || COALESCE(company_id, 0)::text,
                       ARRAY_AGG(id ORDER BY id)
                  FROM product_supplierinfo
                 GROUP BY partner_id, product_tmpl_id, product_code, company_id HAVING COUNT(*) > 1
                 ORDER BY 1 LIMIT %s
                """,
                [bounded],
            ),
            "one_sku_multiple_products": (
                """
                SELECT normalized_sku, ARRAY_AGG(DISTINCT product_id ORDER BY product_id)
                  FROM southern_vendor_catalog_item
                 WHERE product_id IS NOT NULL
                 GROUP BY normalized_sku HAVING COUNT(DISTINCT product_id) > 1
                 ORDER BY normalized_sku LIMIT %s
                """,
                [bounded],
            ),
            "multiple_skus_one_product": (
                """
                SELECT product_id::text, ARRAY_AGG(DISTINCT normalized_sku ORDER BY normalized_sku)
                  FROM southern_vendor_catalog_item
                 WHERE product_id IS NOT NULL
                 GROUP BY product_id HAVING COUNT(DISTINCT normalized_sku) > 1
                 ORDER BY product_id LIMIT %s
                """,
                [bounded],
            ),
            "normalization_collisions": (
                """
                SELECT source_id::text || ':' || normalized_sku, ARRAY_AGG(DISTINCT vendor_sku ORDER BY vendor_sku)
                  FROM southern_vendor_catalog_item
                 GROUP BY source_id, normalized_sku HAVING COUNT(DISTINCT vendor_sku) > 1
                 ORDER BY 1 LIMIT %s
                """,
                [bounded],
            ),
        }
        report = {"company_id": company_id, "generated_at": str(fields.Datetime.now()), "limit": bounded}
        total_conflicts = 0
        for name, (query, params) in queries.items():
            self.env.cr.execute(query, params)
            rows = [{"key": row[0], "record_ids_or_values": row[1]} for row in self.env.cr.fetchall()]
            report[name] = rows
            total_conflicts += len(rows)
        report["blocking"] = bool(total_conflicts)
        report["reported_conflict_groups"] = total_conflicts
        report["report_sha256"] = canonical_sha256(report)
        return report


class SouthernSparexCatalogSweep(models.Model):
    _name = "southern.sparex.catalog.sweep"
    _description = "Sparex Catalog Reconciliation Sweep"
    _order = "completed_at desc, create_date desc, id desc"

    sweep_key = fields.Char(required=True, readonly=True, index=True)
    parser_version = fields.Char(required=True, readonly=True)
    rules_version = fields.Char(required=True, readonly=True)
    state = fields.Selection(
        [("in_progress", "In Progress"), ("complete", "Complete"), ("invalid", "Invalid")],
        default="in_progress",
        required=True,
        readonly=True,
        index=True,
    )
    frontier_page_count = fields.Integer(required=True, readonly=True)
    processed_page_count = fields.Integer(readonly=True)
    resolved_page_count = fields.Integer(readonly=True)
    failed_page_count = fields.Integer(readonly=True)
    skipped_page_count = fields.Integer(readonly=True)
    cooldown_page_count = fields.Integer(readonly=True)
    observed_item_count = fields.Integer(readonly=True)
    evidence_uri = fields.Char(required=True, readonly=True)
    evidence_sha256 = fields.Char(required=True, readonly=True)
    consecutive_complete_count = fields.Integer(default=0, readonly=True)
    absence_candidate_count = fields.Integer(default=0, readonly=True)
    completed_at = fields.Datetime(readonly=True, index=True)

    _sweep_key_unique = models.Constraint("unique(sweep_key)", "Sparex sweep keys must be unique.")

    @api.model
    def record_checkpoint(self, values):
        values = dict(values or {})
        acquire_sparex_catalog_lock(self.env)
        sweep_key = str(values.get("sweep_key") or "").strip()[:128]
        artifact_uri = str(values.get("evidence_uri") or "").strip()
        artifact_sha = str(values.get("evidence_sha256") or "").casefold()
        if not sweep_key or not artifact_uri.startswith("s3://") or not SHA256_PATTERN.fullmatch(artifact_sha):
            raise UserError(_("Sweep checkpoints require a key and immutable S3 evidence."))
        sweep = self.sudo().search([("sweep_key", "=", sweep_key)], limit=1)
        write_values = {
            "parser_version": str(values.get("parser_version") or "")[:64],
            "rules_version": str(values.get("rules_version") or "")[:64],
            "frontier_page_count": max(0, int(values.get("frontier_page_count") or 0)),
            "processed_page_count": max(0, int(values.get("processed_page_count") or 0)),
            "resolved_page_count": max(0, int(values.get("resolved_page_count") or 0)),
            "failed_page_count": max(0, int(values.get("failed_page_count") or 0)),
            "skipped_page_count": max(0, int(values.get("skipped_page_count") or 0)),
            "cooldown_page_count": max(0, int(values.get("cooldown_page_count") or 0)),
            "observed_item_count": max(0, int(values.get("observed_item_count") or 0)),
            "evidence_uri": artifact_uri,
            "evidence_sha256": artifact_sha,
            "state": "in_progress",
        }
        if sweep:
            if sweep.state == "complete":
                raise UserError(_("Completed Sparex sweeps are immutable."))
            sweep.write(write_values)
        else:
            sweep = self.sudo().create({"sweep_key": sweep_key, **write_values})
        return sweep.id

    def action_complete(self):
        acquire_sparex_catalog_lock(self.env)
        source = self.env["southern.vendor.catalog.source"].sudo().search(
            [("company_id", "=", self.env.company.id), ("code", "=", "sparex")], limit=1
        )
        for sweep in self:
            if sweep.state == "complete":
                continue
            if (
                sweep.frontier_page_count <= 0
                or sweep.processed_page_count + sweep.resolved_page_count != sweep.frontier_page_count
                or sweep.failed_page_count
                or sweep.skipped_page_count
                or sweep.cooldown_page_count
                or not sweep.parser_version
                or not sweep.rules_version
            ):
                raise UserError(_("A partial, failed, skipped, or cooldown-interrupted sweep cannot be completed."))
            previous = self.sudo().search(
                [("id", "!=", sweep.id), ("state", "=", "complete")], order="completed_at desc, id desc", limit=1
            )
            consecutive = 1
            if previous and previous.parser_version == sweep.parser_version and previous.rules_version == sweep.rules_version:
                consecutive = previous.consecutive_complete_count + 1
            absent = 0
            if source and consecutive >= 2:
                absent = self.env["southern.vendor.catalog.item"].sudo().search_count(
                    [("source_id", "=", source.id), ("last_seen_sweep_key", "!=", sweep.sweep_key), ("catalog_state", "!=", "archived")]
                )
            sweep.write(
                {
                    "state": "complete",
                    "consecutive_complete_count": consecutive,
                    "absence_candidate_count": absent,
                    "completed_at": fields.Datetime.now(),
                }
            )
        return True


class SouthernSparexCatalogRejection(models.Model):
    _name = "southern.sparex.catalog.rejection"
    _description = "Sparex Catalog Record Rejection"
    _order = "last_failure_at desc, id desc"

    ingestion_id = fields.Many2one(
        "southern.sparex.catalog.ingestion", required=True, readonly=True, ondelete="cascade", index=True
    )
    manifest_sha256 = fields.Char(required=True, readonly=True, index=True)
    payload_sha256 = fields.Char(required=True, readonly=True)
    payload_record_sha256 = fields.Char(required=True, readonly=True)
    record_index = fields.Integer(required=True, readonly=True)
    original_sku = fields.Char(readonly=True, index=True)
    normalized_sku = fields.Char(readonly=True, index=True)
    failure_class = fields.Selection(
        [("identity", "Identity"), ("schema", "Schema"), ("uniqueness", "Uniqueness"), ("transient", "Transient")],
        required=True,
        readonly=True,
        index=True,
    )
    exception_summary = fields.Char(required=True, readonly=True)
    first_failure_at = fields.Datetime(required=True, readonly=True)
    last_failure_at = fields.Datetime(required=True, readonly=True)
    retry_count = fields.Integer(required=True, default=1, readonly=True)
    permanent = fields.Boolean(required=True, readonly=True, index=True)

    _manifest_record_unique = models.Constraint(
        "unique(manifest_sha256, record_index)", "A manifest record can have only one rejection ledger entry."
    )

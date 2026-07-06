# -*- coding: utf-8 -*-
import os
import json
import logging
import tempfile
import time
import odoo
import odoo.tools
import odoo.release
from datetime import datetime, timezone, timedelta
from odoo import models, fields
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DmcBackupLog(models.Model):
    _name = 'dmc.backup.log'
    _description = 'DMC Backup Log'
    _order = 'backup_date desc'

    name          = fields.Char(readonly=True)
    backup_date   = fields.Datetime(default=fields.Datetime.now, readonly=True)
    db_name       = fields.Char(readonly=True)
    odoo_version  = fields.Char(readonly=True)
    size_mb       = fields.Float(digits=(10, 2), readonly=True)
    state         = fields.Selection([('running', 'Running'), ('success', 'Success'), ('failed', 'Failed')], readonly=True)
    error_message = fields.Text(readonly=True)
    attachment_id = fields.Many2one('ir.attachment', ondelete='set null', readonly=True)
    storage_url   = fields.Char(readonly=True, string='Storage URL')
    storage_type  = fields.Selection(
        [('azure', 'Azure Blob'), ('onedrive', 'OneDrive')],
        readonly=True,
        string='Storage',
    )

    def unlink(self):
        self._delete_remote_files()
        self.mapped('attachment_id').unlink()
        return super().unlink()

    def _delete_remote_files(self):
        import requests
        config = self.env['dmc.backup.config'].sudo().search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            return
        records = self.filtered('storage_url')
        if not records:
            return

        _od_token    = None
        _od_drive_id = None

        for record in records:
            # Determine backend from stored field; fall back to URL heuristic for legacy records
            rec_type = record.storage_type or (
                'azure' if '.blob.core.windows.net' in (record.storage_url or '') else 'onedrive'
            )
            if rec_type == 'onedrive':
                if _od_token is None:
                    try:
                        _od_token    = config._get_onedrive_token()
                        _od_drive_id = config._resolve_onedrive_drive(_od_token)
                    except Exception as exc:
                        _logger.warning('OneDrive auth failed during delete: %s', exc)
                        continue
                headers   = {'Authorization': f'Bearer {_od_token}'}
                folder    = (config.onedrive_folder_path or '').strip('/')
                file_name = (record.name or '').strip()
                item_path = f'{folder}/{file_name}' if folder else file_name
                url = (
                    f'https://graph.microsoft.com/v1.0'
                    f'/drives/{_od_drive_id}/root:/{item_path}'
                )
                try:
                    resp = requests.delete(url, headers=headers, timeout=30)
                    if resp.status_code not in (204, 404):
                        resp.raise_for_status()
                    _logger.info('OneDrive file deleted: %s', item_path)
                except Exception as exc:
                    _logger.warning('OneDrive delete failed for %s: %s', item_path, exc)
            else:
                sas_token = (config.azure_sas_token or '').strip()
                if not sas_token:
                    continue
                url = f'{record.storage_url}?{sas_token}'
                try:
                    resp = requests.delete(url, timeout=30)
                    if resp.status_code not in (200, 202, 404):
                        resp.raise_for_status()
                    _logger.info('Azure blob deleted: %s', record.storage_url)
                except Exception as exc:
                    _logger.warning('Azure blob delete failed for %s: %s', record.storage_url, exc)

    def action_download(self):
        self.ensure_one()
        if not self.storage_url:
            raise UserError('No remote storage URL recorded for this backup.')
        # Use stored field; fall back to URL heuristic for legacy records without storage_type
        storage_type = self.storage_type or (
            'azure' if '.blob.core.windows.net' in (self.storage_url or '') else 'onedrive'
        )
        if storage_type == 'azure':
            config = self.env['dmc.backup.config'].sudo().search(
                [('is_default', '=', True)], limit=1
            )
            sas_token = (config.azure_sas_token or '').strip() if config else ''
            if not sas_token:
                raise UserError('No Azure SAS token found on the default destination.')
            url    = f'{self.storage_url}?{sas_token}'
            target = 'self'
        else:
            url    = self.storage_url
            target = 'new'
        return {
            'type':   'ir.actions.act_url',
            'url':    url,
            'target': target,
        }


class DmcBackupService(models.Model):
    _name = 'dmc.backup.service'
    _description = 'DMC Backup Service'

    def run_backup(self):
        config = self.env['dmc.backup.config'].sudo().search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            raise UserError(
                'No default backup destination is configured. '
                'Go to DMC Backup → Configuration and set one as default.'
            )
        retention_days = config.retention_days

        db_name   = self.env.cr.dbname
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        file_name = f'backup_{db_name}_{timestamp}.zip'

        zip_fd, zip_path = tempfile.mkstemp(suffix='.tmp')
        os.close(zip_fd)

        # Create 'running' log immediately via a separate cursor so it is visible at once
        log_id = None
        try:
            with self.env.registry.cursor() as start_cr:
                start_env = self.env(cr=start_cr)
                running_log = start_env['dmc.backup.log'].sudo().create({
                    'name':         file_name,
                    'db_name':      db_name,
                    'odoo_version': odoo.release.version,
                    'state':        'running',
                    'storage_type': config.storage_type,
                })
                log_id = running_log.id
                start_cr.commit()
        except Exception as start_err:
            _logger.warning('Could not create running log: %s', start_err)

        _logger.info('Starting DB backup: %s', file_name)
        try:
            self._dump_db(db_name, zip_path, config)
            file_size = os.path.getsize(zip_path)

            if config.storage_type == 'onedrive':
                storage_url, actual_name = self._push_to_onedrive(zip_path, file_size, file_name, config)
                if actual_name != file_name:
                    _logger.info('OneDrive renamed file: %s → %s', file_name, actual_name)
                    file_name = actual_name  # use actual name for log record
                _logger.info('OneDrive push complete: %s', storage_url)
            else:
                storage_url = self._push_to_azure(zip_path, file_size, file_name, config)
                _logger.info('Azure push complete: %s', storage_url)

            # Write success log on a separate committed cursor so it survives
            # even if the main cron transaction is later rolled back.
            with self.env.registry.cursor() as success_cr:
                success_env = self.env(cr=success_cr)
                if log_id:
                    success_env['dmc.backup.log'].sudo().browse(log_id).write({
                        'name':         file_name,
                        'size_mb':      round(file_size / 1024 / 1024, 2),
                        'state':        'success',
                        'storage_url':  storage_url,
                        'storage_type': config.storage_type,
                    })
                else:
                    success_env['dmc.backup.log'].sudo().create({
                        'name':          file_name,
                        'db_name':       db_name,
                        'odoo_version':  odoo.release.version,
                        'size_mb':       round(file_size / 1024 / 1024, 2),
                        'state':         'success',
                        'storage_url':   storage_url,
                        'storage_type':  config.storage_type,
                    })
                success_cr.commit()

            _logger.info('Backup complete: %s (%.2f MB)', file_name, round(file_size / 1024 / 1024, 2))

        except Exception as e:
            # Update or create failure log on a separate cursor — survives the cron rollback
            try:
                with self.env.registry.cursor() as new_cr:
                    new_env = self.env(cr=new_cr)
                    if log_id:
                        new_env['dmc.backup.log'].sudo().browse(log_id).write({
                            'state':         'failed',
                            'error_message': str(e),
                            'storage_type':  config.storage_type,
                        })
                    else:
                        new_env['dmc.backup.log'].sudo().create({
                            'name':          file_name,
                            'db_name':       db_name,
                            'odoo_version':  odoo.release.version,
                            'state':         'failed',
                            'error_message': str(e),
                            'storage_type':  config.storage_type,
                        })
                    new_cr.commit()
            except Exception as log_err:
                _logger.error('Could not write failure log: %s', log_err)
            _logger.error('Backup failed: %s', e)
            raise

        finally:
            if os.path.exists(zip_path):
                try:
                    os.unlink(zip_path)
                except OSError:
                    pass

        try:
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=retention_days)
            old = self.env['dmc.backup.log'].sudo().search([('backup_date', '<', cutoff)])
            old.unlink()
            _logger.info('Cleanup complete: %d old backup(s) removed', len(old))
        except Exception as cleanup_err:
            _logger.error(
                'Retention cleanup failed (backup was uploaded successfully): %s',
                cleanup_err,
            )

    # ── Backup generation ────────────────────────────────────────────────────

    def _find_pg_dump(self):
        """Return the pg_dump binary for the connected PostgreSQL server version.

        Priority:
        1. Odoo's configured pg_dump (find_pg_tool respects pg_dump_path in odoo.conf)
        2. Version-specific system path matching the server major version, as a
           fallback for environments where the system pg_dump lags behind the server
           (e.g. /usr/bin/pg_dump is pg14 but the server is pg16).
        """
        from odoo.tools.misc import find_pg_tool

        configured = find_pg_tool('pg_dump')

        # If the configured binary's major version matches the server, use it.
        server_major = self.env.cr._obj.connection.server_version // 10000
        try:
            import subprocess as _sp
            out = _sp.run([configured, '--version'], capture_output=True, timeout=5).stdout
            # output: "pg_dump (PostgreSQL) 16.14\n"
            configured_major = int(out.decode().split()[-1].split('.')[0])
            if configured_major == server_major:
                return configured
        except Exception:
            pass

        # Fallback: version-specific path when the configured binary is too old.
        for candidate in (
            f'/usr/lib/postgresql/{server_major}/bin/pg_dump',  # Debian/Ubuntu
            f'/usr/pgsql-{server_major}/bin/pg_dump',           # RHEL/CentOS
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        return configured

    def _is_pg_dump_available(self):
        """Return True if pg_dump can run (pg_settings accessible to the DB user).

        On Odoo SH staging/dev branches the app user has pg_settings revoked, which
        causes pg_dump to fail at startup before it reads any data.  This check lets
        _dump_db decide whether to use pg_dump or the Python/psycopg2 fallback.

        The probe must run inside a savepoint.  A permission-denied error from
        PostgreSQL puts the entire transaction into ABORTED state; without a
        savepoint to roll back to, every subsequent cr.execute() in the same
        transaction fails with InFailedSqlTransaction.
        """
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute("SELECT 1 FROM pg_settings LIMIT 1")
            return True
        except Exception:
            return False

    # Tables skipped from COPY in skip_large_tables mode.  Together these can
    # exceed 2 GB in a mature Odoo database (mail_message + mail_mail alone are
    # ~2.5 GB here) and are not needed for staging restores.
    _SKIP_LARGE_TABLES = frozenset({
        'ir_attachment',
        'mail_message',
        'mail_mail',
        'mailing_trace',
        'marketing_trace',
    })

    def _dump_db(self, db_name, zip_path, config=None):
        import zipfile

        neutralize         = config.neutralize if config else False
        skip_large_tables  = config.skip_large_tables if config else True
        include_filestore  = config.include_filestore if config else False
        purge_stale_assets = config.purge_stale_assets if config else True

        cr = self.env.cr
        cr.execute(
            "SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'"
        )
        modules = dict(cr.fetchall())
        # Float division matches odoo.service.db.dump_db_manifest: "16.14" not "16.0"
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version / 100, 100)
        manifest_bytes = json.dumps({
            'odoo_dump': '1',
            'db_name':       db_name,
            'version':       odoo.release.version,
            'version_info':  odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version':    pg_version,
            'modules':       modules,
        }, indent=4).encode()

        with tempfile.TemporaryDirectory() as tmp_dir:
            dump_path = os.path.join(tmp_dir, 'dump.sql')

            if self._is_pg_dump_available():
                self._run_pg_dump(db_name, dump_path)
            else:
                _logger.warning(
                    'pg_dump unavailable (pg_settings access restricted — '
                    'typical of Odoo SH staging/dev branches); '
                    'using Python/psycopg2 SQL dump as fallback.'
                )
                # ir_attachment must always be excluded when include_filestore is
                # False: copying the DB rows without the actual filestore files
                # produces dangling store_fname references on the restored host.
                if include_filestore:
                    skip_set = (self._SKIP_LARGE_TABLES - {'ir_attachment'}) if skip_large_tables else None
                else:
                    base = self._SKIP_LARGE_TABLES if skip_large_tables else frozenset()
                    skip_set = base | frozenset({'ir_attachment'})
                self._write_python_sql_dump(dump_path, skip_tables=skip_set)

            if neutralize or purge_stale_assets:
                with open(dump_path, 'ab') as nf:
                    if neutralize:
                        self._write_neutralization(nf)
                    if purge_stale_assets:
                        self._write_asset_cleanup(nf)

            with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                zf.write(dump_path, 'dump.sql')
                zf.writestr('manifest.json', manifest_bytes)
                if include_filestore:
                    fs_dir = odoo.tools.config.filestore(db_name)
                    if os.path.isdir(fs_dir):
                        for root, _dirs, files in os.walk(fs_dir):
                            for fname in files:
                                full_path = os.path.join(root, fname)
                                arcname = 'filestore/' + os.path.relpath(full_path, fs_dir)
                                zf.write(full_path, arcname)
                else:
                    zf.writestr(zipfile.ZipInfo('filestore/'), b'')

    def _run_pg_dump(self, db_name, dump_path):
        """Run pg_dump subprocess to produce the SQL dump.

        Matches odoo.service.db.dump_db exactly: --no-owner only, connection
        parameters via exec_pg_environ() (PGHOST/PGPORT/PGUSER/PGPASSWORD).
        """
        import subprocess
        from odoo.tools.misc import exec_pg_environ

        cmd = [self._find_pg_dump(), '--no-owner', '--clean', '--if-exists', '--file=' + dump_path, db_name]
        result = subprocess.run(
            cmd, env=exec_pg_environ(), check=False, timeout=3600,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip()
            raise Exception(f'pg_dump failed (exit {result.returncode}): {err}')

    def _write_python_sql_dump(self, dump_path, skip_tables=None):
        """Write a psql-compatible SQL dump using psycopg2 catalog queries and COPY TO STDOUT.

        Used when pg_dump is unavailable (e.g. Odoo SH staging where the app user
        cannot access pg_settings, which pg_dump requires at startup).  Produces a
        dump.sql that is structurally identical to pg_dump plain-format output and
        is compatible with Odoo SH's restore_db / import utility.

        skip_tables: optional frozenset of table names whose data (DELETE FROM ONLY + COPY)
        is omitted from the dump.  The CREATE TABLE statement is still emitted so the
        schema is complete; the table just starts empty after restore.  Use this to
        exclude large email/tracking tables (mail_message, mail_mail, …) that are
        irrelevant to staging and can push the dump past psql timeout limits on Odoo SH.

        PostgreSQL native table inheritance (INHERITS) is re-established via
        ALTER TABLE ... INHERIT statements emitted between the schema and data
        sections so that action tables (ir_act_client, ir_act_window, etc.) are
        visible through ir_actions even on a fresh database with no --init base.
        """
        cr  = self.env.cr
        raw = cr._obj  # psycopg2 cursor — needed for copy_expert

        # SET LOCAL scopes these to the current transaction so the connection
        # returns to Odoo's pool with the original timeout values intact.
        cr.execute("SET LOCAL statement_timeout = 0")
        cr.execute("SET LOCAL lock_timeout = 0")
        # Empty search_path forces all catalog functions (pg_get_constraintdef,
        # pg_get_indexdef, pg_get_expr, …) to emit fully schema-qualified names.
        # pg_catalog is always implicitly visible regardless of search_path.
        cr.execute("SET LOCAL search_path = ''")

        with open(dump_path, 'wb') as f:
            def w(text):
                f.write(text.encode('utf-8') if isinstance(text, str) else text)

            # ── Header ────────────────────────────────────────────────────────
            # ON_ERROR_ROLLBACK on: psql automatically issues ROLLBACK TO
            # SAVEPOINT after each failed statement.  This recovers the
            # PostgreSQL transaction from the aborted state so subsequent
            # COPY/ALTER statements continue to execute normally.  Without
            # this, a single COPY failure puts the entire --single-transaction
            # into aborted state — every subsequent statement silently fails,
            # the final COMMIT becomes a ROLLBACK, and Odoo SH reverts to the
            # old broken database.
            #
            # ON_ERROR_STOP off: additionally prevents psql itself from
            # exiting on the error, so the file is always read to completion
            # regardless of psql's --on-error-stop=1 command-line flag.
            w("\\set ON_ERROR_ROLLBACK on\n")
            w("\\set ON_ERROR_STOP off\n")
            w("SET statement_timeout = 0;\n")
            w("SET lock_timeout = 0;\n")
            w("SET idle_in_transaction_session_timeout = 0;\n")
            w("SET client_encoding = 'UTF8';\n")
            w("SET standard_conforming_strings = on;\n")
            w("SELECT pg_catalog.set_config('search_path', '', false);\n")
            w("SET check_function_bodies = false;\n")
            w("SET xmloption = content;\n")
            w("SET client_min_messages = warning;\n")
            w("SET row_security = off;\n\n")

            # ── Non-default schemas — must come before extensions so that
            # CREATE EXTENSION ... WITH SCHEMA "x" succeeds (schema must exist)
            cr.execute("""
                SELECT n.nspname
                FROM pg_namespace n
                WHERE n.nspname NOT IN ('public', 'information_schema',
                                        'pg_catalog', 'pg_toast')
                AND n.nspname NOT LIKE 'pg_%'
                ORDER BY n.nspname
            """)
            for (nspname,) in cr.fetchall():
                w(f'CREATE SCHEMA IF NOT EXISTS "{nspname}";\n')
            w('\n')

            # ── Extensions ────────────────────────────────────────────────────
            cr.execute("""
                SELECT e.extname, n.nspname
                FROM pg_extension e
                JOIN pg_namespace n ON n.oid = e.extnamespace
                WHERE e.extname != 'plpgsql'
                ORDER BY e.extname
            """)
            for extname, nspname in cr.fetchall():
                w(f'CREATE EXTENSION IF NOT EXISTS "{extname}" WITH SCHEMA "{nspname}";\n')
            w('\n')

            # ── User-defined functions in public schema ────────────────────────
            cr.execute("""
                SELECT pg_get_functiondef(p.oid)
                FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'public'
                AND p.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
                ORDER BY p.proname, p.oid
            """)
            for (funcdef,) in cr.fetchall():
                if funcdef:
                    w(funcdef.rstrip())
                    w(';\n\n')

            # ── Sequences — skip extension-owned and IDENTITY-auto-sequences ──
            cr.execute("""
                SELECT s.oid, n.nspname, s.relname,
                       sq.seqstart, sq.seqincrement, sq.seqmin, sq.seqmax, sq.seqcycle
                FROM pg_class s
                JOIN pg_namespace n ON n.oid = s.relnamespace
                JOIN pg_sequence sq ON sq.seqrelid = s.oid
                WHERE s.relkind = 'S'
                AND n.nspname = 'public'
                AND s.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
                AND NOT EXISTS (
                    SELECT 1 FROM pg_depend d
                    JOIN pg_attribute a
                        ON a.attrelid = d.refobjid AND a.attnum = d.refobjsubid
                    WHERE d.objid = s.oid
                    AND d.deptype IN ('i', 'a')
                    AND a.attidentity != ''
                )
                ORDER BY s.relname
            """)
            sequences = cr.fetchall()
            for _oid, nspname, seqname, start, inc, min_val, max_val, cycle in sequences:
                w(f'CREATE SEQUENCE IF NOT EXISTS "{nspname}"."{seqname}"\n'
                  f'    START WITH {start} INCREMENT BY {inc}\n'
                  f'    MINVALUE {min_val} MAXVALUE {max_val}\n'
                  f'    {"CYCLE" if cycle else "NO CYCLE"};\n\n')

            # ── Tables — schema only; FK constraints added after data load ────
            cr.execute("""
                SELECT c.oid, n.nspname, c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'r'
                AND n.nspname = 'public'
                AND c.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
                ORDER BY c.relname
            """)
            tables = cr.fetchall()

            for tbl_oid, nspname, tblname in tables:
                cr.execute("""
                    SELECT a.attname,
                           pg_catalog.format_type(a.atttypid, a.atttypmod),
                           pg_catalog.pg_get_expr(ad.adbin, ad.adrelid),
                           a.attnotnull,
                           a.attidentity,
                           a.attgenerated
                    FROM pg_catalog.pg_attribute a
                    LEFT JOIN pg_catalog.pg_attrdef ad
                        ON a.attrelid = ad.adrelid AND a.attnum = ad.adnum
                    WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
                    ORDER BY a.attnum
                """, (tbl_oid,))
                cols = cr.fetchall()
                if not cols:
                    continue

                col_defs = []
                for attname, col_type, default_val, attnotnull, attidentity, attgenerated in cols:
                    col_def = f'    "{attname}" {col_type}'
                    if attidentity == 'a':
                        col_def += ' GENERATED ALWAYS AS IDENTITY'
                    elif attidentity == 'd':
                        col_def += ' GENERATED BY DEFAULT AS IDENTITY'
                    elif attgenerated == 's':
                        # GENERATED ALWAYS AS (expr) STORED — column references in
                        # the expression are only valid with this syntax, not DEFAULT.
                        col_def += f' GENERATED ALWAYS AS ({default_val}) STORED'
                    elif default_val:
                        col_def += f' DEFAULT {default_val}'
                    if attnotnull and not attidentity and not attgenerated:
                        col_def += ' NOT NULL'
                    col_defs.append(col_def)

                w(f'CREATE TABLE IF NOT EXISTS "{nspname}"."{tblname}" (\n')
                w(',\n'.join(col_defs))
                w('\n);\n\n')

            # ── Table inheritance (PostgreSQL native INHERITS) ─────────────────
            #
            # Odoo 19's action tables use PostgreSQL native table inheritance
            # (ir_act_window, ir_act_client, ir_act_server, ir_act_url, and
            # ir_act_report_xml all INHERIT from ir_actions in base_data.sql).
            # Our CREATE TABLE IF NOT EXISTS statements above do NOT include the
            # INHERITS clause, so on a fresh database the inheritance hierarchy
            # is absent after the schema is created.
            #
            # Without INHERITS:
            #   SELECT id, type FROM ir_actions WHERE id = 310
            # returns nothing (the row lives in ir_act_client, not visible
            # through ir_actions), so /web/action/load raises
            # "The action '310' does not exist" on every navigation.
            #
            # We emit ALTER TABLE ... INHERIT ... here (after schema, before
            # data) to restore the hierarchy.  Each statement is wrapped in
            # a DO block so that if --init base already ran and INHERITS is
            # set up, the duplicate-inherit error is caught and silently skipped
            # (idempotent).  On a fresh database the ALTER TABLE succeeds and
            # the hierarchy is in place before COPY fills the child tables.
            cr.execute("""
                SELECT nc.nspname, c.relname, p.relname
                FROM pg_inherits i
                JOIN pg_class c  ON c.oid = i.inhrelid
                JOIN pg_namespace nc ON nc.oid = c.relnamespace
                JOIN pg_class p  ON p.oid = i.inhparent
                JOIN pg_namespace np ON np.oid = p.relnamespace
                WHERE nc.nspname = 'public' AND np.nspname = 'public'
                ORDER BY c.relname
            """)
            inherit_rows = cr.fetchall()
            if inherit_rows:
                w('-- Restore PostgreSQL native table-inheritance hierarchy\n')
                for inh_ns, child_tbl, parent_tbl in inherit_rows:
                    w(f'DO $$ BEGIN ALTER TABLE "{inh_ns}"."{child_tbl}"'
                      f' INHERIT "{inh_ns}"."{parent_tbl}";'
                      f' EXCEPTION WHEN others THEN NULL; END $$;\n')
                w('\n')

            # ── Data — COPY TO STDOUT (works under Odoo SH app-user permissions) ─
            #
            # Odoo SH initializes the database (--init base) BEFORE running the
            # restore dump, so core tables like ir_act_window already contain base
            # Odoo data with IDs starting from 1.  Without DELETE FROM ONLY, COPY
            # fails on PK collision and ON_ERROR_ROLLBACK rolls it back — the table
            # retains the init data, not our production data.  ir_ui_menu COPY
            # succeeds because our custom menu IDs are in a higher range; menus
            # appear but every app click gives "Missing Action" because ir_act_window
            # has only base init records.  DELETE FROM ONLY clears init data so COPY
            # always runs against an empty table with no PK conflicts.
            #
            # ONLY is required to prevent PostgreSQL native-inheritance cascade.
            # Odoo 19 creates its action tables with PG native INHERITS
            # (base_data.sql: ir_act_window, ir_act_client, ir_act_server, etc.
            # all INHERIT from ir_actions).  Without ONLY, DELETE FROM ir_actions
            # cascades through PG inheritance and wipes every child table.  The
            # child tables are processed alphabetically before ir_actions, so
            # their COPY data would be destroyed by the parent DELETE before the
            # dump finishes.  After restore all action tables would be empty, and
            # any user.action_id set during --update all would point to a missing
            # action → "The action 'N' does not exist" on every login.
            # ONLY restricts the DELETE to directly-stored rows only (0 for
            # ir_actions, which has no direct rows — all data lives in child
            # tables).  For all other tables ONLY is a no-op.
            #
            # DISABLE TRIGGER ALL suppresses FK-check triggers so COPY can run
            # before FK constraints are applied (added after all data is loaded).
            # Requires owning the table — Odoo SH app user does.
            for tbl_oid, nspname, tblname in tables:
                cr.execute("""
                    SELECT attname FROM pg_attribute
                    WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped
                    AND attgenerated = ''
                    ORDER BY attnum
                """, (tbl_oid,))
                col_names = [row[0] for row in cr.fetchall()]
                if not col_names:
                    continue
                if skip_tables and tblname in skip_tables:
                    # Schema was already written (CREATE TABLE IF NOT EXISTS above).
                    # Skip DELETE FROM ONLY + COPY so this large table is left empty
                    # on restore — its data is irrelevant for staging and would push
                    # the dump past psql timeout limits on Odoo SH.
                    w(f'-- Skipped data for {tblname} (excluded from dump)\n\n')
                    if tblname == 'ir_attachment':
                        # ir_attachment is skipped (no filestore on restore), but
                        # ir.ui.menu.web_icon_data is a Binary(attachment=True) field:
                        # load_menus() reads its data from ir_attachment.  With the
                        # table empty, web_icon_data is False for every app and the
                        # home screen shows broken box icons instead of module icons.
                        #
                        # Fix: emit only the icon-attachment rows, with filestore data
                        # read in Python and inlined into db_datas (store_fname=NULL).
                        # This makes icons work without needing the filestore on the
                        # restored database.
                        filestore_root = odoo.tools.config.filestore(self.env.cr.dbname)

                        # Non-generated columns (same query pattern used above for COPY)
                        cr.execute("""
                            SELECT attname
                            FROM pg_catalog.pg_attribute
                            WHERE attrelid = 'public.ir_attachment'::regclass
                            AND attnum > 0 AND NOT attisdropped AND attgenerated = ''
                            ORDER BY attnum
                        """)
                        icon_att_cols = [row[0] for row in cr.fetchall()]
                        icon_col_sql  = ', '.join(f'"{c}"' for c in icon_att_cols)

                        cr.execute("""
                            SELECT id, store_fname, db_datas
                            FROM public.ir_attachment
                            WHERE res_model = 'ir.ui.menu' AND res_field = 'web_icon_data'
                            ORDER BY id
                        """)
                        icon_meta = cr.fetchall()

                        if icon_meta:
                            cr.execute(f'DROP TABLE IF EXISTS pg_temp.icon_attach_tmp')
                            cr.execute(f"""
                                CREATE TEMP TABLE pg_temp.icon_attach_tmp AS
                                SELECT {icon_col_sql}
                                FROM public.ir_attachment WHERE false
                            """)
                            try:
                                icons_added = 0
                                for att_id, store_fname, db_datas in icon_meta:
                                    if store_fname:
                                        full_path = os.path.join(filestore_root, store_fname)
                                        try:
                                            with open(full_path, 'rb') as fh:
                                                raw_icon = fh.read()
                                        except OSError:
                                            _logger.warning(
                                                'dmc_backup: menu icon filestore file '
                                                'not found, skipping: %s', full_path)
                                            continue
                                    elif db_datas:
                                        raw_icon = bytes(db_datas)
                                    else:
                                        continue

                                    # Copy full row then inline the bytes
                                    cr.execute(f"""
                                        INSERT INTO pg_temp.icon_attach_tmp ({icon_col_sql})
                                        SELECT {icon_col_sql}
                                        FROM public.ir_attachment WHERE id = %s
                                    """, (att_id,))
                                    cr.execute("""
                                        UPDATE pg_temp.icon_attach_tmp
                                        SET db_datas = %s, store_fname = NULL
                                        WHERE id = %s
                                    """, (raw_icon, att_id))
                                    icons_added += 1

                                if icons_added:
                                    w('-- Menu app-icon attachments '
                                      '(inline bytes, no filestore needed)\n')
                                    w('ALTER TABLE "public"."ir_attachment"'
                                      ' DISABLE TRIGGER ALL;\n')
                                    w("DELETE FROM ONLY \"public\".\"ir_attachment\""
                                      " WHERE res_model = 'ir.ui.menu'"
                                      " AND res_field = 'web_icon_data';\n")
                                    w(f'COPY "public"."ir_attachment"'
                                      f' ({icon_col_sql}) FROM STDIN;\n')
                                    raw.copy_expert(
                                        f'COPY (SELECT {icon_col_sql}'
                                        f' FROM pg_temp.icon_attach_tmp ORDER BY id)'
                                        f' TO STDOUT',
                                        f,
                                    )
                                    w('\\.\n\n')
                                    w('ALTER TABLE "public"."ir_attachment"'
                                      ' ENABLE TRIGGER ALL;\n\n')
                            finally:
                                cr.execute('DROP TABLE IF EXISTS pg_temp.icon_attach_tmp')
                    continue
                col_list = ', '.join(f'"{c}"' for c in col_names)

                # GENERATED ALWAYS AS IDENTITY columns reject explicit values in
                # COPY (PostgreSQL raises "cannot insert a non-DEFAULT value into
                # column ... with GENERATED ALWAYS").  Temporarily relax to
                # GENERATED BY DEFAULT so COPY can supply the production IDs,
                # then restore the stricter constraint afterwards.
                cr.execute("""
                    SELECT attname FROM pg_attribute
                    WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped
                    AND attidentity = 'a'
                    ORDER BY attnum
                """, (tbl_oid,))
                always_identity_cols = [row[0] for row in cr.fetchall()]
                for col in always_identity_cols:
                    w(f'DO $$ BEGIN ALTER TABLE "{nspname}"."{tblname}"'
                      f' ALTER COLUMN "{col}" SET GENERATED BY DEFAULT;'
                      f' EXCEPTION WHEN feature_not_supported THEN NULL; END $$;\n')

                w(f'ALTER TABLE "{nspname}"."{tblname}" DISABLE TRIGGER ALL;\n')
                w(f'DELETE FROM ONLY "{nspname}"."{tblname}";\n')
                w(f'COPY "{nspname}"."{tblname}" ({col_list}) FROM STDIN;\n')
                raw.copy_expert(
                    f'COPY "{nspname}"."{tblname}" ({col_list}) TO STDOUT',
                    f,
                )
                w('\\.\n\n')
                w(f'ALTER TABLE "{nspname}"."{tblname}" ENABLE TRIGGER ALL;\n')

                for col in always_identity_cols:
                    w(f'DO $$ BEGIN ALTER TABLE "{nspname}"."{tblname}"'
                      f' ALTER COLUMN "{col}" SET GENERATED ALWAYS;'
                      f' EXCEPTION WHEN feature_not_supported THEN NULL; END $$;\n')
                w('\n')

            # ── Primary key constraints ────────────────────────────────────────
            #
            # Each ADD CONSTRAINT is wrapped in a DO block so that psql never
            # stops with ON_ERROR_STOP=1 when the constraint already exists
            # (Odoo SH may restore into an existing schema rather than a fresh
            # drop+create) or when FK validation finds stale rows in migration-
            # artifact tables like _ir_property.  EXCEPTION WHEN others catches
            # both duplicate_object (42710) and foreign_key_violation (23503).
            cr.execute("""
                SELECT c.conname,
                       '"' || n.nspname || '"."' || t.relname || '"',
                       pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.contype = 'p' AND n.nspname = 'public'
                ORDER BY t.relname, c.conname
            """)
            for conname, tblref, condef in cr.fetchall():
                w(f'DO $$ BEGIN ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};'
                  f' EXCEPTION WHEN others THEN NULL; END $$;\n')
            w('\n')

            # ── Unique constraints ─────────────────────────────────────────────
            cr.execute("""
                SELECT c.conname,
                       '"' || n.nspname || '"."' || t.relname || '"',
                       pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.contype = 'u' AND n.nspname = 'public'
                ORDER BY t.relname, c.conname
            """)
            for conname, tblref, condef in cr.fetchall():
                w(f'DO $$ BEGIN ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};'
                  f' EXCEPTION WHEN others THEN NULL; END $$;\n')
            w('\n')

            # ── Foreign key constraints ────────────────────────────────────────
            cr.execute("""
                SELECT c.conname,
                       '"' || n.nspname || '"."' || t.relname || '"',
                       pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.contype = 'f' AND n.nspname = 'public'
                ORDER BY t.relname, c.conname
            """)
            for conname, tblref, condef in cr.fetchall():
                w(f'DO $$ BEGIN ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};'
                  f' EXCEPTION WHEN others THEN NULL; END $$;\n')
            w('\n')

            # ── Check constraints ──────────────────────────────────────────────
            cr.execute("""
                SELECT c.conname,
                       '"' || n.nspname || '"."' || t.relname || '"',
                       pg_get_constraintdef(c.oid)
                FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                WHERE c.contype = 'c' AND n.nspname = 'public'
                ORDER BY t.relname, c.conname
            """)
            for conname, tblref, condef in cr.fetchall():
                w(f'DO $$ BEGIN ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};'
                  f' EXCEPTION WHEN others THEN NULL; END $$;\n')
            w('\n')

            # ── Indexes (exclude constraint-backing indexes) ───────────────────
            #
            # IF NOT EXISTS makes the statement idempotent: if the index already
            # exists (existing-schema restore) the statement is a no-op instead
            # of failing with "relation already exists" and stopping psql.
            # pg_indexes.indexdef does NOT include IF NOT EXISTS, so we inject it.
            cr.execute("""
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                AND indexname NOT IN (
                    SELECT conname FROM pg_constraint
                    WHERE conrelid IN (
                        SELECT oid FROM pg_class
                        WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
                    )
                )
                ORDER BY tablename, indexname
            """)
            for (indexdef,) in cr.fetchall():
                if indexdef.startswith('CREATE UNIQUE INDEX '):
                    safe = 'CREATE UNIQUE INDEX IF NOT EXISTS ' + indexdef[len('CREATE UNIQUE INDEX '):]
                elif indexdef.startswith('CREATE INDEX '):
                    safe = 'CREATE INDEX IF NOT EXISTS ' + indexdef[len('CREATE INDEX '):]
                else:
                    safe = indexdef
                w(f'{safe};\n')
            w('\n')

            # ── Views ─────────────────────────────────────────────────────────
            cr.execute("""
                SELECT c.relname, pg_get_viewdef(c.oid, true)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relkind = 'v'
                AND n.nspname = 'public'
                AND c.oid NOT IN (SELECT objid FROM pg_depend WHERE deptype = 'e')
                ORDER BY c.relname
            """)
            for viewname, viewdef in cr.fetchall():
                if viewdef:
                    w(f'CREATE OR REPLACE VIEW "public"."{viewname}" AS\n'
                      f'    {viewdef.strip()};\n\n')

            # ── Advance sequences to their current last_value ─────────────────
            for _oid, nspname, seqname, _s, _i, _mn, _mx, _c in sequences:
                cr.execute(
                    f'SELECT last_value, is_called FROM "{nspname}"."{seqname}"'
                )
                row = cr.fetchone()
                if row:
                    last_val, is_called = row
                    seq_ref = '"' + nspname + '"."' + seqname + '"'
                    w(
                        f"SELECT pg_catalog.setval('{seq_ref}', {last_val},"
                        f" {'true' if is_called else 'false'});\n"
                    )
            w('\n')

            # ── Advance IDENTITY column sequences ─────────────────────────────
            # IDENTITY sequences (GENERATED ALWAYS/BY DEFAULT AS IDENTITY) are
            # managed internally by PostgreSQL and are excluded from the
            # CREATE SEQUENCE block above.  After COPYing rows with explicit ID
            # values the internal sequence still starts at 1.  Odoo's first
            # INSERT after restore would try id=1 → primary key conflict.
            # pg_get_serial_sequence() works for both SERIAL and IDENTITY
            # columns, so we use it here to advance IDENTITY sequences to the
            # actual max value present in each table.
            cr.execute("""
                SELECT n.nspname, c.relname, a.attname
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                AND a.attidentity IN ('a', 'd')
                AND a.attnum > 0
                AND NOT a.attisdropped
                ORDER BY c.relname, a.attname
            """)
            for _id_nspname, id_tblname, id_colname in cr.fetchall():
                tblref  = f'"public"."{id_tblname}"'
                colref  = f'"{id_colname}"'
                w(
                    f"SELECT setval(pg_get_serial_sequence('{tblref}', '{id_colname}'),"
                    f" COALESCE((SELECT max({colref}) FROM {tblref}), 1), true);\n"
                )
            w('\n')

        # Restore search_path so Odoo ORM queries on this cursor (e.g. writing
        # the success log in run_backup) work normally.  SET LOCAL reverts at
        # transaction end, but we return to the same transaction immediately.
        cr.execute("SET LOCAL search_path TO DEFAULT")

    def _write_neutralization(self, f):
        # search_path was set to '' by the dump header; restore public so that
        # unqualified table names (ir_cron, ir_mail_server, etc.) resolve correctly.
        f.write(b'\nBEGIN;\nSET LOCAL search_path TO public, pg_catalog;\n\n-- Neutralization\n\n')

        # Clear per-user home actions so the home screen does not try to open an
        # action whose ID no longer exists in the restored staging database.
        # Odoo stores the user's last-opened action in res_users.action_id; after
        # restore the original ID (e.g. 443) may be gone or point to a different
        # record, causing "The action 'N' does not exist" on every page load.
        # Clearing the field makes Odoo fall back to the default app-list home screen.
        f.write(
            b"DO $$\n"
            b"BEGIN\n"
            b"    UPDATE res_users SET action_id = NULL;\n"
            b"EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;\n"
            b"END $$;\n\n"
        )

        # Deactivate all crons, then re-enable safe system ones
        f.write(b"UPDATE ir_cron SET active = 'f';\n")
        f.write(
            b"UPDATE ir_cron SET active = 't'\n"
            b"    WHERE id IN (SELECT res_id FROM ir_model_data\n"
            b"                  WHERE name = 'autovacuum_job' AND module = 'base');\n\n"
        )

        # Remove sensitive config parameters
        f.write(
            b"DELETE FROM ir_config_parameter\n"
            b"    WHERE key IN (\n"
            b"        'web.base.url.freeze', 'report.url', 'database.enterprise_code',\n"
            b"        'iap_extract_endpoint', 'odoo_ocn.project_id', 'ocn.uuid',\n"
            b"        'product_barcodelookup.api_key', 'web_map.token_map_box'\n"
            b"    );\n\n"
        )

        # Reset DB UUID so staging has a distinct identity
        f.write(
            b"UPDATE ir_config_parameter\n"
            b"    SET value = gen_random_uuid()::text\n"
            b"    WHERE key = 'database.uuid';\n\n"
        )

        # Deactivate mail servers and clear server from templates
        f.write(
            b"DO $$\n"
            b"BEGIN\n"
            b"    UPDATE ir_mail_server SET active = 'f';\n"
            b"    IF EXISTS (\n"
            b"        SELECT 1 FROM ir_module_module\n"
            b"        WHERE name = 'mail'\n"
            b"        AND state IN ('installed', 'to upgrade', 'to remove')\n"
            b"    ) THEN\n"
            b"        UPDATE mail_template SET mail_server_id = NULL;\n"
            b"    END IF;\n"
            b"EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;\n"
            b"END $$;\n\n"
        )

        # Disable website CDN and block crawlers
        f.write(
            b"DO $$\n"
            b"BEGIN\n"
            b"    UPDATE website SET cdn_activated = false;\n"
            b"    UPDATE website SET robots_txt = E'User-agent: *\\nDisallow: /';\n"
            b"EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;\n"
            b"END $$;\n\n"
        )

        # Disable bank sync links
        f.write(
            b"DO $$\n"
            b"BEGIN\n"
            b"    UPDATE account_online_link SET client_id = 'duplicate';\n"
            b"EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;\n"
            b"END $$;\n\n"
        )

        # Re-enable module update notification cron
        f.write(
            b"UPDATE ir_cron SET active = 't'\n"
            b"    WHERE id IN (SELECT res_id FROM ir_model_data\n"
            b"                  WHERE name = 'ir_cron_module_update_notification'\n"
            b"                    AND module = 'mail');\n"
        )

        f.write(b'\nCOMMIT;\n')

    def _write_asset_cleanup(self, f):
        """Strip cached compiled web-asset bundle attachments from the dump.

        These ir_attachment rows are content-addressed by filestore hash, but the
        backup zip does not ship the real filestore (see include_filestore) — after
        restore the row survives while the physical file does not, so the first
        page load crashes with FileNotFoundError instead of Odoo just regenerating
        the bundle. Deleting the rows here makes Odoo recompute them on next
        request. The predicate matches only cached bundle attachments (never real
        user uploads): res_model/name is the pattern used by Odoo's own built-in
        "Regenerate Assets Bundles" debug action; url is the newer /web/assets path.
        """
        f.write(b'\nBEGIN;\nSET LOCAL search_path TO public, pg_catalog;\n\n-- Stale asset bundle cleanup\n\n')
        f.write(
            b"DO $$\n"
            b"BEGIN\n"
            b"    DELETE FROM ir_attachment\n"
            b"        WHERE (res_model = 'ir.ui.view' AND name LIKE 'assets_%')\n"
            b"           OR url LIKE '/web/assets/%';\n"
            b"EXCEPTION WHEN undefined_table OR undefined_column THEN NULL;\n"
            b"END $$;\n"
        )
        f.write(b'\nCOMMIT;\n')

    # ── Azure Blob Storage push ───────────────────────────────────────────────

    def _push_to_azure(self, zip_path, file_size, file_name, config):
        import requests
        account   = (config.azure_account or '').strip()
        container = (config.azure_container or '').strip()
        sas_token = (config.azure_sas_token or '').strip()
        if not all([account, container, sas_token]):
            raise UserError('Default backup destination is missing Azure credentials.')
        blob_url   = f'https://{account}.blob.core.windows.net/{container}/{file_name}?{sas_token}'
        public_url = f'https://{account}.blob.core.windows.net/{container}/{file_name}'
        with open(zip_path, 'rb') as f:
            resp = requests.put(
                blob_url,
                data=f,
                headers={
                    'x-ms-blob-type': 'BlockBlob',
                    'Content-Type':   'application/zip',
                    'Content-Length': str(file_size),
                },
                timeout=300,
            )
        resp.raise_for_status()
        return public_url

    # ── OneDrive push ─────────────────────────────────────────────────────────

    def _push_to_onedrive(self, zip_path, file_size, file_name, config):
        import requests
        token    = config._get_onedrive_token()
        drive_id = config._resolve_onedrive_drive(token)
        config._ensure_onedrive_folder(token, drive_id, config.onedrive_folder_path)

        folder    = (config.onedrive_folder_path or '').strip('/')
        item_path = f'{folder}/{file_name}' if folder else file_name

        session_url = (
            f'https://graph.microsoft.com/v1.0'
            f'/drives/{drive_id}/root:/{item_path}:/createUploadSession'
        )
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        session_resp = requests.post(session_url, headers=headers, json={
            'item': {'@microsoft.graph.conflictBehavior': 'rename'},
        }, timeout=30)
        session_resp.raise_for_status()
        upload_url = session_resp.json()['uploadUrl']

        chunk_size  = 10 * 1024 * 1024
        uploaded    = 0
        web_url     = None
        actual_name = file_name  # updated on final chunk if OneDrive renamed the file
        with open(zip_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                end  = uploaded + len(chunk) - 1
                resp = requests.put(
                    upload_url,
                    data=chunk,
                    headers={
                        'Content-Length': str(len(chunk)),
                        'Content-Range':  f'bytes {uploaded}-{end}/{file_size}',
                    },
                    timeout=120,
                )
                if resp.status_code in (200, 201):
                    item_data   = resp.json()
                    web_url     = item_data.get('webUrl')
                    actual_name = item_data.get('name', file_name)  # capture post-rename name
                elif resp.status_code != 202:
                    resp.raise_for_status()
                uploaded += len(chunk)
        if web_url is None:
            raise UserError('OneDrive upload completed but no webUrl was returned.')
        return web_url, actual_name

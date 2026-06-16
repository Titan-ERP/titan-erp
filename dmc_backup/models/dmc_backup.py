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

            if log_id:
                log = self.env['dmc.backup.log'].sudo().browse(log_id)
                log.write({
                    'name':         file_name,
                    'size_mb':      round(file_size / 1024 / 1024, 2),
                    'state':        'success',
                    'storage_url':  storage_url,
                    'storage_type': config.storage_type,
                })
            else:
                log = self.env['dmc.backup.log'].sudo().create({
                    'name':          file_name,
                    'db_name':       db_name,
                    'odoo_version':  odoo.release.version,
                    'size_mb':       round(file_size / 1024 / 1024, 2),
                    'state':         'success',
                    'storage_url':   storage_url,
                    'storage_type':  config.storage_type,
                })

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
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
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
        import shutil

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

    def _dump_db(self, db_name, zip_path, config=None):
        import shutil
        from odoo.tools import osutil

        neutralize        = config.neutralize        if config else False
        include_filestore = config.include_filestore if config else True

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
                self._write_python_sql_dump(dump_path)

            if neutralize:
                with open(dump_path, 'ab') as nf:
                    self._write_neutralization(nf)

            with open(os.path.join(tmp_dir, 'manifest.json'), 'wb') as mf:
                mf.write(manifest_bytes)

            if include_filestore:
                filestore_path = odoo.tools.config.filestore(db_name)
                if os.path.exists(filestore_path):
                    shutil.copytree(filestore_path, os.path.join(tmp_dir, 'filestore'))

            with open(zip_path, 'wb') as zf:
                osutil.zip_dir(
                    tmp_dir, zf, include_dir=False,
                    fnct_sort=lambda fname: fname != 'dump.sql',
                )

    def _run_pg_dump(self, db_name, dump_path):
        """Run pg_dump subprocess to produce the SQL dump.

        Matches odoo.service.db.dump_db exactly: --no-owner only, connection
        parameters via exec_pg_environ() (PGHOST/PGPORT/PGUSER/PGPASSWORD).
        """
        import subprocess
        from odoo.tools.misc import exec_pg_environ

        cmd = [self._find_pg_dump(), '--no-owner', '--file=' + dump_path, db_name]
        result = subprocess.run(
            cmd, env=exec_pg_environ(), check=False, timeout=3600,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            err = result.stderr.decode('utf-8', errors='replace').strip()
            raise Exception(f'pg_dump failed (exit {result.returncode}): {err}')

    def _write_python_sql_dump(self, dump_path):
        """Write a psql-compatible SQL dump using psycopg2 catalog queries and COPY TO STDOUT.

        Used when pg_dump is unavailable (e.g. Odoo SH staging where the app user
        cannot access pg_settings, which pg_dump requires at startup).  Produces a
        dump.sql that is structurally identical to pg_dump plain-format output and
        is compatible with Odoo SH's restore_db / import utility.
        """
        cr  = self.env.cr
        raw = cr._obj  # psycopg2 cursor — needed for copy_expert

        # Extend session timeouts so long dumps don't get killed.
        cr.execute("SET statement_timeout = 0")
        cr.execute("SET lock_timeout = 0")
        # Empty search_path forces all catalog functions (pg_get_constraintdef,
        # pg_get_indexdef, pg_get_expr, …) to emit fully schema-qualified names.
        # pg_catalog is always implicitly visible regardless of search_path.
        cr.execute("SET LOCAL search_path = ''")

        with open(dump_path, 'wb') as f:
            def w(text):
                f.write(text.encode('utf-8') if isinstance(text, str) else text)

            # ── Header ────────────────────────────────────────────────────────
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

            # ── Non-default schemas (e.g. unaccent_schema) ───────────────────
            cr.execute("""
                SELECT n.nspname
                FROM pg_namespace n
                WHERE n.nspname NOT IN ('public', 'information_schema',
                                        'pg_catalog', 'pg_toast')
                AND n.nspname NOT LIKE 'pg_%'
                AND n.oid NOT IN (SELECT extnamespace FROM pg_extension)
                ORDER BY n.nspname
            """)
            for (nspname,) in cr.fetchall():
                w(f'CREATE SCHEMA IF NOT EXISTS "{nspname}";\n')
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
                           a.attidentity
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
                for attname, col_type, default_val, attnotnull, attidentity in cols:
                    col_def = f'    "{attname}" {col_type}'
                    if attidentity == 'a':
                        col_def += ' GENERATED ALWAYS AS IDENTITY'
                    elif attidentity == 'd':
                        col_def += ' GENERATED BY DEFAULT AS IDENTITY'
                    elif default_val:
                        col_def += f' DEFAULT {default_val}'
                    if attnotnull and not attidentity:
                        col_def += ' NOT NULL'
                    col_defs.append(col_def)

                w(f'CREATE TABLE IF NOT EXISTS "{nspname}"."{tblname}" (\n')
                w(',\n'.join(col_defs))
                w('\n);\n\n')

            # ── Data — COPY TO STDOUT (works under Odoo SH app-user permissions) ─
            for tbl_oid, nspname, tblname in tables:
                cr.execute("""
                    SELECT attname FROM pg_attribute
                    WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped
                    ORDER BY attnum
                """, (tbl_oid,))
                col_names = [row[0] for row in cr.fetchall()]
                if not col_names:
                    continue
                col_list = ', '.join(f'"{c}"' for c in col_names)
                w(f'COPY "{nspname}"."{tblname}" ({col_list}) FROM STDIN;\n')
                raw.copy_expert(
                    f'COPY "{nspname}"."{tblname}" ({col_list}) TO STDOUT',
                    f,
                )
                w('\n\\.\n\n')

            # ── Primary key constraints ────────────────────────────────────────
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
                w(f'ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};\n')
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
                w(f'ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};\n')
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
                w(f'ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};\n')
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
                w(f'ALTER TABLE {tblref} ADD CONSTRAINT "{conname}" {condef};\n')
            w('\n')

            # ── Indexes (exclude constraint-backing indexes) ───────────────────
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
                w(f'{indexdef};\n')
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
        # Restore search_path so Odoo ORM queries on this cursor (e.g. writing
        # the success log in run_backup) work normally.  SET LOCAL reverts at
        # transaction end, but we return to the same transaction immediately.
        cr.execute("SET LOCAL search_path TO DEFAULT")

    def _write_neutralization(self, f):
        f.write(b'\nBEGIN;\n\n-- Neutralization\n\n')

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

# -*- coding: utf-8 -*-
import os
import json
import logging
import tempfile
import zipfile
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

        if config.storage_type == 'onedrive':
            try:
                token    = config._get_onedrive_token()
                drive_id = config._resolve_onedrive_drive(token)
            except Exception as exc:
                _logger.warning('OneDrive auth failed during delete: %s', exc)
                return
            headers = {'Authorization': f'Bearer {token}'}
            folder  = (config.onedrive_folder_path or '').strip('/')
            for record in records:
                file_name = (record.name or '').strip()
                item_path = f'{folder}/{file_name}' if folder else file_name
                url = (
                    f'https://graph.microsoft.com/v1.0'
                    f'/drives/{drive_id}/root:/{item_path}'
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
                return
            for record in records:
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
        if '.blob.core.windows.net' in self.storage_url:
            config = self.env['dmc.backup.config'].sudo().search(
                [('is_default', '=', True)], limit=1
            )
            sas_token = (config.azure_sas_token or '').strip() if config else ''
            if not sas_token:
                raise UserError('No Azure SAS token found on the default destination.')
            url = f'{self.storage_url}?{sas_token}'
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
            registry = odoo.registry(db_name)
            with registry.cursor() as start_cr:
                start_env = self.env(cr=start_cr)
                running_log = start_env['dmc.backup.log'].sudo().create({
                    'name':         file_name,
                    'db_name':      db_name,
                    'odoo_version': odoo.release.version,
                    'state':        'running',
                })
                log_id = running_log.id
                start_cr.commit()
        except Exception as start_err:
            _logger.warning('Could not create running log: %s', start_err)

        _logger.info('Starting DB backup: %s', file_name)
        try:
            self._dump_db(db_name, zip_path)
            file_size = os.path.getsize(zip_path)

            if config.storage_type == 'onedrive':
                storage_url = self._push_to_onedrive(zip_path, file_size, file_name, config)
                _logger.info('OneDrive push complete: %s', storage_url)
            else:
                storage_url = self._push_to_azure(zip_path, file_size, file_name, config)
                _logger.info('Azure push complete: %s', storage_url)

            if log_id:
                log = self.env['dmc.backup.log'].sudo().browse(log_id)
                log.write({
                    'size_mb':     round(file_size / 1024 / 1024, 2),
                    'state':       'success',
                    'storage_url': storage_url,
                })
            else:
                log = self.env['dmc.backup.log'].sudo().create({
                    'name':          file_name,
                    'db_name':       db_name,
                    'odoo_version':  odoo.release.version,
                    'size_mb':       round(file_size / 1024 / 1024, 2),
                    'state':         'success',
                    'storage_url':   storage_url,
                })

            _logger.info('Backup complete: %s (%.2f MB)', file_name, round(file_size / 1024 / 1024, 2))

        except Exception as e:
            # Update or create failure log on a separate cursor — survives the cron rollback
            try:
                registry = odoo.registry(db_name)
                with registry.cursor() as new_cr:
                    new_env = self.env(cr=new_cr)
                    if log_id:
                        new_env['dmc.backup.log'].sudo().browse(log_id).write({
                            'state':         'failed',
                            'error_message': str(e),
                        })
                    else:
                        new_env['dmc.backup.log'].sudo().create({
                            'name':          file_name,
                            'db_name':       db_name,
                            'odoo_version':  odoo.release.version,
                            'state':         'failed',
                            'error_message': str(e),
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

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        old = self.env['dmc.backup.log'].sudo().search([('backup_date', '<', cutoff)])
        old.mapped('attachment_id').unlink()
        old.unlink()
        _logger.info('Cleanup complete: %d old backup(s) removed', len(old))

    # ── Backup generation ────────────────────────────────────────────────────

    def _dump_db(self, db_name, zip_path):
        cr = self.env.cr

        cr.execute(
            "SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'"
        )
        modules = dict(cr.fetchall())
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version // 100, 100)
        manifest = json.dumps({
            'odoo_dump': '1',
            'db_name': db_name,
            'version': odoo.release.version,
            'version_info': odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version': pg_version,
            'modules': modules,
        }, indent=4).encode()

        # Stream manifest + SQL directly into the zip — no intermediate dump.sql on disk.
        # Peak disk usage is the growing compressed zip only; SQL text compresses ~90%.
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('manifest.json', manifest)
            with zf.open('dump.sql', 'w', force_zip64=True) as sql_stream:
                self._generate_sql_dump(sql_stream)

    def _generate_sql_dump(self, stream):
        cr = self.env.cr
        f  = stream

        f.write(b"SET client_encoding = 'UTF8';\n")
        f.write(b"SET standard_conforming_strings = on;\n\n")
        f.write(b"BEGIN;\n\n")

        # ── Sequences ────────────────────────────────────────
        cr.execute("""
            SELECT sequencename, start_value, increment_by, min_value, max_value,
                   cycle,
                   COALESCE(last_value, start_value) AS cur_val,
                   last_value IS NOT NULL           AS is_called
            FROM   pg_sequences
            WHERE  schemaname = 'public'
            ORDER  BY sequencename
        """)
        sequences = cr.fetchall()
        for seq, start, incr, mn, mx, cycle, cur_val, is_called in sequences:
            f.write((
                f'CREATE SEQUENCE IF NOT EXISTS "{seq}"'
                f' START WITH {start} INCREMENT BY {incr}'
                f' MINVALUE {mn} MAXVALUE {mx}'
                + (' CYCLE' if cycle else ' NO CYCLE') + ';\n'
            ).encode())
        f.write(b'\n')

        # ── Tables ────────────────────────────────────────────
        cr.execute("""
            SELECT c.relname
            FROM   pg_class c
            JOIN   pg_namespace ns ON c.relnamespace = ns.oid
            WHERE  ns.nspname = 'public' AND c.relkind = 'r'
            ORDER  BY c.relname
        """)
        tables = [row[0] for row in cr.fetchall()]

        for table in tables:
            cr.execute("""
                SELECT a.attname,
                       pg_catalog.format_type(a.atttypid, a.atttypmod),
                       pg_catalog.pg_get_expr(ad.adbin, ad.adrelid),
                       a.attnotnull
                FROM   pg_catalog.pg_attribute a
                LEFT JOIN pg_catalog.pg_attrdef ad
                       ON ad.adrelid = a.attrelid AND ad.adnum = a.attnum
                WHERE  a.attrelid = (
                           SELECT oid FROM pg_class
                           WHERE  relname = %s
                           AND    relnamespace = (
                                      SELECT oid FROM pg_namespace WHERE nspname = 'public'
                                  )
                       )
                AND    a.attnum > 0 AND NOT a.attisdropped
                ORDER  BY a.attnum
            """, (table,))
            col_defs = []
            for col, dtype, default, notnull in cr.fetchall():
                defn = f'    "{col}" {dtype}'
                if default:
                    defn += f' DEFAULT {default}'
                if notnull:
                    defn += ' NOT NULL'
                col_defs.append(defn)

            f.write(f'CREATE TABLE IF NOT EXISTS "{table}" (\n'.encode())
            f.write(',\n'.join(col_defs).encode())
            f.write(b'\n);\n')

        f.write(b'\n')

        # ── Truncate all tables before loading data ───────────
        if tables:
            table_list = ', '.join(f'"{t}"' for t in tables)
            f.write(f'TRUNCATE {table_list} CASCADE;\n\n'.encode())

        # ── Data ──────────────────────────────────────────────
        for table in tables:
            f.write(f'COPY "{table}" FROM STDIN;\n'.encode())
            try:
                cr._obj.copy_expert(f'COPY "{table}" TO STDOUT', f)
            except Exception as exc:
                raise Exception(f'COPY failed for table "{table}": {exc}') from exc
            f.write(b'\\.\n\n')

        # ── Primary keys + unique constraints ─────────────────
        cr.execute("""
            SELECT c.conname, c.contype, t.relname,
                   (SELECT array_agg(a.attname
                            ORDER BY array_position(c.conkey, a.attnum))
                    FROM   pg_attribute a
                    WHERE  a.attrelid = c.conrelid
                    AND    a.attnum   = ANY(c.conkey)) AS cols
            FROM   pg_constraint c
            JOIN   pg_class t      ON t.oid = c.conrelid
            JOIN   pg_namespace ns ON ns.oid = c.connamespace
            WHERE  ns.nspname = 'public' AND c.contype IN ('p', 'u')
            ORDER  BY t.relname, c.contype DESC
        """)
        for con_name, con_type, table, cols in cr.fetchall():
            col_str = ', '.join(f'"{c}"' for c in cols)
            kw      = 'PRIMARY KEY' if con_type == 'p' else 'UNIQUE'
            f.write(f'ALTER TABLE "{table}" ADD CONSTRAINT "{con_name}" {kw} ({col_str});\n'.encode())
        f.write(b'\n')

        # ── Indexes ───────────────────────────────────────────
        cr.execute("""
            SELECT indexname, indexdef
            FROM   pg_indexes
            WHERE  schemaname = 'public'
            AND    indexname NOT IN (
                       SELECT conname FROM pg_constraint
                       WHERE  connamespace = (
                                  SELECT oid FROM pg_namespace WHERE nspname = 'public'
                              )
                   )
            ORDER  BY indexname
        """)
        for _, idx_def in cr.fetchall():
            f.write(f'{idx_def};\n'.encode())
        f.write(b'\n')

        # ── Foreign keys ──────────────────────────────────────
        fk_action = {
            'a': 'NO ACTION', 'r': 'RESTRICT', 'c': 'CASCADE',
            'n': 'SET NULL',  'd': 'SET DEFAULT',
        }
        cr.execute("""
            SELECT c.conname, t.relname, ft.relname,
                   (SELECT array_agg(a.attname
                            ORDER BY array_position(c.conkey, a.attnum))
                    FROM   pg_attribute a
                    WHERE  a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)),
                   (SELECT array_agg(a.attname
                            ORDER BY array_position(c.confkey, a.attnum))
                    FROM   pg_attribute a
                    WHERE  a.attrelid = c.confrelid AND a.attnum = ANY(c.confkey)),
                   c.confupdtype, c.confdeltype
            FROM   pg_constraint c
            JOIN   pg_class t      ON t.oid = c.conrelid
            JOIN   pg_class ft     ON ft.oid = c.confrelid
            JOIN   pg_namespace ns ON ns.oid = c.connamespace
            WHERE  ns.nspname = 'public' AND c.contype = 'f'
            ORDER  BY t.relname, c.conname
        """)
        for con_name, table, ftable, cols, fcols, upd, dlt in cr.fetchall():
            col_str  = ', '.join(f'"{c}"' for c in cols)
            fcol_str = ', '.join(f'"{c}"' for c in fcols)
            f.write((
                f'ALTER TABLE "{table}" ADD CONSTRAINT "{con_name}"'
                f' FOREIGN KEY ({col_str}) REFERENCES "{ftable}" ({fcol_str})'
                f' ON UPDATE {fk_action.get(upd, "NO ACTION")}'
                f' ON DELETE {fk_action.get(dlt, "NO ACTION")};\n'
            ).encode())
        f.write(b'\n')

        # ── Sequence current values ───────────────────────────
        for seq, _, _, _, _, _, cur_val, is_called in sequences:
            f.write(f"SELECT setval('{seq}', {cur_val}, {str(is_called).lower()});\n".encode())

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

        chunk_size = 10 * 1024 * 1024
        uploaded   = 0
        web_url    = None
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
                    web_url = resp.json().get('webUrl')
                elif resp.status_code != 202:
                    resp.raise_for_status()
                uploaded += len(chunk)
        if web_url is None:
            raise UserError('OneDrive upload completed but no webUrl was returned.')
        return web_url

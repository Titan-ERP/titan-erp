# -*- coding: utf-8 -*-
import os
import json
import logging
import tempfile
import time
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
            registry = odoo.registry(db_name)
            with registry.cursor() as start_cr:
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
                registry = odoo.registry(db_name)
                with registry.cursor() as new_cr:
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
        """Return the pg_dump binary that matches the connected PostgreSQL server version."""
        from odoo.service.db import find_pg_tool

        server_version = self.env.cr._obj.connection.server_version
        pg_major = server_version // 10000  # e.g. 160001 → 16
        for candidate in (
            f'/usr/lib/postgresql/{pg_major}/bin/pg_dump',  # Debian/Ubuntu
            f'/usr/pgsql-{pg_major}/bin/pg_dump',           # RHEL/CentOS
        ):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return find_pg_tool('pg_dump')

    def _dump_db(self, db_name, zip_path, config=None):
        import subprocess
        from odoo.service.db import exec_pg_environ

        neutralize        = config.neutralize        if config else False
        include_filestore = config.include_filestore if config else True

        cr = self.env.cr
        cr.execute(
            "SELECT name, latest_version FROM ir_module_module WHERE state = 'installed'"
        )
        modules = dict(cr.fetchall())
        pg_version = "%d.%d" % divmod(cr._obj.connection.server_version // 100, 100)
        manifest = json.dumps({
            'odoo_dump': '1',
            'db_name':       db_name,
            'version':       odoo.release.version,
            'version_info':  odoo.release.version_info,
            'major_version': odoo.release.major_version,
            'pg_version':    pg_version,
            'modules':       modules,
        }, indent=4).encode()

        cfg = odoo.tools.config
        with tempfile.TemporaryDirectory() as tmp_dir:
            dump_path = os.path.join(tmp_dir, 'dump.sql')

            cmd = [self._find_pg_dump(), '--no-owner', '--no-acl', '--format=p',
                   '--file=' + dump_path]
            if cfg['db_host']:
                cmd += ['--host=' + cfg['db_host']]
            if cfg['db_port']:
                cmd += ['--port=' + str(cfg['db_port'])]
            if cfg['db_user']:
                cmd += ['--username=' + cfg['db_user']]
            cmd.append(db_name)

            result = subprocess.run(
                cmd, env=exec_pg_environ(), check=False, timeout=3600,
                stderr=subprocess.PIPE,
            )
            if result.returncode != 0:
                err = result.stderr.decode('utf-8', errors='replace').strip()
                raise Exception(
                    f'pg_dump failed (exit {result.returncode}): {err}'
                )

            if neutralize:
                with open(dump_path, 'ab') as f:
                    self._write_neutralization(f)

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                zf.writestr('manifest.json', manifest)
                zf.write(dump_path, 'dump.sql')
                if include_filestore:
                    filestore_path = odoo.tools.config.filestore(db_name)
                    if os.path.exists(filestore_path):
                        for dirpath, _dirs, filenames in os.walk(filestore_path):
                            for fname in filenames:
                                abs_path = os.path.join(dirpath, fname)
                                arc_path = os.path.join(
                                    'filestore',
                                    os.path.relpath(abs_path, filestore_path),
                                )
                                zf.write(abs_path, arc_path)

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

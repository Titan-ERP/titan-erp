# -*- coding: utf-8 -*-
from odoo import api, models, fields
from odoo.exceptions import ValidationError, UserError


class DmcBackupConfig(models.Model):
    _name = 'dmc.backup.config'
    _description = 'DMC Backup Destination'
    _order = 'is_default desc, name'

    name         = fields.Char(required=True)
    storage_type = fields.Selection(
        [('azure', 'Azure Blob Storage'), ('onedrive', 'OneDrive')],
        string='Storage Type', default='azure', required=True,
    )

    # ── Azure Blob Storage ────────────────────────────────────────────────────
    azure_account   = fields.Char(string='Storage Account')
    azure_container = fields.Char(string='Container')
    azure_sas_token = fields.Char(string='SAS Token')

    # ── OneDrive ──────────────────────────────────────────────────────────────
    onedrive_client_id     = fields.Char(string='Client ID')
    onedrive_tenant_id     = fields.Char(string='Tenant ID')
    onedrive_client_secret = fields.Char(string='Client Secret')
    onedrive_drive_type    = fields.Selection(
        [('user', 'User OneDrive'), ('sharepoint', 'SharePoint')],
        string='Drive Type',
    )
    onedrive_drive_target  = fields.Char(string='User Email / SharePoint URL')
    onedrive_folder_path   = fields.Char(string='Folder Path')

    # ── Common ────────────────────────────────────────────────────────────────
    is_default     = fields.Boolean(string='Default', default=False, copy=False)
    retention_days = fields.Integer(string='Retention (days)', default=7)
    status_label   = fields.Char(compute='_compute_status_label')

    _CREDENTIAL_FIELDS = frozenset({
        'azure_account', 'azure_container', 'azure_sas_token',
        'onedrive_client_id', 'onedrive_tenant_id', 'onedrive_client_secret',
        'onedrive_drive_type', 'onedrive_drive_target',
    })

    _sql_constraints = [
        ('retention_days_positive', 'CHECK(retention_days >= 1)',
         'Retention period must be at least 1 day.'),
    ]

    def _check_required_by_storage_type(self):
        for rec in self:
            if rec.storage_type == 'azure':
                if not all([rec.azure_account, rec.azure_container, rec.azure_sas_token]):
                    raise ValidationError(
                        'Storage Account, Container, and SAS Token are required for Azure Blob Storage.'
                    )
            elif rec.storage_type == 'onedrive':
                if not all([
                    rec.onedrive_client_id, rec.onedrive_tenant_id,
                    rec.onedrive_client_secret, rec.onedrive_drive_type,
                    rec.onedrive_drive_target,
                ]):
                    raise ValidationError(
                        'Client ID, Tenant ID, Client Secret, Drive Type, and User Email / SharePoint URL '
                        'are all required for OneDrive.'
                    )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record, vals in zip(records, vals_list):
            # Only validate if the user has actually started filling in credentials
            if any(vals.get(f) for f in self._CREDENTIAL_FIELDS):
                record._check_required_by_storage_type()
        return records

    def write(self, vals):
        result = super().write(vals)
        if set(vals.keys()) & self._CREDENTIAL_FIELDS:
            self._check_required_by_storage_type()
        return result

    @api.depends('is_default')
    def _compute_status_label(self):
        for r in self:
            r.status_label = 'Default' if r.is_default else 'Inactive'

    def action_set_default(self):
        self.ensure_one()
        self._check_required_by_storage_type()
        self.sudo().search([('is_default', '=', True), ('id', '!=', self.id)]).write({'is_default': False})
        self.write({'is_default': True})

    # ── OneDrive helpers ──────────────────────────────────────────────────────

    def _get_onedrive_token(self):
        import requests
        self.ensure_one()
        url = (
            f'https://login.microsoftonline.com/'
            f'{(self.onedrive_tenant_id or "").strip()}/oauth2/v2.0/token'
        )
        resp = requests.post(url, data={
            'grant_type':    'client_credentials',
            'client_id':     (self.onedrive_client_id or '').strip(),
            'client_secret': (self.onedrive_client_secret or '').strip(),
            'scope':         'https://graph.microsoft.com/.default',
        }, timeout=30)
        if resp.status_code != 200:
            raise UserError(f'OneDrive authentication failed: {resp.text}')
        return resp.json()['access_token']

    def _resolve_onedrive_drive(self, token):
        import requests
        self.ensure_one()
        headers = {'Authorization': f'Bearer {token}'}
        if self.onedrive_drive_type == 'user':
            url = (
                f'https://graph.microsoft.com/v1.0'
                f'/users/{(self.onedrive_drive_target or "").strip()}/drive'
            )
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                err = resp.json().get('error', {})
                code = err.get('code', resp.status_code)
                msg  = err.get('message', resp.text)
                hint = ''
                if code == 'generalException':
                    hint = (
                        ' Common causes: (1) the App Registration is missing the '
                        'Files.ReadWrite.All application permission with admin consent, '
                        'or (2) the user\'s OneDrive has never been provisioned — '
                        'ask the user to visit their OneDrive URL once.'
                    )
                raise UserError(f'OneDrive user drive not found [{code}]: {msg}{hint}')
            return resp.json()['id']
        else:
            from urllib.parse import urlparse
            parsed   = urlparse((self.onedrive_drive_target or '').strip())
            site_ref = f'{parsed.netloc}:{parsed.path}'
            url = f'https://graph.microsoft.com/v1.0/sites/{site_ref}/drives'
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise UserError(f'SharePoint site not found: {resp.text}')
            drives = resp.json().get('value', [])
            if not drives:
                raise UserError('No document libraries found on the SharePoint site.')
            return drives[0]['id']

    def _ensure_onedrive_folder(self, token, drive_id, folder_path):
        import requests
        path = (folder_path or '').strip('/')
        if not path:
            return
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type':  'application/json',
        }
        current = ''
        for segment in filter(None, path.split('/')):
            if current:
                url = (
                    f'https://graph.microsoft.com/v1.0'
                    f'/drives/{drive_id}/root:/{current}:/children'
                )
            else:
                url = (
                    f'https://graph.microsoft.com/v1.0'
                    f'/drives/{drive_id}/root/children'
                )
            resp = requests.post(url, headers=headers, json={
                'name':   segment,
                'folder': {},
                '@microsoft.graph.conflictBehavior': 'fail',
            }, timeout=30)
            if resp.status_code not in (201, 409):
                raise UserError(
                    f'Could not create OneDrive folder "{segment}": {resp.text}'
                )
            current = f'{current}/{segment}' if current else segment

    # ── Actions ───────────────────────────────────────────────────────────────

    def _test_azure_connection(self):
        import requests
        self.ensure_one()
        account   = (self.azure_account or '').strip()
        container = (self.azure_container or '').strip()
        sas_token = (self.azure_sas_token or '').strip()
        if not all([account, container, sas_token]):
            raise UserError('Storage Account, Container, and SAS Token are required to test the connection.')
        url  = f'https://{account}.blob.core.windows.net/{container}?restype=container&{sas_token}'
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return
        if resp.status_code == 403:
            raise UserError('Azure connection failed: access denied. Check that the SAS token has read permissions on the container.')
        if resp.status_code == 404:
            raise UserError(f'Azure connection failed: container "{container}" not found in account "{account}".')
        raise UserError(f'Azure connection failed [{resp.status_code}]: {resp.text}')

    def action_test_connection(self):
        self.ensure_one()
        if self.storage_type == 'azure':
            self._test_azure_connection()
            message = 'Azure Blob Storage connection successful.'
        else:
            token    = self._get_onedrive_token()
            drive_id = self._resolve_onedrive_drive(token)
            self._ensure_onedrive_folder(token, drive_id, self.onedrive_folder_path)
            message = 'OneDrive connection successful.'
        return {
            'type': 'ir.actions.client',
            'tag':  'display_notification',
            'params': {
                'message': message,
                'type':    'success',
                'sticky':  False,
            },
        }

    def action_browse_onedrive_folders(self):
        self.ensure_one()
        token    = self._get_onedrive_token()
        drive_id = self._resolve_onedrive_drive(token)
        wizard   = self.env['dmc.backup.folder.wizard'].create({
            'config_id':   self.id,
            'drive_id':    drive_id,
            'token':       token,
            'parent_path': (self.onedrive_folder_path or '').strip('/'),
        })
        wizard._load_folders()
        return {
            'type':      'ir.actions.act_window',
            'name':      'Browse OneDrive Folders',
            'res_model': 'dmc.backup.folder.wizard',
            'res_id':    wizard.id,
            'view_mode': 'form',
            'target':    'new',
        }

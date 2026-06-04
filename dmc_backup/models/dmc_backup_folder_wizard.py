# -*- coding: utf-8 -*-
from odoo import models, fields
from odoo.exceptions import UserError


class DmcBackupFolderWizard(models.TransientModel):
    _name = 'dmc.backup.folder.wizard'
    _description = 'OneDrive Folder Browser'

    config_id   = fields.Many2one('dmc.backup.config', required=True)
    drive_id    = fields.Char(required=True, groups='base.group_system')
    token       = fields.Char(required=True, groups='base.group_system')
    parent_path = fields.Char(default='')
    folder_ids  = fields.One2many('dmc.backup.folder.item', 'wizard_id', string='Folders')

    def _load_folders(self):
        import requests
        self.ensure_one()
        path    = (self.parent_path or '').strip('/')
        headers = {'Authorization': f'Bearer {self.token}'}
        if path:
            url = (
                f'https://graph.microsoft.com/v1.0'
                f'/drives/{self.drive_id}/root:/{path}:/children'
                f'?$select=name,folder'
            )
        else:
            url = (
                f'https://graph.microsoft.com/v1.0'
                f'/drives/{self.drive_id}/root/children'
                f'?$select=name,folder'
            )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise UserError(f'Could not list OneDrive folders: {resp.text}')
        items = [i for i in resp.json().get('value', []) if 'folder' in i]
        self.folder_ids.unlink()
        for item in items:
            name      = item['name']
            full_path = f'{path}/{name}' if path else name
            self.env['dmc.backup.folder.item'].create({
                'wizard_id': self.id,
                'name':      name,
                'path':      full_path,
            })

    def action_open_folder(self, folder_path):
        self.ensure_one()
        self.write({'parent_path': folder_path})
        self._load_folders()
        return {
            'type':      'ir.actions.act_window',
            'name':      'Browse OneDrive Folders',
            'res_model': 'dmc.backup.folder.wizard',
            'res_id':    self.id,
            'view_mode': 'form',
            'target':    'new',
        }

    def action_select_folder(self, folder_path):
        self.ensure_one()
        self.config_id.write({'onedrive_folder_path': folder_path})
        return {'type': 'ir.actions.act_window_close'}


class DmcBackupFolderItem(models.TransientModel):
    _name = 'dmc.backup.folder.item'
    _description = 'OneDrive Folder Item'

    wizard_id = fields.Many2one(
        'dmc.backup.folder.wizard', required=True, ondelete='cascade'
    )
    name = fields.Char(readonly=True)
    path = fields.Char(readonly=True)

    def action_open(self):
        self.ensure_one()
        return self.wizard_id.action_open_folder(self.path)

    def action_select(self):
        self.ensure_one()
        return self.wizard_id.action_select_folder(self.path)

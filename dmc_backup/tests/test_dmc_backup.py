# -*- coding: utf-8 -*-
import io
import odoo
from unittest.mock import patch, MagicMock, mock_open
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError


class TestGenerateSqlDump(TransactionCase):
    """Tests for _generate_sql_dump correctness."""

    def setUp(self):
        super().setUp()
        self.service = self.env['dmc.backup.service']

    def _run_dump(self):
        buf = io.BytesIO()
        self.service._generate_sql_dump(buf)
        return buf.getvalue().decode('utf-8')

    def test_dump_has_pg_dump_header(self):
        """dump.sql must start with the pg_dump plain-text header so restore tools accept it."""
        sql = self._run_dump()
        self.assertTrue(sql.startswith('--\n'), 'dump must begin with SQL comment')
        self.assertIn('PostgreSQL database dump', sql[:200])

    def test_copy_failure_raises_with_table_name(self):
        """copy_expert failure must propagate — not silently skip the table."""
        with patch.object(
            self.service.env.cr._obj, 'copy_expert',
            side_effect=Exception('simulated disk error')
        ):
            with self.assertRaises(Exception) as ctx:
                self.service._generate_sql_dump(io.BytesIO())
        self.assertIn('COPY failed for table', str(ctx.exception))

    def test_dump_wrapped_in_transaction(self):
        """SQL dump must contain BEGIN and end with COMMIT."""
        sql = self._run_dump()
        self.assertIn('BEGIN;', sql)
        self.assertTrue(sql.strip().endswith('COMMIT;'))

    def test_truncate_before_copy(self):
        """A TRUNCATE statement must appear before any COPY FROM STDIN."""
        sql = self._run_dump()
        if 'CREATE TABLE' not in sql:
            return
        self.assertIn('TRUNCATE ', sql)
        truncate_pos = sql.find('TRUNCATE ')
        copy_pos     = sql.find('COPY ')
        if copy_pos == -1:
            return
        self.assertLess(truncate_pos, copy_pos)


class TestRunBackup(TransactionCase):
    """Tests for run_backup failure-log persistence."""

    def setUp(self):
        super().setUp()
        self.service = self.env['dmc.backup.service']

    def tearDown(self):
        super().tearDown()
        registry = odoo.registry(self.env.cr.dbname)
        with registry.cursor() as cr:
            cr.execute("DELETE FROM dmc_backup_log WHERE state = 'failed'")

    def test_failure_log_persists_after_rollback(self):
        """A 'failed' dmc.backup.log record must exist even when the cron transaction rolls back."""
        with patch.object(self.service.__class__, '_dump_db', side_effect=Exception('simulated failure')):
            with self.assertRaises(Exception):
                self.service.run_backup()

        failed_logs = self.env['dmc.backup.log'].search([('state', '=', 'failed')])
        self.assertTrue(failed_logs, 'No failed log record found — it was likely rolled back')
        self.assertIn('simulated failure', failed_logs[0].error_message)

    def test_log_has_storage_url_field(self):
        """dmc.backup.log must expose storage_url, not azure_url."""
        log = self.env['dmc.backup.log'].sudo().create({
            'name': 'test.zip',
            'db_name': 'test',
            'odoo_version': '19.0',
            'state': 'success',
            'storage_url': 'https://example.com/test.zip',
        })
        self.assertEqual(log.storage_url, 'https://example.com/test.zip')

    def test_log_has_storage_type_field(self):
        """dmc.backup.log must store a storage_type so deletion routes correctly."""
        log = self.env['dmc.backup.log'].sudo().create({
            'name': 'test.zip',
            'db_name': 'test',
            'odoo_version': '19.0',
            'state': 'success',
            'storage_url': 'https://example.com/test.zip',
            'storage_type': 'azure',
        })
        self.assertEqual(log.storage_type, 'azure')

    def test_run_backup_sets_storage_type_on_log(self):
        """run_backup must write config.storage_type onto the success log."""
        from unittest.mock import patch, mock_open
        config = self.env['dmc.backup.config'].sudo().search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            config = self.env['dmc.backup.config'].create({
                'name': 'Azure Test ST',
                'storage_type': 'azure',
                'azure_account': 'acct',
                'azure_container': 'ctr',
                'azure_sas_token': 'sv=test',
                'is_default': True,
                'retention_days': 30,
            })
        with patch.object(self.service.__class__, '_dump_db', return_value=None), \
             patch.object(self.service.__class__, '_push_to_azure',
                          return_value='https://acct.blob.core.windows.net/ctr/f.zip'), \
             patch('tempfile.mkstemp', return_value=(0, '/tmp/fake_backup.zip')), \
             patch('os.close'), \
             patch('os.path.getsize', return_value=1024), \
             patch('os.path.exists', return_value=False):
            self.service.run_backup()
        log = self.env['dmc.backup.log'].sudo().search(
            [('state', '=', 'success')], limit=1, order='id desc'
        )
        if log:
            self.assertEqual(log.storage_type, 'azure')


class TestDmcBackupConfig(TransactionCase):
    """Tests for dmc.backup.config field constraints."""

    def _azure_vals(self, **kw):
        return {
            'name': 'Azure Test',
            'storage_type': 'azure',
            'azure_account': 'myaccount',
            'azure_container': 'mycontainer',
            'azure_sas_token': 'sv=...',
            **kw,
        }

    def _onedrive_vals(self, **kw):
        return {
            'name': 'OneDrive Test',
            'storage_type': 'onedrive',
            'onedrive_client_id': 'client-id',
            'onedrive_tenant_id': 'tenant-id',
            'onedrive_client_secret': 'secret',
            'onedrive_drive_type': 'user',
            'onedrive_drive_target': 'admin@company.com',
            **kw,
        }

    def test_azure_config_requires_azure_fields(self):
        """Creating an azure config without azure_account must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.env['dmc.backup.config'].create(
                self._azure_vals(azure_account='')
            )

    def test_onedrive_config_requires_onedrive_fields(self):
        """Creating an onedrive config without client_id must raise ValidationError."""
        with self.assertRaises(ValidationError):
            self.env['dmc.backup.config'].create(
                self._onedrive_vals(onedrive_client_id='')
            )

    def test_onedrive_config_valid(self):
        """A fully populated onedrive config must save without error."""
        cfg = self.env['dmc.backup.config'].create(self._onedrive_vals())
        self.assertEqual(cfg.storage_type, 'onedrive')

    def test_azure_config_valid(self):
        """A fully populated azure config must save without error."""
        cfg = self.env['dmc.backup.config'].create(self._azure_vals())
        self.assertEqual(cfg.storage_type, 'azure')

    def _make_config(self):
        return self.env['dmc.backup.config'].create(self._onedrive_vals())

    def test_get_onedrive_token_success(self):
        """_get_onedrive_token must return the access_token string on 200."""
        cfg = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'access_token': 'my-token'}
        with patch('requests.post', return_value=mock_resp) as mock_post:
            token = cfg._get_onedrive_token()
        self.assertEqual(token, 'my-token')
        call_args = mock_post.call_args
        self.assertIn('tenant-id', call_args[0][0])

    def test_get_onedrive_token_failure_raises(self):
        """_get_onedrive_token must raise UserError on non-200 response."""
        cfg = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = 'Unauthorized'
        with patch('requests.post', return_value=mock_resp):
            with self.assertRaises(UserError):
                cfg._get_onedrive_token()

    def test_resolve_drive_user(self):
        """_resolve_onedrive_drive must call /users/{email}/drive for user type."""
        cfg = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'id': 'drive-abc'}
        with patch('requests.get', return_value=mock_resp) as mock_get:
            drive_id = cfg._resolve_onedrive_drive('token')
        self.assertEqual(drive_id, 'drive-abc')
        self.assertIn('admin@company.com', mock_get.call_args[0][0])

    def test_resolve_drive_sharepoint(self):
        """_resolve_onedrive_drive must return first drive id for SharePoint type."""
        cfg = self.env['dmc.backup.config'].create(self._onedrive_vals(
            onedrive_drive_type='sharepoint',
            onedrive_drive_target='https://company.sharepoint.com/sites/it',
        ))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'value': [{'id': 'sp-drive-1'}, {'id': 'sp-drive-2'}]}
        with patch('requests.get', return_value=mock_resp):
            drive_id = cfg._resolve_onedrive_drive('token')
        self.assertEqual(drive_id, 'sp-drive-1')

    def test_resolve_drive_sharepoint_no_libraries_raises(self):
        """_resolve_onedrive_drive must raise UserError when no document libraries found."""
        cfg = self.env['dmc.backup.config'].create(self._onedrive_vals(
            onedrive_drive_type='sharepoint',
            onedrive_drive_target='https://company.sharepoint.com/sites/it',
        ))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'value': []}
        with patch('requests.get', return_value=mock_resp):
            with self.assertRaises(UserError):
                cfg._resolve_onedrive_drive('token')

    def test_action_test_connection_success(self):
        """action_test_connection must return a display_notification action on success."""
        cfg = self._make_config()
        with patch.object(cfg.__class__, '_get_onedrive_token', return_value='tok'), \
             patch.object(cfg.__class__, '_resolve_onedrive_drive', return_value='drv'), \
             patch.object(cfg.__class__, '_ensure_onedrive_folder', return_value=None):
            result = cfg.action_test_connection()
        self.assertEqual(result['type'], 'ir.actions.client')
        self.assertEqual(result['tag'], 'display_notification')
        self.assertEqual(result['params']['type'], 'success')

    def test_action_test_connection_raises_for_azure(self):
        """action_test_connection must raise UserError when storage_type is azure."""
        cfg = self.env['dmc.backup.config'].create(self._azure_vals())
        with self.assertRaises(UserError):
            cfg.action_test_connection()

    def test_ensure_folder_creates_segments(self):
        """_ensure_onedrive_folder must POST once per path segment."""
        cfg = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        with patch('requests.post', return_value=mock_resp) as mock_post:
            cfg._ensure_onedrive_folder('tok', 'drv', 'Backups/Odoo')
        self.assertEqual(mock_post.call_count, 2)
        second_url = mock_post.call_args_list[1][0][0]
        self.assertIn('root:/Backups:/children', second_url)

    def test_ensure_folder_tolerates_409(self):
        """_ensure_onedrive_folder must not raise when folder already exists (409)."""
        cfg = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        with patch('requests.post', return_value=mock_resp):
            cfg._ensure_onedrive_folder('tok', 'drv', 'Backups')

    def test_ensure_folder_raises_on_error(self):
        """_ensure_onedrive_folder must raise UserError on unexpected status codes."""
        cfg = self._make_config()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = 'Internal Server Error'
        with patch('requests.post', return_value=mock_resp):
            with self.assertRaises(UserError):
                cfg._ensure_onedrive_folder('tok', 'drv', 'Backups')


class TestPushToOneDrive(TransactionCase):
    """Tests for _push_to_onedrive and run_backup routing."""

    def setUp(self):
        super().setUp()
        self.service = self.env['dmc.backup.service']
        self.config  = self.env['dmc.backup.config'].create({
            'name':                   'OneDrive Test',
            'storage_type':           'onedrive',
            'onedrive_client_id':     'cid',
            'onedrive_tenant_id':     'tid',
            'onedrive_client_secret': 'sec',
            'onedrive_drive_type':    'user',
            'onedrive_drive_target':  'admin@test.com',
            'onedrive_folder_path':   '/Backups',
            'is_default':             True,
            'retention_days':         7,
        })

    def test_run_backup_routes_to_onedrive(self):
        """run_backup must call _push_to_onedrive when storage_type is onedrive."""
        with patch.object(self.service.__class__, '_dump_db', return_value=None), \
             patch.object(self.service.__class__, '_push_to_onedrive',
                          return_value='https://od.com/file.zip') as mock_od, \
             patch.object(self.service.__class__, '_push_to_azure',
                          return_value=None) as mock_az, \
             patch('tempfile.mkstemp', return_value=(0, '/tmp/fake_backup.zip')), \
             patch('os.makedirs'), \
             patch('os.close'), \
             patch('os.path.exists', return_value=False), \
             patch('builtins.open', mock_open(read_data=b'zipdata')), \
             patch('base64.b64encode', return_value=b'encoded'):
            try:
                self.service.run_backup()
            except Exception:
                pass
            mock_od.assert_called()
            mock_az.assert_not_called()

    def test_push_to_onedrive_streams_in_chunks(self):
        """_push_to_onedrive must call PUT once per 10 MB chunk."""
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as f:
            f.write(b'0' * (25 * 1024 * 1024))
            zip_path = f.name
        try:
            session_resp = MagicMock()
            session_resp.status_code = 200
            session_resp.json.return_value = {'uploadUrl': 'https://upload.example.com/session'}

            chunk_resp = MagicMock()
            chunk_resp.status_code = 202

            final_resp = MagicMock()
            final_resp.status_code = 201
            final_resp.json.return_value = {'webUrl': 'https://od.com/file.zip'}

            with patch.object(
                self.config.__class__, '_get_onedrive_token', return_value='tok'
            ), patch.object(
                self.config.__class__, '_resolve_onedrive_drive', return_value='drv'
            ), patch.object(
                self.config.__class__, '_ensure_onedrive_folder', return_value=None
            ), patch('requests.post', return_value=session_resp), \
               patch('requests.put', side_effect=[chunk_resp, chunk_resp, final_resp]) as mock_put:
                result = self.service._push_to_onedrive(
                    zip_path, 25 * 1024 * 1024, 'backup.zip', self.config
                )
            self.assertEqual(mock_put.call_count, 3)
            self.assertEqual(result, 'https://od.com/file.zip')
        finally:
            os.unlink(zip_path)


class TestFolderWizard(TransactionCase):
    """Tests for the OneDrive folder picker wizard."""

    def setUp(self):
        super().setUp()
        self.config = self.env['dmc.backup.config'].create({
            'name':                   'OD Wizard Test',
            'storage_type':           'onedrive',
            'onedrive_client_id':     'cid',
            'onedrive_tenant_id':     'tid',
            'onedrive_client_secret': 'sec',
            'onedrive_drive_type':    'user',
            'onedrive_drive_target':  'admin@test.com',
        })

    def test_load_folders_populates_items(self):
        """_load_folders must create one folder.item per folder returned by Graph API."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'value': [
                {'name': 'Backups', 'folder': {}},
                {'name': 'Docs',    'folder': {}},
                {'name': 'file.txt'},  # not a folder — must be excluded
            ]
        }
        wizard = self.env['dmc.backup.folder.wizard'].create({
            'config_id':   self.config.id,
            'drive_id':    'drv-id',
            'token':       'tok',
            'parent_path': '',
        })
        with patch('requests.get', return_value=mock_resp):
            wizard._load_folders()
        self.assertEqual(len(wizard.folder_ids), 2)
        self.assertIn('Backups', wizard.folder_ids.mapped('name'))

    def test_action_select_writes_path_to_config(self):
        """action_select on a folder.item must write its path to config.onedrive_folder_path."""
        wizard = self.env['dmc.backup.folder.wizard'].create({
            'config_id':   self.config.id,
            'drive_id':    'drv-id',
            'token':       'tok',
            'parent_path': '',
        })
        item = self.env['dmc.backup.folder.item'].create({
            'wizard_id': wizard.id,
            'name':      'Backups',
            'path':      'Backups',
        })
        item.action_select()
        self.assertEqual(self.config.onedrive_folder_path, 'Backups')

    def test_load_folders_replaces_items_on_second_call(self):
        """Calling _load_folders twice must replace, not accumulate, items."""
        wizard = self.env['dmc.backup.folder.wizard'].create({
            'config_id':   self.config.id,
            'drive_id':    'drv-id',
            'token':       'tok',
            'parent_path': '',
        })
        first_resp = MagicMock()
        first_resp.status_code = 200
        first_resp.json.return_value = {
            'value': [{'name': 'Backups', 'folder': {}}, {'name': 'Docs', 'folder': {}}]
        }
        second_resp = MagicMock()
        second_resp.status_code = 200
        second_resp.json.return_value = {
            'value': [{'name': 'Archive', 'folder': {}}]
        }
        with patch('requests.get', side_effect=[first_resp, second_resp]):
            wizard._load_folders()
            wizard._load_folders()
        self.assertEqual(len(wizard.folder_ids), 1)
        self.assertEqual(wizard.folder_ids[0].name, 'Archive')

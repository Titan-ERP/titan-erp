# -*- coding: utf-8 -*-
import io
import zipfile
import odoo
from unittest.mock import patch, MagicMock, mock_open
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError, ValidationError


class TestDumpDb(TransactionCase):
    """Tests for _dump_db using pg_dump subprocess."""

    def setUp(self):
        super().setUp()
        self.service = self.env['dmc.backup.service']

    def _fake_pg_dump(self, cmd, **kw):
        dump_path = next(a for a in cmd if a.startswith('--file=')).split('=', 1)[1]
        with open(dump_path, 'wb') as f:
            f.write(b'-- pg_dump output\n')
        m = MagicMock()
        m.returncode = 0
        m.stderr = b''
        return m

    def test_dump_db_calls_pg_dump(self):
        """_dump_db must invoke pg_dump with --no-owner and the db name."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch('subprocess.run', side_effect=self._fake_pg_dump) as mock_run, \
                 patch.object(self.service.__class__, '_find_pg_dump', return_value='pg_dump'), \
                 patch('odoo.tools.misc.exec_pg_environ', return_value={}):
                self.service._dump_db(self.env.cr.dbname, zip_path)

            args = mock_run.call_args[0][0]
            self.assertEqual(args[0], 'pg_dump')
            self.assertIn('--no-owner', args)
            self.assertIn(self.env.cr.dbname, args)
        finally:
            os.unlink(zip_path)

    def test_dump_db_zip_contains_empty_filestore_entry(self):
        """_dump_db zip must contain dump.sql first, manifest.json, and an empty filestore/ — no filestore files."""
        import os
        import tempfile
        import zipfile

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch('subprocess.run', side_effect=self._fake_pg_dump), \
                 patch.object(self.service.__class__, '_find_pg_dump', return_value='pg_dump'), \
                 patch('odoo.tools.misc.exec_pg_environ', return_value={}):
                self.service._dump_db(self.env.cr.dbname, zip_path)

            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()

            self.assertEqual(names[0], 'dump.sql', 'dump.sql must be the first entry')
            self.assertIn('manifest.json', names)
            self.assertIn('filestore/', names)
            filestore_files = [n for n in names if n.startswith('filestore/') and n != 'filestore/']
            self.assertFalse(filestore_files, f'No filestore files expected, found: {filestore_files}')
        finally:
            os.unlink(zip_path)

    def test_python_dump_disables_triggers_and_uses_delete(self):
        """Python dump must use DELETE FROM ONLY (not TRUNCATE CASCADE) and DISABLE/ENABLE TRIGGER ALL per table.

        TRUNCATE ... CASCADE would cascade to already-loaded tables when the
        referenced table is truncated later (alphabetical order does not respect
        FK dependency order).

        ONLY is required to prevent PostgreSQL native-inheritance cascade.  Odoo 19
        uses PG INHERITS for the action tables (ir_act_window, ir_act_client, etc.
        INHERIT from ir_actions in base_data.sql).  Without ONLY, DELETE FROM
        ir_actions cascades and wipes all child tables that were already COPYed
        earlier in the alphabetical pass → all action tables end up empty after
        restore → "The action 'N' does not exist" on every login.

        No explicit BEGIN/COMMIT in the data section: if psql uses
        --single-transaction, an explicit COMMIT inside the dump prematurely
        ends psql's outer transaction and causes subsequent statements to run
        in auto-commit, potentially leaving the restore in a broken state.
        """
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        try:
            self.service._write_python_sql_dump(dump_path)
            with open(dump_path, 'r', encoding='utf-8') as f:
                sql = f.read()

            # No standalone transaction wrapper in the data section
            self.assertNotIn('BEGIN;', sql,
                             'BEGIN; must not appear in _write_python_sql_dump output '
                             '(conflicts with psql --single-transaction)')

            # DELETE FROM ONLY instead of plain DELETE FROM or TRUNCATE CASCADE.
            # ONLY prevents PG-native-inheritance cascade through ir_actions.
            self.assertIn('DELETE FROM ONLY', sql,
                          'DELETE FROM ONLY must be used to prevent PG-inheritance cascade '
                          'through ir_actions wiping ir_act_window/ir_act_client/etc.')
            self.assertNotIn('TRUNCATE TABLE', sql,
                             'TRUNCATE TABLE must not be in dump (CASCADE would wipe '
                             'previously loaded tables on existing schemas)')

            # DISABLE/ENABLE TRIGGER ALL present and balanced
            disable_count = sql.count('DISABLE TRIGGER ALL')
            enable_count  = sql.count('ENABLE TRIGGER ALL')
            self.assertGreater(disable_count, 0, 'No DISABLE TRIGGER ALL found in dump')
            self.assertEqual(disable_count, enable_count,
                             'DISABLE/ENABLE TRIGGER ALL count mismatch')

            self.assertIn('COPY ', sql, 'Expected COPY statements in dump')
        finally:
            os.unlink(dump_path)

    def test_python_dump_constraints_are_idempotent(self):
        """ADD CONSTRAINT must use DO blocks and CREATE INDEX must use IF NOT EXISTS.

        Odoo SH may restore a dump into an existing schema (not a fresh drop+create),
        so bare ADD CONSTRAINT fails with 'already exists' and stops psql
        (ON_ERROR_STOP=1).  Wrapping in DO blocks and using IF NOT EXISTS on
        CREATE INDEX makes the post-data section idempotent on any target.
        """
        import os, re, tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        try:
            self.service._write_python_sql_dump(dump_path)
            with open(dump_path, 'r', encoding='utf-8') as f:
                sql = f.read()

            # ADD CONSTRAINT must be inside DO blocks, not bare ALTER TABLE statements
            self.assertIn('DO $$ BEGIN ALTER TABLE', sql,
                          'ADD CONSTRAINT must be wrapped in DO $$ BEGIN...EXCEPTION...END $$ block')
            bare_add = re.findall(r'^ALTER TABLE[^;\n]*ADD CONSTRAINT[^;\n]*;',
                                  sql, re.MULTILINE)
            self.assertEqual(bare_add, [],
                             f'Bare ADD CONSTRAINT found outside DO block: {bare_add[:1]}')

            # CREATE INDEX must use IF NOT EXISTS
            for stmt in re.findall(r'CREATE (?:UNIQUE )?INDEX[^;]+;', sql):
                self.assertIn('IF NOT EXISTS', stmt,
                              f'CREATE INDEX without IF NOT EXISTS: {stmt[:120]}')
        finally:
            os.unlink(dump_path)

    def test_python_dump_emits_inherit_statements(self):
        """Python dump must emit ALTER TABLE ... INHERIT ... for PG native-inheritance tables.

        Odoo 19 creates ir_act_client, ir_act_window, ir_act_server, ir_act_url, and
        ir_act_report_xml with INHERITS (ir_actions) in base_data.sql.  Our CREATE TABLE
        IF NOT EXISTS does NOT include the INHERITS clause — on a fresh database the
        hierarchy is absent.  Without it, SELECT FROM ir_actions WHERE id=N returns nothing
        even though the row lives in a child table, causing "The action 'N' does not exist"
        on every navigation.  The dump must emit ALTER TABLE child INHERIT parent (wrapped
        in DO blocks to be idempotent when --init base already set up INHERITS).
        """
        import os, tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        try:
            self.service._write_python_sql_dump(dump_path)
            with open(dump_path, 'r', encoding='utf-8') as f:
                sql = f.read()

            self.assertIn('ALTER TABLE', sql,
                          'Dump must contain ALTER TABLE statements')
            self.assertIn('INHERIT', sql,
                          'Dump must contain INHERIT keyword to restore PG inheritance hierarchy')
            # Must be inside DO blocks (idempotent)
            self.assertIn('EXCEPTION WHEN others THEN NULL', sql,
                          'INHERIT statements must be wrapped in DO blocks with EXCEPTION handler')
            # Inheritance section must appear before the first COPY (schema before data)
            first_inherit = sql.index('INHERIT') if 'INHERIT' in sql else len(sql)
            first_copy = sql.index('\nCOPY ') if '\nCOPY ' in sql else len(sql)
            self.assertLess(first_inherit, first_copy,
                            'INHERIT statements must appear before COPY data blocks')
        finally:
            os.unlink(dump_path)

    def test_python_dump_skip_large_tables(self):
        """skip_tables omits DELETE FROM + COPY for those tables but keeps a comment."""
        import os, tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        skip = frozenset({'ir_attachment', 'mail_message', 'mail_mail'})
        try:
            self.service._write_python_sql_dump(dump_path, skip_tables=skip)
            with open(dump_path, 'r', encoding='utf-8') as f:
                sql = f.read()

            for tbl in skip:
                self.assertIn(
                    f'-- Skipped data for {tbl}', sql,
                    f'Expected skip comment for {tbl}',
                )
                self.assertNotIn(
                    f'DELETE FROM ONLY "public"."{tbl}"', sql,
                    f'DELETE FROM ONLY must be absent for skipped table {tbl}',
                )
                self.assertNotIn(
                    f'COPY "public"."{tbl}"', sql,
                    f'COPY must be absent for skipped table {tbl}',
                )
        finally:
            os.unlink(dump_path)

    def test_python_dump_header_error_handling(self):
        """dump.sql must begin with ON_ERROR_ROLLBACK on then ON_ERROR_STOP off."""
        import os, tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        try:
            self.service._write_python_sql_dump(dump_path)
            with open(dump_path, 'r', encoding='utf-8') as f:
                line1 = f.readline().rstrip('\n')
                line2 = f.readline().rstrip('\n')
            self.assertEqual(line1, '\\set ON_ERROR_ROLLBACK on',
                             'First line must be \\set ON_ERROR_ROLLBACK on')
            self.assertEqual(line2, '\\set ON_ERROR_STOP off',
                             'Second line must be \\set ON_ERROR_STOP off')
        finally:
            os.unlink(dump_path)

    def test_python_dump_has_delete_from_before_copy(self):
        """DELETE FROM ONLY must precede each COPY block to clear Odoo SH init data.

        Odoo SH initializes the database (--init base) before running the restore dump,
        which populates ir_act_window and other core tables with base Odoo data whose IDs
        conflict with production IDs.  Without DELETE FROM ONLY, COPY fails on PK collision and
        the table retains only the init data, causing Missing Action on all app menus.
        ONLY is required to prevent the PG-inheritance cascade from ir_actions wiping child tables.
        """
        import os, tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        try:
            self.service._write_python_sql_dump(dump_path)
            with open(dump_path, 'r', encoding='utf-8') as f:
                sql = f.read()
            self.assertIn('DELETE FROM ONLY', sql,
                          'DELETE FROM ONLY must appear before each COPY block in the dump '
                          '(prevents PG-inheritance cascade through ir_actions)')
        finally:
            os.unlink(dump_path)

    def test_python_dump_identity_setval(self):
        """Dump must emit pg_get_serial_sequence setval calls for IDENTITY columns."""
        import os, tempfile

        with tempfile.NamedTemporaryFile(suffix='.sql', delete=False) as tf:
            dump_path = tf.name

        try:
            self.service._write_python_sql_dump(dump_path)
            with open(dump_path, 'r', encoding='utf-8') as f:
                sql = f.read()
            self.assertIn('pg_get_serial_sequence', sql,
                          'Dump must contain pg_get_serial_sequence for IDENTITY column setval')
        finally:
            os.unlink(dump_path)

    def test_neutralization_appended_in_own_transaction(self):
        """When neutralize=True the neutralization SQL is appended with BEGIN/COMMIT."""
        import os
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'neutralize': True,
        })

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch('subprocess.run', side_effect=self._fake_pg_dump), \
                 patch.object(self.service.__class__, '_find_pg_dump', return_value='pg_dump'), \
                 patch('odoo.service.db.exec_pg_environ', return_value={}):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                sql = zf.read('dump.sql').decode()

            self.assertIn('BEGIN;', sql)
            self.assertIn('-- Neutralization', sql)
            self.assertIn('COMMIT;', sql)
            self.assertIn('UPDATE ir_cron', sql)
        finally:
            os.unlink(zip_path)

    def test_purge_stale_assets_default_appends_cleanup_sql(self):
        """purge_stale_assets defaults to True and runs even when neutralize=False."""
        import os
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test PSA',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'neutralize': False,
        })

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch('subprocess.run', side_effect=self._fake_pg_dump), \
                 patch.object(self.service.__class__, '_find_pg_dump', return_value='pg_dump'), \
                 patch('odoo.tools.misc.exec_pg_environ', return_value={}):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                sql = zf.read('dump.sql').decode()

            self.assertIn('DELETE FROM ir_attachment', sql)
            self.assertIn("assets_%", sql)
            self.assertNotIn('-- Neutralization', sql)
        finally:
            os.unlink(zip_path)

    def test_purge_stale_assets_false_omits_cleanup_sql(self):
        """purge_stale_assets=False must omit the asset-bundle cleanup SQL."""
        import os
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test PSA off',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'neutralize': False,
            'purge_stale_assets': False,
        })

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch('subprocess.run', side_effect=self._fake_pg_dump), \
                 patch.object(self.service.__class__, '_find_pg_dump', return_value='pg_dump'), \
                 patch('odoo.tools.misc.exec_pg_environ', return_value={}):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                sql = zf.read('dump.sql').decode()

            self.assertNotIn('DELETE FROM ir_attachment', sql)
        finally:
            os.unlink(zip_path)

    def test_include_filestore_streams_real_files(self):
        """include_filestore=True must stream real filestore files into the zip."""
        import os
        import shutil
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test FS',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'include_filestore': True,
        })

        fs_dir = tempfile.mkdtemp()
        sub_dir = os.path.join(fs_dir, '69')
        os.makedirs(sub_dir)
        with open(os.path.join(sub_dir, '69ea99d6'), 'wb') as f:
            f.write(b'fake attachment content')

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch('subprocess.run', side_effect=self._fake_pg_dump), \
                 patch.object(self.service.__class__, '_find_pg_dump', return_value='pg_dump'), \
                 patch('odoo.tools.misc.exec_pg_environ', return_value={}), \
                 patch('odoo.tools.config.filestore', return_value=fs_dir):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
            self.assertIn('filestore/69/69ea99d6', names)
        finally:
            os.unlink(zip_path)
            shutil.rmtree(fs_dir)

    def test_include_filestore_forces_ir_attachment_data_included(self):
        """include_filestore=True must not let skip_large_tables exclude ir_attachment data."""
        import os
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test FS2',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'include_filestore': True,
            'skip_large_tables': True,
        })

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch.object(self.service.__class__, '_is_pg_dump_available', return_value=False), \
                 patch('odoo.tools.config.filestore', return_value=tempfile.gettempdir()):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                sql = zf.read('dump.sql').decode()

            self.assertIn('COPY "public"."ir_attachment"', sql)
            self.assertNotIn('-- Skipped data for ir_attachment', sql)
        finally:
            os.unlink(zip_path)

    def test_no_filestore_always_skips_ir_attachment_even_without_skip_large_tables(self):
        """include_filestore=False must skip ir_attachment full-table COPY even when skip_large_tables=False.

        The bug: skip_large_tables=False passed skip_tables=None, bypassing the
        ir_attachment skip and producing dangling store_fname references after restore.
        Icon attachments (res_model='ir.ui.menu') may still appear — those are emitted
        inline with db_datas and store_fname=NULL, which is safe.
        """
        import os
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test NoFS NoSkip',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'include_filestore': False,
            'skip_large_tables': False,
        })

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch.object(self.service.__class__, '_is_pg_dump_available', return_value=False):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                sql = zf.read('dump.sql').decode()

            self.assertIn('-- Skipped data for ir_attachment', sql,
                          'ir_attachment must be skipped when include_filestore=False, '
                          'regardless of skip_large_tables')
        finally:
            os.unlink(zip_path)

    def test_skip_ir_attachment_still_inlines_menu_icons(self):
        """When ir_attachment is skipped, menu icon attachments must be emitted inline."""
        import os
        import tempfile

        config = self.env['dmc.backup.config'].create({
            'name': 'Test Icon Inline',
            'storage_type': 'azure',
            'azure_account': 'a',
            'azure_container': 'c',
            'azure_sas_token': 't',
            'include_filestore': False,
            'skip_large_tables': True,
        })

        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tf:
            zip_path = tf.name

        try:
            with patch.object(self.service.__class__, '_is_pg_dump_available', return_value=False):
                self.service._dump_db(self.env.cr.dbname, zip_path, config=config)

            with zipfile.ZipFile(zip_path) as zf:
                sql = zf.read('dump.sql').decode()

            self.assertIn('-- Skipped data for ir_attachment', sql)

            icon_count = self.env['ir.attachment'].sudo().search_count([
                ('res_model', '=', 'ir.ui.menu'),
                ('res_field', '=', 'web_icon_data'),
            ])
            if icon_count:
                self.assertIn('-- Menu app-icon attachments', sql,
                              'Icon attachment COPY comment must appear in dump')
                self.assertIn("res_field = 'web_icon_data'", sql,
                              'DELETE for icon attachments must be in dump')
        finally:
            os.unlink(zip_path)


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

    def test_cleanup_failure_does_not_rollback_success_log(self):
        """A cleanup exception must not prevent the success log from being written."""
        config = self.env['dmc.backup.config'].sudo().search(
            [('is_default', '=', True)], limit=1
        )
        if not config:
            config = self.env['dmc.backup.config'].create({
                'name': 'Test', 'storage_type': 'azure',
                'azure_account': 'a', 'azure_container': 'c',
                'azure_sas_token': 'sv=x', 'is_default': True, 'retention_days': 0,
            })

        with patch.object(self.service.__class__, '_dump_db', return_value=None), \
             patch.object(self.service.__class__, '_push_to_azure',
                          return_value='https://a.blob.core.windows.net/c/f.zip'), \
             patch('tempfile.mkstemp', return_value=(0, '/tmp/fake.zip')), \
             patch('os.close'), \
             patch('os.path.getsize', return_value=1024), \
             patch('os.path.exists', return_value=False), \
             patch.object(
                 self.env['dmc.backup.log'].__class__,
                 'unlink',
                 side_effect=Exception('simulated cleanup failure'),
             ):
            self.service.run_backup()  # must NOT raise

        success_logs = self.env['dmc.backup.log'].search([('state', '=', 'success')])
        self.assertTrue(success_logs, 'Success log was rolled back by the cleanup failure')


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

    def test_delete_routes_by_record_storage_type_not_config(self):
        """_delete_remote_files must use record.storage_type, not config.storage_type."""
        # Create an OneDrive config as default (with an azure_sas_token so the azure
        # branch in _delete_remote_files can actually issue the DELETE request)
        od_config = self.env['dmc.backup.config'].create({
            'name': 'OD',
            'storage_type': 'onedrive',
            'onedrive_client_id': 'c', 'onedrive_tenant_id': 't',
            'onedrive_client_secret': 's', 'onedrive_drive_type': 'user',
            'onedrive_drive_target': 'x@y.com', 'is_default': True,
            'azure_sas_token': 'sv=test',
        })
        # Log record that was created under Azure (storage_type='azure')
        log = self.env['dmc.backup.log'].sudo().create({
            'name': 'backup.zip',
            'db_name': 'test',
            'odoo_version': '19.0',
            'state': 'success',
            'storage_url': 'https://acct.blob.core.windows.net/ctr/backup.zip',
            'storage_type': 'azure',
        })
        azure_delete_called = []
        onedrive_delete_called = []

        def mock_delete(url, **kwargs):
            if 'blob.core.windows.net' in url:
                azure_delete_called.append(url)
            elif 'graph.microsoft.com' in url:
                onedrive_delete_called.append(url)
            m = MagicMock()
            m.status_code = 202
            return m

        with patch('requests.delete', side_effect=mock_delete):
            log._delete_remote_files()

        self.assertTrue(azure_delete_called, 'Azure DELETE was not called — routing used wrong backend')
        self.assertFalse(onedrive_delete_called, 'OneDrive DELETE was incorrectly called for an Azure record')


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
                          return_value=('https://od.com/file.zip', 'file.zip')) as mock_od, \
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

    def test_run_backup_stores_actual_name_after_onedrive_rename(self):
        """run_backup must store the OneDrive-assigned filename on the log when the file is renamed."""
        renamed = 'backup_prod_20260601 1.zip'
        with patch.object(self.service.__class__, '_dump_db', return_value=None), \
             patch.object(
                 self.service.__class__, '_push_to_onedrive',
                 return_value=('https://od.com/' + renamed, renamed)
             ), \
             patch('tempfile.mkstemp', return_value=(0, '/tmp/fake_backup.zip')), \
             patch('os.close'), \
             patch('os.path.getsize', return_value=1024), \
             patch('os.path.exists', return_value=False):
            self.service.run_backup()

        log = self.env['dmc.backup.log'].sudo().search(
            [('state', '=', 'success'), ('storage_type', '=', 'onedrive')],
            limit=1, order='id desc',
        )
        self.assertTrue(log, 'No success log record found after run_backup')
        self.assertEqual(
            log.name, renamed,
            f'Log name should be the OneDrive-assigned name "{renamed}", got "{log.name}"',
        )

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
            final_resp.json.return_value = {
                'webUrl': 'https://od.com/file.zip',
                'name':   'file.zip',
            }

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
            self.assertEqual(result, ('https://od.com/file.zip', 'file.zip'))
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

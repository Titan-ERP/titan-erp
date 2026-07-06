# -*- coding: utf-8 -*-
{
    'name': "DMC Backup",

    'summary': "Scheduled database backup to Azure Blob Storage or OneDrive with in-app history",

    'description': """
Scheduled database backup stored in Odoo filestore with in-app history list and
retention-based cleanup. Supports Azure Blob Storage and OneDrive (Microsoft
Graph API) as backup destinations.

Features
--------
- Scheduled daily backup via cron (configurable)
- Backup history list with size, state, and download link
- Multiple backup destinations (Azure Blob Storage, OneDrive)
- Configurable retention period per destination
- OneDrive folder browser to select the target folder interactively

Changelog
---------
19.0.15.0.0
  - Fixed: Python dump now emits a selective COPY of ir.ui.menu icon attachments
    (res_model='ir.ui.menu', res_field='web_icon_data') even when ir_attachment is
    otherwise skipped (include_filestore=False); web_icon_data is a Binary field
    with attachment=True — its bytes live in ir_attachment, not in the ir_ui_menu
    table; load_menus() queries ir_attachment for icon data and returns False when
    the table is empty, so the JS home screen falls back to a generic box icon for
    every app; the fix reads the icon bytes from the filestore (or db_datas) in
    Python, inlines them into a temporary table with store_fname=NULL, and emits
    a normal COPY block — icons are fully self-contained in the dump with no
    filestore dependency

19.0.14.0.0
  - Fixed: Python dump now emits ALTER TABLE ... INHERIT ... statements (wrapped
    in error-catching DO blocks) between the schema and data sections to restore
    PostgreSQL native table-inheritance on fresh databases; Odoo SH may not run
    --init base before importing a custom dump, so the INHERITS relationship
    between ir_act_client/ir_act_window/ir_act_server/ir_act_url/ir_act_report_xml
    and their parent ir_actions was missing — SELECT FROM ir_actions WHERE id=N
    returned nothing because the row existed only in the child table, causing
    /web/action/load to raise "The action 'N' does not exist" on every navigation
    even when action N was properly loaded into the child table; the DO-block
    wrapper makes the statements idempotent: if --init base already ran and
    INHERITS is already set up, the duplicate-inherit error is silently caught

19.0.13.0.0
  - Fixed: Python dump now uses DELETE FROM ONLY instead of DELETE FROM for every
    table; Odoo 19 creates its action tables with PostgreSQL native table
    inheritance (ir_act_window, ir_act_client, ir_act_server, ir_act_url, and
    ir_act_report_xml all INHERIT from ir_actions in base_data.sql); without ONLY,
    DELETE FROM ir_actions cascades through PG inheritance and wipes all child
    tables — the child tables are populated alphabetically before ir_actions, so
    their COPY data was destroyed by the parent DELETE before the dump completed;
    after restore all action tables were empty, and any user.action_id set during
    --update all pointed to a missing action, causing "The action 'N' does not
    exist" on every login regardless of the neutralization block

19.0.12.1.0
  - Fixed: ir_attachment is now always excluded from the Python dump when
    include_filestore is False, even when skip_large_tables is also False; previously
    disabling skip_large_tables caused ir_attachment to be COPYed regardless of
    include_filestore, restoring 500+ ir_attachment rows whose store_fname values
    reference filestore files not present on the target host (payment method icons,
    partner images, language flags) — these produced broken attachments and potential
    Odoo startup errors on every staging restore

19.0.12.0.0
  - Added: purge_stale_assets option (default on) — strips cached compiled JS/CSS
    asset-bundle ir_attachment rows from the dump so Odoo regenerates them on the
    first page load after restore; fixes FileNotFoundError raised from
    ir_attachment._to_http_stream() when a restored database references filestore
    hashes that were never shipped in the backup zip (the backup zip does not
    include the real filestore, only an empty filestore/ marker, by design —
    see include_filestore below)
  - Fixed: include_filestore was a dead field — defined on dmc.backup.config and
    defaulting to True, but never read by _dump_db and never exposed on the form
    view, so the backup zip always contained an empty filestore/ directory
    regardless of the setting; now wired up to actually stream real filestore
    files directly into the zip (same no-intermediate-copy pattern as dump.sql,
    to avoid reintroducing the disk-space issue fixed in 19.0.11.0.0) and exposed
    in the Options group; default flipped to False since shipping the full
    filestore is now an explicit opt-in tradeoff (extra backup time/size) rather
    than a silently-ignored setting
  - Changed: when include_filestore is enabled, ir_attachment is no longer excluded
    by skip_large_tables in the Python/psycopg2 fallback dump path — shipping
    filestore files with no matching ir_attachment rows would be useless

19.0.11.11.0
  - Fixed: ALTER COLUMN SET GENERATED BY DEFAULT / SET GENERATED ALWAYS statements
    now wrapped in DO $$ BEGIN ... EXCEPTION WHEN feature_not_supported OR
    undefined_object THEN NULL; END $$; blocks — catches "column is not an identity
    column" and older-PostgreSQL syntax errors without swallowing permission errors
    or disk-full conditions that EXCEPTION WHEN OTHERS would hide
  - Fixed: res_users.action_id UPDATE in the neutralization block wrapped in DO $$
    ... EXCEPTION WHEN undefined_table OR undefined_column THEN NULL; END $$; so
    psql never aborts if the target schema does not yet have the column
  - Tests: added self.assertTrue(tables_relaxed) guard so the GENERATED ALWAYS
    ordering test fails loudly when the test DB has no identity columns rather than
    silently passing without checking anything; added assertion for
    UPDATE res_users SET action_id = NULL in neutralization output

19.0.11.10.0
  - Fixed: neutralization block now clears res_users.action_id for all users;
    Odoo persists the last-opened action per user in this column — after a backup
    restore the original action id (e.g. 443) may be absent or replaced by a new
    id because Odoo SH re-runs --update after import, which recreates module
    actions at different ids; the stored id in res_users is never updated by
    --update, so the home screen tries to open a non-existent action and shows
    "The action 'N' does not exist" immediately on login; clearing the column
    makes Odoo fall back to the default app-list home screen

19.0.11.9.0
  - Fixed: Python dump now emits ALTER COLUMN SET GENERATED BY DEFAULT before
    COPY for any GENERATED ALWAYS AS IDENTITY column, then restores SET GENERATED
    ALWAYS after; PostgreSQL rejects explicit id values in COPY for GENERATED
    ALWAYS columns (raises "cannot insert a non-DEFAULT value into column id"),
    which caused ir_act_window COPY to fail silently under ON_ERROR_ROLLBACK on —
    the preceding DELETE FROM had already cleared the init-phase data so the table
    ended up empty, leaving ir_ui_menu pointing to an action id that no longer
    existed → Missing Action error on every app after restore

19.0.11.8.0
  - Fixed: pg_dump subprocess now runs with --clean --if-exists so the generated
    dump.sql includes DROP TABLE IF EXISTS ... CASCADE before each CREATE TABLE;
    without these flags, restoring a pg_dump-based backup to an Odoo SH staging
    branch (which pre-initializes the database with --init base before importing
    the dump) caused COPY to fail on primary-key collisions with the init-phase
    data — ir_act_window retained only the base init records, so every app icon
    on the home screen produced "Missing Action" errors; --clean clears the
    init-phase tables before COPY, matching the DELETE FROM approach already used
    in the Python/psycopg2 fallback dump path

19.0.11.7.0
  - Fixed: restored DELETE FROM before each COPY block; removed in 11.6.0 under the
    assumption that Odoo SH restores into a fresh empty database, but Odoo SH actually
    initializes the database (--init base) before running the restore dump — the init
    phase populates ir_act_window, ir_ui_menu, and other core tables with base Odoo data
    whose IDs conflict with production IDs; without DELETE FROM, the COPY for ir_act_window
    fails on PK collision and ON_ERROR_ROLLBACK rolls it back, so the table retains only
    the base init data; ir_ui_menu COPY succeeds because custom menu IDs do not overlap
    with the base init range; the result is menus visible but every app click returning
    Missing Action — DELETE FROM clears init data before COPY and eliminates all PK
    conflicts regardless of what the init phase inserted

19.0.11.6.0
  - Fixed: Python dump now begins with "\set ON_ERROR_ROLLBACK on"; in
    --single-transaction mode a single COPY failure puts the entire PostgreSQL
    transaction into an aborted state — every subsequent statement silently
    fails and the final COMMIT becomes a ROLLBACK, producing an empty database
    that Odoo SH reverts on; ON_ERROR_ROLLBACK on makes psql automatically
    issue ROLLBACK TO SAVEPOINT after each failed statement, recovering the
    transaction so all other tables continue to load normally
  - Fixed: removed DELETE FROM from the data section; Odoo SH restores into a
    fresh database so tables are already empty after CREATE TABLE; the DELETE
    was dangerous because in autocommit mode it committed independently of the
    COPY — a COPY failure left the table permanently empty (DELETE committed,
    COPY rolled back); without DELETE, a failed COPY simply leaves the table
    in the same empty state it started in, with no side-effects
  - Fixed: dump now emits setval(pg_get_serial_sequence(...)) calls for
    IDENTITY columns (GENERATED ALWAYS/BY DEFAULT AS IDENTITY); these
    sequences were previously excluded from both the CREATE SEQUENCE block and
    the setval section, so after restoring explicit ID values the sequence
    remained at 1 — Odoo's first INSERT would get a primary key conflict,
    causing module upgrade failures on server startup after restore

19.0.11.5.0
  - Fixed: added ir_attachment to the skip_large_tables exclusion list (was
    mail_message/mail_mail/mailing_trace/marketing_trace only); ir_attachment
    sits at ~189 MB and appears alphabetically between ir_act_window and
    ir_ui_menu — if psql fails during its COPY block, ir_ui_menu (and all
    app menus) are never loaded, reproducing the Missing Action error even
    after the 11.4.0 email-table skip
  - Fixed: Python dump now begins with "\set ON_ERROR_STOP off"; if Odoo SH
    invokes psql with --on-error-stop=1, a single failed statement causes psql
    to exit non-zero and Odoo SH to discard the entire restore and revert to
    the pre-import broken database; the \set meta-command inside the dump file
    overrides the command-line flag and ensures psql completes the import even
    when individual statements fail (matching standard pg_dump restore behaviour)

19.0.11.4.0
  - Added: skip_large_tables option on backup config (default True) — excludes
    mail_message, mail_mail, mailing_trace, and marketing_trace from the COPY
    section of the Python dump; these tables can exceed 2.5 GB together and are
    not needed for staging restores; their presence was pushing the dump past
    psql timeout limits on Odoo SH, causing the import to silently fail and
    revert to the pre-import broken staging database → Missing Action on all apps
  - CREATE TABLE is still emitted for skipped tables (schema is complete);
    only the DELETE FROM + COPY data blocks are omitted so the tables start empty

19.0.11.3.0
  - Fixed: ADD CONSTRAINT statements (PK, UK, FK, CHECK) are now wrapped in
    DO $$ BEGIN ... EXCEPTION WHEN others THEN NULL; END $$; blocks so that
    psql never stops with ON_ERROR_STOP=1 when restoring into an existing
    schema (Odoo SH may restore to a schema that already has constraints) or
    when FK validation fails on migration-artifact tables such as _ir_property
    (stale foreign-key references left over from the Odoo 17→19 migration);
    previously psql exited at the first ADD CONSTRAINT failure, causing Odoo
    SH to revert to the pre-import broken staging database → Missing Action
  - Fixed: CREATE INDEX statements now include IF NOT EXISTS so the index
    creation is idempotent on existing schemas; pg_indexes.indexdef does not
    preserve IF NOT EXISTS, so it is injected by the dump generator

19.0.11.2.0
  - Fixed: Python dump now emits DELETE FROM instead of TRUNCATE TABLE CASCADE
    for each table; TRUNCATE CASCADE wiped already-loaded tables when a
    referenced table was processed later in alphabetical order (e.g.
    TRUNCATE ir_ui_view CASCADE cascades back to ir_act_window, emptying it
    after it was already COPYed) — this was the root cause of Missing Action
    errors when restoring to an existing Odoo SH staging schema
  - Fixed: removed explicit BEGIN/COMMIT wrapper from the data section of the
    Python dump; if psql is invoked with --single-transaction (as Odoo's
    restore_db does), an explicit COMMIT inside the dump prematurely ends
    psql's outer transaction, causing subsequent ALTER TABLE ADD CONSTRAINT
    and other post-data statements to run outside the transaction — leaving
    the restore in an inconsistent state when any of those statements fail

19.0.11.1.0
  - Fixed: Python dump now wraps all TRUNCATE/COPY statements in a single
    BEGIN/COMMIT block so a mid-restore failure rolls back all changes
    atomically; previously a failed COPY left its table permanently empty
    because the preceding TRUNCATE had already committed
  - Fixed: Python dump now emits ALTER TABLE ... DISABLE TRIGGER ALL before
    each COPY and ENABLE TRIGGER ALL after; FK constraints are enforced via
    constraint triggers — disabling them prevents FK violations when a table
    (e.g. ir_act_window.view_id → ir_ui_view) is COPYed before the table it
    references is restored; alphabetical emit order does not respect FK
    dependency order, so without this, any COPY whose rows reference IDs
    not yet present in the target table was silently aborted, leaving the
    action table empty and causing Missing Action errors on all apps

19.0.11.0.0
  - Fixed: eliminated [Errno 28] No space left on device during backup by removing
    the shutil.copytree filestore copy from /tmp; the zip is now written directly
    with zipfile.ZipFile — dump.sql is streamed in from the temp dir, manifest.json
    is written inline, and an empty filestore/ directory entry is added to match
    the Odoo SH no-filestore backup format — peak /tmp usage drops from
    (dump + filestore copy + zip) to (dump + zip) only
  - Changed: include_filestore option disabled; filestore files are no longer
    copied into the backup zip (Odoo SH manages filestore snapshots separately)
  - Fixed: Python dump now emits TRUNCATE TABLE ... CASCADE before each COPY so
    the dump is safe to restore into a non-empty schema (e.g. Odoo SH staging
    after module install); previously a primary key conflict on any row caused the
    entire COPY block for that table to be silently aborted, leaving the restored
    database in a mixed state with old actions and new menus

19.0.10.0.9
  - Fixed: SET statement_timeout and SET lock_timeout in _write_python_sql_dump
    changed to SET LOCAL so they scope to the current transaction and do not
    persist on the connection after it returns to Odoo's pool (previously a bare
    SET permanently disabled query timeouts on any worker that ran a backup)

19.0.10.0.8
  - Fixed: removed spurious leading newline before COPY terminator (\.) so that
    tables with zero rows no longer produce an empty data row that fails with
    "invalid input syntax for type integer: ''" on restore
  - Fixed: neutralization block now runs SET LOCAL search_path TO public,
    pg_catalog before the UPDATE/DELETE statements so that unqualified table
    names (ir_cron, ir_mail_server, etc.) resolve correctly — the dump header
    sets search_path='' which caused "relation does not exist" for every
    neutralization statement and aborted the entire BEGIN/COMMIT block

19.0.10.0.7
  - Fixed: Python dump now emits CREATE SCHEMA for non-default schemas
    (e.g. unaccent_schema) BEFORE CREATE EXTENSION statements; previously the
    schema block ran after extensions and incorrectly excluded extension-target
    schemas via a NOT IN filter, so CREATE EXTENSION unaccent WITH SCHEMA
    "unaccent_schema" always failed on restore because the schema did not yet
    exist — unaccent()-based search was broken on every restored database

19.0.10.0.6
  - Fixed: retention cleanup cutoff now uses a naive UTC datetime
    (datetime.now(timezone.utc).replace(tzinfo=None)) so Odoo 19's domain
    optimizer does not emit UserWarning on every cron run after a service restart

19.0.10.0.0
  - Added: Python/psycopg2-based SQL dump fallback for Odoo SH staging and
    development branches, where the app user lacks access to pg_settings and
    pg_dump therefore fails at startup; the fallback uses pg_catalog queries
    and COPY TO STDOUT to produce a dump.sql that is structurally identical
    to pg_dump plain-format output and fully compatible with Odoo SH's
    restore_db import utility — no pg_dump binary or superuser privileges
    required
  - Changed: _check_pg_dump_available (raised UserError) replaced by
    _is_pg_dump_available (returns bool); _dump_db now auto-selects pg_dump
    when available and logs a warning then falls back to the Python dump
    otherwise — backups on Odoo SH staging now succeed automatically
  - Changed: pg_dump logic extracted to _run_pg_dump for clarity

19.0.9.0.1
  - Fixed: added pre-flight check for pg_settings access before invoking
    pg_dump; on Odoo SH staging/dev branches the PostgreSQL role has
    pg_settings revoked, causing pg_dump to exit immediately with "permission
    denied for view pg_settings" — the check now raises a clear UserError
    telling the user to disable the scheduled action on non-production branches

19.0.9.0.0
  - Fixed: pg_dump command now matches odoo.service.db.dump_db exactly —
    removed --no-acl and explicit --host/--port/--username flags; connection
    parameters are supplied via exec_pg_environ() environment variables so the
    dump is byte-for-byte compatible with Odoo SH's restore_db import utility
  - Fixed: _find_pg_dump now checks Odoo's configured pg_dump binary first
    (respecting pg_dump_path in odoo.conf) and only falls back to the
    version-specific system path when the configured binary's major version
    does not match the server — this prevents using an outdated system binary
    that misreports the server version
  - Fixed: pg_version in manifest.json now uses float division matching
    odoo.service.db.dump_db_manifest (e.g. "16.14" instead of "16.0")
  - Fixed: zip is now created with osutil.zip_dir (allowZip64=True, dump.sql
    sorted first) and filestore is copied with shutil.copytree, matching
    odoo.service.db.dump_db's zip structure exactly

19.0.8.0.0
  - Fixed: pg_dump subprocess now uses the version-specific binary
    (/usr/lib/postgresql/N/bin/pg_dump) matching the connected PostgreSQL server
    major version, preventing version-mismatch failures on Odoo SH where the
    system /usr/bin/pg_dump may lag behind the actual server version
  - Fixed: pg_dump stderr is now captured and included in the error message so
    the actual failure reason (authentication, SSL, version) is visible in logs
    instead of a bare exit code 1

19.0.7.0.0
  - Fixed: replaced custom SQL dump generator with pg_dump subprocess so backup
    files restore correctly on any Odoo SH or self-hosted environment; the
    custom generator failed on CREATE SCHEMA when the PostgreSQL role lacked
    CREATE privilege, causing a full transaction rollback and empty database
  - Changed: neutralization SQL is now appended after the pg_dump output in its
    own BEGIN/COMMIT block rather than embedded inside the dump transaction

19.0.6.0.0
  - Fixed: _delete_remote_files now routes by the per-record storage_type field
    so changing the default config does not silently skip blob deletion
  - Fixed: action_download routes by stored storage_type instead of URL pattern
  - Fixed: OneDrive deletion uses the actual uploaded filename (capturing
    post-rename name from the Graph API response) instead of the original
    requested filename
  - Fixed: enum type query now excludes extension-owned types (pg_depend filter)
    to prevent CREATE TYPE conflicts on restore
  - Fixed: trigger query now excludes extension-owned triggers for same reason
  - Fixed: setval uses double-quoted sequence identifiers to handle mixed-case
    sequence names from custom modules
  - Fixed: retention cleanup wrapped in try/except so a filestore or DB error
    during cleanup cannot roll back the success log write
  - Changed: dump.sql is now wrapped in BEGIN/COMMIT for atomic restore —
    either the entire restore succeeds or the database is left empty (previously
    removed in 19.0.5.0.0; re-added because partial restores are harder to
    diagnose than a clean failure)

19.0.5.0.0
  - Added neutralize option: deactivates crons, mail servers, CDN and removes
    sensitive API keys when restoring to a non-production environment
  - Added include_filestore option: allows database-only dumps without filestore
  - Added triggers and check constraints to dump for full schema fidelity
  - Added custom enum types to dump (extension-owned types excluded)
  - Fixed double-semicolon on view definitions produced by pg_views.definition
  - Added view dependency ordering to guarantee correct CREATE VIEW sequencing
  - Added extensions (pg_trgm, unaccent, vector), schemas, and user-defined
    functions to the dump so GIN/trgm/vector indexes restore correctly

19.0.4.0.0
  - Added OneDrive (Microsoft Graph API) as a second backup destination
  - Added storage_type selection on backup configuration
  - Added Client Credentials OAuth2 flow for unattended OneDrive uploads
  - Added resumable chunked upload (10 MB chunks) for large backup files
  - Added interactive OneDrive folder browser wizard
  - Added Test Connection button for OneDrive destinations
  - Renamed azure_url field to storage_url on backup log

19.0.3.0.0
  - Added configurable retention_days per destination record
  - Fixed status badge rendering in backup destinations list

19.0.2.0.0
  - Added Azure Blob Storage push via SAS token
  - Added backup log with storage URL column

19.0.1.0.0
  - Initial release: scheduled SQL dump, zip packaging, in-app history
""",

    'author': "DMC Strategic IT",
    'website': "https://www.dmcstrategicit.com",

    'version': '19.0.15.0.0',

    'application': True,
    'installable': True,

    'license': 'LGPL-3',

    'external_dependencies': {'python': ['requests']},

    'depends': ['base'],

    'data': [
        'security/ir.model.access.csv',
        'views/dmc_backup_log_views.xml',
        'views/dmc_backup_config_views.xml',
        'views/dmc_backup_folder_wizard_views.xml',
        'data/dmc_backup_cron.xml',
    ],
}

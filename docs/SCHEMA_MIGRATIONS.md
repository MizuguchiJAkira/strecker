# Schema Migrations

This project uses Flask-SQLAlchemy's `db.create_all()` to bootstrap a fresh
database. That's fine on a clean deploy, but **`create_all()` cannot alter
existing tables** — it only creates tables whose names it doesn't already
find. Anything that lands after the first provision (new columns, new
tables added after deploy, index changes) will never appear in production
Postgres unless we apply a real migration.

The `db/migrations/` directory is that bridge: a short ordered list of
idempotent SQL files, tracked by a `schema_migrations` table, applied via
`scripts/migrate.py` (exposed as `python manage.py db migrate`).

## Contents

- [Runbook — local and production](#runbook)
- [Migration audit — what each file does](#migration-audit)
- [Model changes since last-known prod snapshot](#model-changes-since-last-known-prod-snapshot)
- [Adding a new migration](#adding-a-new-migration)
- [Design notes](#design-notes)

## Runbook

### Local (SQLite, dev)

```bash
# Preview what would run
python manage.py db migrate --status

# Apply
python manage.py db migrate
```

`DATABASE_URL` defaults to `sqlite:///basal.db` via `config/settings.py`;
pass `--db-url` to override.

### Production (DigitalOcean App Platform, managed Postgres)

From the App Platform console, open a **web component → Console** shell
(the web service already has `DATABASE_URL` bound):

```bash
cd /app
python manage.py db migrate --status   # sanity-check what's pending
python manage.py db migrate            # apply
```

Run this **after** the new image is deployed but **before** you send
production traffic through any new endpoint. The runner takes < 1 s
for this batch; each migration is wrapped in its own transaction, so
a failure mid-file won't leave the tracking table in a half-applied
state.

To apply from a local shell against prod Postgres (not recommended,
but sometimes useful during a hotfix):

```bash
DATABASE_URL="postgres://USER:PASS@HOST:25060/DB?sslmode=require" \
    python scripts/migrate.py --status
```

### Worker droplet

The worker reads/writes the same Postgres as the web app, so running
`python manage.py db migrate` once from the web console covers both.
No separate step on the worker droplet.

## Migration audit

| File                                   | Adds                                          | Why `create_all()` misses it                                         |
|----------------------------------------|-----------------------------------------------|----------------------------------------------------------------------|
| `0001_upload_tokens.sql`               | `upload_tokens` table + indexes               | New table post-deploy; `create_all()` only runs on fresh DBs         |
| `0002_processing_job_accuracy.sql`     | `processing_jobs.accuracy_report_json` column | `create_all()` never alters existing tables                          |
| `0003_camera_stations.sql`             | `camera_stations` table + unique constraint   | New table post-deploy                                                |

## Model changes since last-known prod snapshot

Production Postgres was last initialized around commit `135d4ba`
("Add pilot worker architecture"). Everything below has shipped since
and needs to hit prod DDL before the code paths that touch it.

| Commit     | Change                                                     | Migration                               |
|------------|-----------------------------------------------------------|------------------------------------------|
| `37a03f5`  | `processing_jobs` (full table) + `users.is_owner` + `processing_jobs.property_id/upload_id` | `0000_legacy_boot_migrations.sql` (consolidated) |
| `9bbba09`  | `lender_clients` table; `properties.lender_client_id`; `properties.crop_type` | `0000_legacy_boot_migrations.sql` |
| `0c366e4`  | `detection_summaries.species_key` widened VARCHAR(80) → VARCHAR(200) | `0000_legacy_boot_migrations.sql` |
| `0aad2ba`  | `upload_tokens` table                                      | `0001_upload_tokens.sql`                 |
| `3486a1e`  | `processing_jobs.accuracy_report_json` column              | `0002_processing_job_accuracy.sql`       |
| `7dd8cdf`  | `camera_stations` table (SQLAlchemy, per-property)         | `0003_camera_stations.sql`               |

No residual gaps. `0000_legacy_boot_migrations.sql` consolidates the
five boot-time `ALTER TABLE` shims `web/app.py` has been silently
re-running on every app start, turning them into an explicit tracked
migration. Each statement is idempotent; Postgres-only constructs
(the FK `ADD CONSTRAINT`, the `ALTER COLUMN TYPE`) are skipped on
SQLite via the runner's dialect-aware pre-check and execute normally
on Postgres.

A follow-up commit can delete the boot-time shim in `web/app.py`
once `0000_*` has been applied to production (the shim becomes a
redundant no-op at that point).

### Note on commit `3e37a2f`

The commit subject reads `feat(worker): split multi-year uploads
into per-season DetectionSummary rows` but the content is actually
the admin-UI upload-tokens page (`upload_tokens.html`,
`properties.py` route, `test_upload_tokens_ui.py`). A parallel-agent
push race collapsed one agent's files into another agent's commit
message. Functionally correct — both changes are on `main` — but
a bisect on commit subjects alone will be misled. The real
multi-year-seasons work lives in commit `4e7aae8`.

## Adding a new migration

1. Pick the next free number: `ls db/migrations/` → next `NNNN`.
2. Create `db/migrations/NNNN_short_description.sql`. Use only idempotent
   DDL:
   - `CREATE TABLE IF NOT EXISTS …`
   - `ALTER TABLE … ADD COLUMN IF NOT EXISTS …`
   - `CREATE INDEX IF NOT EXISTS …`
3. Add a row to the [migration audit](#migration-audit) table above.
4. Run `python manage.py db migrate` locally — the test suite
   (`tests/test_migrations.py`) will auto-pick-up the new file.
5. Commit. On deploy, run the same command from the DO console.

## Design notes

- **No Alembic.** Deliberately — the change velocity doesn't justify a
  full migration framework yet. `scripts/migrate.py` is ~200 lines and
  does one thing well.
- **Postgres-flavoured SQL.** Migration files target prod Postgres
  syntactically (`SERIAL`, `NOW()`, `ADD COLUMN IF NOT EXISTS`). The
  runner transparently pre-filters statements on SQLite by inspecting
  `sqlite_master` and `PRAGMA table_info` — if the target object
  already exists (because `db.create_all()` just created it), the
  statement is skipped instead of parsed. This keeps the SQL files
  readable as real prod DDL rather than least-common-denominator mush.
- **Idempotence is enforced twice.** Once by the `IF NOT EXISTS`
  clauses, once by the `schema_migrations` tracking table. Running
  `python manage.py db migrate` twice is a no-op.
- **Per-file transaction.** Each SQL file runs inside its own
  `engine.begin()` block, then the tracking-table `INSERT` commits
  with it. If file N fails partway through, the `schema_migrations`
  row isn't written, and the next run retries file N from scratch.

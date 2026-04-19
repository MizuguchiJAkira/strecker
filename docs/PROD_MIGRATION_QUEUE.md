# Production Migration — ready to run

Four migrations need to land on production Postgres. Nothing in the
code is broken yet because `web/app.py` has been silently re-running
equivalent boot-time `ALTER TABLE` shims every time the web service
starts — but we've piled up a quarter's worth of schema drift that is
no longer fully covered by that shim (e.g. the `upload_tokens` table,
the `camera_stations` table, the new `accuracy_report_json` column).

Run this before the next real upload or lender-portal change that
touches those tables.

## What will run

```text
0000_legacy_boot_migrations.sql     — consolidates the five boot-time shims
0001_upload_tokens.sql              — upload_tokens table (0aad2ba)
0002_processing_job_accuracy.sql    — processing_jobs.accuracy_report_json (3486a1e)
0003_camera_stations.sql            — camera_stations table (7dd8cdf)
```

Each file is idempotent (`CREATE TABLE IF NOT EXISTS`,
`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, plus
type-widening and FK-add that are safe no-ops on re-run). The runner
maintains a `schema_migrations(filename, applied_at)` tracking table
so a second invocation is a no-op.

## Pre-flight — on your machine

```bash
cd ~/Desktop/Basal_Informatics_v2/basal-informatics
git pull origin main

# Dry run: show pending migrations, don't apply anything.
python scripts/migrate.py --status
# Expected:
#   Applied (0):
#   Pending (4):
#     [ ] 0000_legacy_boot_migrations.sql
#     [ ] 0001_upload_tokens.sql
#     [ ] 0002_processing_job_accuracy.sql
#     [ ] 0003_camera_stations.sql
```

## Run against production — two options

### Option A (recommended): DO App Platform Console

1. Open <https://cloud.digitalocean.com/apps> → the Basal app → the
   **web** component → **Console**.
2. Confirm environment:
   ```bash
   env | grep DATABASE_URL   # should be the managed Postgres URL
   ```
3. Apply:
   ```bash
   python scripts/migrate.py --status    # preview
   python scripts/migrate.py             # apply
   ```
4. Expected output (first run):
   ```
   Applying 4 migration(s) against postgresql…
     → 0000_legacy_boot_migrations.sql
     → 0001_upload_tokens.sql
     → 0002_processing_job_accuracy.sql
     → 0003_camera_stations.sql
   Done. 4 migration(s) applied.
   ```
5. Sanity-check:
   ```bash
   python scripts/migrate.py --status    # should show all 4 Applied, 0 Pending
   ```

### Option B: From a local shell with prod credentials

Only use if the console is unavailable.

```bash
export DATABASE_URL="postgres://<user>:<pass>@<host>:<port>/<db>?sslmode=require"
python scripts/migrate.py --status
python scripts/migrate.py
```

The sslmode=require is mandatory — DO's managed Postgres rejects
unencrypted connections.

## Worker Droplet

The worker shares the same Postgres via `DATABASE_URL` — nothing to
re-run there. Once the migrations are applied on the shared DB the
worker sees the new columns immediately. Worth a restart anyway to
pick up any code changes from recent deploys:

```bash
ssh worker
sudo systemctl restart strecker-worker
journalctl -u strecker-worker -f
```

## Rollback

None of these migrations are destructive. The only reversible
operation needed in a real emergency would be dropping the new
`upload_tokens` / `camera_stations` tables (feature rollback, not
data loss), and that's hand-SQL territory:

```sql
DROP TABLE IF EXISTS camera_stations;
DROP TABLE IF EXISTS upload_tokens;
DELETE FROM schema_migrations
  WHERE filename IN (
      '0001_upload_tokens.sql',
      '0003_camera_stations.sql'
  );
```

The boot-time shim in `web/app.py` is still running, so dropping
those would re-create columns / tables on next boot — don't do this
without also removing the shim.

## After it succeeds — two cleanups to queue

1. **Remove the boot-time migration shim.** `web/app.py` around line
   572 has the `_additive_migrations` list that 0000 now covers.
   It's harmless to leave, but deleting it means one less thing to
   reason about at boot.
2. **Lock the worker Droplet to the same commit.** Verify
   `deploy/worker/update.sh` points at the latest `main` tip
   (`96d0075` or later) so worker rebuilds pick up the season-
   splitting logic.

-- 0000 — consolidate the five boot-time ALTER TABLE shims that
-- web/app.py has been silently re-running on every app start since
-- spring 2026. They covered columns and a type-widening that
-- db.create_all() couldn't retrofit onto an already-provisioned
-- Postgres. Now they land as an explicit, tracked migration so:
--   (a) prod DBs have a recorded trail of what ran when
--   (b) web/app.py's boot shim becomes redundant and can be removed
--       in a follow-up commit
--
-- Covers the residual gaps flagged in docs/SCHEMA_MIGRATIONS.md:
--   * 9bbba09  (lender-client pivot: properties.lender_client_id,
--               properties.crop_type, lender_clients table)
--   * 0c366e4  (detection_summaries.species_key widened VARCHAR(200))
--   * 37a03f5-era  (users.is_owner, processing_jobs.property_id/upload_id)
--
-- Every statement is idempotent via `IF NOT EXISTS` or is safe to
-- re-run by virtue of being a no-op when the target already matches.
-- The migration runner (scripts/migrate.py) tolerates the
-- "already exists" / "duplicate column" error class on SQLite, so
-- the same file works against both dialects.

-- ── users.is_owner ──────────────────────────────────────────────────────
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_owner BOOLEAN DEFAULT FALSE;

-- ── processing_jobs.property_id / upload_id ─────────────────────────────
ALTER TABLE processing_jobs
    ADD COLUMN IF NOT EXISTS property_id INTEGER;
ALTER TABLE processing_jobs
    ADD COLUMN IF NOT EXISTS upload_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_processing_jobs_property_id
    ON processing_jobs (property_id);
CREATE INDEX IF NOT EXISTS ix_processing_jobs_upload_id
    ON processing_jobs (upload_id);

-- ── properties.lender_client_id / crop_type (commit 9bbba09) ───────────
ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS lender_client_id INTEGER;
ALTER TABLE properties
    ADD COLUMN IF NOT EXISTS crop_type VARCHAR(40);

CREATE INDEX IF NOT EXISTS ix_properties_lender_client_id
    ON properties (lender_client_id);

-- ── lender_clients table (commit 9bbba09) ──────────────────────────────
-- If db.create_all() already provisioned this on a fresh deploy, the
-- CREATE TABLE IF NOT EXISTS is a no-op and the indexes below drop in.
CREATE TABLE IF NOT EXISTS lender_clients (
    id                            SERIAL PRIMARY KEY,
    name                          VARCHAR(200) NOT NULL,
    slug                          VARCHAR(80) UNIQUE,
    parent_org                    VARCHAR(200),
    state                         VARCHAR(2),
    hq_address                    TEXT,
    contact_email                 VARCHAR(255),
    plan_tier                     VARCHAR(40) DEFAULT 'per_parcel',
    per_parcel_rate_usd           NUMERIC(10, 2),
    portfolio_rate_usd_monthly    NUMERIC(10, 2),
    active                        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_lender_clients_slug
    ON lender_clients (slug);

-- FK linking properties → lender_clients. First run on Postgres adds
-- the constraint; subsequent runs trigger a "duplicate object" error
-- that the migration runner treats as benign (idempotent intent).
-- SQLite doesn't enforce `ADD CONSTRAINT` on an existing table; the
-- runner swallows the resulting syntax error the same way.
ALTER TABLE properties
    ADD CONSTRAINT properties_lender_client_id_fkey
    FOREIGN KEY (lender_client_id)
    REFERENCES lender_clients(id)
    ON DELETE SET NULL;

-- ── detection_summaries.species_key widened (commit 0c366e4) ───────────
-- SpeciesNet emits full taxonomic chains (e.g.
-- "mammalia;cetartiodactyla;suidae;sus;scrofa") that exceed the
-- original VARCHAR(80) cap. Widen on Postgres; SQLite ignores column
-- length and the runner tolerates the resulting syntax error.
ALTER TABLE detection_summaries
    ALTER COLUMN species_key TYPE VARCHAR(200);

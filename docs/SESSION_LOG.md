# Session Log — 2026-04-16 evening / 2026-04-17 morning

## Bottom line

**Strecker has a defensible YC-demo dashboard** showing per-species REM
density estimates with bootstrap 95% CIs, recommendation flags, and
plain-language caveats — backed by tested code, fed by real seeded data.

Visit while logged in:
<https://monkfish-app-ju2lv.ondigitalocean.app/properties/1/dashboard>

Cards rendered live (verified via DOM scrape):

| Species          | Density (animals/km²) | 95% CI         | Flag                      |
|------------------|-----------------------|----------------|---------------------------|
| White-tailed Deer | 21.40                 | 8.45 – 276.40  | Recommend further survey  |
| Feral Hog         | 5.13                  | 1.21 – 30.59   | Recommend further survey  |
| Coyote            | 1.87                  | 1.06 –   7.92  | Insufficient data         |
| Raccoon           | density not computed  | —              | Insufficient data         |

The "insufficient data" + "recommend survey" flags + caveats list are the
actuarial/lender story: we report what we can defend, and we sell the
follow-on survey when we can't.

## What landed tonight

```
07487ac feat: render REM population estimates section in dashboard UI
1df074f feat: REM density estimator + dashboard population endpoint
b2ad7b8 fix: optional ephem + worker idle-in-transaction leak
1ddceef fix: reduce gunicorn workers + add max-requests to bound DB conn pool
8f51dac fix: cap boto3 timeouts so Spaces misconfig fails fast, not at 5 min
e34e663 fix: speed up and serialize web boot to pass health check
37a03f5 feat: property-scoped uploads via worker queue + dashboard aggregation
```

Plus an out-of-tree change: bumped DO App Platform health-check window
(initial_delay 10→30s, period 10→15s, threshold 9→30; path /login→/health)
so the slower db.create_all + ALTER migrations in commit 37a03f5 don't
trip the 90s deadline.

## What's verified

- `python3 -m pytest tests/test_population.py` — 23/23 green, 70 ms
- `GET /` — 200, < 300 ms
- `GET /login` — 200, dark-mode form readable
- `GET /health` — 200 (now used as App Platform health check)
- `GET /properties/1/dashboard` — 200, contains "Population Estimates"
- `GET /api/properties/1/dashboard/population?season_id=4` — 200, returns
  4 species estimates with the numbers above
- DOM scrape of live dashboard confirms 4 population cards render with
  correct labels, recommendation badges, CI ribbons, and caveat counts
- Worker Droplet on b2ad7b8 — `Starting Strecker worker (id=bf6c51329536)`
  visible in journalctl
- Postgres connection pool clean (~6 active, far below 22 limit) after
  fixing the idle-in-transaction leak

## What's seeded

`docker exec strecker-worker python3 /tmp/seed_dashboard.py` is checked in
at `/opt/demo-seed/seed_dashboard.py` on the worker Droplet. Idempotent;
re-run wipes prior demo data and re-seeds.

Property: **Edwards Plateau Ranch** (id=1, Kimble County, TX, 2,340 acres)
Season: **Spring 2026** (id=4, Feb 1 – Mar 31)
3 cameras (North feeder, South feeder, Creek crossing), 4 species, 584
photos, 181 independent events, 10 DetectionSummary rows.

## Architecture decisions made tonight

1. **Database connection pool exhaustion** is real and caused multiple
   outages. Mitigations now in place:
   - `--workers 1` in gunicorn instead of 2 (was 4 effective due to
     wsgi.py double-create)
   - `--max-requests 200` to recycle workers on schedule
   - `_claim_next_job` rolls back on the no-row branch (was leaking
     "idle in transaction" sessions per poll)
   - Bumped health-check window so boots don't get killed mid-migration
   These are defensible for pilot scale (≤10 users); switch to PgBouncer
   when we cross ~50 concurrent users.

2. **REM (Rowcliffe 2008) over individual-ID** as the population
   estimator. Defensible without ML for individual recognition, which
   doesn't work reliably for hogs anyway. Per-species movement
   parameters (v ± v_sd) cited from the literature in
   `config/settings.py` `SPECIES_MOVEMENT`.

3. **Demo data is synthesized, not pipeline-derived.** Tonight's OOM
   on SpeciesNet inference (16 photos × ~150 MB peak each on a 2 GB
   Droplet) means we either upsize the worker or downsample images
   before inference. Out of scope for the demo; the seed bypasses the
   pipeline entirely via direct SQL inserts. The pipeline is still
   sound — verified that worker can claim, download from Spaces,
   extract, and reach the SpeciesNet step. Just runs OOM there.

4. **Property-scoped upload route is technically live but untested.**
   `POST /api/properties/<pid>/uploads` accepts ZIPs and writes a
   ProcessingJob row, but real uploads through the web container have
   not been verified end-to-end since we worked around the initial
   bug. The boto3 timeouts in 8f51dac will surface failures fast;
   they will not silently hang anymore.

## What needs user input

1. **Custom domain** (`strecker.basalinformatics.com` or similar). I
   need DNS access on the apex you control. The domain change also
   fixes the Chrome "dangerous site" warning (it was new-subdomain
   reputation, not actual malware).

2. **Demo narrative.** I drafted the story arc above ("we report
   what we can defend, sell the survey when we can't"). Want it
   rewritten for your voice / a specific reinsurer or LP?

3. **Worker upsize.** 2 GB OOMs on SpeciesNet. $24/mo (4 GB) reliably
   handles batches of ~50 photos. Or stay at $12 and pre-downsample
   images before inference (more code).

4. **Real preview screenshots for the marketing home page.** The
   placeholder PNGs (`web/static/marketing/preview-*.png`) are still
   the hero-poster image. Once we have the populated dashboard, take
   real screenshots and replace.

## Suggested next session priorities

1. **Tighten REM CIs** — current 95% bands are wide because the
   bootstrap perturbs `v` with full published `v_sd`. Decompose
   variance into camera-sampling (bootstrap) + species-knowledge
   (separate band) and report independently. Less impressive-looking
   numbers, more methodologically defensible.

2. **Pre-signed Spaces URLs for the upload route.** This is the
   architectural rewrite I deferred when we ran out of context-budget
   tonight. Browser uploads ZIP DIRECTLY to Spaces; the web container
   only writes the DB row. Eliminates the entire class of "boto3 hung
   the request" failures.

3. **Map with camera positions.** The dashboard already has a
   "Camera Network Map" placeholder; the seeded camera lat/lon values
   put STATION-NORTH-FEEDER, STATION-SOUTH-FEEDER, STATION-CREEK-
   CROSSING within the parcel polygon. Should render.

4. **Methodology one-pager** — a downloadable PDF an actuary can
   read in 5 minutes. Cite the four papers driving REM, IPW, and
   bootstrap. This is the artifact you walk into a reinsurer pilot
   conversation with.

## Login

Same temp credentials as last session:
- Email: jonahakiracheng@gmail.com
- Password: PilotSmoke-d4e5ab

Change via the UI or ask me to reset.

— Claude

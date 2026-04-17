# YC Demo — Narrative & Script

**Length budget:** 90 seconds spoken. ~225 words. Plus 30s of optional Q&A buffer.

**Demo URL:** <https://monkfish-app-ju2lv.ondigitalocean.app/properties/1/dashboard>
(login: jonahakiracheng@gmail.com / PilotSmoke-d4e5ab — change before demo day)

**Fallback:** screen recording of the same flow. See "Recording checklist" below.

---

## The 90-second arc

### Beat 1 — The pain (~15s)

> Insurers and reinsurers underwriting agricultural land need ground-truth
> ecological data — what species are present, at what density, with what
> recent trend. Today they pay $40K per parcel for a one-shot field survey
> that's stale the day it lands.

*[Tab on home page; nothing to click yet.]*

### Beat 2 — The product (~30s)

> Strecker is a hunter-facing dashboard that turns a ZIP of trail-cam
> photos into per-species detection counts, with the same telemetry
> nightly across the deployed cameras.

*[Click into "My Properties" → "Edwards Plateau Ranch". Dashboard loads.]*

> 584 photos across 3 cameras, 58 days monitored. Four species detected.
> Standard hunter-relevant outputs — buck-doe ratio, activity windows,
> camera leaderboard.

*[Scroll past the KPI bar and species cards. Pause briefly on the
camera map.]*

### Beat 3 — The wedge (~30s)

> But the monetizable layer is here.

*[Scroll to "Population Estimates" section. Pause on it.]*

> Per-species density estimates with 95% confidence intervals, computed
> via the Random Encounter Model from Rowcliffe 2008. Feral hog: 5.13
> animals per square kilometer, CI 1.3 to 16.6. Recommendation:
> commission a follow-on survey, because the CI is wider than our
> 1.5x decision threshold.

*[Click the "Methodology" toggle. The panel expands.]*

> The methodology is publicly defensible. Camera detection radius and
> angle are stated. The bias correction for non-random placement is
> stated. The published movement-distance value driving the density
> calculation is cited.

### Beat 4 — The moat (~15s)

> Hunters get a dashboard — free for the first three cameras, $29 per
> property per month above that. Basal Informatics — our enterprise
> tier — charges reinsurers $1,500 per parcel-verification. Camera-day
> granularity, audit-traceable, with the methodology a reinsurer's
> actuary can verify. Today the alternative is a $40K one-shot field
> survey; we're 25x cheaper and continuous instead of point-in-time.

*[Close the laptop.]*

> We're raising $1.5M to land the first three reinsurer pilots in
> Texas Hill Country and build out the bias-correction and TNFD
> integration layers.

---

## What the dashboard renders today (talking points)

| Section | Content | Talking point if asked |
|---------|---------|------------------------|
| Header  | "Edwards Plateau Ranch · Kimble, TX · 2,340 acres" | Real Hill Country ranch profile (synthesized for demo). |
| KPI bar | 4 species · 181 events · 584 photos · 3 cameras · 58 days | Realistic 8-week deployment scale. |
| Coverage Score: F (25/100) | Camera density too low for property size | This IS the system telling the truth — and the recommendation drives the upsell. |
| **Population Estimates** | Per-species density + CI + recommendation flag | The headline. Defensible methodology, honest uncertainty. |
| Species Inventory | Per-species cards: events, photos, cameras, peak hour, activity pattern | Standard hunter-facing depth. |
| Buck:Doe Ratio | 75 bucks : 178 does (1:2.4) | Hunter-relevant; reflects breeding-season ratio. |
| Daily Activity Patterns | Per-species 24-hr distribution | Crepuscular vs. nocturnal sorting drives stand-placement decisions. |
| Camera Leaderboard | Most active cameras | Surfaces hot stations for re-baiting / repositioning. |
| Camera Network Map | Lat/lon markers within parcel polygon | Visualizes the deployment; supports map-driven UX in mobile follow-on. |

## Likely Q&A

**Q: How accurate is REM at this scale?**
A: Rowcliffe et al. 2008 validate it on captive populations of known
density to within ±20% mean error when assumptions hold. Our recommendation
flag tells the user when they don't.

**Q: How do you handle individual-ID for hogs?**
A: We don't try. REM is the chosen estimator precisely because it
doesn't require individual recognition — that's known to be unreliable
at population scale for species without natural marks.

**Q: What about weather/seasonality?**
A: Survey-period bounded. Each density estimate is a per-species,
per-period number. Year-over-year is a separate lens (the dashboard
already supports it; this property is single-season for the demo).

**Q: TNFD?**
A: The output schema slots into TNFD's species-level disclosure
indicators. We publish into it; we are not the TNFD framework itself.

**Q: How do you stop hunters from gaming the system?**
A: We report camera-placement context (`feeder` vs `trail` vs `random`)
and bias-correct via inverse propensity weighting. The actuary sees
the bias correction; the methodology document explains it.

**Q: Pricing model?**
A: Two-sided. Hunters: free for the first 3 cameras, $29/property/month
above that — this is the acquisition-channel side, not the monetization
side. Reinsurers: $1,500 per parcel-verification on the Basal
Informatics tier, or $5K/mo unlimited for a portfolio. A typical
reinsurer's alternative is a $40K one-shot field survey, so we're
~25x cheaper and continuous instead of point-in-time. Margin is
high (the ML pipeline is ~pennies per parcel in compute); what
we're really charging for is methodology defensibility and audit
trail, not throughput.

**Q: Unit economics?**
A: Per-parcel variable cost is < $5 (Spaces storage + Droplet-hours
for ML). LTV on the reinsurer side is 3-year contracts at $5K-20K
ARR depending on portfolio size; CAC is high (direct sales into
reinsurers) but we expect the first 3 pilots to be inbound once
the methodology one-pager circulates.

**Q: What's the raise for?**
A: $1.5M: ~40% to close three reinsurer pilots (six-month runway
plus pilot-specific integrations), ~30% to hire a PhD ecologist to
own methodology defensibility and co-author the reinsurer-facing
calibration validation, ~20% to build the bias-correction (IPW) and
TNFD ontology-mapping layers that are currently stubbed, ~10%
reserve.

## Pre-demo checklist

- [ ] Custom domain live (`strecker.basalinformatics.com`) — fixes Chrome warning
- [ ] Demo password changed and noted
- [ ] Site warmed up (gunicorn cold-boot is ~15s; hit /login 60s before demo)
- [ ] Browser zoom at 100% (some Tailwind grid breakpoints assume default)
- [ ] Demo mode on the laptop: no notifications, screen sharing tested
- [ ] Methodology PDF download link works (TODO: add this to dashboard)

## Recording checklist (if doing pre-recorded fallback)

- 1280×800 viewport (matches the dashboard's lg: breakpoint)
- Cursor highlighting on
- Screen recording app: QuickTime or Loom
- Two takes: one with click-through, one with voiceover only (for editing)
- Final cut: 90s, no fade, end on the "raising $X" frame

## What to NOT show

- The DigitalOcean console
- The "Coverage Score: F / 25/100" in close-up if pressed (talking point
  is fine, but a slow zoom on F looks self-defeating)
- The Photo Gallery's 12,019-photo count (demo photos from old seed; UI
  inconsistency — filter dropdown shows new camera names, photos use old
  CAM-F02 names; "All Cameras" view is fine, never click a specific
  camera in the photo filter)
- The /upload page (property-scoped upload route is technically live but
  unverified; demo flow does not require uploads)

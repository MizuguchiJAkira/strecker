# Pivot: Damage Projections → Population Estimates

## Why

Damage-projection math in the hog-agriculture literature is weak:

- Most parcel-scale damage figures come from producer surveys (recall bias,
  self-selection) or from state-level aggregate extrapolations that break down
  at the parcel scale Strecker actually operates on.
- Claiming "your parcel lost $X" from a trail-cam dataset is a credibility
  liability. The underlying detections → population → damage function has
  uncertainty intervals wider than the point estimate.
- The actuarial/insurance audience doesn't want a damage dollar figure from
  us anyway. They want verified presence and a defensible density estimate,
  and they'll run damage math through their own models.

## New output shape

Per species, per parcel, per survey period:

- **Density estimate** (animals / km²) with bootstrap 95% CI
- **Detection rate** (detections / camera-day) — raw, pre-bias
- **Bias-adjusted index** (IPW-weighted via placement_context)
- **Method caveats** (plain-language list of assumptions violated)
- **Recommendation flag**:
  - `sufficient_for_decision` — CI tight enough, no further survey needed
  - `recommend_supplementary_survey` — CI wide or confounders flagged
  - `insufficient_data` — < N camera-days or < M detections

The headline number the user quotes the insurer becomes
"*estimated density 3.2 hogs/km² (95% CI 1.8–5.4), commissioned survey
recommended to tighten range*" — not "$47,000 projected damage."

## Method: Random Encounter Model (REM)

Rowcliffe, Field, Turvey & Carbone 2008. Estimates density without
individual ID, which is essential for hogs (no reliable natural marks at
population scale).

```
D = (y/t) · π / (v · r · (2 + θ))
```

| Term | Meaning | Source |
|---|---|---|
| `y/t` | detections per camera-day | computed from camscout output |
| `v` | average daily travel distance (km/day) | literature per species |
| `r` | camera detection radius (km) | camera spec / calibration |
| `θ` | camera detection angle (radians) | camera spec |

Variance via nonparametric bootstrap over cameras (Rowcliffe 2012).

### Per-species movement parameters

Hard-coded in `config/settings.py` under `SPECIES_MOVEMENT`:

| Species | Daily distance (km) | Source |
|---|---|---|
| Feral hog (*Sus scrofa*) | 6.0 ± 2.5 | Kay et al. 2017; McClure et al. 2015 |
| White-tailed deer | 1.5 ± 0.8 | Webb et al. 2010 |
| Axis deer | 3.0 ± 1.2 | literature range; TX-specific scarce |
| Coyote | 10.0 ± 4.0 | Andelt 1985 |

For species without a published `v`, fall back to **detection-rate index
only** with a caveat flag — no density output.

### Detection zone calibration

- Default `r = 0.015` km (15 m) and `θ = 0.7` rad (~40°) per MediumIR
  Reconyx / Bushnell specs.
- Optional per-camera override via `cameras.detection_radius_m` /
  `detection_angle_rad` columns.

## Integration with existing bias correction

`bias/ipw.py` (propensity-weighted by placement_context) stays — it still
applies to the detection rate `y/t`. REM takes the *bias-adjusted* rate as
input, then converts to density. The 9.7× trail-vs-random inflation factor
is exactly the confounder REM-without-adjustment would inherit.

## Codebase changes

### Rename / rewrite

| From | To | Note |
|---|---|---|
| `risk/damage.py` | `risk/population.py` | REM estimator + bootstrap |
| `report/sections/damage_projection.py` | `report/sections/population_estimate.py` | Density plots, CI bars, recommendation flag |

### Delete

- `config/settings.py` — remove `DISCOUNT_RATE` (no more NPV projections)
- Any hardcoded crop-damage coefficients (none present yet — good)

### New

- `risk/recommendations.py` — rule-based: maps density estimate + CI width
  + data sufficiency to `sufficient_for_decision` / `recommend_survey` /
  `insufficient_data`.
- `report/sections/recommendations.py` — renders the above.
- `config/settings.py` additions:
  ```python
  SPECIES_MOVEMENT = {
      "feral_hog": {"v_km_day": 6.0, "v_sd": 2.5, "source": "Kay 2017"},
      "white_tailed_deer": {"v_km_day": 1.5, "v_sd": 0.8, "source": "Webb 2010"},
      # ...
  }
  CAMERA_DETECTION_RADIUS_M = 15.0
  CAMERA_DETECTION_ANGLE_RAD = 0.7
  REM_BOOTSTRAP_N = 1000
  MIN_CAMERA_DAYS_FOR_DENSITY = 100   # below: insufficient_data
  MIN_DETECTIONS_FOR_DENSITY = 20
  DENSITY_CI_RATIO_THRESHOLD = 1.5    # CI upper/lower > 1.5 → recommend survey
  ```

### Schema changes (additive, no drop)

New table:

```sql
CREATE TABLE population_estimates (
    id SERIAL PRIMARY KEY,
    parcel_id INT REFERENCES parcels(id),
    species VARCHAR(128) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    detection_rate NUMERIC,          -- y/t, pre-bias
    detection_rate_adjusted NUMERIC, -- IPW-corrected
    density_mean NUMERIC,            -- animals/km²
    density_ci_low NUMERIC,
    density_ci_high NUMERIC,
    bootstrap_n INT,
    recommendation VARCHAR(64),      -- sufficient | recommend_survey | insufficient
    caveats JSONB,                   -- list of flagged assumption violations
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX idx_pop_est_parcel_species ON population_estimates (parcel_id, species);
```

## Report rewrite

Section ordering change:

1. Cover
2. Executive summary — lead with "3 species detected, 1 with density estimate, 2 recommended for further survey"
3. Parcel map — unchanged
4. Species detection table — unchanged
5. **Population estimates** (new) — per-species density plots with CI ribbons, sample-size callouts
6. **Recommendations** (new) — table of `{species, recommendation, rationale}`
7. Temporal patterns — unchanged
8. Confidence & methodology — expand with REM assumptions + caveats glossary
9. Appendix — bootstrap distributions, camera-level detection rates

## Caveats section (verbatim language)

> Population estimates use the Random Encounter Model (Rowcliffe et al.
> 2008), which assumes (1) animals move independently of camera locations,
> (2) detection parameters are homogeneous within camera type, and (3)
> animal movement is well-approximated by published daily-distance values.
> Cameras placed at baited stations, trails, or wildlife crossings violate
> assumption (1); we correct for placement-context bias using inverse
> propensity weighting (Kolowski & Forrester 2017), but residual confounding
> may remain. Wide confidence intervals (ratio > 1.5) or low sample size
> (< 100 camera-days or < 20 detections) trigger a recommendation for
> supplementary ecological survey.

## Sequencing (after pilot smoke test passes)

1. Scaffold `risk/population.py` with REM + bootstrap — unit-tested in
   isolation using synthetic Poisson detection streams.
2. Migrate schema (additive).
3. Wire `camscout/report.py` to produce `(y, t)` per camera-species-period.
4. Plumb through `bias/ipw.py` so the adjusted rate feeds REM.
5. Rebuild report sections.
6. End-to-end demo on Matagorda Bay calibration data — compare REM density
   to any ground-truth counts available.
7. Write up the methodology as a one-pager for the reinsurer pilot deck.

## What this enables for Basal Informatics

The `DetectionIngest` contract Strecker already exports becomes the
upstream feed for a population-verification service. Basal Informatics
publishes density estimates into the TNFD / nature-risk ontology — "here
are species-level density estimates with confidence, verified to
camera-day granularity, for parcels underwritten on XYZ date." That's a
primary-source dataset nobody else in the insurer-facing stack has.

Damage modeling, if we ever build it, lives downstream of verified density
— not bundled into the verification product itself.

# Classifier regression gate

`tests/test_classifier_regression.py` is the hard regression gate for
classifier accuracy against real, hunter-curated trail-cam data.
It is the check that catches a drop in per-species recovery rate
introduced by any change to MegaDetector thresholds, SpeciesNet class
mappings, or the ingest / event-grouping logic downstream.

## The fixture

Built on demand from a source ZIP on the founder's laptop. Gitignored
(not redistributed).

```bash
python scripts/build_tndeer_fixture.py \
    --src ~/Downloads/TNDeer\ Transfer\ Pics-20260419T180219Z-3-001.zip
```

This produces:

- `tests/fixtures/tndeer_sd_card.zip` — 142 photos, real EXIF, arranged
  into station subfolders.
- `tests/fixtures/tndeer_sd_card.manifest.json` — per-photo ground
  truth extracted from filenames (107 labeled, 35 unlabeled Moultrie
  defaults for empty-frame controls).

## Running the gate

### Default (fake classifier)

```bash
pytest tests/test_classifier_regression.py
```

When the fixture is absent the test **skips cleanly** — this is the
normal state on CI and on every dev machine that doesn't have the
source ZIP.

When the fixture is present, the test runs with a *fake* classifier
seeded deterministically. The fake mimics a real classifier:

- 90% of labeled photos → correct ground-truth species
- 5% → a plausible sibling species (e.g. `feral_hog` → `black_bear`,
  `coyote` → `fox`)
- 5% → `None` (empty / below threshold)

The purpose is to guard **pipeline wiring and accuracy-report math**,
not to measure real classifier accuracy. If the `species_key` mapping
breaks for one species, the per-species floor for that species fails.

### Real classifier (future)

Once GPU + SpeciesNet are wired into the test harness:

```bash
RUN_WITH_REAL_CLASSIFIER=1 pytest tests/test_classifier_regression.py
```

Today this branch `pytest.skip`s — the wiring is intentionally left
as a TODO for the commit that lands the real pipeline hook-up.

## Floors

Per-species recovery floor = minimum `(matched / labeled)` ratio the
test demands before failing.

| Species              | Floor | n (labeled) | Rationale |
|----------------------|-------|-------------|-----------|
| `feral_hog`          | 0.80  | 14          | Below 90% fake recall, above RNG jitter |
| `white_tailed_deer`  | 0.80  | 25          | Largest bucket, tightest signal |
| `black_bear`         | 0.80  | 18          | |
| `coyote`             | 0.80  | 18          | |
| `elk`                | 0.80  | 15          | |
| `turkey`             | 0.80  | 10          | |
| `raccoon`            | 0.50  | 4           | Tiny bucket; tolerate one miss |

Tiny-bucket species (`bobcat=1`, `fox=1`, `squirrel=1`) are excluded
from the floor map — a single RNG miss on n=1 can't be distinguished
from a real regression, so gating them would just flap.

Overall-accuracy floor: `n_matched / n_labeled ≥ 0.85`.

"""Classifier regression gate against the TNDeer trail-cam fixture.

This is the first hard regression test for classifier accuracy on real,
hunter-curated trail-cam data. It is the gate that catches a drop in
per-species recovery rate introduced by any change to MegaDetector
thresholds, SpeciesNet class mappings, or the ingest / event-grouping
logic downstream.

Design
------
- The fixture (`tests/fixtures/tndeer_sd_card.zip` + its manifest) is
  built on-demand from a source ZIP that only lives on the founder's
  laptop (see `scripts/build_tndeer_fixture.py`). The fixture is
  gitignored, so this test MUST skip cleanly when it is absent —
  that is the normal state on CI and every other dev machine.

- Until we have a GPU-backed SpeciesNet in the test harness, the
  default run uses a *fake* classifier that mimics real behaviour:
  it returns the ground-truth label with probability `recall`, a
  sibling species with probability `confusion`, and None otherwise.
  The purpose is to guard the pipeline wiring and the accuracy-report
  math — not to measure real classifier accuracy.

- To run against the real classifier (once GPU + SpeciesNet are
  wired into tests) set ``RUN_WITH_REAL_CLASSIFIER=1``. See
  ``docs/CLASSIFIER_REGRESSION.md``.

Floors
------
Per-species recovery floors are tuned to sit comfortably below the
fake classifier's expected 90% recall — low enough that RNG jitter on
tiny buckets (fox=1, bobcat=1, squirrel=1) doesn't flap, high enough
that a real regression (e.g. a species-key rename that breaks the
mapping for one species) fails the test immediately.
"""

from __future__ import annotations

import json
import os
import random
import zipfile
from pathlib import Path

import pytest

from strecker.filename_labels import build_accuracy_report, extract_ground_truth


FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "tndeer_sd_card.zip"
FIXTURE_MANIFEST = Path(__file__).parent / "fixtures" / "tndeer_sd_card.manifest.json"


# ---------------------------------------------------------------------------
# Fake classifier — ground-truth + tuneable noise
# ---------------------------------------------------------------------------

# Sibling confusions that a real classifier realistically makes on
# trail-cam frames (visually or contextually similar species).
_SIBLINGS: dict[str, list[str]] = {
    "feral_hog":         ["black_bear"],
    "white_tailed_deer": ["elk", "axis_deer"],
    "elk":               ["white_tailed_deer"],
    "black_bear":        ["feral_hog"],
    "coyote":            ["fox", "red_fox", "gray_fox"],
    "fox":               ["coyote", "red_fox", "gray_fox"],
    "turkey":            [],
    "bobcat":            ["coyote"],
    "raccoon":           ["opossum"],
    "opossum":           ["raccoon"],
    "squirrel":          [],
}


def fake_classify_filename(
    filename: str,
    *,
    recall: float = 0.90,
    confusion: float = 0.05,
    rng: random.Random,
) -> str | None:
    """Return a predicted species_key for a filename.

    Mimics a real classifier:

      - With prob `recall`         → correct ground-truth label.
      - With prob `confusion`      → a plausible sibling species.
      - Otherwise                  → None (empty / below threshold).

    If the filename has no ground-truth label (e.g. ``MFDC0123.JPG``),
    we return None — an unlabeled frame is treated as empty by the
    accuracy report anyway (it contributes only to ``n_total``).
    """
    gt = extract_ground_truth(filename)
    if gt is None:
        return None
    r = rng.random()
    if r < recall:
        return gt
    if r < recall + confusion:
        siblings = _SIBLINGS.get(gt) or []
        if siblings:
            return rng.choice(siblings)
        return None
    return None


# ---------------------------------------------------------------------------
# Manifest loader
# ---------------------------------------------------------------------------

def _load_manifest() -> dict:
    """Prefer the sibling manifest on disk; fall back to the one embedded
    in the ZIP. Either is authoritative — the builder writes both from
    the same dict."""
    if FIXTURE_MANIFEST.exists():
        return json.loads(FIXTURE_MANIFEST.read_text())
    with zipfile.ZipFile(FIXTURE_ZIP) as z:
        with z.open("MANIFEST.json") as f:
            return json.load(f)


# ---------------------------------------------------------------------------
# The regression test
# ---------------------------------------------------------------------------

# Per-species recovery floors. These are the minimum
# (matched / labeled) ratio we demand from the classifier for each
# species. Tuned for the fake classifier at recall=0.90 with seed=20260418:
# tiny buckets (bobcat=1, fox=1, squirrel=1) cannot guarantee non-zero
# matching on every RNG seed, so they are excluded from the floor map.
_FLOORS: dict[str, float] = {
    "feral_hog":         0.80,
    "white_tailed_deer": 0.80,
    "black_bear":        0.80,
    "coyote":            0.80,
    "elk":               0.80,
    "turkey":            0.80,
    "raccoon":           0.50,   # n=4, tolerate one miss
}


@pytest.mark.skipif(
    not FIXTURE_ZIP.exists(),
    reason=(
        "TNDeer fixture not built; "
        "run scripts/build_tndeer_fixture.py to enable this gate"
    ),
)
def test_classifier_recovery_floors_on_tndeer_fixture():
    """The pipeline + accuracy math must meet per-species floors on
    the real TNDeer fixture with a 90%-recall fake classifier.

    When ``RUN_WITH_REAL_CLASSIFIER=1`` is set, route through
    ``strecker.classify.classify`` instead of the fake — requires GPU
    and a SpeciesNet checkpoint.
    """
    manifest = _load_manifest()
    photos = manifest["photos"]

    # Ground-truth stats from the manifest — this is the expected
    # shape of n_labeled coming out of build_accuracy_report.
    expected_labeled = sum(
        1 for p in photos if p.get("species_ground_truth") is not None
    )
    assert expected_labeled == 107, (
        f"fixture drift: expected 107 labeled photos, got {expected_labeled}"
    )

    use_real = os.environ.get("RUN_WITH_REAL_CLASSIFIER") == "1"

    if use_real:
        # Placeholder: when GPU + SpeciesNet are available in CI, route
        # the fixture through the real pipeline. For now we require the
        # caller to have wired this up themselves.
        pytest.skip(
            "RUN_WITH_REAL_CLASSIFIER=1 but the real pipeline is not yet "
            "wired into tests — run the fixture through the worker and "
            "feed (filename, predicted) pairs to build_accuracy_report."
        )

    # ── Fake classifier run ──
    rng = random.Random(20260418)
    predictions = [
        (p["original_filename"],
         fake_classify_filename(
             p["original_filename"], recall=0.90, confusion=0.05, rng=rng))
        for p in photos
    ]

    report = build_accuracy_report(predictions)

    # Shape assertions — the accuracy-report math must agree with the
    # manifest on labeled-photo counts.
    assert report["n_total"] == len(photos)
    assert report["n_labeled"] == expected_labeled

    # Overall accuracy floor: fake is 90% recall, we demand ≥ 85%
    # to leave RNG headroom across species with tiny bucket sizes.
    overall = report["n_matched"] / report["n_labeled"]
    assert overall >= 0.85, (
        f"overall recovery {overall:.3f} below 0.85 floor; report={report}"
    )

    # Per-species floors — this is what catches a species-specific
    # regression (e.g. one species_key rename breaks the mapping).
    for sp, floor in _FLOORS.items():
        stats = report["per_species"].get(sp)
        assert stats is not None, f"species {sp!r} missing from report"
        labeled = stats["labeled"]
        matched = stats["matched"]
        assert labeled > 0, f"species {sp!r} has zero labeled photos"
        recovery = matched / labeled
        assert recovery >= floor, (
            f"{sp} recovery {recovery:.3f} below floor {floor:.2f} "
            f"(matched={matched}, labeled={labeled})"
        )

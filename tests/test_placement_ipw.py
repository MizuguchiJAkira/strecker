"""Unit tests for bias.placement_ipw.

Strategy:
  - Synthesize biased deployments where we know the true random-placement
    rate, verify the literature-prior method recovers it.
  - Verify Hájek IPW with uniform target reweights correctly.
  - Edge cases: missing context, zero camera_days, all-one-context,
    species not in factor table.
  - Verify diagnostics (ESS, max-weight ratio) and caveat emission.
"""

from dataclasses import dataclass
from typing import Optional

import pytest

from bias.placement_ipw import (
    DEFAULT_INFLATION_FACTORS,
    BiasCorrectionResult,
    IPWDiagnostics,
    adjusted_rate_for_rem,
    compute_bias_correction,
    compute_propensities,
    hajek_weighted_rate,
    literature_adjusted_rate,
)


@dataclass
class Effort:
    """Minimal duck-type for the effort objects placement_ipw consumes."""
    camera_days: float
    detections: int
    placement_context: Optional[str] = None


# ---------------------------------------------------------------------------
# compute_propensities
# ---------------------------------------------------------------------------

def test_propensities_basic_proportions():
    efforts = [
        Effort(30, 5, "feeder"),
        Effort(30, 5, "feeder"),
        Effort(30, 1, "trail"),
        Effort(30, 1, "random"),
    ]
    p = compute_propensities(efforts, min_propensity=0.0)
    assert p["feeder"] == pytest.approx(0.5)
    assert p["trail"] == pytest.approx(0.25)
    assert p["random"] == pytest.approx(0.25)


def test_propensities_floor_applied():
    # 1 of 100 cameras at "random" → empirical 0.01, should floor to 0.05.
    efforts = [Effort(30, 0, "feeder") for _ in range(99)] + [Effort(30, 0, "random")]
    p = compute_propensities(efforts, min_propensity=0.05)
    assert p["random"] == 0.05
    assert p["feeder"] == pytest.approx(0.99)


def test_propensities_missing_context_becomes_unknown():
    efforts = [Effort(30, 0, None), Effort(30, 0, "trail")]
    p = compute_propensities(efforts, min_propensity=0.0)
    assert "unknown" in p
    assert p["unknown"] == pytest.approx(0.5)


def test_propensities_empty_input():
    assert compute_propensities([]) == {}


# ---------------------------------------------------------------------------
# Hájek IPW
# ---------------------------------------------------------------------------

def test_hajek_uniform_target_reweights_to_equal_context_weight():
    # 3 feeder cams (rate 1.0), 1 trail cam (rate 0.1).
    # Empirical mean across cameras: (3*1.0 + 1*0.1) / 4 = 0.775
    # Uniform target across 2 contexts: 0.5 * 1.0 + 0.5 * 0.1 = 0.55
    efforts = [
        Effort(10, 10, "feeder"),  # rate 1.0
        Effort(10, 10, "feeder"),
        Effort(10, 10, "feeder"),
        Effort(10, 1, "trail"),    # rate 0.1
    ]
    rate, props, diag = hajek_weighted_rate(efforts, min_propensity=0.0)
    assert rate == pytest.approx(0.55, rel=1e-6)
    assert diag.n_cameras == 4


def test_hajek_with_p_equal_to_q_collapses_to_mean():
    # Target == empirical → Hájek collapses to plain mean of per-cam rates.
    efforts = [Effort(10, 5, "feeder"), Effort(10, 1, "trail")]
    propensities = compute_propensities(efforts, min_propensity=0.0)
    rate, _, _ = hajek_weighted_rate(efforts, target_distribution=propensities,
                                     min_propensity=0.0)
    expected_mean = (0.5 + 0.1) / 2
    assert rate == pytest.approx(expected_mean, rel=1e-6)


def test_hajek_diagnostics_ess_equal_n_when_balanced():
    # Equal context counts + uniform target → all weights equal → ESS == n.
    efforts = [
        Effort(10, 5, "feeder"),
        Effort(10, 5, "trail"),
    ]
    _, _, diag = hajek_weighted_rate(efforts, min_propensity=0.0)
    assert diag.effective_sample_size == pytest.approx(2.0, abs=0.01)
    assert diag.max_weight_ratio == pytest.approx(1.0, abs=0.01)


def test_hajek_diagnostics_ess_below_n_when_imbalanced():
    # 9 feeder + 1 trail; uniform target up-weights the lone trail by 9×.
    efforts = [Effort(10, 5, "feeder")] * 9 + [Effort(10, 1, "trail")]
    _, _, diag = hajek_weighted_rate(efforts, min_propensity=0.0)
    assert diag.effective_sample_size < diag.n_cameras
    assert diag.max_weight_ratio > 1.0


def test_hajek_skips_zero_camera_days():
    efforts = [Effort(0, 0, "feeder"), Effort(10, 10, "feeder")]
    rate, _, diag = hajek_weighted_rate(efforts, min_propensity=0.0)
    assert rate == pytest.approx(1.0)
    assert diag.n_cameras == 1


# ---------------------------------------------------------------------------
# Literature-prior adjustment
# ---------------------------------------------------------------------------

def test_literature_adjustment_recovers_known_truth_for_hog():
    # Construct a deployment where the *true* random rate is 0.5.
    # Inflation factors for feral_hog: feeder 10×, trail 4×, random 1×.
    # Place 2 feeder cams observing rate 5.0, 2 trail cams observing 2.0,
    # 2 random cams observing 0.5. Per-camera deflated:
    #   feeder: 5.0/10 = 0.5
    #   trail:  2.0/4  = 0.5
    #   random: 0.5/1  = 0.5
    # Mean → 0.5. Recovers truth.
    efforts = [
        Effort(10, 50, "feeder"),
        Effort(10, 50, "feeder"),
        Effort(10, 20, "trail"),
        Effort(10, 20, "trail"),
        Effort(10, 5, "random"),
        Effort(10, 5, "random"),
    ]
    rate = literature_adjusted_rate("feral_hog", efforts)
    assert rate == pytest.approx(0.5, rel=1e-6)


def test_literature_adjustment_returns_none_for_unknown_species():
    efforts = [Effort(10, 5, "feeder")]
    assert literature_adjusted_rate("raccoon", efforts) is None


def test_literature_adjustment_caller_override_factors():
    # Override the table entirely; verify our override is applied.
    efforts = [Effort(10, 10, "feeder")]    # rate 1.0
    rate = literature_adjusted_rate("anything", efforts,
                                    factors={"feeder": 2.0})
    assert rate == pytest.approx(0.5)


def test_literature_adjustment_skips_zero_camera_days():
    efforts = [Effort(0, 0, "feeder"), Effort(10, 10, "random")]
    rate = literature_adjusted_rate("feral_hog", efforts)
    assert rate == pytest.approx(1.0)   # only the random cam contributes; 1.0/1.0


# ---------------------------------------------------------------------------
# compute_bias_correction (top-level)
# ---------------------------------------------------------------------------

def test_compute_bias_correction_full_record_for_hog():
    efforts = [
        Effort(10, 50, "feeder"),
        Effort(10, 20, "trail"),
        Effort(10, 5, "random"),
    ]
    result = compute_bias_correction("feral_hog", efforts)
    assert isinstance(result, BiasCorrectionResult)
    assert result.method_used == "literature_prior"
    # Raw rate: (50+20+5) / (10+10+10) = 75/30 = 2.5
    assert result.raw_rate == pytest.approx(2.5)
    # Literature-adjusted rate: mean(5/10, 2/4, 0.5/1) = mean(0.5, 0.5, 0.5)
    assert result.literature_adjusted_rate == pytest.approx(0.5)
    assert result.empirical_ipw_rate is not None
    assert "feeder" in result.contexts_present
    assert any("Kolowski" in n for n in result.method_notes)


def test_compute_bias_correction_unknown_species_falls_back_to_empirical():
    efforts = [Effort(10, 5, "feeder"), Effort(10, 1, "trail")]
    result = compute_bias_correction("raccoon", efforts)
    assert result.method_used == "empirical_ipw"
    assert result.literature_adjusted_rate is None
    assert result.empirical_ipw_rate is not None
    assert any("No literature-prior" in n for n in result.method_notes)


def test_compute_bias_correction_emits_no_random_caveat():
    efforts = [Effort(10, 50, "feeder"), Effort(10, 20, "trail")]
    result = compute_bias_correction("feral_hog", efforts)
    assert any("no random-placement" in c for c in result.caveats)


def test_compute_bias_correction_no_caveat_when_random_present():
    efforts = [Effort(10, 50, "feeder"), Effort(10, 5, "random")]
    result = compute_bias_correction("feral_hog", efforts)
    assert not any("no random-placement" in c for c in result.caveats)


def test_compute_bias_correction_emits_low_ess_caveat():
    # 9 feeder + 1 trail with uniform-target empirical IPW → ESS << n.
    efforts = [Effort(10, 5, "feeder")] * 9 + [Effort(10, 1, "trail")]
    result = compute_bias_correction("feral_hog", efforts, min_propensity=0.0)
    assert any("Effective sample size" in c for c in result.caveats)


def test_compute_bias_correction_emits_max_weight_caveat():
    # 19 feeder + 1 trail → trail weight 10× mean → triggers >5× flag.
    efforts = [Effort(10, 5, "feeder")] * 19 + [Effort(10, 1, "trail")]
    result = compute_bias_correction("feral_hog", efforts, min_propensity=0.0)
    assert any("Maximum camera weight" in c for c in result.caveats)


def test_compute_bias_correction_zero_efforts():
    result = compute_bias_correction("feral_hog", [])
    assert result.raw_rate == 0.0
    assert result.method_used == "none"


# ---------------------------------------------------------------------------
# adjusted_rate_for_rem (convenience wrapper)
# ---------------------------------------------------------------------------

def test_adjusted_rate_for_rem_prefers_literature():
    efforts = [Effort(10, 50, "feeder"), Effort(10, 5, "random")]
    rate, result = adjusted_rate_for_rem("feral_hog", efforts)
    assert rate == result.literature_adjusted_rate
    assert rate == pytest.approx(0.5)


def test_adjusted_rate_for_rem_falls_back_to_empirical_for_unknown_species():
    efforts = [Effort(10, 5, "feeder"), Effort(10, 1, "trail")]
    rate, result = adjusted_rate_for_rem("raccoon", efforts)
    assert rate == result.empirical_ipw_rate
    assert result.method_used == "empirical_ipw"


def test_adjusted_rate_for_rem_falls_back_to_raw_when_all_else_fails():
    rate, result = adjusted_rate_for_rem("raccoon", [])
    assert rate == 0.0
    assert result.method_used == "none"


# ---------------------------------------------------------------------------
# Sanity: default factor table has "random" == 1.0 for all listed species
# ---------------------------------------------------------------------------

def test_default_factor_table_random_is_unity():
    for sp, table in DEFAULT_INFLATION_FACTORS.items():
        assert table.get("random") == 1.0, (
            f"Random-placement factor must be 1.0 for {sp} "
            f"(it's the reference category)."
        )

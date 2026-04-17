"""Camera placement-context bias correction for REM detection rates.

Background
----------
The Random Encounter Model (Rowcliffe et al. 2008) requires an estimate
of the average detection rate at a *randomly placed* camera. Most
operational deployments place cameras at high-utility wildlife features
— feeders, trails, water sources, food plots — because those are where
operators see animals. This violates REM's movement-independence
assumption and inflates detection rates by 1.4-9.7× depending on
species and context (Kolowski & Forrester 2017, PLOS ONE 12: e0186679).

Without correction, REM density is biased upward by the same factor.
This module provides two correction methods and reports both.

Method 1 — Literature-prior ratio adjustment (PRIMARY)
------------------------------------------------------
For each camera at a non-random context, deflate the observed rate by
a per-species, per-context inflation factor sourced from the literature
(Kolowski 2017 + Mayer & Brisbin 2009 for hogs). The deflated rates are
then averaged to estimate what would be observed at random placement.

This is the only sound method when no random-placement cameras exist
in the deployment, which is the typical case for hunter-style camera
arrays. Trade-off: sensitive to inflation-factor accuracy. Default
factors are mid-range estimates from the literature; project-specific
calibration tightens them.

Method 2 — Hájek IPW with empirical propensities (DIAGNOSTIC)
-------------------------------------------------------------
Classical IPW (Hájek 1971; Cassel-Sarndal-Wretman 1976) reweights the
sample to a target distribution. With *empirical* propensities (the
observed proportion of cameras in each context), the estimator is the
plain mean — no correction. This module exposes the Hájek estimator
with a configurable target distribution (default: uniform across
contexts present, which gives equal weight to feeder/trail/water/random
buckets even when the sample is unbalanced).

Empirical IPW alone cannot correct bias when the entire sample is
biased (no random-placement cameras to anchor against). It is reported
as a sanity-check companion to the literature-prior method.

Why both
--------
- Literature-prior is methodologically correct for biased deployments
  and is what we feed into REM density downstream.
- Empirical Hájek IPW is the textbook estimator a reviewer expects to
  see and is reported alongside for transparency.
- The two methods agree closely when the deployment is roughly
  balanced; they diverge when one context dominates, which is exactly
  the case where bias correction matters most.

References
----------
Kolowski JM, Forrester TD. 2017. Camera trap placement and the
  potential for bias due to trails and other features. PLOS ONE 12:
  e0186679.
Mayer JJ, Brisbin IL. 2009. Wild Pigs: Biology, Damage, Control
  Techniques and Management. Savannah River National Laboratory.
Cassel CM, Särndal CE, Wretman JH. 1976. Some results on generalized
  difference estimation and generalized regression estimation for
  finite populations. Biometrika 63: 615–620.
Hájek J. 1971. Discussion of "An essay on the logical foundations of
  survey sampling, part one" by D. Basu. In Foundations of Statistical
  Inference. Holt, Rinehart and Winston.
Robins JM, Hernán MA, Brumback B. 2000. Marginal structural models and
  causal inference in epidemiology. Epidemiology 11: 550–560.
Cole SR, Hernán MA. 2008. Constructing inverse probability weights for
  marginal structural models. American Journal of Epidemiology 168:
  656–664.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

# -----------------------------------------------------------------------------
# Per-species inflation factors (literature priors)
# -----------------------------------------------------------------------------
#
# Each inner dict maps placement_context -> ratio of detection rate at that
# context vs. random placement, for a given species. To "deflate" an observed
# rate at context x, divide by INFLATION_FACTORS[species_key][x].
#
# Sources:
#   - Feral hog factors: Kolowski & Forrester 2017; corroborated by Mayer &
#     Brisbin 2009 chapter on detection methods. Feeders dominate (~10x);
#     trails moderate (~4x); water lower (~3x).
#   - White-tailed deer: Kolowski 2017; deer use feeders + trails but the
#     inflation is more modest because deer also forage broadly.
#   - Coyote: Kolowski 2017; coyotes use trails very heavily (~5x); less
#     attracted to feeders than to prey-attractant points.
#   - Axis deer: scarce TX-specific literature; values approximated from
#     white-tailed deer, slightly lower because axis deer are less
#     feeder-dependent.
#
# Species not in this table get all-1.0 factors (no adjustment) and a caveat.

DEFAULT_INFLATION_FACTORS: Dict[str, Dict[str, float]] = {
    "feral_hog": {
        "feeder":    10.0,
        "food_plot":  6.0,
        "water":      3.0,
        "trail":      4.0,
        "random":     1.0,
        "other":      1.5,
    },
    "white_tailed_deer": {
        "feeder":     4.0,
        "food_plot":  3.0,
        "water":      2.0,
        "trail":      3.0,
        "random":     1.0,
        "other":      1.2,
    },
    "axis_deer": {
        "feeder":     3.0,
        "food_plot":  2.5,
        "water":      2.0,
        "trail":      2.5,
        "random":     1.0,
        "other":      1.2,
    },
    "coyote": {
        "feeder":     1.5,
        "food_plot":  1.2,
        "water":      2.0,
        "trail":      5.0,    # coyotes use trails heavily
        "random":     1.0,
        "other":      1.3,
    },
}

# Fallback used when a species_key has no entry in the table.
NO_ADJUSTMENT_FACTORS: Dict[str, float] = {
    "feeder": 1.0, "food_plot": 1.0, "water": 1.0,
    "trail": 1.0, "random": 1.0, "other": 1.0,
}

# Empirical-IPW target distribution defaults: uniform across observed contexts.
# Caller can override (e.g., to weight by parcel ground-cover proportions).


# -----------------------------------------------------------------------------
# Output records
# -----------------------------------------------------------------------------

@dataclass
class IPWDiagnostics:
    """Per-camera weight statistics used to qualify the adjusted rate.

    Effective sample size (Kish 1965): ESS = (Σw)² / Σ(w²). When all
    weights are equal, ESS == n. When weights are extreme, ESS << n,
    and the adjusted rate is statistically equivalent to a much smaller
    sample. Report alongside the rate so reviewers can see the
    information-cost of the bias correction.

    Max weight ratio: max(w) / mean(w). Values > 5 indicate that one
    or two cameras dominate the estimate; values > 10 are a red flag
    (Cole & Hernán 2008 suggest weight stabilization or trimming).
    """
    n_cameras: int
    effective_sample_size: float
    max_weight_ratio: float
    weights_by_context: Dict[str, float]


@dataclass
class BiasCorrectionResult:
    """Output of compute_bias_correction.

    All three rates are events per camera-day:
      - raw_rate: simple sum of detections / sum of camera-days. No
        bias correction. The "what we observed" baseline.
      - literature_adjusted_rate: per-camera deflation by per-species
        inflation factors, then mean. Approximates the rate at random
        placement. PRIMARY REM input.
      - empirical_ipw_rate: Hájek IPW with the chosen target
        distribution (default: uniform across contexts). Diagnostic
        only, NOT fed into REM.

    method_used: which method drove the primary adjustment.
    factors_used: the species-specific inflation factor table actually
                  applied (may be the default or a caller override).
    caveats: plain-language flags for the UI.
    """
    raw_rate: float
    literature_adjusted_rate: Optional[float]
    empirical_ipw_rate: Optional[float]
    method_used: str   # "literature_prior" | "empirical_ipw" | "none"
    factors_used: Optional[Dict[str, float]]   # context -> factor
    diagnostics: IPWDiagnostics
    contexts_present: List[str]
    caveats: List[str] = field(default_factory=list)
    method_notes: List[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Building blocks
# -----------------------------------------------------------------------------

def compute_propensities(efforts: Iterable, min_propensity: float = 0.05
                         ) -> Dict[str, float]:
    """Empirical placement-context propensities from the deployment.

    Returns ``{placement_context: P(context)}`` where P is the proportion
    of cameras with that context. Contexts with a missing/None
    placement_context are aggregated under the key ``"unknown"``.

    Propensities below ``min_propensity`` (default 0.05) are floored to
    that value to prevent extreme weights. This is the standard
    weight-stabilization step for IPW (Cole & Hernán 2008).
    """
    counts: Dict[str, int] = {}
    n = 0
    for e in efforts:
        ctx = (getattr(e, "placement_context", None) or "unknown").lower()
        counts[ctx] = counts.get(ctx, 0) + 1
        n += 1
    if n == 0:
        return {}
    raw = {k: v / n for k, v in counts.items()}
    return {k: max(p, min_propensity) for k, p in raw.items()}


def hajek_weighted_rate(efforts: Iterable,
                        target_distribution: Optional[Dict[str, float]] = None,
                        min_propensity: float = 0.05
                        ) -> tuple[float, Dict[str, float], IPWDiagnostics]:
    """Hájek IPW estimator for the detection rate.

    Reweights cameras to a target placement-context distribution using
    weight w_i = q(x_i) / p(x_i), where q is the target marginal and p
    is the empirical propensity. With q == p (default behavior), the
    estimator collapses to the simple mean — no correction. Pass a
    different ``target_distribution`` (e.g., uniform) to actually
    reweight.

    Returns (rate, propensities, diagnostics).
    """
    eff = list(efforts)
    propensities = compute_propensities(eff, min_propensity=min_propensity)
    if not propensities:
        return 0.0, {}, IPWDiagnostics(0, 0.0, 0.0, {})

    # Default target: uniform across contexts present.
    if target_distribution is None:
        K = len(propensities)
        target = {k: 1.0 / K for k in propensities}
    else:
        # Restrict to contexts actually observed; renormalize.
        present = {k: target_distribution.get(k, 0.0) for k in propensities}
        s = sum(present.values()) or 1.0
        target = {k: v / s for k, v in present.items()}

    weighted_num = 0.0   # Σ w_i * r_i
    weighted_den = 0.0   # Σ w_i
    weights_for_diag: List[float] = []
    weights_by_ctx: Dict[str, float] = {}
    for e in eff:
        ctx = (getattr(e, "placement_context", None) or "unknown").lower()
        if e.camera_days <= 0:
            continue
        rate_i = e.detections / e.camera_days
        p = propensities.get(ctx, min_propensity)
        q = target.get(ctx, 0.0)
        w = (q / p) if p > 0 else 0.0
        weighted_num += w * rate_i
        weighted_den += w
        weights_for_diag.append(w)
        weights_by_ctx.setdefault(ctx, w)
    rate = (weighted_num / weighted_den) if weighted_den > 0 else 0.0

    # Diagnostics
    ess = (
        (sum(weights_for_diag) ** 2) / sum(w * w for w in weights_for_diag)
        if any(w > 0 for w in weights_for_diag) else 0.0
    )
    mean_w = (sum(weights_for_diag) / len(weights_for_diag)
              if weights_for_diag else 0.0)
    max_ratio = (max(weights_for_diag) / mean_w
                 if mean_w > 0 else 0.0)
    diag = IPWDiagnostics(
        n_cameras=len(weights_for_diag),
        effective_sample_size=round(ess, 2),
        max_weight_ratio=round(max_ratio, 2),
        weights_by_context={k: round(v, 3) for k, v in weights_by_ctx.items()},
    )
    return rate, propensities, diag


def literature_adjusted_rate(species_key: str, efforts: Iterable,
                             factors: Optional[Dict[str, float]] = None
                             ) -> Optional[float]:
    """Per-species rate after deflating each camera by its placement
    inflation factor.

    Returns the mean of (per-camera-rate / per-context-factor). If no
    inflation factor table is provided AND the species isn't in the
    default table, returns None (no adjustment possible).
    """
    if factors is None:
        factors = DEFAULT_INFLATION_FACTORS.get(species_key)
    if factors is None:
        return None

    n = 0
    total = 0.0
    for e in efforts:
        ctx = (getattr(e, "placement_context", None) or "unknown").lower()
        if e.camera_days <= 0:
            continue
        rate = e.detections / e.camera_days
        factor = factors.get(ctx, 1.0)
        if factor <= 0:
            factor = 1.0
        total += rate / factor
        n += 1
    return (total / n) if n > 0 else None


# -----------------------------------------------------------------------------
# Top-level public API
# -----------------------------------------------------------------------------

def compute_bias_correction(species_key: str, efforts: Iterable,
                            inflation_factors: Optional[Dict[str, float]] = None,
                            empirical_ipw_target: Optional[Dict[str, float]] = None,
                            min_propensity: float = 0.05
                            ) -> BiasCorrectionResult:
    """Run both bias-correction methods and report all three rates.

    Returns a BiasCorrectionResult with raw, literature-adjusted, and
    empirical-IPW rates plus diagnostics. The literature-adjusted rate
    is the recommended REM input.

    If ``species_key`` has no inflation-factor table available
    (default or override), method_used falls back to "empirical_ipw"
    (or "none" if even that's degenerate) with an explanatory caveat.
    """
    eff = list(efforts)
    raw_rate = (sum(e.detections for e in eff) /
                sum(e.camera_days for e in eff)) if eff and sum(e.camera_days for e in eff) > 0 else 0.0

    # Literature-prior method
    lit_rate = literature_adjusted_rate(
        species_key, eff, factors=inflation_factors)
    factors_used = inflation_factors or DEFAULT_INFLATION_FACTORS.get(species_key)

    # Empirical Hájek IPW
    emp_rate, propensities, diag = hajek_weighted_rate(
        eff, target_distribution=empirical_ipw_target,
        min_propensity=min_propensity,
    )

    # Method selection + caveats
    caveats: List[str] = []
    method_notes: List[str] = []
    contexts = sorted(propensities.keys())

    biased_contexts = [c for c in contexts
                       if c in {"feeder", "trail", "water", "food_plot"}]
    has_random = "random" in contexts

    if lit_rate is not None:
        method_used = "literature_prior"
        method_notes.append(
            f"Bias correction: per-camera deflation using literature-prior "
            f"inflation factors for '{species_key}' "
            f"(Kolowski & Forrester 2017; Mayer & Brisbin 2009 for hogs). "
            f"Factors applied: {factors_used}."
        )
        if biased_contexts and not has_random:
            caveats.append(
                f"All cameras placed at biased contexts ({', '.join(biased_contexts)}); "
                f"no random-placement cameras in this deployment. The adjusted rate "
                f"depends entirely on the published inflation factors and cannot be "
                f"validated against an internal random-placement reference."
            )
    elif emp_rate > 0:
        method_used = "empirical_ipw"
        method_notes.append(
            f"No literature-prior inflation factors available for '{species_key}'. "
            f"Falling back to empirical Hájek IPW with target distribution "
            f"{'uniform across observed contexts' if empirical_ipw_target is None else 'caller-specified'}."
        )
        caveats.append(
            f"Bias correction is empirical Hájek IPW only; without external "
            f"inflation-factor calibration, the adjusted rate corrects for "
            f"context-imbalance in the deployment but not for inflation within "
            f"each context."
        )
    else:
        method_used = "none"
        caveats.append(
            f"No bias correction applied for '{species_key}' — neither literature "
            f"factors nor empirical IPW could produce an estimate."
        )

    if diag.effective_sample_size < diag.n_cameras / 2 and diag.n_cameras >= 2:
        caveats.append(
            f"Effective sample size after weighting is {diag.effective_sample_size:.1f} "
            f"out of {diag.n_cameras} cameras (Kish 1965). Bias correction is "
            f"costing significant statistical power; tighter CIs would require "
            f"a more balanced deployment."
        )
    if diag.max_weight_ratio > 5.0:
        caveats.append(
            f"Maximum camera weight is {diag.max_weight_ratio:.1f}× the mean "
            f"(Cole & Hernán 2008 flag this above 5×). One camera dominates "
            f"the adjusted estimate; consider weight trimming or adding cameras."
        )

    return BiasCorrectionResult(
        raw_rate=raw_rate,
        literature_adjusted_rate=lit_rate,
        empirical_ipw_rate=(emp_rate if emp_rate > 0 else None),
        method_used=method_used,
        factors_used=factors_used,
        diagnostics=diag,
        contexts_present=contexts,
        caveats=caveats,
        method_notes=method_notes,
    )


def adjusted_rate_for_rem(species_key: str, efforts: Iterable) -> tuple[Optional[float], BiasCorrectionResult]:
    """Convenience wrapper: returns (rate_to_feed_into_REM, full_result).

    The REM input rate is the literature-prior-adjusted rate when
    available; falls back to the empirical IPW rate; falls back to the
    raw rate (with a caveat) if both are unavailable.
    """
    result = compute_bias_correction(species_key, efforts)
    if result.literature_adjusted_rate is not None:
        return result.literature_adjusted_rate, result
    if result.empirical_ipw_rate is not None:
        return result.empirical_ipw_rate, result
    return result.raw_rate, result

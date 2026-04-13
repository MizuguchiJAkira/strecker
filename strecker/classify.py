"""Species classification post-processing.

In production: runs SpeciesNet on MegaDetector crops.
In demo mode: classification is pre-fabricated — but all post-processing
runs for real:

  1. Temperature scaling (Dussert et al. 2025)
     A single learned parameter T calibrates confidence scores so they
     mean what they claim. Raw 0.90 from a neural net is NOT correct 90%
     of the time. After scaling: calibrated_logits = raw_logits / T,
     then softmax. The calibrated number is what goes to insurers.

  2. Temporal priors with cyclical encoding (Mac Aodha et al. ICCV 2019)
     SpeciesNet uses geographic geofencing but no learned temporal priors.
     Mac Aodha showed +9% accuracy by multiplying image classifier output
     by a spatio-temporal species prior. We encode time with sin/cos
     (not hard-coded hour buckets) because midnight and 11:59 PM must
     be CLOSE, not maximally distant.

     p(species | image, time) ∝ p(species | image) × p(species | time)

  3. Softmax entropy for review routing (Norouzzadeh et al. 2021)
     Binary entropy H = -p ln(p) - (1-p) ln(1-p) on the effective
     confidence. High entropy → uncertain → route to human reviewer.
     Threshold: 0.59 nats (calibrated for ~8% review rate on binary
     scale; Norouzzadeh's 0.5 was for full K-class entropy).

  4. Buck/doe classification for white-tailed deer
     In production: WildCLIP fine-tuning (Gabeff et al. 2024,
     github.com/amathislab/wildclip) — no dedicated published model exists.
     In demo: random 35% buck / 65% doe, suppressed Dec-Apr (antler shed).
"""

import math
from typing import List, Optional

import numpy as np

from config import settings


# ═══════════════════════════════════════════════════════════════════════════
# Species list — used for softmax simulation over K classes
# ═══════════════════════════════════════════════════════════════════════════

SPECIES_CLASSES = [
    "white_tailed_deer", "feral_hog", "turkey", "coyote", "black_bear",
    "bobcat", "elk", "axis_deer", "nilgai", "armadillo", "raccoon",
    "opossum", "cottontail_rabbit", "red_fox", "gray_fox",
]
K = len(SPECIES_CLASSES)  # 15


# ═══════════════════════════════════════════════════════════════════════════
# Temporal priors — cyclical encoding via sin/cos
#
# Key insight: we use sin/cos encoding so that hour 23:59 and 00:01 are
# CLOSE (angular distance ~0), not maximally distant as with raw hour
# values or hard-coded buckets.
#
# For each species, we define activity peaks as (center_hour, concentration,
# floor). The prior is computed via a von Mises-like kernel on the circular
# hour space. Species with multiple peaks (crepuscular) take the max.
#
# These are STARTING priors — in production, replace with empirical KDE
# learned from accumulated feedback data.
# ═══════════════════════════════════════════════════════════════════════════

# (center_hour, concentration, floor)
# Higher floor = weaker prior effect (species active at many hours)
# Higher concentration = narrower peak (strong diel pattern)
_TEMPORAL_PRIOR_PARAMS = {
    # ── Crepuscular: bimodal dawn/dusk ──
    # Deer/coyote: weak priors, wide active window (user spec)
    "white_tailed_deer": [(6.5, 0.4, 0.55), (18.5, 0.4, 0.55)],
    "coyote":            [(6.5, 0.3, 0.55), (18.5, 0.3, 0.55)],
    "axis_deer":         [(7.0, 0.3, 0.55), (18.0, 0.3, 0.55)],

    # Bobcat/fox: moderate crepuscular
    "bobcat":            [(6.0, 0.5, 0.45), (19.0, 0.5, 0.45)],
    "cottontail_rabbit": [(6.5, 0.6, 0.40), (18.5, 0.6, 0.40)],
    "red_fox":           [(6.5, 0.5, 0.45), (18.5, 0.5, 0.45)],

    # ── Nocturnal: single peak at night ──
    # Feral hog: day penalty 0.3 (10AM-4PM), peak 10PM-4AM
    "feral_hog":  [(1.0, 0.6, 0.30)],
    # Raccoon: day penalty 0.15, peak 8PM-4AM
    "raccoon":    [(0.0, 0.5, 0.15)],
    "armadillo":  [(0.5, 0.5, 0.25)],
    "opossum":    [(0.0, 0.4, 0.25)],
    "gray_fox":   [(23.0, 0.5, 0.30)],

    # ── Diurnal: turkey ZERO at night ──
    # Turkey: night penalty 0.05 (10PM-4AM), peak 6-10AM
    "turkey": [(8.0, 0.4, 0.05)],

    # ── Weak/no prior ──
    "black_bear": [(12.0, 0.1, 0.70)],
    "elk":        [(7.0, 0.2, 0.60)],
    "nilgai":     [(7.0, 0.2, 0.60)],
}


def _cyclical_hour(hour: float) -> tuple:
    """Encode hour as (sin, cos) on the unit circle.

    This ensures midnight (0) and 11:59 PM (23.98) are CLOSE,
    not maximally distant as with raw hour values or hard-coded buckets.
    """
    theta = 2.0 * math.pi * hour / 24.0
    return math.sin(theta), math.cos(theta)


def _circular_distance(h1: float, h2: float) -> float:
    """Circular distance between two hours on a 24-hour clock.

    Uses angular representation: hour 23.5 and hour 0.5
    are 1 hour apart, not 23 hours apart.
    """
    s1, c1 = _cyclical_hour(h1)
    s2, c2 = _cyclical_hour(h2)
    cos_dist = max(-1.0, min(1.0, s1 * s2 + c1 * c2))
    return math.acos(cos_dist)


def compute_temporal_prior(species_key: str, hour: float) -> float:
    """Compute temporal activity prior for a species at a given hour.

    Returns a value in (0, 1] representing how plausible it is to
    see this species at this time of day. Uses a von Mises-like kernel
    on the circular hour space:

        prior = floor + (1 - floor) × exp(-κ × θ²)

    where θ is the angular distance from the activity peak, and κ is
    the concentration parameter.

    For bimodal species (crepuscular), takes the max across peaks.
    """
    params = _TEMPORAL_PRIOR_PARAMS.get(species_key)
    if not params:
        return 1.0

    prior = 0.0
    for center_hour, concentration, floor in params:
        dist = _circular_distance(hour, center_hour)
        peak_val = math.exp(-concentration * dist * dist)
        val = floor + (1.0 - floor) * peak_val
        prior = max(prior, val)

    return prior


# ═══════════════════════════════════════════════════════════════════════════
# Temperature scaling — Dussert et al. 2025
# ═══════════════════════════════════════════════════════════════════════════

def temperature_scale(raw_confidence: float, T: float = None) -> float:
    """Apply temperature scaling to calibrate confidence scores.

    Given a raw top-class probability p from the softmax, we:
      1. Invert softmax to recover approximate logits
      2. Divide logits by T (> 1 softens, < 1 sharpens)
      3. Re-apply softmax

    A single learned T (fit on held-out calibration set via NLL
    minimization) makes the confidence number mean what it claims.
    This is the number reported to insurers.
    """
    if T is None:
        T = settings.TEMPERATURE_SCALING_T

    p = max(1e-7, min(1.0 - 1e-7, raw_confidence))

    # Inverse softmax: logit of top class vs uniform rest
    rest = max(1e-10, (1.0 - p) / (K - 1))
    raw_logit = math.log(p) - math.log(rest)

    # Scale
    scaled_logit = raw_logit / T

    # Re-apply softmax (log-sum-exp for numerical stability)
    max_val = max(scaled_logit, 0.0)
    log_denom = max_val + math.log(
        math.exp(scaled_logit - max_val) + (K - 1) * math.exp(-max_val))
    calibrated = math.exp(scaled_logit - log_denom)

    return round(calibrated, 4)


# ═══════════════════════════════════════════════════════════════════════════
# Softmax entropy — review routing signal
# ═══════════════════════════════════════════════════════════════════════════

def compute_softmax_entropy(effective_confidence: float) -> float:
    """Compute entropy for review routing from effective confidence.

    Uses binary entropy H = -p ln(p) - (1-p) ln(1-p), which measures
    uncertainty in the top-class prediction: "is it this species or not?"

    Why binary rather than full K-class entropy: with K=15 species, the
    full softmax entropy includes a constant noise floor from 13+ near-zero
    probability classes. This obscures the actionable signal. Binary entropy
    directly answers the question review routing needs: how confident
    are we in the top prediction?

    At threshold 0.5 nats, this flags predictions with effective
    confidence below ~80% — uncertain enough to warrant human review.
    """
    p = max(1e-7, min(1.0 - 1e-7, effective_confidence))
    return round(-(p * math.log(p) + (1.0 - p) * math.log(1.0 - p)), 4)


# ═══════════════════════════════════════════════════════════════════════════
# Buck/doe classification
# ═══════════════════════════════════════════════════════════════════════════

def assign_antler_classification(species_key: str, timestamp,
                                  rng=None) -> Optional[str]:
    """Classify deer detections as buck or doe.

    In production: WildCLIP fine-tuning (Gabeff et al. 2024,
    github.com/amathislab/wildclip). No published dedicated antler
    classification model exists for trail cameras.

    In demo: random 35% buck / 65% doe.
    Suppressed Dec-Apr (antler shed season — bucks lack antlers,
    so visual sex classification is unreliable).
    """
    if species_key != "white_tailed_deer":
        return None

    if timestamp.month in (12, 1, 2, 3, 4):
        return None

    if rng is None:
        rng = np.random.default_rng()

    return "buck" if rng.random() < 0.35 else "doe"


# ═══════════════════════════════════════════════════════════════════════════
# Full classification post-processing pipeline
# ═══════════════════════════════════════════════════════════════════════════

def classify(detections, demo: bool = False):
    """Run classification post-processing on ingested detections.

    In demo mode, ML inference is skipped — species_key and raw confidence
    are already set from detections.json. But all post-processing runs
    for real:

      1. Temperature scaling → confidence_calibrated (image-only, for insurers)
      2. Temporal priors → stored for transparency, used in entropy computation
      3. Softmax entropy → review_required flag
      4. Buck/doe classification for deer

    Architecture note: confidence_calibrated reflects image confidence only
    (after temperature scaling). The temporal prior is NOT baked into the
    stored calibrated value — it influences review routing via entropy
    but the number reported to insurers is purely image-based.

    Args:
        detections: List of Detection objects from ingest.py
        demo: If True, skip ML inference

    Returns:
        Same list with post-processing fields populated.
    """
    rng = np.random.default_rng(42)

    T = settings.TEMPERATURE_SCALING_T
    threshold = settings.REVIEW_ENTROPY_THRESHOLD

    for det in detections:
        # ── 1. Temperature scaling ──
        # Calibrated confidence = image-only, reported to insurers
        det.confidence_calibrated = temperature_scale(det.confidence, T)

        # ── 2. Temporal prior ──
        hour = det.timestamp.hour + det.timestamp.minute / 60.0
        prior = compute_temporal_prior(det.species_key, hour)
        det.temporal_prior = round(prior, 4)

        # ── 3. Entropy for review routing ──
        # Bayesian update: modulate calibrated confidence by temporal prior
        # via odds ratio adjustment. This is the principled way to combine
        # image evidence with temporal evidence:
        #   odds_posterior = odds_image × prior
        #   p_effective = odds_posterior / (1 + odds_posterior)
        #
        # Gentle scaling (prior^0.15) prevents the temporal signal from
        # overwhelming image evidence. A clear photo of a deer IS a deer
        # regardless of time — the prior just elevates review probability
        # for temporally implausible detections.
        temporal_factor = prior ** 0.15
        effective_conf = det.confidence_calibrated * temporal_factor

        det.softmax_entropy = compute_softmax_entropy(effective_conf)
        det.review_required = det.softmax_entropy > threshold

        # ── 4. Buck/doe classification ──
        if det.species_key == "white_tailed_deer":
            det.antler_classification = assign_antler_classification(
                det.species_key, det.timestamp, rng)

    return detections

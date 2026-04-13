"""Inverse probability weighting for bias-corrected detection rates.

Applies IPW using propensity scores to produce unbiased estimates
of species detection frequency from non-randomly placed cameras.

The key equation (from causal inference / survey sampling):

  adjusted_freq = sum(detected_i * w_i) / sum(w_i)

where:
  detected_i = 1 if camera i detected this species (independent event)
  w_i = stabilized inverse probability weight for camera i

Weight computation:
  raw_weight_i = 1 / propensity_score_i
  stabilized_weight_i = (n_cameras / N_total) / propensity_score_i
  → trim at 5th/95th percentiles to prevent extreme weights

Why stabilization? Raw IPW gives unbiased estimates but high variance
when some cameras have very low propensity (unusual placement).
Stabilized weights preserve consistency while reducing variance
(Robins, Hernán & Brumback, 2000).

Why trimming? A camera placed in a truly unusual spot gets a massive
weight (e.g., 1/0.02 = 50×). One misclassification at that camera
distorts the entire estimate. Trimming at 5th/95th is standard in
epidemiology (Cole & Hernán, 2008).

Expected demo behavior:
  - Feral hog: raw ~87% → adjusted ~65-72% (feeders inflate detection)
  - White-tailed deer: raw ~98% → adjusted ~93-97% (ubiquitous species)
  - Turkey: raw ~65% → adjusted ~68-75% (under-represented on ridges)
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from config.species_reference import SPECIES_REFERENCE


def compute_ipw(propensity_scores: np.ndarray,
                camera_rows: List[Dict],
                detections=None,
                demo: bool = False) -> Dict:
    """Compute inverse probability weights and adjusted detection frequencies.

    Args:
        propensity_scores: P(camera=1 | covariates) for each camera
        camera_rows: Camera covariate dicts (from covariates.py)
        detections: List of Detection objects (from ingest+classify)
        demo: If True, use demo data

    Returns:
        Full bias correction result dict with per-species adjusted frequencies.
    """
    n_cameras = len(camera_rows)
    n_total = n_cameras + 500  # cameras + reference points

    # ── 1. Compute stabilized weights ──
    # Raw: w_i = 1 / e(X_i)
    # Stabilized: w_i = (n_cameras / N_total) / e(X_i)
    #   Stabilization multiplies by marginal probability of treatment
    #   (Robins, Hernán & Brumback 2000) — preserves consistency,
    #   reduces variance vs raw IPW.
    marginal_prob = n_cameras / n_total
    raw_weights = 1.0 / np.clip(propensity_scores, 0.01, 0.99)
    stabilized_weights = marginal_prob / np.clip(propensity_scores, 0.01, 0.99)

    # ── 2. Trim extreme weights ──
    # With small camera networks (n < 30), percentile trimming is too
    # coarse. Instead, cap the ratio of max:min weight at MAX_WEIGHT_RATIO.
    # This is standard in small-sample causal inference (Lee et al. 2011).
    MAX_WEIGHT_RATIO = 8.0
    w_min = stabilized_weights.min()
    w_max = stabilized_weights.max()
    if w_max / max(w_min, 1e-6) > MAX_WEIGHT_RATIO:
        cap = w_min * MAX_WEIGHT_RATIO
        stabilized_weights = np.clip(stabilized_weights, w_min, cap)

    # Also apply percentile trimming for larger datasets
    if n_cameras >= 30:
        p5, p95 = np.percentile(stabilized_weights, [5, 95])
        stabilized_weights = np.clip(stabilized_weights, p5, p95)

    trimmed_weights = stabilized_weights

    # Normalize so weights sum to n_cameras (preserves sample size)
    trimmed_weights = trimmed_weights * (n_cameras / trimmed_weights.sum())

    # ── 3. Build camera → weight mapping ──
    camera_ids = [r["point_id"] for r in camera_rows]
    weight_map = dict(zip(camera_ids, trimmed_weights))

    # ── 4. Build camera → species detected (independent events) ──
    camera_species = _build_camera_species_map(
        camera_ids, detections, demo=demo)

    # ── 5. Compute raw and adjusted detection frequencies ──
    all_species = set()
    for sp_set in camera_species.values():
        all_species.update(sp_set.keys())

    per_species = {}
    for sp_key in sorted(all_species):
        # Raw: fraction of cameras that detected this species
        detected = np.array([
            1.0 if sp_key in camera_species.get(cid, {}) else 0.0
            for cid in camera_ids
        ])
        raw_freq = detected.mean() * 100.0

        # Adjusted: weighted fraction
        # adjusted = sum(detected_i * w_i) / sum(w_i)
        adjusted_freq = (
            (detected * trimmed_weights).sum() / trimmed_weights.sum()
        ) * 100.0

        adjustment_ratio = adjusted_freq / raw_freq if raw_freq > 0 else 1.0

        ref = SPECIES_REFERENCE.get(sp_key, {})
        per_species[sp_key] = {
            "common_name": ref.get("common_name", sp_key),
            "raw_detection_frequency_pct": round(raw_freq, 1),
            "adjusted_detection_frequency_pct": round(adjusted_freq, 1),
            "adjustment_ratio": round(adjustment_ratio, 3),
            "delta_pct": round(adjusted_freq - raw_freq, 1),
            "n_cameras_detected": int(detected.sum()),
            "n_cameras_total": n_cameras,
        }

    # ── 6. Per-camera weight details ──
    camera_weights = []
    for i, cid in enumerate(camera_ids):
        camera_weights.append({
            "camera_id": cid,
            "placement_context": camera_rows[i].get("placement_context", ""),
            "propensity_score": round(float(propensity_scores[i]), 4),
            "raw_weight": round(float(raw_weights[i]), 4),
            "stabilized_weight": round(float(stabilized_weights[i]), 4),
            "trimmed_weight": round(float(trimmed_weights[i]), 4),
        })

    return {
        "per_species": per_species,
        "camera_weights": camera_weights,
        "weight_stats": {
            "trim_lower": round(float(trimmed_weights.min()), 4),
            "trim_upper": round(float(trimmed_weights.max()), 4),
            "mean_weight": round(float(trimmed_weights.mean()), 4),
            "std_weight": round(float(trimmed_weights.std()), 4),
            "max_weight": round(float(trimmed_weights.max()), 4),
            "min_weight": round(float(trimmed_weights.min()), 4),
        },
    }


def _build_camera_species_map(camera_ids: List[str],
                              detections=None,
                              demo: bool = False
                              ) -> Dict[str, Dict[str, int]]:
    """Build mapping of camera_id → {species_key: n_independent_events}.

    Uses independent events (NOT raw photos) per the spec.
    """
    camera_species = defaultdict(lambda: defaultdict(int))

    if detections is not None:
        # From Detection objects
        seen_events = set()
        for det in detections:
            eid = det.independent_event_id
            if eid and eid not in seen_events:
                seen_events.add(eid)
                camera_species[det.camera_id][det.species_key] += 1
    elif demo:
        # Load from demo detections.json
        det_path = (Path(__file__).parent.parent
                    / "demo" / "demo_data" / "detections.json")
        with open(det_path) as f:
            raw_dets = json.load(f)

        seen_events = set()
        for d in raw_dets:
            eid = d.get("independent_event_id", "")
            if eid and eid not in seen_events:
                seen_events.add(eid)
                camera_species[d["camera_id"]][d["species_key"]] += 1

    return dict(camera_species)


# ═══════════════════════════════════════════════════════════════════════════
# Full bias correction pipeline
# ═══════════════════════════════════════════════════════════════════════════

def run_bias_correction(cameras_json: List[Dict] = None,
                        detections=None,
                        n_reference: int = 500,
                        demo: bool = False) -> Dict:
    """Run the complete bias correction pipeline.

    Orchestrates: covariates → propensity model → IPW → adjusted frequencies.

    Returns the full bias correction result dict.
    """
    from bias.covariates import build_covariate_matrix
    from bias.propensity import fit_propensity_model

    # Step 1: Build covariate matrix
    camera_rows, reference_rows = build_covariate_matrix(
        cameras_json=cameras_json, n_reference=n_reference, demo=demo)

    # Step 2: Fit propensity model
    prop_result = fit_propensity_model(camera_rows, reference_rows)

    auc = prop_result["auc"]
    bias_detected = prop_result["bias_detected"]

    if not bias_detected:
        # AUC < 0.6 → minimal bias, pass through raw frequencies
        camera_ids = [r["point_id"] for r in camera_rows]
        camera_species = _build_camera_species_map(
            camera_ids, detections, demo=demo)

        all_species = set()
        for sp_set in camera_species.values():
            all_species.update(sp_set.keys())

        per_species = {}
        for sp_key in sorted(all_species):
            detected = sum(
                1 for cid in camera_ids
                if sp_key in camera_species.get(cid, {}))
            freq = detected / len(camera_ids) * 100.0
            ref = SPECIES_REFERENCE.get(sp_key, {})
            per_species[sp_key] = {
                "common_name": ref.get("common_name", sp_key),
                "raw_detection_frequency_pct": round(freq, 1),
                "adjusted_detection_frequency_pct": round(freq, 1),
                "adjustment_ratio": 1.0,
                "delta_pct": 0.0,
                "n_cameras_detected": detected,
                "n_cameras_total": len(camera_ids),
            }

        return {
            "bias_correction_applied": False,
            "reason": f"AUC={auc:.3f} < 0.6 — placement bias is minimal",
            "propensity_model_auc": auc,
            "n_cameras": len(camera_rows),
            "n_reference_points": len(reference_rows),
            "top_placement_predictors": prop_result["top_predictors"],
            "covariate_comparison": prop_result["covariate_comparison"],
            "per_species": per_species,
        }

    # Step 3: Compute IPW
    ipw_result = compute_ipw(
        propensity_scores=prop_result["propensity_scores"],
        camera_rows=camera_rows,
        detections=detections,
        demo=demo,
    )

    return {
        "bias_correction_applied": True,
        "propensity_model_auc": auc,
        "n_cameras": len(camera_rows),
        "n_reference_points": len(reference_rows),
        "top_placement_predictors": prop_result["top_predictors"],
        "covariate_comparison": prop_result["covariate_comparison"],
        "diagnostics": prop_result["diagnostics"],
        "per_species": ipw_result["per_species"],
        "camera_weights": ipw_result["camera_weights"],
        "weight_stats": ipw_result["weight_stats"],
    }

"""Propensity score estimation for camera placement.

Models the probability of non-random camera placement given landscape
covariates. This is the core of the bias correction system.

Key insight (Kolowski & Forrester 2017): trail and feeder cameras
detect 9.7× more animals than randomly placed cameras. Tanwar et al.
2021 showed random camera RAI correlates with actual density at r=0.93
while trail camera RAI shows r=0.38 — basically noise.

The fix is inverse probability weighting from the causal inference
literature. We model P(camera placed here | landscape covariates),
then weight observations by 1/P. Cameras placed in typical locations
(near feeders, water) get DOWN-weighted because they'd be expected
anywhere. Cameras in unusual spots get UP-weighted because their
observations are more informative about the true landscape.

Decision rule:
  - AUC < 0.6: placement bias is minimal → pass through raw frequencies
  - AUC ≥ 0.6: extract propensity scores and apply IPW

Uses scikit-learn LogisticRegression with L2 regularization.
"""

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler


# Continuous covariates used in the propensity model.
# NOTE: placement_context is NOT a model feature. Reference points
# have no placement context; including it would let the model trivially
# separate cameras from reference points (AUC→1.0) without learning
# anything about landscape bias. The model must answer: "given ONLY
# the landscape at this point, how likely is a hunter to place a camera?"
_CONTINUOUS_COVARIATES = [
    "distance_to_water_m",
    "distance_to_road_m",
    "slope_degrees",
    "canopy_cover_pct",
    "relative_elevation",
    "distance_to_edge_m",
    "mean_temp_c",
    "total_precip_mm",
]

# Categorical covariates (one-hot encoded) — landscape features only
_CATEGORICAL_COVARIATES = {
    "nlcd_code": [41, 42, 43, 52, 71, 81, 82],
    "aspect": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
}


def _encode_features(rows: List[Dict]) -> Tuple[np.ndarray, List[str]]:
    """Encode covariates into a feature matrix for logistic regression.

    Continuous features are included as-is (will be standardized).
    Categorical features are one-hot encoded (drop-first to avoid
    collinearity).

    Returns:
        (X, feature_names) — feature matrix and corresponding names.
    """
    feature_names = list(_CONTINUOUS_COVARIATES)

    # One-hot columns (drop first category for each)
    for cat_name, categories in _CATEGORICAL_COVARIATES.items():
        for cat_val in categories[1:]:  # Drop first as reference
            feature_names.append(f"{cat_name}_{cat_val}")

    n = len(rows)
    X = np.zeros((n, len(feature_names)))

    for i, row in enumerate(rows):
        # Continuous
        for j, cov in enumerate(_CONTINUOUS_COVARIATES):
            X[i, j] = row.get(cov, 0.0)

        # Categorical one-hot
        col_offset = len(_CONTINUOUS_COVARIATES)
        for cat_name, categories in _CATEGORICAL_COVARIATES.items():
            val = row.get(cat_name)
            for k, cat_val in enumerate(categories[1:]):
                if str(val) == str(cat_val):
                    X[i, col_offset + k] = 1.0
            col_offset += len(categories) - 1

    return X, feature_names


def fit_propensity_model(camera_rows: List[Dict],
                         reference_rows: List[Dict],
                         ) -> Dict:
    """Fit a logistic regression propensity model.

    Binary outcome: camera = 1, reference point = 0.
    Features: continuous + one-hot categorical covariates.

    Returns dict with:
      - model: fitted LogisticRegression
      - scaler: fitted StandardScaler
      - feature_names: list of feature names
      - auc: ROC AUC on the full dataset
      - bias_detected: True if AUC ≥ 0.6
      - propensity_scores: scores for camera locations only
      - top_predictors: top 3 coefficients with interpretation
      - diagnostics: model diagnostics dict
    """
    all_rows = camera_rows + reference_rows
    y = np.array([r["is_camera"] for r in all_rows])

    X, feature_names = _encode_features(all_rows)

    # Standardize continuous features (categorical are 0/1, OK to scale)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Fit logistic regression with L2 regularization
    # C=1.0 is default — moderate regularization
    model = LogisticRegression(
        penalty="l2", C=1.0, max_iter=1000,
        solver="lbfgs", random_state=42)
    model.fit(X_scaled, y)

    # Predicted probabilities
    proba = model.predict_proba(X_scaled)[:, 1]

    # AUC
    auc = roc_auc_score(y, proba)
    bias_detected = auc >= 0.6

    # Propensity scores for cameras only
    n_cameras = len(camera_rows)
    camera_propensity = proba[:n_cameras]

    # Top predictors by absolute coefficient
    coefs = model.coef_[0]
    coef_pairs = list(zip(feature_names, coefs))
    coef_pairs.sort(key=lambda x: abs(x[1]), reverse=True)

    top_predictors = []
    for feat_name, coef in coef_pairs[:5]:
        interpretation = _interpret_coefficient(
            feat_name, coef, camera_rows, reference_rows)
        top_predictors.append({
            "covariate": feat_name,
            "coefficient": round(coef, 4),
            "interpretation": interpretation,
        })

    # Diagnostics
    diagnostics = {
        "n_cameras": n_cameras,
        "n_reference": len(reference_rows),
        "auc": round(auc, 4),
        "bias_detected": bias_detected,
        "intercept": round(model.intercept_[0], 4),
        "n_features": len(feature_names),
        "propensity_min": round(float(camera_propensity.min()), 4),
        "propensity_max": round(float(camera_propensity.max()), 4),
        "propensity_mean": round(float(camera_propensity.mean()), 4),
    }

    # Covariate comparison: camera means vs reference means
    covariate_comparison = {}
    for cov in _CONTINUOUS_COVARIATES:
        cam_vals = [r[cov] for r in camera_rows if cov in r]
        ref_vals = [r[cov] for r in reference_rows if cov in r]
        if cam_vals and ref_vals:
            cam_mean = np.mean(cam_vals)
            ref_mean = np.mean(ref_vals)
            ratio = cam_mean / ref_mean if ref_mean != 0 else 1.0
            covariate_comparison[cov] = {
                "camera_mean": round(cam_mean, 1),
                "landscape_mean": round(ref_mean, 1),
                "ratio": round(ratio, 2),
            }

    return {
        "model": model,
        "scaler": scaler,
        "feature_names": feature_names,
        "auc": round(auc, 4),
        "bias_detected": bias_detected,
        "propensity_scores": camera_propensity,
        "top_predictors": top_predictors,
        "diagnostics": diagnostics,
        "covariate_comparison": covariate_comparison,
    }


def _interpret_coefficient(feat_name: str, coef: float,
                           camera_rows: List[Dict],
                           reference_rows: List[Dict]) -> str:
    """Generate human-readable interpretation of a coefficient."""
    direction = "more" if coef > 0 else "less"
    odds_ratio = math.exp(abs(coef))

    # Placement context coefficients
    if feat_name.startswith("placement_context_"):
        ctx = feat_name.replace("placement_context_", "")
        if coef > 0:
            return (f"{ctx.title()} cameras {odds_ratio:.1f}× "
                    f"more likely than reference placement")
        return f"{ctx.title()} cameras {odds_ratio:.1f}× less likely"

    # Continuous covariates — compare camera vs reference means
    base_cov = feat_name
    cam_vals = [r.get(base_cov, 0) for r in camera_rows if base_cov in r]
    ref_vals = [r.get(base_cov, 0) for r in reference_rows if base_cov in r]

    if cam_vals and ref_vals:
        cam_mean = np.mean(cam_vals)
        ref_mean = np.mean(ref_vals)
        if ref_mean > 0:
            ratio = cam_mean / ref_mean
            if ratio < 1:
                return (f"Cameras {1/ratio:.1f}× closer "
                        f"({cam_mean:.0f} vs {ref_mean:.0f} landscape avg)")
            else:
                return (f"Cameras {ratio:.1f}× farther "
                        f"({cam_mean:.0f} vs {ref_mean:.0f} landscape avg)")

    return f"Coefficient {coef:.3f} (odds ratio {odds_ratio:.2f}×)"

"""SpeciesNet detection + classification pipeline.

Uses Google's SpeciesNet ensemble (MegaDetector v5 + species classifier +
geofencing) to detect animals and classify species in trail camera photos.

SpeciesNet recognizes 2000+ species worldwide. The ensemble:
  1. MegaDetector finds animal bounding boxes
  2. Species classifier identifies species from crops
  3. Geofencing filters to species expected in the given region

Usage:
    from strecker.detect import run_speciesnet
    predictions = run_speciesnet("/path/to/photos", state="TX")
"""

import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import settings

logger = logging.getLogger(__name__)

# Lazy-loaded model singleton
_model = None


def _get_model():
    """Load SpeciesNet model (downloads ~1.5GB on first use)."""
    global _model
    if _model is not None:
        return _model

    try:
        from speciesnet import SpeciesNet
    except ImportError:
        raise ImportError(
            "speciesnet package not installed. "
            "Run: pip install speciesnet"
        )

    logger.info("Loading SpeciesNet model (first run downloads weights)...")
    _model = SpeciesNet(
        model_name=getattr(settings, "SPECIESNET_MODEL", "kaggle:google/speciesnet/pyTorch/v4.0.2a/1"),
        components="all",
        geofence=True,
    )
    logger.info("SpeciesNet model loaded successfully")
    return _model


def run_speciesnet(
    image_dir: str,
    country: str = "USA",
    state: str = None,
    confidence_threshold: float = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    """Run the full SpeciesNet ensemble on a folder of images.

    Args:
        image_dir: Path to directory containing .jpg/.png images.
        country: ISO 3166-1 alpha-3 country code for geofencing.
        state: US state two-letter code (e.g., "TX") for finer geofencing.
        confidence_threshold: Minimum prediction confidence to keep.
            Defaults to settings.MEGADETECTOR_CONFIDENCE_THRESHOLD (0.15).
        batch_size: Images per inference batch.

    Returns:
        Dict keyed by filepath with prediction results:
        {
            "path/to/img.jpg": {
                "prediction": "odocoileus_virginianus",
                "prediction_score": 0.932,
                "detections": [...],  # MegaDetector bounding boxes
                "classifications": {...},  # top-5 species + scores
            },
            ...
        }
    """
    if confidence_threshold is None:
        confidence_threshold = getattr(
            settings, "MEGADETECTOR_CONFIDENCE_THRESHOLD", 0.15
        )

    image_dir = Path(image_dir)
    if not image_dir.exists():
        raise FileNotFoundError(f"Image directory not found: {image_dir}")

    model = _get_model()

    logger.info(
        f"Running SpeciesNet on {image_dir} "
        f"(country={country}, state={state})"
    )

    # Run the full ensemble: detect + classify + geofence
    predictions = model.predict(
        folders=[str(image_dir)],
        country=country,
        admin1_region=state,
        batch_size=batch_size,
        run_mode="multi_thread",
        progress_bars=False,
    )

    if not predictions:
        logger.warning("SpeciesNet returned no predictions")
        return {}

    # predictions is a dict with "predictions" key containing list of results
    results = {}
    pred_list = predictions.get("predictions", [])

    for pred in pred_list:
        filepath = pred.get("filepath", "")

        # Extract the final ensemble prediction
        prediction = pred.get("prediction", "")
        prediction_score = pred.get("prediction_score", 0.0)

        # Skip low-confidence or blank/empty predictions
        if prediction_score < confidence_threshold:
            continue
        if prediction in ("blank", "empty", "", None):
            continue

        # Get detection bounding boxes (from MegaDetector)
        detections = pred.get("detections", [])

        # Get classification details (top-5)
        classifications = pred.get("classifications", {})

        results[filepath] = {
            "prediction": prediction,
            "prediction_score": prediction_score,
            "detections": detections,
            "classifications": classifications,
        }

    logger.info(
        f"SpeciesNet classified {len(results)} images with animals "
        f"out of {len(pred_list)} total"
    )

    return results


# ── Species key mapping ──────────────────────────────────────────────────────
# SpeciesNet returns scientific names (e.g., "odocoileus_virginianus").
# Map to our internal species_key format.

_SCIENTIFIC_TO_KEY = {
    "odocoileus_virginianus": "white_tailed_deer",
    "odocoileus hemionus": "mule_deer",
    "sus_scrofa": "feral_hog",
    "meleagris_gallopavo": "wild_turkey",
    "canis_latrans": "coyote",
    "ursus_americanus": "black_bear",
    "lynx_rufus": "bobcat",
    "cervus_canadensis": "elk",
    "axis_axis": "axis_deer",
    "boselaphus_tragocamelus": "nilgai",
    "dasypus_novemcinctus": "armadillo",
    "procyon_lotor": "raccoon",
    "didelphis_virginiana": "opossum",
    "sylvilagus_floridanus": "cottontail_rabbit",
    "vulpes_vulpes": "red_fox",
    "urocyon_cinereoargenteus": "gray_fox",
    # Higher-level taxa fallbacks
    "cervidae": "white_tailed_deer",  # deer family default
    "suidae": "feral_hog",
    "canidae": "coyote",
    "felidae": "bobcat",
    "ursidae": "black_bear",
    "leporidae": "cottontail_rabbit",
    "procyonidae": "raccoon",
    "didelphidae": "opossum",
    "dasypodidae": "armadillo",
}


def speciesnet_label_to_key(label: str) -> str:
    """Convert a SpeciesNet prediction label to our internal species key.

    SpeciesNet uses scientific names or higher taxa. We map these to
    the snake_case keys used in config/species_reference.py.

    Falls back to a cleaned version of the label if no mapping exists.
    """
    if not label:
        return "unknown"

    # Normalize: lowercase, replace spaces with underscores
    normalized = label.lower().strip().replace(" ", "_")

    # Direct lookup
    if normalized in _SCIENTIFIC_TO_KEY:
        return _SCIENTIFIC_TO_KEY[normalized]

    # Check if any key contains this as a substring (for partial matches)
    for sci_name, key in _SCIENTIFIC_TO_KEY.items():
        if sci_name in normalized or normalized in sci_name:
            return key

    # Return cleaned label as-is (unknown species still get tracked)
    return normalized

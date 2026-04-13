"""Species reference lookup table.

Maps species_key to ecological metadata: common name, scientific name,
native/invasive status, ESA listing, and corridor movement weights.
15 species for Edwards Plateau demo.
"""

from typing import Optional

SPECIES_REFERENCE = {
    "white_tailed_deer": {
        "common_name": "White-tailed Deer",
        "scientific_name": "Odocoileus virginianus",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.25, "ridge": 0.15, "forest_grass_edge": 0.30, "forest_crop_edge": 0.20, "wetland_margin": 0.10}
    },
    "feral_hog": {
        "common_name": "Feral Hog",
        "scientific_name": "Sus scrofa",
        "native": False, "invasive": True, "esa_status": None,
        "corridor_weights": {"riparian": 0.45, "ridge": 0.05, "forest_grass_edge": 0.20, "forest_crop_edge": 0.20, "wetland_margin": 0.10},
        "damage_model": {
            "source": "USDA-APHIS",
            "base_cost_per_acre_per_year": 53.79,
            "frequency_multiplier_curve": "logistic",
            "ecoregion_calibration": {
                "edwards_plateau": 1.15,
                "cross_timbers": 1.0,
                "east_central_texas_plains": 0.95,
                "western_gulf_coastal_plain": 1.05
            }
        }
    },
    "turkey": {
        "common_name": "Wild Turkey",
        "scientific_name": "Meleagris gallopavo",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.15, "ridge": 0.30, "forest_grass_edge": 0.25, "forest_crop_edge": 0.15, "wetland_margin": 0.15}
    },
    "coyote": {
        "common_name": "Coyote", "scientific_name": "Canis latrans",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.20, "ridge": 0.20, "forest_grass_edge": 0.25, "forest_crop_edge": 0.20, "wetland_margin": 0.15}
    },
    "black_bear": {
        "common_name": "Black Bear", "scientific_name": "Ursus americanus",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.35, "ridge": 0.25, "forest_grass_edge": 0.15, "forest_crop_edge": 0.10, "wetland_margin": 0.15}
    },
    "bobcat": {
        "common_name": "Bobcat", "scientific_name": "Lynx rufus",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.30, "ridge": 0.25, "forest_grass_edge": 0.20, "forest_crop_edge": 0.10, "wetland_margin": 0.15}
    },
    "elk": {
        "common_name": "Elk", "scientific_name": "Cervus canadensis",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.20, "ridge": 0.25, "forest_grass_edge": 0.30, "forest_crop_edge": 0.15, "wetland_margin": 0.10}
    },
    "axis_deer": {
        "common_name": "Axis Deer", "scientific_name": "Axis axis",
        "native": False, "invasive": True, "esa_status": None,
        "corridor_weights": {"riparian": 0.20, "ridge": 0.15, "forest_grass_edge": 0.35, "forest_crop_edge": 0.20, "wetland_margin": 0.10}
    },
    "nilgai": {
        "common_name": "Nilgai", "scientific_name": "Boselaphus tragocamelus",
        "native": False, "invasive": True, "esa_status": None,
        "corridor_weights": {"riparian": 0.15, "ridge": 0.10, "forest_grass_edge": 0.30, "forest_crop_edge": 0.30, "wetland_margin": 0.15}
    },
    "armadillo": {
        "common_name": "Nine-banded Armadillo", "scientific_name": "Dasypus novemcinctus",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.30, "ridge": 0.10, "forest_grass_edge": 0.25, "forest_crop_edge": 0.20, "wetland_margin": 0.15}
    },
    "raccoon": {
        "common_name": "Raccoon", "scientific_name": "Procyon lotor",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.40, "ridge": 0.10, "forest_grass_edge": 0.20, "forest_crop_edge": 0.15, "wetland_margin": 0.15}
    },
    "opossum": {
        "common_name": "Virginia Opossum", "scientific_name": "Didelphis virginiana",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.30, "ridge": 0.10, "forest_grass_edge": 0.25, "forest_crop_edge": 0.20, "wetland_margin": 0.15}
    },
    "cottontail_rabbit": {
        "common_name": "Eastern Cottontail", "scientific_name": "Sylvilagus floridanus",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.15, "ridge": 0.10, "forest_grass_edge": 0.40, "forest_crop_edge": 0.25, "wetland_margin": 0.10}
    },
    "red_fox": {
        "common_name": "Red Fox", "scientific_name": "Vulpes vulpes",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.20, "ridge": 0.15, "forest_grass_edge": 0.30, "forest_crop_edge": 0.20, "wetland_margin": 0.15}
    },
    "gray_fox": {
        "common_name": "Gray Fox", "scientific_name": "Urocyon cinereoargenteus",
        "native": True, "invasive": False, "esa_status": None,
        "corridor_weights": {"riparian": 0.25, "ridge": 0.20, "forest_grass_edge": 0.25, "forest_crop_edge": 0.15, "wetland_margin": 0.15}
    }
}


def assign_risk_flag(species_key: str, detection_frequency_pct: float) -> Optional[str]:
    ref = SPECIES_REFERENCE.get(species_key)
    if not ref:
        return None
    if ref["invasive"]:
        if detection_frequency_pct >= 70: return "INVASIVE — HIGH"
        elif detection_frequency_pct >= 30: return "INVASIVE — MODERATE"
        else: return "INVASIVE — LOW"
    if ref["esa_status"]:
        return f"ESA — {ref['esa_status']}"
    return None


def confidence_to_grade(pct: float) -> str:
    """Convert confidence percentage to letter grade.

    Scale calibrated for realistic camera-trap deployments where
    14-30 cameras cover 1,000-5,000 acres. Full corridor coverage
    is impractical at field-deployable densities, so the scale
    rewards strong placement over brute-force density.
    """
    if pct >= 30: return "A"
    if pct >= 25: return "A-"
    if pct >= 20: return "B+"
    if pct >= 16: return "B"
    if pct >= 12: return "B-"
    if pct >= 9:  return "C+"
    if pct >= 7:  return "C"
    if pct >= 5:  return "C-"
    if pct >= 3:  return "D"
    return "F"

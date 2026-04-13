"""Extract covariates for camera placement bias correction.

Builds a covariate matrix for propensity score modeling. Each row
is either a camera location (label=1) or a random reference point
within the parcel boundary (label=0).

Covariates (per Kolowski & Forrester 2017 + Tanwar et al. 2021):
  - placement_context: feeder/trail/food_plot/water/random/other
    (most predictive — feeder cameras detect 9.7× more than random)
  - distance_to_water_m: from NHD/fingerprint data
  - distance_to_road_m: estimated from parcel geometry
  - slope_degrees: from elevation data
  - canopy_cover_pct: from NLCD Tree Canopy
  - distance_to_edge_m: distance to NLCD class boundary
  - relative_elevation: (elev - parcel_min) / (parcel_max - parcel_min)
  - nlcd_code: land cover class (categorical)
  - aspect: N/S/E/W (categorical, from slope/aspect)
  - mean_temp_c: mean temperature during deployment (Madsen et al. 2020)
  - total_precip_mm: total precipitation during deployment

In production: camera covariates from habitat fingerprints + API calls;
reference points from ST_GeneratePoints(parcel_boundary, 500).

In demo: camera covariates from cameras.json; reference points generated
with distributions shifted to represent landscape averages (cameras are
biased toward water, feeders, roads — reference points are not).
"""

import json
import math
import random
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np


# ═══════════════════════════════════════════════════════════════════════════
# Parcel geometry utilities
# ═══════════════════════════════════════════════════════════════════════════

def _load_parcel_boundary(demo: bool = False) -> List[Tuple[float, float]]:
    """Load parcel boundary as list of (lon, lat) vertices."""
    if demo:
        path = (Path(__file__).parent.parent
                / "demo" / "demo_data" / "parcel.geojson")
        with open(path) as f:
            geojson = json.load(f)
        coords = geojson["features"][0]["geometry"]["coordinates"][0]
        return [(c[0], c[1]) for c in coords]
    raise NotImplementedError("Production parcel loading not implemented")


def _point_in_polygon(px: float, py: float,
                      polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon test. (px, py) = (lon, lat)."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and \
           (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _parcel_bbox(boundary: List[Tuple[float, float]]):
    """Return (min_lon, min_lat, max_lon, max_lat)."""
    lons = [p[0] for p in boundary]
    lats = [p[1] for p in boundary]
    return min(lons), min(lats), max(lons), max(lats)


# ═══════════════════════════════════════════════════════════════════════════
# Reference point generation
# ═══════════════════════════════════════════════════════════════════════════

# Edwards Plateau landscape distribution parameters.
# These represent what a RANDOM point on the landscape looks like.
# Cameras are biased: closer to water, lower slope, near feeders/roads.
# Reference points reflect the true landscape.
_LANDSCAPE_PARAMS = {
    "distance_to_water_m": {"mean": 420, "std": 180, "min": 10, "max": 900},
    "distance_to_road_m":  {"mean": 550, "std": 250, "min": 20, "max": 1200},
    "slope_degrees":       {"mean": 6.5, "std": 3.5, "min": 0.5, "max": 25},
    "canopy_cover_pct":    {"mean": 28, "std": 18, "min": 0, "max": 80},
    "elevation_m":         {"mean": 535, "std": 25, "min": 480, "max": 600},
    "distance_to_edge_m":  {"mean": 350, "std": 200, "min": 5, "max": 800},
    "mean_temp_c":         {"mean": 19.2, "std": 1.5, "min": 14, "max": 26},
    "total_precip_mm":     {"mean": 680, "std": 120, "min": 400, "max": 1000},
}

# NLCD distribution for Edwards Plateau landscape (approximate %)
_NLCD_LANDSCAPE_DIST = {
    41: 0.35,   # Deciduous Forest
    52: 0.30,   # Shrub/Scrub
    71: 0.25,   # Grassland
    42: 0.05,   # Evergreen Forest
    81: 0.03,   # Pasture/Hay
    82: 0.02,   # Cultivated Crops
}

# Aspect categories
_ASPECTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def generate_reference_points(boundary: List[Tuple[float, float]],
                              n_points: int = 500,
                              seed: int = 42) -> List[Dict]:
    """Generate random reference points within parcel boundary.

    In production: ST_GeneratePoints(parcel_boundary, 500) in PostGIS,
    then query covariates at each point from spatial layers, excluding
    water and developed NLCD classes.

    In demo: uniform spatial sampling within boundary, then assign
    realistic covariate values from landscape distribution parameters.
    Reference points are NOT biased toward water/feeders/roads —
    they represent the true landscape distribution.
    """
    rng = np.random.default_rng(seed)
    py_rng = random.Random(seed)
    bbox = _parcel_bbox(boundary)

    points = []
    attempts = 0
    while len(points) < n_points and attempts < n_points * 20:
        lon = rng.uniform(bbox[0], bbox[2])
        lat = rng.uniform(bbox[1], bbox[3])
        attempts += 1

        if _point_in_polygon(lon, lat, boundary):
            points.append((lon, lat))

    # Assign covariates from landscape distributions
    reference_rows = []
    nlcd_codes = list(_NLCD_LANDSCAPE_DIST.keys())
    nlcd_weights = list(_NLCD_LANDSCAPE_DIST.values())

    for i, (lon, lat) in enumerate(points):
        row = {
            "point_id": f"REF-{i:04d}",
            "lat": lat,
            "lon": lon,
            "is_camera": 0,
            "placement_context": "none",
        }

        for cov, params in _LANDSCAPE_PARAMS.items():
            val = rng.normal(params["mean"], params["std"])
            val = max(params["min"], min(params["max"], val))
            row[cov] = round(val, 2)

        # Relative elevation within parcel
        elev_range = (480, 600)
        row["relative_elevation"] = round(
            (row["elevation_m"] - elev_range[0])
            / (elev_range[1] - elev_range[0]), 3)

        # NLCD from landscape distribution (exclude water/developed)
        row["nlcd_code"] = int(rng.choice(nlcd_codes, p=nlcd_weights))

        # Aspect (roughly uniform)
        row["aspect"] = py_rng.choice(_ASPECTS)

        reference_rows.append(row)

    return reference_rows


# ═══════════════════════════════════════════════════════════════════════════
# Camera covariate extraction
# ═══════════════════════════════════════════════════════════════════════════

def extract_camera_covariates(cameras_json: List[Dict] = None,
                              demo: bool = False,
                              seed: int = 42) -> List[Dict]:
    """Extract covariates for camera locations.

    In production: queries from habitat_fingerprints + spatial APIs.
    In demo: reads from cameras.json and adds estimated covariates
    that reflect placement bias.

    Camera locations are BIASED:
    - Feeders/food plots: close to roads (ATV access), low slope
    - Water: very close to water (by definition)
    - Trail: near game trails, moderate distance to water, on ridges
    - Random: should look like reference points
    """
    if demo and cameras_json is None:
        cam_path = (Path(__file__).parent.parent
                    / "demo" / "demo_data" / "cameras.json")
        with open(cam_path) as f:
            cameras_json = json.load(f)

    rng = np.random.default_rng(seed)
    py_rng = random.Random(seed)

    # Placement-specific covariate distributions.
    # These encode HOW hunters choose camera locations — the key insight
    # from Kolowski & Forrester 2017 and Tanwar et al. 2021.
    _PLACEMENT_BIASES = {
        "feeder": {
            "distance_to_road_m": (120, 50),       # Very close — ATV access
            "distance_to_edge_m": (100, 50),       # Near clearings
            "mean_temp_c": (19.5, 1.0),
            "total_precip_mm": (680, 100),
        },
        "food_plot": {
            "distance_to_road_m": (150, 60),       # Close — equipment access
            "distance_to_edge_m": (30, 20),        # AT the edge (plot = clearing)
            "mean_temp_c": (19.5, 1.0),
            "total_precip_mm": (680, 100),
        },
        "water": {
            "distance_to_road_m": (300, 120),
            "distance_to_edge_m": (80, 40),        # Riparian = edge habitat
            "mean_temp_c": (18.8, 1.2),            # Cooler near water
            "total_precip_mm": (700, 110),
        },
        "trail": {
            "distance_to_road_m": (400, 150),       # Further from roads
            "distance_to_edge_m": (150, 80),
            "mean_temp_c": (19.2, 1.0),
            "total_precip_mm": (670, 100),
        },
        "random": {
            "distance_to_road_m": (500, 220),       # Like landscape average
            "distance_to_edge_m": (320, 180),
            "mean_temp_c": (19.2, 1.5),
            "total_precip_mm": (680, 120),
        },
        "other": {
            "distance_to_road_m": (450, 200),
            "distance_to_edge_m": (250, 130),
            "mean_temp_c": (19.0, 1.3),
            "total_precip_mm": (680, 110),
        },
    }

    camera_rows = []
    elevations = [c["elevation_m"] for c in cameras_json]
    elev_min, elev_max = min(elevations), max(elevations)
    elev_span = max(1, elev_max - elev_min)

    for cam in cameras_json:
        ctx = cam.get("placement_context", "other")
        biases = _PLACEMENT_BIASES.get(ctx, _PLACEMENT_BIASES["other"])

        row = {
            "point_id": cam["camera_id"],
            "lat": cam["lat"],
            "lon": cam["lon"],
            "is_camera": 1,
            "placement_context": ctx,
            # From actual camera data
            "distance_to_water_m": cam["distance_to_water_m"],
            "slope_degrees": cam["slope_degrees"],
            "canopy_cover_pct": cam["canopy_cover_pct"],
            "elevation_m": cam["elevation_m"],
            "nlcd_code": cam["nlcd_code"],
            "relative_elevation": round(
                (cam["elevation_m"] - elev_min) / elev_span, 3),
        }

        # Generated covariates from placement-specific distributions
        for cov in ["distance_to_road_m", "distance_to_edge_m",
                     "mean_temp_c", "total_precip_mm"]:
            mu, sigma = biases[cov]
            val = rng.normal(mu, sigma)
            param = _LANDSCAPE_PARAMS.get(cov, {})
            val = max(param.get("min", 0), min(param.get("max", 9999), val))
            row[cov] = round(val, 2)

        row["aspect"] = py_rng.choice(_ASPECTS)

        camera_rows.append(row)

    return camera_rows


# ═══════════════════════════════════════════════════════════════════════════
# Full covariate matrix builder
# ═══════════════════════════════════════════════════════════════════════════

def build_covariate_matrix(cameras_json: List[Dict] = None,
                           n_reference: int = 500,
                           demo: bool = False) -> Tuple[List[Dict], List[Dict]]:
    """Build the complete covariate matrix for propensity modeling.

    Returns:
        (camera_rows, reference_rows) — each a list of covariate dicts.
        Rows have is_camera = 1 or 0 respectively.
    """
    if demo:
        boundary = _load_parcel_boundary(demo=True)
    else:
        raise NotImplementedError("Production covariate extraction not implemented")

    camera_rows = extract_camera_covariates(
        cameras_json=cameras_json, demo=demo)
    reference_rows = generate_reference_points(
        boundary, n_points=n_reference)

    return camera_rows, reference_rows

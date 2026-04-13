"""Wildlife corridor generation per habitat unit.

Models species-specific movement corridors within each habitat unit.
Corridor coverage is the key ecological quality metric — how well do
our cameras sample each species' expected movement routes?

Corridor types (from settings.py buffer defaults):
  - Riparian: 100m buffer around NHD flowlines
  - Ridge: DEM-derived ridgelines (50m buffer)
  - Forest-grass edge: NLCD boundary between forest and grassland
  - Forest-crop edge: NLCD boundary between forest and cultivated
  - Wetland margin: NLCD boundary of wetland classes

In production: derived from NHD flowlines, USGS 3DEP DEM ridgeline
extraction, and NLCD land cover raster edge detection. Stored as
PostGIS LineString geometries.

In demo: generates realistic corridor segments based on camera
positions and habitat characteristics. Corridors follow plausible
landscape features — riparian corridors connect water-proximate
cameras, ridges connect high-elevation cameras, edges connect
cameras at land cover transitions.
"""

import math
import random
from typing import Dict, List, Tuple

from config import settings
from habitat.store import get_db, _lock, haversine_m


# Typical corridor lengths (km) per type in Edwards Plateau landscape
# These are per habitat unit — total length of that corridor type
_CORRIDOR_LENGTH_RANGES = {
    "riparian":         (1.2, 3.5),   # Stream valleys
    "ridge":            (0.8, 2.5),   # Ridge tops
    "forest_grass_edge": (1.5, 4.0),  # Longest — lots of edge habitat
    "forest_crop_edge":  (0.3, 1.5),  # Limited cultivated land
    "wetland_margin":    (0.2, 0.8),  # Rare on Edwards Plateau
}


def generate_corridors(demo: bool = False) -> List[Dict]:
    """Generate corridor geometries for all habitat units.

    Reads habitat units and camera positions from the store,
    generates corridor line segments appropriate to each unit's
    land cover type, and inserts into the corridors table.

    Returns:
        List of corridor dicts with type, length, and endpoints.
    """
    db = get_db()
    rng = random.Random(42)

    with _lock:
        # Get all habitat units
        units = db.execute("SELECT * FROM habitat_units").fetchall()

    if not units:
        return []

    all_corridors = []

    for unit in units:
        hu_id = unit["id"]
        nlcd_code = unit["nlcd_code"]

        # Get cameras in this unit
        with _lock:
            cameras = db.execute(
                "SELECT camera_id, lat, lon, placement_context "
                "FROM camera_stations WHERE habitat_unit_id = ?",
                (hu_id,)).fetchall()

        if not cameras:
            continue

        # Generate corridors appropriate to this land cover type
        unit_corridors = _generate_unit_corridors(
            hu_id, nlcd_code, cameras, rng)
        all_corridors.extend(unit_corridors)

    # Insert all corridors
    with _lock:
        # Clear existing corridors for clean re-runs
        db.execute("DELETE FROM corridors")

        for corr in all_corridors:
            db.execute("""
                INSERT INTO corridors
                    (habitat_unit_id, corridor_type, length_km,
                     start_lat, start_lon, end_lat, end_lon)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                corr["habitat_unit_id"], corr["corridor_type"],
                corr["length_km"],
                corr["start_lat"], corr["start_lon"],
                corr["end_lat"], corr["end_lon"],
            ))

        db.commit()

    return all_corridors


def _generate_unit_corridors(hu_id: str, nlcd_code: int,
                             cameras: list, rng: random.Random
                             ) -> List[Dict]:
    """Generate corridor segments for a single habitat unit.

    Corridor placement depends on land cover type:
    - Forest (41/42/43): strong riparian + edge corridors
    - Shrub/Scrub (52): strong ridge corridors
    - Grassland (71): strong edge + riparian corridors
    """
    corridors = []
    cam_positions = [(c["lat"], c["lon"]) for c in cameras]

    # Determine which corridor types are present and their relative
    # importance based on NLCD code
    type_weights = _corridor_weights_for_nlcd(nlcd_code)

    for corr_type, weight in type_weights.items():
        if weight < 0.1:
            continue

        min_km, max_km = _CORRIDOR_LENGTH_RANGES[corr_type]
        # Scale length by weight — more important corridors are longer
        total_length_km = rng.uniform(min_km, max_km) * weight

        # Generate 2-4 segments per corridor type
        n_segments = rng.randint(2, 4)
        seg_length_km = total_length_km / n_segments

        for seg_i in range(n_segments):
            seg = _generate_segment(
                corr_type, seg_length_km, cam_positions, rng)
            seg["habitat_unit_id"] = hu_id
            corridors.append(seg)

    return corridors


def _corridor_weights_for_nlcd(nlcd_code: int) -> Dict[str, float]:
    """Determine corridor type importance based on NLCD land cover.

    Returns scaling factors (0-1) for each corridor type.
    """
    if nlcd_code in (41, 42, 43):
        # Forest — strong riparian and edge corridors
        return {
            "riparian": 1.0,
            "ridge": 0.6,
            "forest_grass_edge": 1.0,
            "forest_crop_edge": 0.5,
            "wetland_margin": 0.3,
        }
    elif nlcd_code == 52:
        # Shrub/Scrub — strong ridge and edge corridors
        return {
            "riparian": 0.6,
            "ridge": 1.0,
            "forest_grass_edge": 0.8,
            "forest_crop_edge": 0.3,
            "wetland_margin": 0.2,
        }
    elif nlcd_code == 71:
        # Grassland — strong edge and riparian corridors
        return {
            "riparian": 0.8,
            "ridge": 0.4,
            "forest_grass_edge": 1.0,
            "forest_crop_edge": 0.7,
            "wetland_margin": 0.4,
        }
    else:
        # Default
        return {
            "riparian": 0.6,
            "ridge": 0.5,
            "forest_grass_edge": 0.5,
            "forest_crop_edge": 0.3,
            "wetland_margin": 0.3,
        }


def _generate_segment(corr_type: str, length_km: float,
                      cam_positions: List[Tuple[float, float]],
                      rng: random.Random) -> Dict:
    """Generate a single corridor segment near camera positions.

    Corridor segments are placed to partially overlap camera detection
    radii (realistic — cameras are often placed along corridors) but
    also extend beyond them (creating coverage gaps for gap analysis).
    """
    # Pick a random camera as anchor
    anchor_lat, anchor_lon = rng.choice(cam_positions)

    # Convert length to approximate lat/lon delta
    # 1 km ≈ 0.009 degrees latitude, ≈ 0.0104 degrees longitude at 30°N
    km_to_lat = 0.009
    km_to_lon = 0.0104

    # Random bearing
    bearing = rng.uniform(0, 2 * math.pi)

    # Offset start point slightly from anchor (corridor passes near camera)
    offset_km = rng.uniform(0.05, 0.3)
    start_lat = anchor_lat + offset_km * km_to_lat * math.sin(bearing + 1.2)
    start_lon = anchor_lon + offset_km * km_to_lon * math.cos(bearing + 1.2)

    # End point along bearing
    end_lat = start_lat + length_km * km_to_lat * math.sin(bearing)
    end_lon = start_lon + length_km * km_to_lon * math.cos(bearing)

    # Corridor-type-specific placement adjustments
    if corr_type == "riparian":
        # Bias toward lower elevation (water-proximate cameras)
        start_lat -= 0.001 * rng.random()
    elif corr_type == "ridge":
        # Bias toward higher elevation
        start_lat += 0.001 * rng.random()

    actual_length_km = round(
        haversine_m(start_lat, start_lon, end_lat, end_lon) / 1000.0, 3)

    return {
        "corridor_type": corr_type,
        "length_km": round(actual_length_km, 3),
        "start_lat": round(start_lat, 6),
        "start_lon": round(start_lon, 6),
        "end_lat": round(end_lat, 6),
        "end_lon": round(end_lon, 6),
    }


def get_corridors(habitat_unit_id: str) -> List[Dict]:
    """Retrieve all corridors for a habitat unit."""
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM corridors WHERE habitat_unit_id = ?",
        (habitat_unit_id,))
    return [dict(row) for row in cursor]


def get_corridor_summary(habitat_unit_id: str) -> Dict[str, float]:
    """Get total corridor length (km) per type for a habitat unit."""
    db = get_db()
    cursor = db.execute("""
        SELECT corridor_type, SUM(length_km) as total_km,
               COUNT(*) as n_segments
        FROM corridors
        WHERE habitat_unit_id = ?
        GROUP BY corridor_type
    """, (habitat_unit_id,))

    summary = {}
    for row in cursor:
        summary[row["corridor_type"]] = {
            "total_km": round(row["total_km"], 3),
            "segments": row["n_segments"],
        }
    return summary

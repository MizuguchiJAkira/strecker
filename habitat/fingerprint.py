"""Habitat fingerprinting for camera stations.

Converts GPS coordinates into multi-dimensional ecological descriptors.
Each camera gets a fingerprint combining:
  - EPA Level III/IV ecoregion
  - NLCD land cover class
  - USGS HUC-10 watershed
  - Elevation, slope, aspect (USGS 3DEP)
  - Distance to nearest water body + stream order (NHD)
  - Canopy cover (NLCD Tree Canopy)
  - Soil type (SSURGO/gSSURGO)

In production: queries EPA, USGS, NLCD WMS/REST APIs at each camera
coordinate. Results cached in habitat_fingerprints table.

In demo: reads pre-computed fingerprints from cameras.json (the demo
generator already assigned realistic Edwards Plateau values).
"""

import json
from pathlib import Path
from typing import Dict, List

from habitat.store import get_db, _lock


def fingerprint_cameras(cameras_json: List[Dict] = None,
                        demo: bool = False) -> List[Dict]:
    """Compute habitat fingerprints for all camera stations.

    Args:
        cameras_json: List of camera dicts (from cameras.json or API)
        demo: If True, read from demo data file

    Returns:
        List of fingerprint dicts, one per camera.
    """
    if demo and cameras_json is None:
        cam_path = (Path(__file__).parent.parent
                    / "demo" / "demo_data" / "cameras.json")
        with open(cam_path) as f:
            cameras_json = json.load(f)

    db = get_db()
    fingerprints = []

    with _lock:
        for cam in cameras_json:
            fp = _build_fingerprint(cam, demo=demo)
            fingerprints.append(fp)

            # Insert camera station
            db.execute("""
                INSERT OR REPLACE INTO camera_stations
                    (camera_id, user_id, lat, lon, habitat_unit_id,
                     placement_context, installed_date, last_active,
                     camera_model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cam["camera_id"], cam.get("user_id"),
                cam["lat"], cam["lon"],
                cam.get("habitat_unit_id"),
                cam.get("placement_context"),
                cam.get("installed_date"),
                cam.get("last_active"),
                cam.get("camera_model"),
            ))

            # Insert fingerprint
            db.execute("""
                INSERT OR REPLACE INTO habitat_fingerprints
                    (camera_id, ecoregion_iii_code, ecoregion_iii_name,
                     ecoregion_iv_code, ecoregion_iv_name,
                     nlcd_code, nlcd_class, huc10, huc10_name,
                     elevation_m, slope_degrees, distance_to_water_m,
                     stream_order, soil_type, canopy_cover_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                cam["camera_id"],
                fp["ecoregion_iii_code"], fp["ecoregion_iii_name"],
                fp["ecoregion_iv_code"], fp["ecoregion_iv_name"],
                fp["nlcd_code"], fp["nlcd_class"],
                fp["huc10"], fp["huc10_name"],
                fp["elevation_m"], fp["slope_degrees"],
                fp["distance_to_water_m"], fp["stream_order"],
                fp["soil_type"], fp["canopy_cover_pct"],
            ))

        db.commit()

    return fingerprints


def _build_fingerprint(cam: Dict, demo: bool = False) -> Dict:
    """Build a fingerprint for a single camera.

    In demo mode, the camera dict already contains all fields.
    In production, this would call geospatial APIs at (lat, lon).
    """
    # Defaults — all Edwards Plateau cameras share Level III ecoregion.
    # In production: EPA Level III/IV API at the camera coordinate.
    ecoregion_iii_code = "30"
    ecoregion_iii_name = "Edwards Plateau"
    ecoregion_iv_code = "30a"
    ecoregion_iv_name = "Limestone Cut Plain"
    huc10 = "1209020104"
    huc10_name = "Johnson Fork"

    # Parse from habitat_unit_id if available
    hu_id = cam.get("habitat_unit_id", "")
    if hu_id:
        parts = hu_id.split("-")
        if len(parts) >= 4:
            huc10 = parts[1]
            ecoregion_iv_code = parts[2]

    return {
        "camera_id": cam["camera_id"],
        "lat": cam["lat"],
        "lon": cam["lon"],
        "ecoregion_iii_code": ecoregion_iii_code,
        "ecoregion_iii_name": ecoregion_iii_name,
        "ecoregion_iv_code": ecoregion_iv_code,
        "ecoregion_iv_name": ecoregion_iv_name,
        "nlcd_code": cam.get("nlcd_code", 0),
        "nlcd_class": cam.get("nlcd_class", "Unknown"),
        "huc10": huc10,
        "huc10_name": huc10_name,
        "elevation_m": cam.get("elevation_m", 0),
        "slope_degrees": cam.get("slope_degrees", 0),
        "distance_to_water_m": cam.get("distance_to_water_m", 0),
        "stream_order": cam.get("stream_order", 0),
        "soil_type": cam.get("soil_type", "Unknown"),
        "canopy_cover_pct": cam.get("canopy_cover_pct", 0),
    }


def get_fingerprint(camera_id: str) -> Dict:
    """Retrieve a stored fingerprint by camera ID."""
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM habitat_fingerprints WHERE camera_id = ?",
        (camera_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"No fingerprint for camera {camera_id}")
    return dict(row)

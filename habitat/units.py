"""Habitat unit delineation.

Defines habitat units as the geographic intersection of
HUC-10 watershed, EPA Level IV ecoregion, and NLCD land cover class.
Each unit gets a unique ID: HU-{huc10}-{ecoregion_iv}-{nlcd}.

This is the fundamental spatial unit for Basal Informatics. Individual
camera detections are useless to an insurer — they need parcel-level
assessments. Habitat units aggregate cameras into ecologically coherent
zones so that:

  1. Species confidence is computed per zone (not per camera)
  2. Bias correction accounts for placement context within each zone
  3. Corridor coverage is measured against expected movement routes
  4. Risk assessments map parcels to overlapping habitat units

In production: units are created from the intersection of NLCD raster,
EPA ecoregion polygons, and USGS WBD HUC-10 boundaries, stored as
MultiPolygon geometries in PostGIS.

In demo: units are derived from the camera fingerprints by grouping
cameras with matching (huc10, ecoregion_iv, nlcd_code).
"""

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Tuple

from habitat.store import get_db, _lock


# NLCD code → class name mapping (subset relevant to Edwards Plateau)
NLCD_CLASSES = {
    11: "Open Water",
    21: "Developed, Open Space",
    22: "Developed, Low Intensity",
    31: "Barren Land",
    41: "Deciduous Forest",
    42: "Evergreen Forest",
    43: "Mixed Forest",
    52: "Shrub/Scrub",
    71: "Grassland/Herbaceous",
    81: "Pasture/Hay",
    82: "Cultivated Crops",
    90: "Woody Wetlands",
    95: "Emergent Herbaceous Wetlands",
}


def delineate_units(demo: bool = False) -> List[Dict]:
    """Group cameras into habitat units by HUC-10 x ecoregion IV x NLCD.

    Reads from habitat_fingerprints (populated by fingerprint.py),
    groups cameras, computes aggregate stats, and inserts into
    habitat_units table.

    Returns:
        List of habitat unit dicts with stats.
    """
    db = get_db()

    with _lock:
        # Read all fingerprints
        cursor = db.execute("""
            SELECT f.camera_id, f.huc10, f.huc10_name,
                   f.ecoregion_iv_code, f.ecoregion_iv_name,
                   f.ecoregion_iii_code, f.ecoregion_iii_name,
                   f.nlcd_code, f.nlcd_class,
                   c.user_id, c.installed_date, c.last_active,
                   c.lat, c.lon
            FROM habitat_fingerprints f
            JOIN camera_stations c ON f.camera_id = c.camera_id
        """)
        rows = cursor.fetchall()

    if not rows:
        return []

    # Group cameras by (huc10, ecoregion_iv, nlcd_code)
    groups = defaultdict(list)
    for row in rows:
        key = (row["huc10"], row["ecoregion_iv_code"], row["nlcd_code"])
        groups[key].append(dict(row))

    units = []
    with _lock:
        for (huc10, eco_iv, nlcd_code), cameras in groups.items():
            hu_id = f"HU-{huc10}-{eco_iv}-{nlcd_code}"

            # Aggregate stats
            n_cameras = len(cameras)
            user_ids = set(c["user_id"] for c in cameras if c["user_id"])
            n_users = len(user_ids)

            # Monitoring period
            install_dates = []
            active_dates = []
            for c in cameras:
                if c["installed_date"]:
                    install_dates.append(
                        datetime.strptime(c["installed_date"], "%Y-%m-%d"))
                if c["last_active"]:
                    active_dates.append(
                        datetime.strptime(c["last_active"], "%Y-%m-%d"))

            if install_dates and active_dates:
                mon_start = min(install_dates)
                mon_end = max(active_dates)
                mon_months = max(1, int(
                    (mon_end - mon_start).days / 30.44))
                total_cam_nights = sum(
                    (datetime.strptime(c["last_active"], "%Y-%m-%d")
                     - datetime.strptime(c["installed_date"], "%Y-%m-%d")).days
                    for c in cameras
                    if c["installed_date"] and c["last_active"]
                )
            else:
                mon_start = None
                mon_end = None
                mon_months = 0
                total_cam_nights = 0

            # Area estimate — convex hull of camera positions (rough)
            # In production: actual polygon area from raster intersection
            lats = [c["lat"] for c in cameras]
            lons = [c["lon"] for c in cameras]
            lat_span = max(lats) - min(lats) if len(lats) > 1 else 0.01
            lon_span = max(lons) - min(lons) if len(lons) > 1 else 0.01
            # Rough km2: 1 degree lat ≈ 111km, 1 degree lon ≈ 95km at 30°N
            area_km2 = round(lat_span * 111 * lon_span * 95, 2)
            area_km2 = max(area_km2, 0.5)  # Minimum 0.5 km2

            # Metadata from first camera
            first = cameras[0]
            nlcd_class = first.get("nlcd_class",
                                   NLCD_CLASSES.get(nlcd_code, "Unknown"))

            unit = {
                "id": hu_id,
                "huc10": huc10,
                "huc10_name": first.get("huc10_name", ""),
                "ecoregion_iv_code": eco_iv,
                "ecoregion_iv_name": first.get("ecoregion_iv_name", ""),
                "ecoregion_iii_code": first.get("ecoregion_iii_code", ""),
                "ecoregion_iii_name": first.get("ecoregion_iii_name", ""),
                "nlcd_code": nlcd_code,
                "nlcd_class": nlcd_class,
                "area_km2": area_km2,
                "camera_count": n_cameras,
                "total_camera_nights": total_cam_nights,
                "contributing_users": n_users,
                "monitoring_start": (mon_start.strftime("%Y-%m-%d")
                                     if mon_start else None),
                "monitoring_end": (mon_end.strftime("%Y-%m-%d")
                                   if mon_end else None),
                "monitoring_months": mon_months,
                "cameras": [c["camera_id"] for c in cameras],
            }
            units.append(unit)

            # Insert into habitat_units table
            db.execute("""
                INSERT OR REPLACE INTO habitat_units
                    (id, huc10, huc10_name,
                     ecoregion_iv_code, ecoregion_iv_name,
                     ecoregion_iii_code, ecoregion_iii_name,
                     nlcd_code, nlcd_class, area_km2,
                     camera_count, total_camera_nights,
                     contributing_users,
                     monitoring_start, monitoring_end,
                     monitoring_months)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                hu_id, huc10, first.get("huc10_name", ""),
                eco_iv, first.get("ecoregion_iv_name", ""),
                first.get("ecoregion_iii_code", ""),
                first.get("ecoregion_iii_name", ""),
                nlcd_code, nlcd_class, area_km2,
                n_cameras, total_cam_nights, n_users,
                unit["monitoring_start"], unit["monitoring_end"],
                mon_months,
            ))

        db.commit()

    return units


def get_unit(hu_id: str) -> Dict:
    """Retrieve a habitat unit by ID."""
    db = get_db()
    cursor = db.execute("SELECT * FROM habitat_units WHERE id = ?", (hu_id,))
    row = cursor.fetchone()
    if not row:
        raise ValueError(f"Habitat unit {hu_id} not found")
    return dict(row)


def get_cameras_in_unit(hu_id: str) -> List[Dict]:
    """Get all cameras assigned to a habitat unit."""
    db = get_db()
    cursor = db.execute(
        "SELECT * FROM camera_stations WHERE habitat_unit_id = ?",
        (hu_id,))
    return [dict(row) for row in cursor]

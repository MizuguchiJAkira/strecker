"""Species confidence scoring per habitat unit.

Computes per-species confidence scores based on how well cameras
cover each species' expected movement corridors. This is the core
ecological quality metric — it answers: "How confident are we that
our camera network captured the true activity of this species in
this habitat unit?"

Algorithm for each species × habitat unit:

  1. Load corridor_weights from SPECIES_REFERENCE
  2. Compute weighted corridor length:
       WCL = sum(corridor_km[type] * weight[type])
  3. For each camera, compute species-specific detection radius
     (large 200m, medium 150m, small 100m from settings)
  4. Calculate corridor coverage: what % of weighted corridor length
     falls within detection radius of >= 1 camera
  5. Temporal modifier: monitoring_months / 12 (full year = 1.0)
  6. Detection frequency modifier:
     - Cameras that detected the species: weight 1.0
     - Cameras that didn't detect: weight 0.5
       (they still cover corridor but absence is less informative)
  7. Overall confidence = corridor_coverage * temporal * detection_freq
  8. Convert to letter grade via confidence_to_grade()

Result stored in species_confidence table.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from config import settings
from config.species_reference import (
    SPECIES_REFERENCE, confidence_to_grade,
)
from habitat.store import get_db, _lock, point_to_segment_distance_m


# Species body-size classification for detection radius
_SPECIES_SIZE = {
    "white_tailed_deer": "large",
    "feral_hog":         "large",
    "elk":               "large",
    "axis_deer":         "large",
    "nilgai":            "large",
    "black_bear":        "large",
    "coyote":            "medium",
    "bobcat":            "medium",
    "turkey":            "medium",
    "red_fox":           "medium",
    "gray_fox":          "medium",
    "raccoon":           "small",
    "armadillo":         "small",
    "opossum":           "small",
    "cottontail_rabbit": "small",
}


def compute_confidence(detections=None, demo: bool = False) -> List[Dict]:
    """Compute species confidence scores for all habitat units.

    Reads corridors, camera positions, and detection data from the
    store. For each species present in each habitat unit, computes
    corridor coverage, temporal modifier, detection frequency, and
    overall confidence.

    Args:
        detections: List of Detection objects (from ingest + classify).
                    If None, reads from SQLite detections table.
        demo: If True, use demo data mode.

    Returns:
        List of species_confidence dicts.
    """
    db = get_db()

    # ── Seed detections into SQLite if provided directly ──
    if detections is not None:
        _seed_detections(db, detections)

    # ── Get all habitat units ──
    with _lock:
        units = db.execute("SELECT * FROM habitat_units").fetchall()

    if not units:
        return []

    all_confidence = []

    for unit in units:
        hu_id = unit["id"]
        mon_months = unit["monitoring_months"] or 0

        # Get cameras in this unit
        with _lock:
            cameras = db.execute("""
                SELECT camera_id, lat, lon
                FROM camera_stations
                WHERE habitat_unit_id = ?
            """, (hu_id,)).fetchall()

        if not cameras:
            continue

        # Get corridors in this unit
        with _lock:
            corridors = db.execute("""
                SELECT corridor_type, length_km,
                       start_lat, start_lon, end_lat, end_lon
                FROM corridors
                WHERE habitat_unit_id = ?
            """, (hu_id,)).fetchall()

        # Get species detected per camera
        with _lock:
            det_rows = db.execute("""
                SELECT camera_id, species_key,
                       COUNT(*) as n_photos,
                       COUNT(DISTINCT independent_event_id) as n_events,
                       AVG(confidence_calibrated) as mean_cal
                FROM detections
                WHERE camera_id IN (
                    SELECT camera_id FROM camera_stations
                    WHERE habitat_unit_id = ?
                )
                GROUP BY camera_id, species_key
            """, (hu_id,)).fetchall()

        # Build detection lookup: camera_id → set of species
        camera_species = defaultdict(dict)
        for dr in det_rows:
            camera_species[dr["camera_id"]][dr["species_key"]] = {
                "n_photos": dr["n_photos"],
                "n_events": dr["n_events"],
                "mean_cal": dr["mean_cal"],
            }

        # Get all species detected in this unit
        species_in_unit = set()
        for cam_sp in camera_species.values():
            species_in_unit.update(cam_sp.keys())

        # Compute confidence for each species
        for sp_key in species_in_unit:
            ref = SPECIES_REFERENCE.get(sp_key)
            if not ref:
                continue

            weights = ref.get("corridor_weights", {})
            if not weights:
                continue

            # ── 1. Corridor coverage ──
            coverage = _compute_corridor_coverage(
                sp_key, cameras, corridors, weights)

            # ── 2. Temporal modifier ──
            temporal = min(1.0, mon_months / 12.0) if mon_months > 0 else 0.5

            # ── 3. Detection frequency modifier ──
            n_cameras_total = len(cameras)
            n_cameras_detected = sum(
                1 for c in cameras
                if sp_key in camera_species.get(c["camera_id"], {}))
            # Detected cameras = 1.0, non-detected = 0.5
            det_freq = ((n_cameras_detected * 1.0
                         + (n_cameras_total - n_cameras_detected) * 0.5)
                        / n_cameras_total)

            # ── 4. Overall confidence ──
            overall = coverage * temporal * det_freq * 100.0
            overall = min(100.0, round(overall, 1))

            # ── 5. Grade ──
            grade = confidence_to_grade(overall)

            # Aggregate species stats
            total_events = sum(
                cs.get(sp_key, {}).get("n_events", 0)
                for cs in camera_species.values())
            total_photos = sum(
                cs.get(sp_key, {}).get("n_photos", 0)
                for cs in camera_species.values())
            mean_cls_conf = 0.0
            cls_count = 0
            for cs in camera_species.values():
                if sp_key in cs and cs[sp_key]["mean_cal"]:
                    mean_cls_conf += cs[sp_key]["mean_cal"]
                    cls_count += 1
            mean_cls_conf = (mean_cls_conf / cls_count * 100.0
                             if cls_count else 0.0)

            raw_det_freq_pct = (n_cameras_detected / n_cameras_total * 100.0
                                if n_cameras_total else 0.0)

            conf_entry = {
                "habitat_unit_id": hu_id,
                "species_key": sp_key,
                "common_name": ref.get("common_name", sp_key),
                "total_detections": total_events,
                "total_photos": total_photos,
                "cameras_detected": n_cameras_detected,
                "cameras_total": n_cameras_total,
                "detection_frequency_pct": round(raw_det_freq_pct, 1),
                "classification_confidence_pct": round(mean_cls_conf, 1),
                "corridor_coverage_pct": round(coverage * 100, 1),
                "temporal_modifier": round(temporal, 3),
                "detection_freq_modifier": round(det_freq, 3),
                "overall_confidence_pct": overall,
                "confidence_grade": grade,
                "monitoring_months": mon_months,
            }
            all_confidence.append(conf_entry)

            # Insert into species_confidence table
            with _lock:
                db.execute("""
                    INSERT OR REPLACE INTO species_confidence
                        (habitat_unit_id, species_key,
                         total_detections, cameras_detected,
                         cameras_total, detection_frequency_pct,
                         raw_detection_frequency_pct,
                         classification_confidence_pct,
                         corridor_coverage_pct,
                         overall_confidence_pct, confidence_grade,
                         monitoring_start, monitoring_end,
                         monitoring_months)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    hu_id, sp_key,
                    total_events, n_cameras_detected,
                    n_cameras_total, round(raw_det_freq_pct, 1),
                    round(raw_det_freq_pct, 1),
                    round(mean_cls_conf, 1),
                    round(coverage * 100, 1),
                    overall, grade,
                    unit["monitoring_start"], unit["monitoring_end"],
                    mon_months,
                ))

        with _lock:
            db.commit()

    return all_confidence


def _compute_corridor_coverage(species_key: str,
                               cameras: list,
                               corridors: list,
                               weights: Dict[str, float]) -> float:
    """Compute what fraction of weighted corridor length is covered.

    For each corridor segment, samples points along the segment and
    checks if any camera's detection radius covers that point.
    The coverage is weighted by the species' corridor_weights.

    Returns a value in [0, 1].
    """
    if not corridors:
        return 0.5  # No corridors → use default 50%

    # Detection radius for this species
    size = _SPECIES_SIZE.get(species_key, "medium")
    radius_m = settings.DETECTION_RADIUS[size]

    # Camera positions
    cam_positions = [(c["lat"], c["lon"]) for c in cameras]

    weighted_covered = 0.0
    weighted_total = 0.0

    for corr in corridors:
        ctype = corr["corridor_type"]
        weight = weights.get(ctype, 0.0)
        if weight < 0.01:
            continue

        length_km = corr["length_km"]
        weighted_total += length_km * weight

        # Sample points along the corridor segment
        n_samples = max(5, int(length_km * 10))  # ~100m spacing
        covered_samples = 0

        for i in range(n_samples):
            t = i / max(1, n_samples - 1)
            pt_lat = corr["start_lat"] + t * (corr["end_lat"] - corr["start_lat"])
            pt_lon = corr["start_lon"] + t * (corr["end_lon"] - corr["start_lon"])

            # Check if any camera covers this point
            for clat, clon in cam_positions:
                dist = point_to_segment_distance_m(
                    clat, clon, pt_lat, pt_lon, pt_lat, pt_lon)
                if dist <= radius_m:
                    covered_samples += 1
                    break

        frac_covered = covered_samples / n_samples
        weighted_covered += length_km * weight * frac_covered

    if weighted_total < 0.001:
        return 0.5

    return weighted_covered / weighted_total


def _seed_detections(db, detections):
    """Seed Detection objects into the SQLite detections table.

    Creates the table if it doesn't exist. Only inserts if table is
    empty (avoids duplicates on re-run).
    """
    with _lock:
        # Ensure table exists
        db.execute("""
            CREATE TABLE IF NOT EXISTS detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                camera_id TEXT,
                species_key TEXT,
                confidence REAL,
                confidence_calibrated REAL,
                timestamp TEXT,
                image_filename TEXT,
                megadetector_confidence REAL,
                burst_group_id TEXT,
                independent_event_id TEXT,
                review_required INTEGER DEFAULT 0,
                softmax_entropy REAL,
                temporal_prior REAL,
                antler_classification TEXT
            )
        """)
        db.commit()

        count = db.execute("SELECT COUNT(*) FROM detections").fetchone()
        if count and count[0] > 0:
            return

        for det in detections:
            db.execute("""
                INSERT INTO detections
                    (camera_id, species_key, confidence,
                     confidence_calibrated, timestamp, image_filename,
                     megadetector_confidence, burst_group_id,
                     independent_event_id, review_required,
                     softmax_entropy, temporal_prior,
                     antler_classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                det.camera_id, det.species_key, det.confidence,
                det.confidence_calibrated,
                det.timestamp.isoformat(),
                det.image_filename, det.megadetector_confidence,
                det.burst_group_id, det.independent_event_id,
                int(det.review_required),
                det.softmax_entropy, det.temporal_prior,
                det.antler_classification,
            ))

        db.commit()


def get_species_confidence(habitat_unit_id: str,
                           species_key: Optional[str] = None
                           ) -> List[Dict]:
    """Retrieve stored confidence scores for a habitat unit."""
    db = get_db()
    if species_key:
        cursor = db.execute("""
            SELECT * FROM species_confidence
            WHERE habitat_unit_id = ? AND species_key = ?
        """, (habitat_unit_id, species_key))
    else:
        cursor = db.execute("""
            SELECT * FROM species_confidence
            WHERE habitat_unit_id = ?
            ORDER BY overall_confidence_pct DESC
        """, (habitat_unit_id,))
    return [dict(row) for row in cursor]

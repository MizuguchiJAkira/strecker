"""Monitoring gap analysis — identify uncovered corridor segments.

For segments > 200m not covered by any camera's detection radius:
  1. Record corridor type and gap length
  2. Identify which species' confidence would improve most
  3. Estimate cameras needed: segment_length / (detection_radius * 2)
  4. Sort by projected confidence increase descending
  5. Top 3 gaps feed into the PDF report's Data Confidence section

This is INTERNAL (not hunter-facing) — it informs Basal Informatics
about where to recommend additional camera placement, and gives
insurers transparency about data quality limitations.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from config import settings
from config.species_reference import SPECIES_REFERENCE
from habitat.store import get_db, _lock, haversine_m, point_to_segment_distance_m


# Minimum gap length (meters) to flag
MIN_GAP_LENGTH_M = 200.0

# Species size → detection radius (same as confidence.py)
_SPECIES_SIZE = {
    "white_tailed_deer": "large", "feral_hog": "large",
    "elk": "large", "axis_deer": "large", "nilgai": "large",
    "black_bear": "large",
    "coyote": "medium", "bobcat": "medium", "turkey": "medium",
    "red_fox": "medium", "gray_fox": "medium",
    "raccoon": "small", "armadillo": "small",
    "opossum": "small", "cottontail_rabbit": "small",
}


def analyze_gaps(demo: bool = False) -> List[Dict]:
    """Identify uncovered corridor segments across all habitat units.

    For each corridor segment, walks along the segment and finds
    contiguous stretches not covered by any camera. Gaps > 200m are
    flagged with the species most affected and the estimated number
    of cameras needed to close the gap.

    Returns:
        List of gap dicts sorted by projected confidence increase.
    """
    db = get_db()

    with _lock:
        units = db.execute("SELECT id FROM habitat_units").fetchall()

    if not units:
        return []

    all_gaps = []

    for unit in units:
        hu_id = unit["id"]
        unit_gaps = _find_gaps_in_unit(hu_id)
        all_gaps.extend(unit_gaps)

    # Sort by projected confidence increase descending
    all_gaps.sort(key=lambda g: -g["projected_confidence_increase_pct"])

    # Store top gaps in monitoring_gaps table
    with _lock:
        db.execute("DELETE FROM monitoring_gaps")
        for gap in all_gaps:
            db.execute("""
                INSERT INTO monitoring_gaps
                    (habitat_unit_id, corridor_type,
                     gap_start_lat, gap_start_lon,
                     gap_end_lat, gap_end_lon,
                     gap_length_m, species_most_affected,
                     projected_confidence_increase_pct,
                     cameras_needed)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                gap["habitat_unit_id"], gap["corridor_type"],
                gap["gap_start_lat"], gap["gap_start_lon"],
                gap["gap_end_lat"], gap["gap_end_lon"],
                gap["gap_length_m"], gap["species_most_affected"],
                gap["projected_confidence_increase_pct"],
                gap["cameras_needed"],
            ))
        db.commit()

    return all_gaps


def _find_gaps_in_unit(hu_id: str) -> List[Dict]:
    """Find coverage gaps in a single habitat unit."""
    db = get_db()

    with _lock:
        cameras = db.execute("""
            SELECT camera_id, lat, lon
            FROM camera_stations WHERE habitat_unit_id = ?
        """, (hu_id,)).fetchall()

        corridors = db.execute("""
            SELECT id, corridor_type, length_km,
                   start_lat, start_lon, end_lat, end_lon
            FROM corridors WHERE habitat_unit_id = ?
        """, (hu_id,)).fetchall()

        # Get species confidence scores (to compute projected increase)
        conf_rows = db.execute("""
            SELECT species_key, corridor_coverage_pct,
                   overall_confidence_pct
            FROM species_confidence
            WHERE habitat_unit_id = ?
        """, (hu_id,)).fetchall()

    if not cameras or not corridors:
        return []

    cam_positions = [(c["lat"], c["lon"]) for c in cameras]

    # Build lookup: species → current confidence
    species_conf = {}
    for cr in conf_rows:
        species_conf[cr["species_key"]] = {
            "corridor_coverage_pct": cr["corridor_coverage_pct"] or 0,
            "overall_confidence_pct": cr["overall_confidence_pct"] or 0,
        }

    # Use medium detection radius as baseline for gap detection
    base_radius_m = settings.DETECTION_RADIUS["medium"]

    gaps = []

    for corr in corridors:
        ctype = corr["corridor_type"]
        length_km = corr["length_km"]
        length_m = length_km * 1000.0

        # Sample points along the corridor (every ~50m)
        n_samples = max(10, int(length_m / 50))
        sample_spacing_m = length_m / n_samples

        # Walk along corridor, tracking gap start/end
        in_gap = False
        gap_start_idx = 0

        for i in range(n_samples + 1):
            t = i / n_samples
            pt_lat = (corr["start_lat"]
                      + t * (corr["end_lat"] - corr["start_lat"]))
            pt_lon = (corr["start_lon"]
                      + t * (corr["end_lon"] - corr["start_lon"]))

            # Check if any camera covers this point
            covered = False
            for clat, clon in cam_positions:
                dist = haversine_m(pt_lat, pt_lon, clat, clon)
                if dist <= base_radius_m:
                    covered = True
                    break

            if not covered:
                if not in_gap:
                    in_gap = True
                    gap_start_idx = i
            else:
                if in_gap:
                    # Gap ended — check if it's long enough
                    gap_length_m = (i - gap_start_idx) * sample_spacing_m
                    if gap_length_m >= MIN_GAP_LENGTH_M:
                        gap = _create_gap_record(
                            hu_id, corr, ctype,
                            gap_start_idx, i, n_samples,
                            gap_length_m, species_conf)
                        gaps.append(gap)
                    in_gap = False

        # Handle gap at end of corridor
        if in_gap:
            gap_length_m = (n_samples - gap_start_idx) * sample_spacing_m
            if gap_length_m >= MIN_GAP_LENGTH_M:
                gap = _create_gap_record(
                    hu_id, corr, ctype,
                    gap_start_idx, n_samples, n_samples,
                    gap_length_m, species_conf)
                gaps.append(gap)

    return gaps


def _create_gap_record(hu_id: str, corr, ctype: str,
                       start_idx: int, end_idx: int, n_samples: int,
                       gap_length_m: float,
                       species_conf: Dict) -> Dict:
    """Create a gap record with species impact analysis."""
    # Compute gap coordinates
    t_start = start_idx / n_samples
    t_end = min(1.0, end_idx / n_samples)

    gap_start_lat = (corr["start_lat"]
                     + t_start * (corr["end_lat"] - corr["start_lat"]))
    gap_start_lon = (corr["start_lon"]
                     + t_start * (corr["end_lon"] - corr["start_lon"]))
    gap_end_lat = (corr["start_lat"]
                   + t_end * (corr["end_lat"] - corr["start_lat"]))
    gap_end_lon = (corr["start_lon"]
                   + t_end * (corr["end_lon"] - corr["start_lon"]))

    # Find species most affected by this gap
    # Species with highest corridor_weight for this corridor type
    # AND lowest current confidence benefit most from filling this gap
    best_species = None
    best_increase = 0.0

    for sp_key, ref in SPECIES_REFERENCE.items():
        weights = ref.get("corridor_weights", {})
        weight = weights.get(ctype, 0.0)
        if weight < 0.05:
            continue

        current = species_conf.get(sp_key, {})
        current_cov = current.get("corridor_coverage_pct", 50.0)

        # Projected increase: proportional to gap length, weight,
        # and inversely proportional to current coverage
        # A 500m gap on a riparian corridor (weight 0.45) for hogs
        # improves coverage more than a 500m gap on a ridge (weight 0.05)
        projected = (weight * gap_length_m / 1000.0
                     * (100.0 - current_cov) / 100.0
                     * 5.0)  # Scaling factor
        projected = min(15.0, round(projected, 1))

        if projected > best_increase:
            best_increase = projected
            best_species = sp_key

    if not best_species:
        best_species = "unknown"
        best_increase = 0.0

    # Cameras needed: gap_length / (2 * detection_radius)
    size = _SPECIES_SIZE.get(best_species, "medium")
    radius_m = settings.DETECTION_RADIUS[size]
    cameras_needed = max(1, int(gap_length_m / (2 * radius_m) + 0.5))

    return {
        "habitat_unit_id": hu_id,
        "corridor_type": ctype,
        "gap_start_lat": round(gap_start_lat, 6),
        "gap_start_lon": round(gap_start_lon, 6),
        "gap_end_lat": round(gap_end_lat, 6),
        "gap_end_lon": round(gap_end_lon, 6),
        "gap_length_m": round(gap_length_m, 1),
        "species_most_affected": best_species,
        "projected_confidence_increase_pct": best_increase,
        "cameras_needed": cameras_needed,
    }


def get_top_gaps(habitat_unit_id: Optional[str] = None,
                 limit: int = 3) -> List[Dict]:
    """Get the top N monitoring gaps by projected confidence increase.

    These feed into the PDF report's Data Confidence section.
    """
    db = get_db()

    if habitat_unit_id:
        cursor = db.execute("""
            SELECT * FROM monitoring_gaps
            WHERE habitat_unit_id = ?
            ORDER BY projected_confidence_increase_pct DESC
            LIMIT ?
        """, (habitat_unit_id, limit))
    else:
        cursor = db.execute("""
            SELECT * FROM monitoring_gaps
            ORDER BY projected_confidence_increase_pct DESC
            LIMIT ?
        """, (limit,))

    return [dict(row) for row in cursor]

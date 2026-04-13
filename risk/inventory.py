"""Species inventory assembly for parcel risk assessment.

Aggregates detection data across habitat units overlapping a parcel,
pulls IPW-adjusted detection frequencies when bias correction was
applied, and sorts by risk relevance: invasive-high first, then
ESA-flagged, then native by detection frequency.

The inventory is the species table the insurer actually reads — it
needs to be correct, bias-corrected, and honest about confidence.
"""

from collections import defaultdict
from typing import Dict, List, Optional

from config.species_reference import (
    SPECIES_REFERENCE, assign_risk_flag,
)
from habitat.store import get_db, _lock


def assemble_inventory(
    habitat_unit_ids: List[str],
    hu_overlap_fractions: Dict[str, float],
    bias_result: Optional[Dict] = None,
) -> List[Dict]:
    """Build the species inventory for a parcel.

    Args:
        habitat_unit_ids: HU IDs that overlap the queried parcel.
        hu_overlap_fractions: {hu_id: fraction} — what % of each HU
            the parcel covers (1.0 = entire HU inside parcel).
        bias_result: Output from run_bias_correction() if available.
            Contains per_species with adjusted detection frequencies.

    Returns:
        List of species inventory dicts, sorted by risk relevance.
    """
    db = get_db()

    # ── 1. Pull species confidence data for overlapping HUs ──
    species_data = defaultdict(lambda: {
        "events": 0,
        "cameras_detected": 0,
        "cameras_total": 0,
        "confidence_pcts": [],
        "grades": [],
        "detection_freqs": [],
        "raw_detection_freqs": [],
        "habitat_units": [],
    })

    for hu_id in habitat_unit_ids:
        overlap = hu_overlap_fractions.get(hu_id, 1.0)

        with _lock:
            rows = db.execute("""
                SELECT species_key, total_detections, cameras_detected,
                       cameras_total, detection_frequency_pct,
                       raw_detection_frequency_pct,
                       overall_confidence_pct, confidence_grade
                FROM species_confidence
                WHERE habitat_unit_id = ?
            """, (hu_id,)).fetchall()

        for row in rows:
            sp = row["species_key"]
            sd = species_data[sp]
            # Weight events by overlap fraction
            sd["events"] += int(row["total_detections"] * overlap)
            sd["cameras_detected"] += row["cameras_detected"]
            sd["cameras_total"] += row["cameras_total"]
            sd["confidence_pcts"].append(row["overall_confidence_pct"])
            sd["grades"].append(row["confidence_grade"])
            sd["detection_freqs"].append(
                row["detection_frequency_pct"] or 0.0)
            sd["raw_detection_freqs"].append(
                row["raw_detection_frequency_pct"]
                or row["detection_frequency_pct"]
                or 0.0)
            sd["habitat_units"].append(hu_id)

    # ── 2. Build inventory entries ──
    inventory = []

    for sp_key, sd in species_data.items():
        ref = SPECIES_REFERENCE.get(sp_key)
        if not ref:
            continue

        # Average detection frequency across HUs
        avg_det_freq = (sum(sd["detection_freqs"])
                        / len(sd["detection_freqs"])
                        if sd["detection_freqs"] else 0.0)
        avg_raw_freq = (sum(sd["raw_detection_freqs"])
                        / len(sd["raw_detection_freqs"])
                        if sd["raw_detection_freqs"] else 0.0)

        # If bias correction was applied, use adjusted frequency
        if bias_result and bias_result.get("bias_correction_applied"):
            per_sp = bias_result.get("per_species", {})
            if sp_key in per_sp:
                avg_det_freq = per_sp[sp_key][
                    "adjusted_detection_frequency_pct"]
                avg_raw_freq = per_sp[sp_key][
                    "raw_detection_frequency_pct"]

        # Best confidence grade (most favorable across HUs)
        avg_conf = (sum(sd["confidence_pcts"])
                    / len(sd["confidence_pcts"])
                    if sd["confidence_pcts"] else 0.0)
        best_grade = _best_grade(sd["grades"]) if sd["grades"] else "F"

        risk_flag = assign_risk_flag(sp_key, avg_det_freq)

        entry = {
            "species_key": sp_key,
            "common_name": ref["common_name"],
            "scientific_name": ref["scientific_name"],
            "native": ref["native"],
            "invasive": ref["invasive"],
            "esa_status": ref.get("esa_status"),
            "risk_flag": risk_flag,
            "independent_events": sd["events"],
            "detection_frequency_pct": round(avg_det_freq, 1),
            "raw_detection_frequency_pct": round(avg_raw_freq, 1),
            "confidence_grade": best_grade,
            "confidence_pct": round(avg_conf, 1),
            "cameras_detected": sd["cameras_detected"],
            "cameras_total": sd["cameras_total"],
            "habitat_units": sd["habitat_units"],
        }
        inventory.append(entry)

    # ── 3. Sort by risk relevance ──
    # Invasive-high first, then ESA, then native by detection frequency
    inventory.sort(key=_risk_sort_key)

    return inventory


def _risk_sort_key(entry: Dict):
    """Sort key: invasive-high → invasive-moderate → invasive-low →
    ESA → native (by descending detection frequency)."""
    flag = entry.get("risk_flag") or ""
    if "INVASIVE" in flag:
        if "HIGH" in flag:
            return (0, -entry["detection_frequency_pct"])
        if "MODERATE" in flag:
            return (1, -entry["detection_frequency_pct"])
        return (2, -entry["detection_frequency_pct"])
    if "ESA" in flag:
        return (3, -entry["detection_frequency_pct"])
    return (4, -entry["detection_frequency_pct"])


def _best_grade(grades: List[str]) -> str:
    """Return the most favorable grade from a list."""
    order = ["A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D", "F"]
    best_idx = len(order) - 1
    for g in grades:
        if g in order:
            idx = order.index(g)
            best_idx = min(best_idx, idx)
    return order[best_idx]

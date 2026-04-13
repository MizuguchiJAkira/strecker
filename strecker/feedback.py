"""Human feedback collection and regional performance tracking.

Collects hunter corrections on species classifications and ecological
assessments. Updates regional_performance table for OOD performance
estimation.

Per Sara Beery (MIT CSAIL): you cannot estimate out-of-distribution
performance from validation data — it is impossible. So accuracy must
come from user corrections in each deployment region. This module
implements that closed loop:

  1. Hunter reviews uncertain detections (sorted by entropy)
  2. Hunter submits corrections (misclassification, false positive, etc.)
  3. System recomputes per-region, per-species accuracy
  4. Risk Synthesis Engine reads accuracy estimates when generating
     insurer reports — low accuracy → lower confidence grade

The feedback_corrections and regional_performance tables are defined
in db/schema.sql. This module works against either PostGIS (production)
or an in-memory SQLite store (demo mode, no database required).
"""

import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from config.species_reference import SPECIES_REFERENCE


# ═══════════════════════════════════════════════════════════════════════════
# In-memory SQLite store for demo mode (no PostGIS required)
#
# In production, swap every _demo_db call for psycopg2 against PostGIS.
# The SQL is written to be portable — same column names, same logic.
# ═══════════════════════════════════════════════════════════════════════════

_demo_lock = threading.Lock()
_demo_db = None


def _get_demo_db() -> sqlite3.Connection:
    """Get or create the in-memory SQLite database for demo mode."""
    global _demo_db
    if _demo_db is None:
        _demo_db = sqlite3.connect(":memory:", check_same_thread=False)
        _demo_db.row_factory = sqlite3.Row
        _init_demo_schema(_demo_db)
    return _demo_db


def reset_demo_db():
    """Reset the demo database (for testing)."""
    global _demo_db
    if _demo_db is not None:
        _demo_db.close()
    _demo_db = None


def _init_demo_schema(db: sqlite3.Connection):
    """Create tables matching the PostGIS schema in SQLite."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS camera_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT UNIQUE NOT NULL,
            user_id TEXT,
            habitat_unit_id TEXT,
            placement_context TEXT
        );

        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT,
            species_key TEXT NOT NULL,
            confidence REAL NOT NULL,
            confidence_calibrated REAL,
            timestamp TEXT NOT NULL,
            image_filename TEXT,
            megadetector_confidence REAL,
            burst_group_id TEXT,
            independent_event_id TEXT,
            review_required INTEGER DEFAULT 0,
            softmax_entropy REAL,
            temporal_prior REAL,
            antler_classification TEXT
        );

        CREATE TABLE IF NOT EXISTS habitat_units (
            id TEXT PRIMARY KEY,
            huc10 TEXT,
            ecoregion_iv_code TEXT,
            nlcd_code INTEGER,
            nlcd_class TEXT
        );

        CREATE TABLE IF NOT EXISTS feedback_corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_id INTEGER,
            camera_id TEXT,
            original_species_key TEXT NOT NULL,
            corrected_species_key TEXT,
            original_confidence REAL,
            user_id TEXT,
            habitat_unit_id TEXT,
            correction_type TEXT NOT NULL,
            ecological_note TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS regional_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habitat_unit_id TEXT NOT NULL,
            species_key TEXT NOT NULL,
            total_predictions INTEGER DEFAULT 0,
            total_corrections INTEGER DEFAULT 0,
            estimated_classification_accuracy_pct REAL,
            ecological_validation_status TEXT DEFAULT 'unvalidated',
            calibration_source TEXT,
            last_updated TEXT DEFAULT (datetime('now')),
            UNIQUE(habitat_unit_id, species_key)
        );

        CREATE INDEX IF NOT EXISTS idx_det_review
            ON detections(review_required);
        CREATE INDEX IF NOT EXISTS idx_det_entropy
            ON detections(softmax_entropy);
        CREATE INDEX IF NOT EXISTS idx_fc_hu
            ON feedback_corrections(habitat_unit_id);
    """)
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Demo data seeder — populate SQLite from pipeline Detection objects
# ═══════════════════════════════════════════════════════════════════════════

def seed_demo_detections(detections, cameras_json: list = None):
    """Seed the demo SQLite database from pipeline Detection objects.

    Call this after ingest + classify so all fields are populated.
    Also seeds camera_stations and habitat_units.

    Args:
        detections: List of Detection dataclass instances
        cameras_json: Raw camera dicts from cameras.json (optional)
    """
    db = _get_demo_db()

    # Seed camera_stations
    if cameras_json:
        for cam in cameras_json:
            db.execute("""
                INSERT OR IGNORE INTO camera_stations
                    (camera_id, user_id, habitat_unit_id, placement_context)
                VALUES (?, ?, ?, ?)
            """, (cam["camera_id"], cam.get("user_id"),
                  cam.get("habitat_unit_id"), cam.get("placement_context")))

    # Seed habitat_units from camera data
    if cameras_json:
        hu_seen = set()
        for cam in cameras_json:
            hu_id = cam.get("habitat_unit_id")
            if hu_id and hu_id not in hu_seen:
                hu_seen.add(hu_id)
                db.execute("""
                    INSERT OR IGNORE INTO habitat_units
                        (id, nlcd_code, nlcd_class)
                    VALUES (?, ?, ?)
                """, (hu_id, cam.get("nlcd_code"), cam.get("nlcd_class")))

    # Seed detections
    for det in detections:
        db.execute("""
            INSERT INTO detections
                (camera_id, species_key, confidence, confidence_calibrated,
                 timestamp, image_filename, megadetector_confidence,
                 burst_group_id, independent_event_id, review_required,
                 softmax_entropy, temporal_prior, antler_classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            det.camera_id, det.species_key, det.confidence,
            det.confidence_calibrated,
            det.timestamp.isoformat(), det.image_filename,
            det.megadetector_confidence, det.burst_group_id,
            det.independent_event_id, int(det.review_required),
            det.softmax_entropy, det.temporal_prior,
            det.antler_classification,
        ))

    db.commit()


def seed_demo_corrections():
    """Pre-seed ~50 realistic feedback corrections for demo.

    Creates corrections that produce realistic accuracy numbers:
      - Deer: 97.8% accuracy (high confidence, few errors)
      - Feral hog: 94.2% accuracy (more nocturnal confusion)
      - Turkey: 96.5% accuracy (distinctive, but occasional raptor confusion)
      - Raccoon: 91.3% accuracy (nocturnal, confused with opossum/armadillo)
      - Others: varying rates

    Also initializes regional_performance rows.
    """
    import random
    rng = random.Random(42)

    db = _get_demo_db()

    # Get habitat_unit_ids from cameras
    cursor = db.execute("SELECT DISTINCT habitat_unit_id FROM camera_stations")
    hu_ids = [row[0] for row in cursor if row[0]]
    if not hu_ids:
        hu_ids = ["HU-1209020104-30a-41"]

    # Define correction scenarios per species.
    # n_corrections calibrated against total independent events to produce
    # realistic accuracy numbers when computed against the primary HU:
    #   deer:  1384 events → 12 corrections → 97.8% accuracy
    #   hog:    674 events → 39 corrections → 94.2% accuracy
    #   turkey: 442 events →  8 corrections → 96.5% accuracy
    #   raccoon: 378 events → 33 corrections → 91.3% accuracy
    # (species, n_corrections, common_confusion, correction_types)
    correction_specs = [
        ("white_tailed_deer", 12, "axis_deer",
         ["misclassification"] * 8 + ["false_positive"] * 3
         + ["missed_detection"]),
        ("feral_hog", 39, "armadillo",
         ["misclassification"] * 25 + ["false_positive"] * 10
         + ["missed_detection"] * 4),
        ("turkey", 8, "coyote",
         ["misclassification"] * 5 + ["false_positive"] * 3),
        ("raccoon", 33, "opossum",
         ["misclassification"] * 20 + ["false_positive"] * 10
         + ["missed_detection"] * 3),
        ("armadillo", 12, "raccoon",
         ["misclassification"] * 8 + ["false_positive"] * 4),
        ("coyote", 6, "gray_fox",
         ["misclassification"] * 4 + ["false_positive"] * 2),
        ("bobcat", 5, "gray_fox",
         ["misclassification"] * 3 + ["false_positive"] * 2),
        ("cottontail_rabbit", 4, "armadillo",
         ["misclassification"] * 3 + ["false_positive"]),
        ("axis_deer", 3, "white_tailed_deer",
         ["misclassification"] * 2 + ["false_positive"]),
        ("opossum", 5, "raccoon",
         ["misclassification"] * 3 + ["false_positive"] * 2),
        ("red_fox", 2, "gray_fox",
         ["misclassification"] * 2),
        ("gray_fox", 2, "red_fox",
         ["misclassification"] * 2),
    ]

    # Also add 3 ecological_mismatch entries
    ecological_notes = [
        ("CAM-F01", "Heavy root damage observed around feeder despite "
         "low hog detection frequency on this camera — suspect trail "
         "camera angle missing low-profile approach"),
        ("CAM-T02", "Axis deer sign (tracks, browse line) present in "
         "this area but no camera detections — possible gap in coverage"),
        ("CAM-W01", "Raccoon latrine found at water crossing 50m "
         "upstream — high raccoon activity confirmed by field visit"),
    ]

    correction_count = 0

    for sp, n_corr, confusion_sp, corr_types in correction_specs:
        # Get detection IDs for this species
        cursor = db.execute(
            "SELECT id, confidence, camera_id FROM detections "
            "WHERE species_key = ? ORDER BY confidence ASC LIMIT ?",
            (sp, n_corr * 3))
        det_rows = cursor.fetchall()

        if not det_rows:
            continue

        for i in range(min(n_corr, len(det_rows))):
            det = det_rows[i]
            # Look up actual HU for this camera so corrections land
            # in the same HU as the predictions they're correcting
            cam_hu = db.execute(
                "SELECT habitat_unit_id FROM camera_stations "
                "WHERE camera_id = ?", (det[2],)).fetchone()
            hu_id = cam_hu[0] if cam_hu and cam_hu[0] else hu_ids[0]
            corr_type = corr_types[i % len(corr_types)]

            if corr_type == "false_positive":
                corrected = None  # Not a real animal
            elif corr_type == "missed_detection":
                corrected = sp  # Species was there but not detected
            else:
                corrected = confusion_sp

            db.execute("""
                INSERT INTO feedback_corrections
                    (detection_id, camera_id, original_species_key,
                     corrected_species_key, original_confidence,
                     user_id, habitat_unit_id, correction_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (det[0], det[2], sp, corrected,
                  det[1], "USER-01", hu_id, corr_type))
            correction_count += 1

    # Ecological mismatch entries
    for camera_id, note in ecological_notes:
        hu_id = rng.choice(hu_ids)
        db.execute("""
            INSERT INTO feedback_corrections
                (camera_id, original_species_key, corrected_species_key,
                 user_id, habitat_unit_id, correction_type,
                 ecological_note)
            VALUES (?, 'general', NULL, 'USER-01', ?, 'ecological_mismatch', ?)
        """, (camera_id, hu_id, note))
        correction_count += 1

    db.commit()

    # Now initialize regional_performance for all species in each HU
    for hu_id in hu_ids:
        for sp, _, _, _ in correction_specs:
            # Count total predictions for this species
            cursor = db.execute("""
                SELECT COUNT(DISTINCT independent_event_id)
                FROM detections d
                JOIN camera_stations c ON d.camera_id = c.camera_id
                WHERE d.species_key = ?
                  AND c.habitat_unit_id = ?
            """, (sp, hu_id))
            total_preds = cursor.fetchone()[0]

            if total_preds == 0:
                # Fall back to total across all HUs
                cursor = db.execute("""
                    SELECT COUNT(DISTINCT independent_event_id)
                    FROM detections WHERE species_key = ?
                """, (sp,))
                total_preds = cursor.fetchone()[0]

            update_regional_performance(hu_id, sp)

    return correction_count


# ═══════════════════════════════════════════════════════════════════════════
# Core feedback operations
# ═══════════════════════════════════════════════════════════════════════════

def submit_correction(detection_id: int,
                      corrected_species_key: Optional[str],
                      user_id: str,
                      correction_type: str) -> Dict:
    """Submit a species classification correction.

    Validates the corrected species, inserts into feedback_corrections,
    looks up the detection's habitat_unit_id, and triggers
    update_regional_performance().

    Args:
        detection_id: ID of the detection being corrected
        corrected_species_key: Correct species (None for false_positive)
        user_id: Who submitted the correction
        correction_type: 'misclassification', 'false_positive',
                         'missed_detection'

    Returns:
        Dict with correction_id, habitat_unit_id, updated accuracy.

    Raises:
        ValueError: If species_key invalid or detection not found.
    """
    # Validate correction type
    valid_types = {"misclassification", "false_positive", "missed_detection"}
    if correction_type not in valid_types:
        raise ValueError(
            f"Invalid correction_type '{correction_type}'. "
            f"Must be one of: {valid_types}")

    # Validate corrected species (if not false_positive)
    if corrected_species_key is not None:
        if corrected_species_key not in SPECIES_REFERENCE:
            raise ValueError(
                f"Unknown species '{corrected_species_key}'. "
                f"Valid keys: {sorted(SPECIES_REFERENCE.keys())}")

    db = _get_demo_db()

    with _demo_lock:
        # Look up the detection
        cursor = db.execute(
            "SELECT d.species_key, d.confidence, d.camera_id, "
            "       c.habitat_unit_id "
            "FROM detections d "
            "LEFT JOIN camera_stations c ON d.camera_id = c.camera_id "
            "WHERE d.id = ?",
            (detection_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Detection {detection_id} not found")

        original_species = row[0]
        original_conf = row[1]
        camera_id = row[2]
        habitat_unit_id = row[3] or "UNKNOWN"

        # Insert correction
        cursor = db.execute("""
            INSERT INTO feedback_corrections
                (detection_id, camera_id, original_species_key,
                 corrected_species_key, original_confidence,
                 user_id, habitat_unit_id, correction_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (detection_id, camera_id, original_species, corrected_species_key,
              original_conf, user_id, habitat_unit_id, correction_type))
        correction_id = cursor.lastrowid
        db.commit()

    # Update regional performance for affected species
    accuracy = update_regional_performance(habitat_unit_id, original_species)

    return {
        "correction_id": correction_id,
        "detection_id": detection_id,
        "original_species": original_species,
        "corrected_species": corrected_species_key,
        "correction_type": correction_type,
        "habitat_unit_id": habitat_unit_id,
        "updated_accuracy_pct": accuracy,
    }


def submit_ecological_feedback(camera_id: str,
                               user_id: str,
                               ecological_note: str,
                               correction_type: str = "ecological_mismatch"
                               ) -> Dict:
    """Submit free-text ecological ground-truth feedback.

    For feedback that isn't about individual photos but about the
    ecological assessment itself — e.g., "heavy root damage despite
    low hog detection" or "axis deer sign present but no detections."

    corrected_species_key is NULL for these. They inform qualitative
    ecological validation, not per-species accuracy.

    Args:
        camera_id: Camera station associated with the observation
        user_id: Who submitted the feedback
        ecological_note: Free-text description of the observation
        correction_type: Usually 'ecological_mismatch'

    Returns:
        Dict with feedback_id and camera_id.
    """
    db = _get_demo_db()

    with _demo_lock:
        # Look up habitat_unit_id for this camera
        cursor = db.execute(
            "SELECT habitat_unit_id FROM camera_stations WHERE camera_id = ?",
            (camera_id,))
        row = cursor.fetchone()
        habitat_unit_id = row[0] if row else "UNKNOWN"

        cursor = db.execute("""
            INSERT INTO feedback_corrections
                (camera_id, original_species_key, corrected_species_key,
                 user_id, habitat_unit_id, correction_type,
                 ecological_note)
            VALUES (?, 'general', NULL, ?, ?, ?, ?)
        """, (camera_id, user_id, habitat_unit_id,
              correction_type, ecological_note))
        feedback_id = cursor.lastrowid
        db.commit()

    return {
        "feedback_id": feedback_id,
        "camera_id": camera_id,
        "habitat_unit_id": habitat_unit_id,
        "correction_type": correction_type,
        "ecological_note": ecological_note,
    }


def update_regional_performance(habitat_unit_id: str,
                                species_key: str) -> Optional[float]:
    """Recompute estimated accuracy for a habitat unit × species.

    Formula:
        accuracy = (total_predictions - total_corrections) / total_predictions × 100

    Validation status:
        'calibrated'           — calibration_source includes 'paired_field_survey'
        'partially_validated'  — total_corrections >= 50
        'unvalidated'          — insufficient feedback

    Args:
        habitat_unit_id: Which habitat unit to update
        species_key: Which species to update

    Returns:
        Updated accuracy percentage, or None if no data.
    """
    db = _get_demo_db()

    with _demo_lock:
        # Count total predictions (independent events) for this species
        # in this habitat unit
        cursor = db.execute("""
            SELECT COUNT(DISTINCT d.independent_event_id)
            FROM detections d
            JOIN camera_stations c ON d.camera_id = c.camera_id
            WHERE d.species_key = ?
              AND c.habitat_unit_id = ?
        """, (species_key, habitat_unit_id))
        total_preds = cursor.fetchone()[0]

        if total_preds == 0:
            # Fall back: count all predictions for this species regardless
            # of habitat unit (demo has cameras in limited HUs)
            cursor = db.execute("""
                SELECT COUNT(DISTINCT independent_event_id)
                FROM detections WHERE species_key = ?
            """, (species_key,))
            total_preds = cursor.fetchone()[0]

        if total_preds == 0:
            return None

        # Count corrections for this species in this habitat unit
        cursor = db.execute("""
            SELECT COUNT(*) FROM feedback_corrections
            WHERE habitat_unit_id = ?
              AND original_species_key = ?
              AND correction_type != 'ecological_mismatch'
        """, (habitat_unit_id, species_key))
        total_corrections = cursor.fetchone()[0]

        # Compute accuracy
        accuracy = (total_preds - total_corrections) / total_preds * 100
        accuracy = max(0.0, min(100.0, round(accuracy, 1)))

        # Determine validation status
        cursor = db.execute("""
            SELECT calibration_source FROM regional_performance
            WHERE habitat_unit_id = ? AND species_key = ?
        """, (habitat_unit_id, species_key))
        row = cursor.fetchone()
        existing_source = row[0] if row else None

        if existing_source and "paired_field_survey" in (existing_source or ""):
            status = "calibrated"
        elif total_corrections >= 50:
            status = "partially_validated"
        else:
            status = "unvalidated"

        # Upsert into regional_performance
        db.execute("""
            INSERT INTO regional_performance
                (habitat_unit_id, species_key, total_predictions,
                 total_corrections, estimated_classification_accuracy_pct,
                 ecological_validation_status, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(habitat_unit_id, species_key) DO UPDATE SET
                total_predictions = excluded.total_predictions,
                total_corrections = excluded.total_corrections,
                estimated_classification_accuracy_pct = excluded.estimated_classification_accuracy_pct,
                ecological_validation_status = excluded.ecological_validation_status,
                last_updated = excluded.last_updated
        """, (habitat_unit_id, species_key, total_preds,
              total_corrections, accuracy, status))
        db.commit()

    return accuracy


# ═══════════════════════════════════════════════════════════════════════════
# Query operations
# ═══════════════════════════════════════════════════════════════════════════

def get_review_queue(habitat_unit_id: Optional[str] = None,
                     limit: int = 50) -> List[Dict]:
    """Get detections flagged for human review, ordered by uncertainty.

    Returns highest-entropy detections first — these are the ones where
    the classifier is most uncertain and human review has the most value.

    Each item includes the image filename, top prediction with confidence,
    camera_id, and timestamp.

    Args:
        habitat_unit_id: Filter by habitat unit (optional)
        limit: Max results to return

    Returns:
        List of dicts with detection info for review.
    """
    db = _get_demo_db()

    if habitat_unit_id:
        cursor = db.execute("""
            SELECT d.id, d.image_filename, d.species_key, d.confidence,
                   d.confidence_calibrated, d.softmax_entropy,
                   d.camera_id, d.timestamp, d.temporal_prior,
                   d.independent_event_id
            FROM detections d
            JOIN camera_stations c ON d.camera_id = c.camera_id
            WHERE d.review_required = 1
              AND c.habitat_unit_id = ?
            ORDER BY d.softmax_entropy DESC
            LIMIT ?
        """, (habitat_unit_id, limit))
    else:
        cursor = db.execute("""
            SELECT d.id, d.image_filename, d.species_key, d.confidence,
                   d.confidence_calibrated, d.softmax_entropy,
                   d.camera_id, d.timestamp, d.temporal_prior,
                   d.independent_event_id
            FROM detections d
            WHERE d.review_required = 1
            ORDER BY d.softmax_entropy DESC
            LIMIT ?
        """, (limit,))

    results = []
    for row in cursor:
        results.append({
            "detection_id": row[0],
            "image_filename": row[1],
            "predicted_species": row[2],
            "raw_confidence": round(row[3], 4),
            "calibrated_confidence": round(row[4], 4) if row[4] else None,
            "softmax_entropy": round(row[5], 4) if row[5] else None,
            "camera_id": row[6],
            "timestamp": row[7],
            "temporal_prior": round(row[8], 4) if row[8] else None,
            "independent_event_id": row[9],
        })

    return results


def get_regional_accuracy(habitat_unit_id: str,
                          species_key: Optional[str] = None) -> List[Dict]:
    """Get current accuracy estimates for a habitat unit.

    Used by the Risk Synthesis Engine when generating insurer reports.
    Lower accuracy → lower confidence grade on the assessment.

    Args:
        habitat_unit_id: Which habitat unit
        species_key: Filter to one species (optional)

    Returns:
        List of dicts with accuracy stats per species.
    """
    db = _get_demo_db()

    if species_key:
        cursor = db.execute("""
            SELECT species_key, total_predictions, total_corrections,
                   estimated_classification_accuracy_pct,
                   ecological_validation_status, calibration_source,
                   last_updated
            FROM regional_performance
            WHERE habitat_unit_id = ? AND species_key = ?
        """, (habitat_unit_id, species_key))
    else:
        cursor = db.execute("""
            SELECT species_key, total_predictions, total_corrections,
                   estimated_classification_accuracy_pct,
                   ecological_validation_status, calibration_source,
                   last_updated
            FROM regional_performance
            WHERE habitat_unit_id = ?
            ORDER BY total_predictions DESC
        """, (habitat_unit_id,))

    results = []
    for row in cursor:
        ref = SPECIES_REFERENCE.get(row[0], {})
        results.append({
            "species_key": row[0],
            "common_name": ref.get("common_name", row[0]),
            "total_predictions": row[1],
            "total_corrections": row[2],
            "accuracy_pct": row[3],
            "validation_status": row[4],
            "calibration_source": row[5],
            "last_updated": row[6],
        })

    return results

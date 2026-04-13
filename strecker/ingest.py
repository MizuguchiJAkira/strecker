"""Ingest trail camera photos and group into bursts + independent events.

Two modes:
  - Production: accept ZIP of images, extract EXIF, register detections
  - Demo: load pre-fabricated detections.json

Implements two-threshold event grouping (standard in camera trap ecology
but absent from every consumer trail cam tool):

  1. Burst grouping (< 60s): photos from the same trigger event.
     Used for ensemble classification — multiple crops of the same animal
     improve species ID accuracy.

  2. Independence thresholding (30 min): consecutive same-species detections
     at the same camera within 30 minutes = one ecological event.
     All downstream analysis uses independent events, never raw photo counts.
     Without this, a hog visiting a feeder 12 times overnight inflates
     detection frequency 4-10x and blows up damage projections.

     Reference: Cunningham et al. 2021 — 30-min threshold is standard.
"""

import json
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Detection data model
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Detection:
    """Single photo-level detection record."""
    camera_id: str
    species_key: str
    confidence: float
    timestamp: datetime
    image_filename: str = ""
    megadetector_confidence: float = 0.0

    # Set by classify.py post-processing
    confidence_calibrated: Optional[float] = None
    temporal_prior: Optional[float] = None
    softmax_entropy: Optional[float] = None
    review_required: bool = False

    # Set by burst/independence grouping
    burst_group_id: Optional[str] = None
    independent_event_id: Optional[str] = None

    # Burst-level ensemble results
    burst_ensemble_species: Optional[str] = None
    burst_ensemble_confidence: Optional[float] = None

    # Deer-specific
    antler_classification: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d


# ═══════════════════════════════════════════════════════════════════════════
# Demo data loader
# ═══════════════════════════════════════════════════════════════════════════

def load_demo_detections() -> List[Detection]:
    """Load pre-fabricated detections from demo/demo_data/detections.json.

    Strips pre-assigned burst/event IDs — the pipeline recomputes them
    from scratch, just as it would in production.
    """
    demo_path = Path(__file__).parent.parent / "demo" / "demo_data" / "detections.json"
    if not demo_path.exists():
        raise FileNotFoundError(
            f"Demo data not found at {demo_path}. "
            "Run 'python manage.py demo generate' first.")

    with open(demo_path) as f:
        raw = json.load(f)

    detections = []
    for r in raw:
        det = Detection(
            camera_id=r["camera_id"],
            species_key=r["species_key"],
            confidence=r["confidence"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            image_filename=r.get("image_filename", ""),
            megadetector_confidence=r.get("megadetector_confidence", 0.0),
            # Intentionally NOT copying: burst_group_id, independent_event_id,
            # confidence_calibrated, review_required — pipeline recomputes these.
            antler_classification=r.get("antler_classification"),
        )
        detections.append(det)

    return detections


# ═══════════════════════════════════════════════════════════════════════════
# Burst grouping — photos within 60s at same camera = one trigger burst
# ═══════════════════════════════════════════════════════════════════════════

def assign_burst_groups(detections: List[Detection]) -> List[Detection]:
    """Group detections into bursts by camera_id + 60-second window.

    Sorts by (camera_id, timestamp), then walks forward: any detection
    within BURST_THRESHOLD_SECONDS of the previous detection at the same
    camera gets the same burst_group_id.

    Each burst also gets an ensemble classification: the species with the
    highest attention-weighted mean confidence across burst members.
    In production this would use attention over MegaDetector crops;
    in demo mode it's a confidence-weighted vote.
    """
    threshold = timedelta(seconds=settings.BURST_THRESHOLD_SECONDS)

    # Sort by camera then time
    detections.sort(key=lambda d: (d.camera_id, d.timestamp))

    burst_counter = 0
    current_burst = []
    prev_cam = None
    prev_time = None

    for det in detections:
        # New burst if: different camera, or gap > threshold
        if (det.camera_id != prev_cam
                or prev_time is None
                or (det.timestamp - prev_time) > threshold):
            # Close previous burst
            if current_burst:
                _finalize_burst(current_burst, burst_counter)
            burst_counter += 1
            current_burst = [det]
        else:
            current_burst.append(det)

        prev_cam = det.camera_id
        prev_time = det.timestamp

    # Close final burst
    if current_burst:
        _finalize_burst(current_burst, burst_counter)

    return detections


def _finalize_burst(burst: List[Detection], burst_num: int):
    """Assign burst_group_id and compute ensemble classification."""
    bid = f"BG-{burst[0].camera_id}-{burst[0].timestamp:%Y%m%d%H%M%S}"

    # Ensemble: confidence-weighted species vote across burst
    # In production: attention-weighted aggregation over MegaDetector crops
    species_scores = {}
    for det in burst:
        sp = det.species_key
        # Use calibrated confidence if available, else raw
        conf = det.confidence_calibrated or det.confidence
        species_scores[sp] = species_scores.get(sp, 0.0) + conf

    best_species = max(species_scores, key=species_scores.get)
    best_conf = species_scores[best_species] / sum(
        1 for d in burst if d.species_key == best_species)

    for det in burst:
        det.burst_group_id = bid
        det.burst_ensemble_species = best_species
        det.burst_ensemble_confidence = round(best_conf, 4)


# ═══════════════════════════════════════════════════════════════════════════
# Independence thresholding — 30-min window = one ecological event
# ═══════════════════════════════════════════════════════════════════════════

def assign_independent_events(detections: List[Detection]) -> List[Detection]:
    """Apply 30-minute independence threshold per camera × species.

    Consecutive detections of the same species at the same camera within
    30 minutes are grouped as one independent event. This is the ecological
    unit used for all downstream analysis.

    Why: A deer that triggers a camera 8 times in 20 minutes is one visit,
    not 8 separate ecological events. Without this, detection frequency
    is inflated 4-10x (e.g., hog at feeder overnight).

    Reference: Cunningham et al. 2021 — 30-min threshold is standard
    in camera trap ecology.
    """
    threshold = timedelta(minutes=settings.INDEPENDENCE_THRESHOLD_MINUTES)

    # Sort by camera, species, time
    detections.sort(key=lambda d: (d.camera_id, d.species_key, d.timestamp))

    event_counter = 0
    prev_cam = None
    prev_species = None
    prev_event_time = None  # timestamp of FIRST detection in current event

    for det in detections:
        new_event = (
            det.camera_id != prev_cam
            or det.species_key != prev_species
            or prev_event_time is None
            or (det.timestamp - prev_event_time) > threshold
        )

        if new_event:
            event_counter += 1
            prev_event_time = det.timestamp

        det.independent_event_id = (
            f"IE-{det.camera_id}-{det.species_key}-{event_counter:06d}")

        prev_cam = det.camera_id
        prev_species = det.species_key

    return detections


# ═══════════════════════════════════════════════════════════════════════════
# Full ingestion pipeline
# ═══════════════════════════════════════════════════════════════════════════

def extract_upload(zip_path: str, extract_dir: str) -> Path:
    """Extract a ZIP upload to a working directory.

    Handles nested folders (SD cards often have DCIM/100MEDIA/... structure).
    Returns the directory containing the extracted images.
    """
    zip_path = Path(zip_path)
    extract_dir = Path(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Extracting {zip_path.name} to {extract_dir}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Filter out __MACOSX, .DS_Store, etc.
        members = [
            m for m in zf.namelist()
            if not m.startswith("__MACOSX")
            and not m.endswith(".DS_Store")
            and not m.startswith(".")
        ]
        for member in members:
            zf.extract(member, extract_dir)

    logger.info(f"Extracted {len(members)} files")
    return extract_dir


def parse_camera_id(file_path: Path, base_dir: Path) -> str:
    """Derive camera ID from folder structure or filename.

    Common SD card layouts:
      - CAM-F01/IMG_0001.JPG  → CAM-F01
      - DCIM/100MEDIA/RCNX0001.JPG → DCIM (fallback)
      - CAM-F01_20260131_235114_00.jpg → CAM-F01 (from filename)

    Falls back to parent folder name.
    """
    rel = file_path.relative_to(base_dir)
    parts = rel.parts

    # If first folder looks like a camera ID, use it
    if len(parts) > 1:
        folder = parts[0]
        # Check if it looks like a camera ID (CAM-xxx, or short identifier)
        if re.match(r"^(CAM|cam|CAMERA|camera)[-_]", folder):
            return folder.upper()
        # Use folder name unless it's generic (DCIM, 100MEDIA, etc.)
        generic = {"dcim", "100media", "100eos", "media", "photos", "images"}
        if folder.lower() not in generic:
            return folder

    # Try to extract from filename pattern: CAM-F01_20260131_...
    stem = file_path.stem
    match = re.match(r"(CAM[-_]\w+?)[-_]\d{8}", stem, re.IGNORECASE)
    if match:
        return match.group(1).upper()

    # Fallback: use parent folder name
    return file_path.parent.name or "UNKNOWN"


def parse_timestamp_from_exif(image_path: Path) -> Optional[datetime]:
    """Extract capture timestamp from EXIF data."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        img = Image.open(image_path)
        exif = img._getexif()
        if exif is None:
            return None

        # Look for DateTimeOriginal (tag 36867) or DateTime (tag 306)
        for tag_id in (36867, 36868, 306):
            val = exif.get(tag_id)
            if val:
                # EXIF format: "2026:01:31 23:51:14"
                try:
                    return datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                except (ValueError, TypeError):
                    continue
    except Exception:
        pass
    return None


def parse_timestamp_from_filename(filename: str) -> Optional[datetime]:
    """Extract timestamp from common trail cam filename patterns.

    Patterns:
      - CAM-F01_20260131_235114_00.jpg → 2026-01-31 23:51:14
      - RCNX0001_20260131235114.jpg
      - IMG_20260131_235114.jpg
    """
    # Pattern: _YYYYMMDD_HHMMSS_ or _YYYYMMDDHHMMSS
    match = re.search(r"(\d{8})[_-]?(\d{6})", filename)
    if match:
        try:
            return datetime.strptime(
                match.group(1) + match.group(2), "%Y%m%d%H%M%S"
            )
        except ValueError:
            pass
    return None


def ingest_from_directory(
    image_dir: str,
    state: str = None,
) -> List[Detection]:
    """Ingest real photos from a directory using SpeciesNet.

    Pipeline:
      1. Run SpeciesNet ensemble (MegaDetector + species classifier + geofencing)
      2. Parse camera ID and timestamp for each detection
      3. Map SpeciesNet labels to internal species keys

    Args:
        image_dir: Directory containing extracted photos (possibly nested).
        state: US state code (e.g., "TX") for geofencing.

    Returns:
        List of Detection objects (without burst/event grouping — that's
        applied in ingest()).
    """
    from strecker.detect import run_speciesnet, speciesnet_label_to_key

    image_dir = Path(image_dir)
    logger.info(f"Ingesting photos from {image_dir}")

    # Step 1: Run SpeciesNet (MegaDetector + classifier + geofence)
    predictions = run_speciesnet(str(image_dir), state=state)

    if not predictions:
        logger.warning("SpeciesNet found no animal detections")
        return []

    logger.info(f"SpeciesNet classified {len(predictions)} images with animals")

    # Step 2: Build Detection objects
    detections = []
    for filepath, pred in predictions.items():
        file_path = Path(filepath)

        # Get relative path for image_filename
        try:
            file_rel = str(file_path.relative_to(image_dir))
        except ValueError:
            file_rel = file_path.name

        # Parse camera ID
        camera_id = parse_camera_id(file_path, image_dir)

        # Parse timestamp (EXIF first, filename fallback)
        timestamp = parse_timestamp_from_exif(file_path)
        if timestamp is None:
            timestamp = parse_timestamp_from_filename(file_path.name)
        if timestamp is None:
            # Last resort: file modification time
            try:
                timestamp = datetime.fromtimestamp(file_path.stat().st_mtime)
            except OSError:
                timestamp = datetime.now()

        # Map SpeciesNet label to our species key
        species_key = speciesnet_label_to_key(pred["prediction"])
        species_confidence = pred["prediction_score"]

        # Get MegaDetector confidence from detections
        md_confidence = 0.0
        for det_box in pred.get("detections", []):
            conf = det_box.get("conf", det_box.get("confidence", 0.0))
            md_confidence = max(md_confidence, float(conf))

        det = Detection(
            camera_id=camera_id,
            species_key=species_key,
            confidence=species_confidence,
            timestamp=timestamp,
            image_filename=file_rel,
            megadetector_confidence=md_confidence,
        )
        detections.append(det)

    logger.info(
        f"Built {len(detections)} detections from "
        f"{len(set(d.camera_id for d in detections))} cameras, "
        f"{len(set(d.species_key for d in detections))} species"
    )
    return detections


def ingest(
    demo: bool = False,
    image_dir: str = None,
    zip_path: str = None,
    extract_dir: str = None,
    state: str = None,
) -> List[Detection]:
    """Run the full ingestion pipeline.

    Args:
        demo: If True, load from detections.json instead of processing images.
        image_dir: Directory of extracted photos (skip extraction step).
        zip_path: Path to ZIP file to extract and process.
        extract_dir: Where to extract the ZIP (default: temp dir next to ZIP).
        state: US state code (e.g., "TX") for SpeciesNet geofencing.

    Returns:
        List of Detection objects with burst and independence grouping applied.
    """
    if demo:
        detections = load_demo_detections()
    elif zip_path:
        # Extract ZIP then process
        zip_path = Path(zip_path)
        if extract_dir is None:
            extract_dir = str(zip_path.parent / f"extracted_{zip_path.stem}")
        image_dir = str(extract_upload(str(zip_path), extract_dir))
        detections = ingest_from_directory(image_dir, state=state)
    elif image_dir:
        detections = ingest_from_directory(image_dir, state=state)
    else:
        raise ValueError(
            "Must provide demo=True, image_dir, or zip_path"
        )

    # Step 1: Burst grouping (60s threshold)
    detections = assign_burst_groups(detections)

    # Step 2: Independence thresholding (30-min threshold)
    detections = assign_independent_events(detections)

    return detections

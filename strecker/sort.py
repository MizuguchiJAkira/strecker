"""Photo sorting for hunter delivery.

Organizes classified photos into species folders for the hunter.
This is the free value proposition that drives Strecker adoption:
hunters upload trail cam SD cards, we sort their photos by species
for free, then use the data for nature-risk assessments.

In demo mode: no actual images exist, so we create empty placeholder
files in the folder structure and generate a CSV manifest with all
classification metadata.
"""

import csv
import os
from collections import defaultdict
from pathlib import Path
from typing import List

from strecker.ingest import Detection


def sort_detections(detections: List[Detection],
                    output_dir: str = "demo/output/sorted",
                    demo: bool = False) -> str:
    """Organize detections into species subfolders with CSV manifest.

    Creates:
        {output_dir}/
            manifest.csv          — Full classification metadata
            white_tailed_deer/    — Species subfolders
                CAM-F01_20250303_184639_00.jpg
                ...
            feral_hog/
                ...

    In demo mode, image files are empty placeholders (0 bytes).
    In production, this would copy/symlink actual image files.

    Args:
        detections: Classified Detection objects from classify()
        output_dir: Root directory for sorted output
        demo: If True, create empty placeholder files

    Returns:
        Path to the manifest CSV file.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # ── Build species subfolders ──
    species_dirs = set()
    for det in detections:
        species_dirs.add(det.species_key)

    for sp in sorted(species_dirs):
        (output_path / sp).mkdir(parents=True, exist_ok=True)

    # ── Create placeholder files (demo mode) ──
    if demo:
        for det in detections:
            if det.image_filename:
                placeholder = output_path / det.species_key / det.image_filename
                if not placeholder.exists():
                    placeholder.touch()

    # ── Write CSV manifest ──
    manifest_path = output_path / "manifest.csv"
    fieldnames = [
        "filename", "species", "confidence", "confidence_calibrated",
        "timestamp", "burst_group_id", "independent_event_id",
        "camera_id", "review_required", "antler_classification",
        "temporal_prior", "softmax_entropy",
    ]

    # Sort by species then timestamp for clean output
    sorted_dets = sorted(detections, key=lambda d: (d.species_key, d.timestamp))

    with open(manifest_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for det in sorted_dets:
            writer.writerow({
                "filename": det.image_filename,
                "species": det.species_key,
                "confidence": round(det.confidence, 4),
                "confidence_calibrated": (
                    round(det.confidence_calibrated, 4)
                    if det.confidence_calibrated is not None else ""),
                "timestamp": det.timestamp.isoformat(),
                "burst_group_id": det.burst_group_id or "",
                "independent_event_id": det.independent_event_id or "",
                "camera_id": det.camera_id,
                "review_required": det.review_required,
                "antler_classification": det.antler_classification or "",
                "temporal_prior": (
                    round(det.temporal_prior, 4)
                    if det.temporal_prior is not None else ""),
                "softmax_entropy": (
                    round(det.softmax_entropy, 4)
                    if det.softmax_entropy is not None else ""),
            })

    # ── Summary stats ──
    species_counts = defaultdict(lambda: {"photos": 0, "events": set()})
    for det in detections:
        species_counts[det.species_key]["photos"] += 1
        if det.independent_event_id:
            species_counts[det.species_key]["events"].add(
                det.independent_event_id)

    return str(manifest_path)

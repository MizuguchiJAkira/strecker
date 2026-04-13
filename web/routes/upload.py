"""Upload routes — hunter photo upload interface.

GET  /upload   — Upload form
POST /upload   — Accept ZIP, trigger Strecker pipeline, redirect to results
GET  /upload/status/<job_id> — Poll endpoint for async job status
"""

import json
import logging
import shutil
import threading
import uuid
import zipfile
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from flask import (
    Blueprint, current_app, redirect, render_template, request, url_for,
    jsonify,
)

from config import settings

upload_bp = Blueprint("upload", __name__)
logger = logging.getLogger(__name__)

# Thread-safe in-memory cache (authoritative state lives in DB)
_jobs_lock = threading.Lock()
_jobs = {}

# Max upload size: 500 MB
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _get_job(job_id: str) -> dict:
    """Get job from memory cache, falling back to DB."""
    with _jobs_lock:
        if job_id in _jobs:
            return _jobs[job_id].copy()

    # Fall back to DB (survives server restarts)
    try:
        from db.models import ProcessingJob
        from web.app import db as _unused  # ensure app context
        from flask import current_app
        with current_app.app_context():
            pj = ProcessingJob.query.filter_by(job_id=job_id).first()
            if pj:
                return pj.to_dict()
    except Exception:
        pass
    return None


def _set_job(job_id: str, data: dict):
    """Update job in both memory cache and DB."""
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(data)
        else:
            _jobs[job_id] = data


def _persist_job(job_id: str, app):
    """Persist current job state to the database."""
    try:
        with app.app_context():
            from db.models import db, ProcessingJob
            pj = ProcessingJob.query.filter_by(job_id=job_id).first()
            if not pj:
                pj = ProcessingJob(job_id=job_id)
                db.session.add(pj)

            with _jobs_lock:
                data = _jobs.get(job_id, {})

            pj.property_name = data.get("property_name")
            pj.status = data.get("status", "queued")
            pj.error_message = data.get("error_message")
            pj.n_photos = data.get("n_photos")
            pj.n_species = data.get("n_species")
            pj.n_events = data.get("n_events")
            pj.report_path = data.get("report_path")
            pj.appendix_path = data.get("appendix_path")
            pj.completed_at = (
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at") else None
            )
            species = data.get("species", [])
            if species:
                pj.species_json = json.dumps(species)

            db.session.commit()
    except Exception:
        logger.exception(f"Failed to persist job {job_id} to DB")


def _cleanup_extracted(job_id: str):
    """Remove extracted images after processing to reclaim disk space.

    Keeps the report PDF and appendix CSV but deletes the extracted
    source photos which are the bulk of disk usage.
    """
    try:
        extract_dir = Path(settings.UPLOAD_DIR) / job_id
        if not extract_dir.exists():
            return

        # Delete extracted_ subdirectories (raw photos)
        for child in extract_dir.iterdir():
            if child.is_dir() and child.name.startswith("extracted_"):
                shutil.rmtree(child, ignore_errors=True)
                logger.info(f"Cleaned up extracted photos: {child}")

        # Delete the upload.zip itself
        zip_file = extract_dir / "upload.zip"
        if zip_file.exists():
            zip_file.unlink(missing_ok=True)
            logger.info(f"Cleaned up upload ZIP: {zip_file}")
    except Exception:
        logger.exception(f"Cleanup failed for job {job_id}")


def _run_pipeline(job_id: str, zip_path: str, property_name: str,
                  demo: bool, state: str = None, app=None):
    """Run the Strecker pipeline (in background thread).

    Updates _jobs[job_id] with results or error, persists to DB.
    """
    try:
        from strecker.ingest import ingest
        from strecker.classify import classify
        from strecker.report import generate_report
        from config.species_reference import SPECIES_REFERENCE

        _set_job(job_id, {"status": "processing"})

        # Ingest: extract ZIP + run SpeciesNet (or load demo data)
        if demo:
            photos = ingest(demo=True)
        else:
            extract_dir = str(Path(zip_path).parent / f"extracted_{job_id}")
            photos = ingest(zip_path=zip_path, extract_dir=extract_dir, state=state)

        _set_job(job_id, {"status": "classifying"})

        # Classify: temperature scaling, temporal priors, entropy routing
        detections = classify(photos, demo=demo)

        _set_job(job_id, {"status": "reporting"})

        # Generate PDF report
        output_dir = Path(settings.UPLOAD_DIR) / job_id / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = str(output_dir / "game_inventory_report.pdf")
        generate_report(
            detections,
            output_path=report_path,
            property_name=property_name,
            demo=demo,
        )

        # Build species summary for results page
        species_stats = defaultdict(lambda: {
            "events": set(), "photos": 0, "cameras": set()
        })
        for det in detections:
            sp = species_stats[det.species_key]
            sp["events"].add(det.independent_event_id)
            sp["photos"] += 1
            sp["cameras"].add(det.camera_id)

        species_list = []
        for sp_key, stats in sorted(
                species_stats.items(),
                key=lambda x: -len(x[1]["events"])):
            ref = SPECIES_REFERENCE.get(sp_key, {})
            species_list.append({
                "common_name": ref.get(
                    "common_name",
                    sp_key.replace("_", " ").title()
                ),
                "events": len(stats["events"]),
                "photos": stats["photos"],
                "cameras": len(stats["cameras"]),
            })

        n_events = sum(s["events"] for s in species_list)

        _set_job(job_id, {
            "status": "complete",
            "n_photos": f"{len(detections):,}",
            "n_species": len(species_list),
            "n_events": f"{n_events:,}",
            "report_path": report_path,
            "appendix_path": str(output_dir / "events_appendix.csv"),
            "species": species_list,
            "completed_at": datetime.utcnow().isoformat(),
        })

        logger.info(
            f"Job {job_id} complete: {len(detections)} detections, "
            f"{len(species_list)} species, {n_events} events"
        )

        # Persist final state to DB
        if app:
            _persist_job(job_id, app)

        # Cleanup extracted photos to reclaim disk space
        if not demo:
            _cleanup_extracted(job_id)

    except Exception as e:
        logger.exception(f"Pipeline failed for job {job_id}")
        _set_job(job_id, {
            "status": "error",
            "error_message": str(e),
        })
        if app:
            _persist_job(job_id, app)


@upload_bp.route("/upload", methods=["GET", "POST"])
def upload():
    """GET: show upload form. POST: process uploaded photos."""
    if request.method == "GET":
        return render_template("upload.html")

    job_id = str(uuid.uuid4())[:8]
    property_name = request.form.get("property_name", "My Property")
    state = request.form.get("state", "TX")
    demo_mode = current_app.config.get("DEMO_MODE", False)

    _set_job(job_id, {
        "job_id": job_id,
        "status": "queued",
        "property_name": property_name,
        "submitted_at": datetime.utcnow().isoformat(),
    })

    if demo_mode:
        _run_pipeline(job_id, zip_path=None, property_name=property_name,
                      demo=True, app=current_app._get_current_object())
        return redirect(url_for("results.results", job_id=job_id))

    # ── Production mode: handle real file upload ──

    uploaded_file = request.files.get("photos")
    if not uploaded_file or uploaded_file.filename == "":
        _set_job(job_id, {"status": "error", "error_message": "No file uploaded"})
        return redirect(url_for("results.results", job_id=job_id))

    # Validate file type
    filename = uploaded_file.filename.lower()
    if not filename.endswith(".zip"):
        _set_job(job_id, {"status": "error", "error_message": "Please upload a ZIP file"})
        return redirect(url_for("results.results", job_id=job_id))

    # Save uploaded file
    upload_dir = Path(settings.UPLOAD_DIR) / job_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    zip_path = str(upload_dir / "upload.zip")
    uploaded_file.save(zip_path)

    file_size = Path(zip_path).stat().st_size
    if file_size > MAX_UPLOAD_BYTES:
        _set_job(job_id, {
            "status": "error",
            "error_message": (
                f"File too large ({file_size // (1024*1024)} MB). "
                f"Maximum is {MAX_UPLOAD_BYTES // (1024*1024)} MB."
            ),
        })
        Path(zip_path).unlink(missing_ok=True)
        return redirect(url_for("results.results", job_id=job_id))

    # Validate ZIP integrity before spawning the pipeline
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise zipfile.BadZipFile(f"Corrupt file in ZIP: {bad}")
            # Check that the ZIP actually contains image files
            image_exts = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}
            has_images = any(
                Path(n).suffix.lower() in image_exts
                for n in zf.namelist()
                if not n.startswith("__MACOSX") and not n.endswith("/")
            )
            if not has_images:
                _set_job(job_id, {
                    "status": "error",
                    "error_message": "ZIP file contains no image files (.jpg, .png, .tif).",
                })
                Path(zip_path).unlink(missing_ok=True)
                return redirect(url_for("results.results", job_id=job_id))
    except zipfile.BadZipFile as e:
        _set_job(job_id, {
            "status": "error",
            "error_message": f"Invalid or corrupt ZIP file: {e}",
        })
        Path(zip_path).unlink(missing_ok=True)
        return redirect(url_for("results.results", job_id=job_id))

    logger.info(
        f"Job {job_id}: received {file_size // 1024} KB upload "
        f"for property '{property_name}'"
    )

    # Persist initial state to DB
    app = current_app._get_current_object()
    _persist_job(job_id, app)

    # Run pipeline in background thread
    thread = threading.Thread(
        target=_run_pipeline,
        args=(job_id, zip_path, property_name, False, state, app),
        daemon=True,
    )
    thread.start()

    return redirect(url_for("results.results", job_id=job_id))


@upload_bp.route("/upload/status/<job_id>")
def job_status(job_id):
    """Poll endpoint for async job status."""
    job = _get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "job_id": job.get("job_id", job_id),
        "status": job["status"],
        "error_message": job.get("error_message"),
    })

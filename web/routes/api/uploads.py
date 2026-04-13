"""API routes for photo uploads.

POST /api/properties/<pid>/uploads  — accept ZIP, create Upload record
GET  /api/uploads/<id>/status       — poll processing status

Processing triggers strecker.ingest() and strecker.classify(), then
auto-creates a Season (if none exists for current date range) and
populates DetectionSummary rows.
"""

import os
import json
import threading
from datetime import datetime, date, timedelta
from collections import defaultdict
from pathlib import Path

from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user, login_required

from db.models import db, Property, Upload, Season, DetectionSummary, Camera
from config import settings

uploads_api_bp = Blueprint("uploads_api", __name__, url_prefix="/api")


def _get_user_property(property_id):
    """Get a property owned by current_user, or None."""
    prop = Property.query.get(property_id)
    if prop and prop.user_id == current_user.id:
        return prop
    return None


@uploads_api_bp.route("/properties/<int:property_id>/uploads", methods=["POST"])
@login_required
def create_upload(property_id):
    """Accept a ZIP file upload and create an Upload record.

    Triggers background processing in demo mode.
    """
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    # Create upload record
    upload = Upload(
        property_id=prop.id,
        user_id=current_user.id,
        status="pending",
    )
    db.session.add(upload)
    db.session.commit()

    # Handle file upload if present
    file = request.files.get("file")
    if file and file.filename:
        upload_dir = Path(settings.UPLOAD_DIR) / str(upload.id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_path = upload_dir / file.filename
        file.save(str(file_path))

    # Start background processing
    upload_id = upload.id
    app = current_app._get_current_object()
    thread = threading.Thread(
        target=_process_upload,
        args=(app, upload_id, prop.id),
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        "id": upload.id,
        "status": upload.status,
        "property_id": prop.id,
    }), 201


@uploads_api_bp.route("/uploads/<int:upload_id>/status", methods=["GET"])
@login_required
def get_upload_status(upload_id):
    """Poll upload processing status."""
    upload = Upload.query.get(upload_id)
    if not upload:
        return jsonify({"error": "Upload not found"}), 404

    # Verify ownership
    prop = Property.query.get(upload.property_id)
    if not prop or prop.user_id != current_user.id:
        return jsonify({"error": "Upload not found"}), 404

    return jsonify({
        "id": upload.id,
        "status": upload.status,
        "photo_count": upload.photo_count,
        "error_message": upload.error_message,
        "uploaded_at": upload.uploaded_at.isoformat() if upload.uploaded_at else None,
        "processed_at": upload.processed_at.isoformat() if upload.processed_at else None,
    })


def _process_upload(app, upload_id, property_id):
    """Background processing: run Strecker pipeline and populate summaries.

    Uses demo mode for now — production will process actual uploaded ZIPs.
    """
    with app.app_context():
        upload = Upload.query.get(upload_id)
        if not upload:
            return

        try:
            upload.status = "processing"
            db.session.commit()

            # Run Strecker pipeline (demo mode)
            from strecker.ingest import ingest
            from strecker.classify import classify

            detections = ingest(demo=True)
            detections = classify(detections, demo=True)

            upload.photo_count = len(detections)

            # Auto-create season if none exists for current quarter
            today = date.today()
            quarter = (today.month - 1) // 3
            quarter_start = date(today.year, quarter * 3 + 1, 1)
            if quarter == 3:
                quarter_end = date(today.year, 12, 31)
            else:
                quarter_end = date(today.year, (quarter + 1) * 3 + 1, 1) - timedelta(days=1)

            season_names = ["Spring", "Summer", "Fall", "Winter"]
            season_name = f"{season_names[quarter]} {today.year}"

            season = Season.query.filter_by(
                property_id=property_id,
                name=season_name,
            ).first()

            if not season:
                season = Season(
                    property_id=property_id,
                    name=season_name,
                    start_date=quarter_start,
                    end_date=quarter_end,
                )
                db.session.add(season)
                db.session.commit()

            upload.season_id = season.id

            # Aggregate detections into DetectionSummary rows
            # Group by (camera_id_str, species_key)
            cameras = Camera.query.filter_by(property_id=property_id).all()
            camera_map = {c.camera_label: c.id for c in cameras}

            agg = defaultdict(lambda: {
                "total_photos": 0,
                "events": set(),
                "confidences": [],
                "first_seen": None,
                "last_seen": None,
                "buck_count": 0,
                "doe_count": 0,
                "hourly": [0] * 24,
            })

            for det in detections:
                cam_id = camera_map.get(det.camera_id)
                if not cam_id:
                    continue

                key = (cam_id, det.species_key)
                a = agg[key]
                a["total_photos"] += 1
                if det.independent_event_id:
                    a["events"].add(det.independent_event_id)
                a["confidences"].append(det.confidence_calibrated or det.confidence)

                ts = det.timestamp
                if a["first_seen"] is None or ts < a["first_seen"]:
                    a["first_seen"] = ts
                if a["last_seen"] is None or ts > a["last_seen"]:
                    a["last_seen"] = ts

                if det.antler_classification == "buck":
                    a["buck_count"] += 1
                elif det.antler_classification == "doe":
                    a["doe_count"] += 1

                a["hourly"][ts.hour] += 1

            for (cam_id, species_key), a in agg.items():
                # Check for existing summary
                existing = DetectionSummary.query.filter_by(
                    season_id=season.id,
                    camera_id=cam_id,
                    species_key=species_key,
                ).first()

                hourly = a["hourly"]
                peak_hour = hourly.index(max(hourly)) if max(hourly) > 0 else None

                if existing:
                    existing.total_photos = (existing.total_photos or 0) + a["total_photos"]
                    existing.independent_events = (existing.independent_events or 0) + len(a["events"])
                    if a["confidences"]:
                        existing.avg_confidence = round(sum(a["confidences"]) / len(a["confidences"]), 4)
                    existing.peak_hour = peak_hour
                else:
                    summary = DetectionSummary(
                        season_id=season.id,
                        camera_id=cam_id,
                        species_key=species_key,
                        total_photos=a["total_photos"],
                        independent_events=len(a["events"]),
                        avg_confidence=round(sum(a["confidences"]) / len(a["confidences"]), 4) if a["confidences"] else None,
                        first_seen=a["first_seen"],
                        last_seen=a["last_seen"],
                        buck_count=a["buck_count"],
                        doe_count=a["doe_count"],
                        peak_hour=peak_hour,
                        hourly_distribution=json.dumps(hourly),
                    )
                    db.session.add(summary)

            upload.status = "complete"
            upload.processed_at = datetime.utcnow()
            db.session.commit()

        except Exception as e:
            upload.status = "error"
            upload.error_message = str(e)
            db.session.commit()

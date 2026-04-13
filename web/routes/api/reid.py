"""API endpoints for deer re-identification ("My Deer" feature).

Endpoints:
    GET  /api/properties/<pid>/deer              — list all tracked individuals
    GET  /api/properties/<pid>/deer/<deer_id>     — individual detail + sightings
    POST /api/properties/<pid>/deer/run           — trigger re-ID pipeline
    PUT  /api/properties/<pid>/deer/<deer_id>     — update name/notes/age_class
    POST /api/properties/<pid>/deer/merge         — merge two individuals
    POST /api/properties/<pid>/deer/<deer_id>/confirm — confirm a sighting match
    POST /api/properties/<pid>/deer/new           — create new individual from uploaded photo
    POST /api/properties/<pid>/deer/<deer_id>/sighting — add sighting photo to existing individual
"""

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from flask_login import login_required, current_user

from db.models import db, Property, DeerIndividual, DeerSighting

logger = logging.getLogger(__name__)

reid_api_bp = Blueprint("reid_api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_property(pid):
    return Property.query.filter_by(id=pid, user_id=current_user.id).first()


COMMON_NAMES = {
    "white_tailed_deer": "White-tailed Deer",
    "axis_deer": "Axis Deer",
}


# ---------------------------------------------------------------------------
# 1. List all tracked individuals for a property
# ---------------------------------------------------------------------------

@reid_api_bp.route("/properties/<int:pid>/deer", methods=["GET"])
@login_required
def list_deer(pid):
    """Return all tracked deer for a property, sorted by last_seen desc."""
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    species_filter = request.args.get("species", "")
    sex_filter = request.args.get("sex", "")

    query = DeerIndividual.query.filter_by(property_id=pid)
    if species_filter:
        query = query.filter_by(species_key=species_filter)
    if sex_filter:
        query = query.filter_by(sex=sex_filter)

    deer = query.order_by(DeerIndividual.last_seen.desc()).all()

    return jsonify({
        "deer": [
            {
                "individual_id": d.individual_id,
                "display_name": d.display_name,
                "species_key": d.species_key,
                "common_name": COMMON_NAMES.get(d.species_key, d.species_key),
                "sex": d.sex,
                "age_class": d.age_class,
                "first_seen": d.first_seen.isoformat() if d.first_seen else None,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "sighting_count": d.sighting_count,
                "profile_photo_url": d.profile_photo_url,
                "is_confirmed": d.is_confirmed,
                "notes": d.notes,
            }
            for d in deer
        ],
        "total": len(deer),
    })


# ---------------------------------------------------------------------------
# 2. Individual detail + sighting history
# ---------------------------------------------------------------------------

@reid_api_bp.route("/properties/<int:pid>/deer/<deer_id>", methods=["GET"])
@login_required
def get_deer_detail(pid, deer_id):
    """Return detailed info for a single individual with all sightings."""
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    deer = DeerIndividual.query.filter_by(
        individual_id=deer_id, property_id=pid
    ).first()
    if not deer:
        return jsonify({"error": "Individual not found"}), 404

    sightings = DeerSighting.query.filter_by(
        individual_id=deer_id
    ).order_by(DeerSighting.timestamp.desc()).all()

    # Camera summary
    camera_counts = {}
    for s in sightings:
        camera_counts[s.camera_id] = camera_counts.get(s.camera_id, 0) + 1

    return jsonify({
        "individual_id": deer.individual_id,
        "display_name": deer.display_name,
        "species_key": deer.species_key,
        "common_name": COMMON_NAMES.get(deer.species_key, deer.species_key),
        "sex": deer.sex,
        "age_class": deer.age_class,
        "first_seen": deer.first_seen.isoformat() if deer.first_seen else None,
        "last_seen": deer.last_seen.isoformat() if deer.last_seen else None,
        "sighting_count": deer.sighting_count,
        "profile_photo_url": deer.profile_photo_url,
        "is_confirmed": deer.is_confirmed,
        "notes": deer.notes,
        "camera_summary": camera_counts,
        "sightings": [
            {
                "id": s.id,
                "image_filename": s.image_filename,
                "camera_id": s.camera_id,
                "timestamp": s.timestamp.isoformat() if s.timestamp else None,
                "confidence": round(s.confidence, 3) if s.confidence else None,
                "photo_url": s.photo_url,
                "is_confirmed": s.is_confirmed,
            }
            for s in sightings
        ],
    })


# ---------------------------------------------------------------------------
# 3. Trigger re-ID pipeline
# ---------------------------------------------------------------------------

@reid_api_bp.route("/properties/<int:pid>/deer/run", methods=["POST"])
@login_required
def run_reid(pid):
    """Trigger the re-ID pipeline for a property.

    This processes all deer photos in the sorted output directory,
    clusters them into individuals, and populates the database.
    """
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    from strecker.reid import run_reid_pipeline

    sorted_dir = Path(__file__).parent.parent.parent.parent / "demo" / "output" / "sorted"

    try:
        individuals, sightings = run_reid_pipeline(
            photo_dir=sorted_dir,
            property_id=pid,
            demo=True,
        )

        # Clear existing re-ID data for this property
        DeerSighting.query.filter(
            DeerSighting.individual_id.in_(
                db.session.query(DeerIndividual.individual_id).filter_by(property_id=pid)
            )
        ).delete(synchronize_session=False)
        DeerIndividual.query.filter_by(property_id=pid).delete()

        # Persist new results
        for ind in individuals:
            db_ind = DeerIndividual(
                individual_id=ind.individual_id,
                property_id=pid,
                species_key=ind.species_key,
                display_name=ind.display_name,
                sex=ind.sex,
                age_class=ind.age_class,
                first_seen=ind.first_seen,
                last_seen=ind.last_seen,
                sighting_count=ind.sighting_count,
                profile_photo_url=ind.profile_photo_url,
                centroid_embedding=json.dumps(
                    ind.centroid_embedding.tolist()
                ) if ind.centroid_embedding is not None else None,
            )
            db.session.add(db_ind)

        for s in sightings:
            species_key = None
            ind = next((i for i in individuals if i.individual_id == s.individual_id), None)
            if ind:
                species_key = ind.species_key

            db_s = DeerSighting(
                individual_id=s.individual_id,
                image_filename=s.image_filename,
                camera_id=s.camera_id,
                timestamp=s.timestamp,
                confidence=s.confidence,
                species_key=species_key,
                photo_url=f"/photos/{species_key}/{s.image_filename}" if species_key else None,
            )
            db.session.add(db_s)

        db.session.commit()

        return jsonify({
            "status": "complete",
            "individuals_found": len(individuals),
            "total_sightings": len(sightings),
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Re-ID pipeline error: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# 4. Update individual (name, notes, age_class)
# ---------------------------------------------------------------------------

@reid_api_bp.route("/properties/<int:pid>/deer/<deer_id>", methods=["PUT"])
@login_required
def update_deer(pid, deer_id):
    """Update display name, notes, age class, or sex for an individual."""
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    deer = DeerIndividual.query.filter_by(
        individual_id=deer_id, property_id=pid
    ).first()
    if not deer:
        return jsonify({"error": "Individual not found"}), 404

    data = request.get_json()
    if "display_name" in data:
        deer.display_name = data["display_name"]
    if "notes" in data:
        deer.notes = data["notes"]
    if "age_class" in data:
        deer.age_class = data["age_class"]
    if "sex" in data:
        deer.sex = data["sex"]

    db.session.commit()
    return jsonify({"status": "updated"})


# ---------------------------------------------------------------------------
# 5. Merge two individuals
# ---------------------------------------------------------------------------

@reid_api_bp.route("/properties/<int:pid>/deer/merge", methods=["POST"])
@login_required
def merge_deer(pid):
    """Merge two individuals (user confirms they're the same deer).

    Body: {"keep_id": "DEER-abc123", "merge_id": "DEER-def456"}
    """
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    data = request.get_json()
    keep_id = data.get("keep_id")
    merge_id = data.get("merge_id")

    if not keep_id or not merge_id:
        return jsonify({"error": "keep_id and merge_id required"}), 400

    keep = DeerIndividual.query.filter_by(individual_id=keep_id, property_id=pid).first()
    merge = DeerIndividual.query.filter_by(individual_id=merge_id, property_id=pid).first()

    if not keep or not merge:
        return jsonify({"error": "Individual(s) not found"}), 404

    # Transfer sightings
    DeerSighting.query.filter_by(individual_id=merge_id).update(
        {"individual_id": keep_id}, synchronize_session=False
    )

    # Update keep stats
    keep.sighting_count += merge.sighting_count
    keep.first_seen = min(keep.first_seen, merge.first_seen) if keep.first_seen and merge.first_seen else keep.first_seen or merge.first_seen
    keep.last_seen = max(keep.last_seen, merge.last_seen) if keep.last_seen and merge.last_seen else keep.last_seen or merge.last_seen
    keep.is_confirmed = True

    # Delete merged individual
    db.session.delete(merge)
    db.session.commit()

    return jsonify({
        "status": "merged",
        "kept": keep_id,
        "removed": merge_id,
        "new_sighting_count": keep.sighting_count,
    })


# ---------------------------------------------------------------------------
# 6. Confirm a sighting
# ---------------------------------------------------------------------------

@reid_api_bp.route(
    "/properties/<int:pid>/deer/<deer_id>/confirm", methods=["POST"]
)
@login_required
def confirm_sighting(pid, deer_id):
    """Mark a sighting as user-confirmed."""
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    data = request.get_json()
    sighting_id = data.get("sighting_id")

    sighting = DeerSighting.query.filter_by(
        id=sighting_id, individual_id=deer_id
    ).first()
    if not sighting:
        return jsonify({"error": "Sighting not found"}), 404

    sighting.is_confirmed = True

    # Also mark individual as confirmed
    deer = DeerIndividual.query.filter_by(
        individual_id=deer_id, property_id=pid
    ).first()
    if deer:
        deer.is_confirmed = True

    db.session.commit()

    return jsonify({"status": "confirmed"})


# ---------------------------------------------------------------------------
# Helpers — deer photo uploads
# ---------------------------------------------------------------------------

DEER_UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads" / "deer"

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_deer_photo(file, property_id):
    """Save an uploaded deer photo and return the stored filename + URL."""
    ext = file.filename.rsplit(".", 1)[1].lower() if "." in file.filename else "jpg"
    unique_name = f"p{property_id}_{uuid.uuid4().hex[:12]}.{ext}"

    dest_dir = DEER_UPLOAD_DIR / str(property_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / unique_name
    file.save(str(dest_path))

    photo_url = f"/api/deer-photos/{property_id}/{unique_name}"
    return unique_name, photo_url


# ---------------------------------------------------------------------------
# 7. Create new individual from uploaded photo
# ---------------------------------------------------------------------------

@reid_api_bp.route("/properties/<int:pid>/deer/new", methods=["POST"])
@login_required
def create_deer(pid):
    """Create a new individual deer from an uploaded photo.

    Form data:
        photo: image file (required)
        display_name: string (required) — hunter's name for this deer
        sex: buck / doe / unknown (optional, default unknown)
        species_key: white_tailed_deer / axis_deer (optional, default white_tailed_deer)
        notes: string (optional)
        camera_id: string (optional) — which camera captured this
    """
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    file = request.files.get("photo")
    if not file or not file.filename:
        return jsonify({"error": "Photo is required"}), 400
    if not _allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Use JPG, PNG, or WebP."}), 400

    display_name = request.form.get("display_name", "").strip()
    if not display_name:
        return jsonify({"error": "Name is required"}), 400

    sex = request.form.get("sex", "unknown")
    species_key = request.form.get("species_key", "white_tailed_deer")
    notes = request.form.get("notes", "")
    camera_id = request.form.get("camera_id", "")

    # Save photo
    filename, photo_url = _save_deer_photo(file, pid)

    # Generate individual ID
    short_hash = hashlib.md5(
        f"{pid}:{filename}:{datetime.utcnow().isoformat()}".encode()
    ).hexdigest()[:6]
    individual_id = f"DEER-{short_hash}"

    now = datetime.utcnow()

    # Create individual record
    deer = DeerIndividual(
        individual_id=individual_id,
        property_id=pid,
        species_key=species_key,
        display_name=display_name,
        sex=sex,
        first_seen=now,
        last_seen=now,
        sighting_count=1,
        profile_photo_url=photo_url,
        notes=notes,
        is_confirmed=True,  # user-created = confirmed
    )
    db.session.add(deer)

    # Create first sighting
    sighting = DeerSighting(
        individual_id=individual_id,
        image_filename=filename,
        camera_id=camera_id,
        timestamp=now,
        confidence=1.0,  # manually tagged = 100%
        species_key=species_key,
        photo_url=photo_url,
        is_confirmed=True,
    )
    db.session.add(sighting)
    db.session.commit()

    return jsonify({
        "status": "created",
        "individual_id": individual_id,
        "display_name": display_name,
        "photo_url": photo_url,
    }), 201


# ---------------------------------------------------------------------------
# 8. Add sighting photo to existing individual
# ---------------------------------------------------------------------------

@reid_api_bp.route(
    "/properties/<int:pid>/deer/<deer_id>/sighting", methods=["POST"]
)
@login_required
def add_sighting_photo(pid, deer_id):
    """Upload a new photo as a sighting for an existing individual.

    Form data:
        photo: image file (required)
        camera_id: string (optional)
        notes: string (optional)
    """
    prop = _get_user_property(pid)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    deer = DeerIndividual.query.filter_by(
        individual_id=deer_id, property_id=pid
    ).first()
    if not deer:
        return jsonify({"error": "Individual not found"}), 404

    file = request.files.get("photo")
    if not file or not file.filename:
        return jsonify({"error": "Photo is required"}), 400
    if not _allowed_file(file.filename):
        return jsonify({"error": "Invalid file type. Use JPG, PNG, or WebP."}), 400

    camera_id = request.form.get("camera_id", "")
    now = datetime.utcnow()

    # Save photo
    filename, photo_url = _save_deer_photo(file, pid)

    # Create sighting
    sighting = DeerSighting(
        individual_id=deer_id,
        image_filename=filename,
        camera_id=camera_id,
        timestamp=now,
        confidence=1.0,
        species_key=deer.species_key,
        photo_url=photo_url,
        is_confirmed=True,
    )
    db.session.add(sighting)

    # Update individual stats
    deer.sighting_count = (deer.sighting_count or 0) + 1
    deer.last_seen = now
    deer.is_confirmed = True

    db.session.commit()

    return jsonify({
        "status": "added",
        "individual_id": deer_id,
        "sighting_count": deer.sighting_count,
        "photo_url": photo_url,
    })


# ---------------------------------------------------------------------------
# 9. Serve uploaded deer photos
# ---------------------------------------------------------------------------

@reid_api_bp.route("/deer-photos/<int:pid>/<filename>", methods=["GET"])
def serve_deer_photo(pid, filename):
    """Serve user-uploaded deer photos."""
    photo_path = DEER_UPLOAD_DIR / str(pid) / filename
    if not photo_path.exists():
        return jsonify({"error": "Photo not found"}), 404
    return send_file(str(photo_path), mimetype="image/jpeg")

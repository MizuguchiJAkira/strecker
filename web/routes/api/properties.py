"""API routes for Property and Camera CRUD.

All endpoints return JSON and require authentication.
Ownership is verified on every request (property.user_id == current_user.id).
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from db.models import db, Property, Camera

properties_api_bp = Blueprint("properties_api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _property_json(prop):
    """Serialize a Property to dict."""
    return {
        "id": prop.id,
        "name": prop.name,
        "county": prop.county,
        "state": prop.state,
        "acreage": prop.acreage,
        "boundary_geojson": prop.boundary_geojson,
        "created_at": prop.created_at.isoformat() if prop.created_at else None,
        "updated_at": prop.updated_at.isoformat() if prop.updated_at else None,
    }


def _camera_json(cam):
    """Serialize a Camera to dict."""
    return {
        "id": cam.id,
        "property_id": cam.property_id,
        "camera_label": cam.camera_label,
        "name": cam.name,
        "lat": cam.lat,
        "lon": cam.lon,
        "placement_context": cam.placement_context,
        "camera_model": cam.camera_model,
        "installed_date": cam.installed_date.isoformat() if cam.installed_date else None,
        "is_active": cam.is_active,
        "created_at": cam.created_at.isoformat() if cam.created_at else None,
        "updated_at": cam.updated_at.isoformat() if cam.updated_at else None,
    }


def _get_user_property(property_id):
    """Get a property owned by current_user, or None."""
    prop = Property.query.get(property_id)
    if prop and prop.user_id == current_user.id:
        return prop
    return None


# ---------------------------------------------------------------------------
# Property endpoints
# ---------------------------------------------------------------------------

@properties_api_bp.route("/properties", methods=["GET"])
@login_required
def list_properties():
    """List all properties owned by the current user."""
    props = Property.query.filter_by(user_id=current_user.id).all()
    return jsonify([_property_json(p) for p in props])


@properties_api_bp.route("/properties", methods=["POST"])
@login_required
def create_property():
    """Create a new property."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400

    prop = Property(
        user_id=current_user.id,
        name=name,
        county=data.get("county"),
        state=data.get("state"),
        acreage=data.get("acreage"),
        boundary_geojson=data.get("boundary_geojson"),
    )
    db.session.add(prop)
    db.session.commit()

    return jsonify(_property_json(prop)), 201


@properties_api_bp.route("/properties/<int:property_id>", methods=["GET"])
@login_required
def get_property(property_id):
    """Get a single property with its cameras."""
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    result = _property_json(prop)
    result["cameras"] = [_camera_json(c) for c in prop.cameras.all()]
    return jsonify(result)


@properties_api_bp.route("/properties/<int:property_id>", methods=["PUT"])
@login_required
def update_property(property_id):
    """Update an existing property."""
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    for field in ("name", "county", "state", "acreage", "boundary_geojson"):
        if field in data:
            setattr(prop, field, data[field])

    db.session.commit()
    return jsonify(_property_json(prop))


@properties_api_bp.route("/properties/<int:property_id>", methods=["DELETE"])
@login_required
def delete_property(property_id):
    """Delete a property and its cameras."""
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    # Delete associated cameras first
    Camera.query.filter_by(property_id=prop.id).delete()
    db.session.delete(prop)
    db.session.commit()

    return jsonify({"message": "Property deleted"}), 200


# ---------------------------------------------------------------------------
# Camera endpoints
# ---------------------------------------------------------------------------

@properties_api_bp.route("/properties/<int:property_id>/cameras", methods=["GET"])
@login_required
def list_cameras(property_id):
    """List cameras for a property."""
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    cameras = Camera.query.filter_by(property_id=prop.id).all()
    return jsonify([_camera_json(c) for c in cameras])


@properties_api_bp.route("/properties/<int:property_id>/cameras", methods=["POST"])
@login_required
def create_camera(property_id):
    """Create a camera on a property."""
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    cam = Camera(
        property_id=prop.id,
        camera_label=data.get("camera_label"),
        name=data.get("name"),
        lat=data.get("lat"),
        lon=data.get("lon"),
        placement_context=data.get("placement_context"),
        camera_model=data.get("camera_model"),
    )
    db.session.add(cam)
    db.session.commit()

    return jsonify(_camera_json(cam)), 201


@properties_api_bp.route("/cameras/<int:camera_id>", methods=["PUT"])
@login_required
def update_camera(camera_id):
    """Update a camera."""
    cam = Camera.query.get(camera_id)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404

    # Verify ownership through property
    prop = Property.query.get(cam.property_id)
    if not prop or prop.user_id != current_user.id:
        return jsonify({"error": "Camera not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    for field in (
        "camera_label", "name", "lat", "lon",
        "placement_context", "camera_model", "is_active",
    ):
        if field in data:
            setattr(cam, field, data[field])

    db.session.commit()
    return jsonify(_camera_json(cam))


@properties_api_bp.route("/cameras/<int:camera_id>", methods=["DELETE"])
@login_required
def delete_camera(camera_id):
    """Delete a camera."""
    cam = Camera.query.get(camera_id)
    if not cam:
        return jsonify({"error": "Camera not found"}), 404

    # Verify ownership through property
    prop = Property.query.get(cam.property_id)
    if not prop or prop.user_id != current_user.id:
        return jsonify({"error": "Camera not found"}), 404

    db.session.delete(cam)
    db.session.commit()

    return jsonify({"message": "Camera deleted"}), 200

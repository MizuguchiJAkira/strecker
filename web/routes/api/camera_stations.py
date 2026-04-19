"""API routes for CameraStation — per-property mapping of hunter
station-code short codes (e.g. ``CW``, ``BS``, ``MH``) to a
``placement_context`` used by ``bias/placement_ipw.py``.

All endpoints return JSON and require authentication. Ownership is
verified on every request (``property.user_id == current_user.id``).
"""

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from config import settings
from db.models import db, Property, CameraStation

camera_stations_api_bp = Blueprint(
    "camera_stations_api", __name__, url_prefix="/api"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _station_json(st):
    return {
        "id": st.id,
        "property_id": st.property_id,
        "station_code": st.station_code,
        "placement_context": st.placement_context,
        "label": st.label,
        "created_at": st.created_at.isoformat() if st.created_at else None,
        "updated_at": st.updated_at.isoformat() if st.updated_at else None,
    }


def _get_user_property(property_id):
    prop = Property.query.get(property_id)
    if prop and prop.user_id == current_user.id:
        return prop
    return None


def _normalize_code(raw):
    if raw is None:
        return None
    code = str(raw).strip().upper()
    if not code or not code.isalpha() or not (2 <= len(code) <= 8):
        return None
    return code


def _valid_context(ctx):
    return ctx in settings.PLACEMENT_CONTEXTS


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@camera_stations_api_bp.route(
    "/properties/<int:property_id>/camera-stations", methods=["GET"]
)
@login_required
def list_stations(property_id):
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404
    stations = CameraStation.query.filter_by(property_id=prop.id).all()
    return jsonify([_station_json(s) for s in stations])


@camera_stations_api_bp.route(
    "/properties/<int:property_id>/camera-stations", methods=["POST"]
)
@login_required
def create_station(property_id):
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    code = _normalize_code(data.get("station_code"))
    if not code:
        return jsonify({"error": "station_code must be 2-8 alpha chars"}), 400

    ctx = data.get("placement_context")
    if not _valid_context(ctx):
        return jsonify({
            "error": (
                f"placement_context must be one of "
                f"{settings.PLACEMENT_CONTEXTS}"
            )
        }), 400

    existing = CameraStation.query.filter_by(
        property_id=prop.id, station_code=code
    ).first()
    if existing:
        return jsonify({
            "error": f"station_code '{code}' already mapped on this property"
        }), 409

    st = CameraStation(
        property_id=prop.id,
        station_code=code,
        placement_context=ctx,
        label=data.get("label"),
    )
    db.session.add(st)
    db.session.commit()
    return jsonify(_station_json(st)), 201


@camera_stations_api_bp.route(
    "/properties/<int:property_id>/camera-stations/<station_code>",
    methods=["PATCH"],
)
@login_required
def update_station(property_id, station_code):
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    code = _normalize_code(station_code)
    if not code:
        return jsonify({"error": "invalid station_code"}), 400

    st = CameraStation.query.filter_by(
        property_id=prop.id, station_code=code
    ).first()
    if not st:
        return jsonify({"error": "CameraStation not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    if "placement_context" in data:
        ctx = data["placement_context"]
        if not _valid_context(ctx):
            return jsonify({
                "error": (
                    f"placement_context must be one of "
                    f"{settings.PLACEMENT_CONTEXTS}"
                )
            }), 400
        st.placement_context = ctx
    if "label" in data:
        st.label = data["label"]

    db.session.commit()
    return jsonify(_station_json(st))


@camera_stations_api_bp.route(
    "/properties/<int:property_id>/camera-stations/<station_code>",
    methods=["DELETE"],
)
@login_required
def delete_station(property_id, station_code):
    prop = _get_user_property(property_id)
    if not prop:
        return jsonify({"error": "Property not found"}), 404

    code = _normalize_code(station_code)
    if not code:
        return jsonify({"error": "invalid station_code"}), 400

    st = CameraStation.query.filter_by(
        property_id=prop.id, station_code=code
    ).first()
    if not st:
        return jsonify({"error": "CameraStation not found"}), 404

    db.session.delete(st)
    db.session.commit()
    return jsonify({"message": "CameraStation deleted"}), 200

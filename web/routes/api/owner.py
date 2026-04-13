"""Owner-only API — coverage data for the internal map.

GET /api/owner/coverage — aggregated habitat coverage data
    Returns generalized locations (snapped to ~10km grid), upload counts,
    and timestamps. No user names or exact coordinates exposed.
"""

import math
import logging
from collections import defaultdict
from datetime import datetime

from flask import Blueprint, jsonify, abort
from flask_login import login_required, current_user

from db.models import db, Property, Camera, Upload

logger = logging.getLogger(__name__)

owner_api_bp = Blueprint("owner_api", __name__, url_prefix="/api/owner")


def _require_owner():
    from flask import current_app
    if current_app.config.get("DEMO_MODE"):
        return  # skip owner check in demo mode
    if not getattr(current_user, "is_owner", False):
        abort(404)


def _snap_to_grid(lat, lon, precision=1):
    """Snap coordinates to a grid to generalize exact locations.

    precision=1 → ~10km grid (0.1 degree ≈ 11km)
    precision=0 → ~100km grid (1 degree ≈ 111km)
    """
    factor = 10 ** precision
    return round(lat * factor) / factor, round(lon * factor) / factor


# US ecoregion labels based on rough lat/lon ranges
# Simplified lookup — real version would use EPA Level III ecoregions
HABITAT_LABELS = {
    "TX_south": "South Texas Brush Country",
    "TX_hill": "Edwards Plateau",
    "TX_east": "East Texas Piney Woods",
    "TX_pan": "Texas Panhandle Grasslands",
    "TX_coast": "Texas Gulf Coast",
    "SE_coastal": "Southeast Coastal Plain",
    "SE_piedmont": "Piedmont",
    "MW_hardwood": "Midwest Hardwood Forest",
    "MW_prairie": "Great Plains Prairie",
    "NE_mixed": "Northeast Mixed Forest",
    "NW_conifer": "Pacific Northwest Conifer",
    "RM_montane": "Rocky Mountain Montane",
    "SW_desert": "Southwest Desert Scrub",
    "GL_boreal": "Great Lakes Boreal",
    "AP_forest": "Appalachian Forest",
}


def _classify_habitat(lat, lon, state):
    """Assign a habitat label based on state + coordinates.

    Rough classification — production would use EPA ecoregion shapefiles.
    """
    if state == "TX":
        if lat < 27.5:
            return "South Texas Brush Country"
        elif lat < 30 and lon > -97:
            return "Texas Gulf Coast"
        elif lat < 31 and lon < -99:
            return "Edwards Plateau"
        elif lat > 33:
            return "Texas Panhandle Grasslands"
        elif lon > -96:
            return "East Texas Piney Woods"
        else:
            return "Edwards Plateau"
    elif state in ("FL", "GA", "SC", "AL", "MS", "LA"):
        if lat < 31:
            return "Southeast Coastal Plain"
        return "Piedmont"
    elif state in ("OH", "IN", "IL", "MO", "IA", "WI", "MI", "MN"):
        if lat > 45:
            return "Great Lakes Boreal"
        return "Midwest Hardwood Forest"
    elif state in ("KS", "NE", "SD", "ND", "OK"):
        return "Great Plains Prairie"
    elif state in ("NY", "PA", "NJ", "CT", "MA", "VT", "NH", "ME", "RI"):
        return "Northeast Mixed Forest"
    elif state in ("VA", "WV", "KY", "TN", "NC"):
        return "Appalachian Forest"
    elif state in ("WA", "OR"):
        return "Pacific Northwest Conifer"
    elif state in ("CO", "MT", "WY", "ID", "UT"):
        return "Rocky Mountain Montane"
    elif state in ("AZ", "NM", "NV"):
        return "Southwest Desert Scrub"
    else:
        return f"{state} Habitat"


@owner_api_bp.route("/coverage", methods=["GET"])
@login_required
def get_coverage():
    """Return aggregated coverage data for the national map.

    Response shape:
    {
        "habitats": [
            {
                "id": "hab_29.5_-98.3",
                "lat": 29.5,
                "lon": -98.3,
                "habitat": "Edwards Plateau",
                "state": "TX",
                "properties": 3,       # count, no names
                "cameras": 42,
                "total_uploads": 15,
                "total_photos": 12450,
                "uploads": [            # timestamp list
                    {"date": "2025-12-15", "photos": 340},
                    ...
                ]
            },
            ...
        ],
        "summary": {
            "total_properties": 12,
            "total_cameras": 156,
            "total_uploads": 48,
            "total_photos": 52300,
            "states_covered": 5,
            "habitats_covered": 8,
        }
    }
    """
    _require_owner()

    # Pull all properties with their cameras and uploads
    properties = Property.query.all()

    # Group by snapped grid cell
    grid = defaultdict(lambda: {
        "lats": [], "lons": [], "states": set(),
        "property_ids": set(), "camera_count": 0,
        "uploads": [], "total_photos": 0,
    })

    for prop in properties:
        cameras = Camera.query.filter_by(property_id=prop.id).all()
        uploads = Upload.query.filter_by(
            property_id=prop.id, status="complete"
        ).order_by(Upload.uploaded_at.desc()).all()

        # Use camera centroid for location, fall back to property state
        if cameras and any(c.lat and c.lon for c in cameras):
            valid = [(c.lat, c.lon) for c in cameras if c.lat and c.lon]
            avg_lat = sum(ll[0] for ll in valid) / len(valid)
            avg_lon = sum(ll[1] for ll in valid) / len(valid)
        else:
            # No coordinates — skip (can't place on map)
            continue

        snap_lat, snap_lon = _snap_to_grid(avg_lat, avg_lon)
        cell_key = f"{snap_lat}_{snap_lon}"

        cell = grid[cell_key]
        cell["lats"].append(avg_lat)
        cell["lons"].append(avg_lon)
        cell["states"].add(prop.state or "??")
        cell["property_ids"].add(prop.id)
        cell["camera_count"] += len(cameras)

        for u in uploads:
            cell["uploads"].append({
                "date": u.uploaded_at.strftime("%Y-%m-%d") if u.uploaded_at else None,
                "photos": u.photo_count or 0,
            })
            cell["total_photos"] += u.photo_count or 0

    # Build response
    habitats = []
    total_props = set()
    total_cams = 0
    total_uploads = 0
    total_photos = 0
    states = set()

    for cell_key, cell in grid.items():
        avg_lat = sum(cell["lats"]) / len(cell["lats"])
        avg_lon = sum(cell["lons"]) / len(cell["lons"])
        state = list(cell["states"])[0] if cell["states"] else "??"
        habitat = _classify_habitat(avg_lat, avg_lon, state)

        habitats.append({
            "id": f"hab_{cell_key}",
            "lat": round(avg_lat, 1),
            "lon": round(avg_lon, 1),
            "habitat": habitat,
            "state": state,
            "properties": len(cell["property_ids"]),
            "cameras": cell["camera_count"],
            "total_uploads": len(cell["uploads"]),
            "total_photos": cell["total_photos"],
            "uploads": sorted(cell["uploads"],
                              key=lambda u: u["date"] or "", reverse=True),
        })

        total_props.update(cell["property_ids"])
        total_cams += cell["camera_count"]
        total_uploads += len(cell["uploads"])
        total_photos += cell["total_photos"]
        states.update(cell["states"])

    return jsonify({
        "habitats": habitats,
        "summary": {
            "total_properties": len(total_props),
            "total_cameras": total_cams,
            "total_uploads": total_uploads,
            "total_photos": total_photos,
            "states_covered": len(states),
            "habitats_covered": len(habitats),
        },
    })


@owner_api_bp.route("/coverage/seed-demo", methods=["POST"])
@login_required
def seed_demo_coverage():
    """Seed demo coverage data — fake properties across the US for visualization.

    Creates properties with cameras at realistic locations but owned by a
    synthetic 'network' user. No real user data involved.
    """
    _require_owner()

    from db.models import User
    from werkzeug.security import generate_password_hash
    import random

    # Demo coverage points: (lat, lon, state, region_name, num_cameras)
    demo_points = [
        # Texas (heavy coverage)
        (30.25, -99.10, "TX", "Edwards Plateau", 18),
        (29.40, -98.50, "TX", "South Texas", 12),
        (28.80, -97.50, "TX", "Matagorda Bay", 24),
        (31.90, -95.30, "TX", "East Texas", 8),
        (33.50, -101.80, "TX", "West Texas", 6),
        (27.50, -98.40, "TX", "South Brush Country", 15),
        (30.50, -96.30, "TX", "Post Oak Savannah", 10),
        # Southeast
        (32.30, -86.20, "AL", "Central Alabama", 7),
        (33.80, -84.40, "GA", "North Georgia", 9),
        (34.00, -81.00, "SC", "Midlands SC", 5),
        (30.40, -87.20, "FL", "NW Florida", 4),
        (32.30, -90.20, "MS", "Central MS", 6),
        (30.90, -91.50, "LA", "SE Louisiana", 8),
        # Midwest
        (40.00, -83.00, "OH", "Central Ohio", 5),
        (39.80, -86.20, "IN", "Central Indiana", 4),
        (41.90, -89.60, "IL", "Northern Illinois", 3),
        (44.50, -89.50, "WI", "Central Wisconsin", 6),
        (38.60, -92.20, "MO", "Central Missouri", 7),
        (42.00, -93.50, "IA", "Central Iowa", 4),
        # Northeast
        (42.30, -72.60, "MA", "Western Mass", 3),
        (41.30, -74.80, "NY", "Hudson Valley", 5),
        (40.80, -77.90, "PA", "Central PA", 8),
        # Appalachian
        (37.50, -79.40, "VA", "Shenandoah Valley", 6),
        (36.10, -80.30, "NC", "Piedmont NC", 7),
        (37.80, -84.30, "KY", "Bluegrass Region", 5),
        (35.90, -83.90, "TN", "East Tennessee", 4),
        # Plains
        (38.50, -98.80, "KS", "Central Kansas", 4),
        (41.20, -96.00, "NE", "Eastern Nebraska", 3),
        (35.50, -97.50, "OK", "Central Oklahoma", 5),
    ]

    # Find or create a 'network' user for demo properties
    network_user = User.query.filter_by(email="network@basal.eco").first()
    if not network_user:
        network_user = User(
            email="network@basal.eco",
            password_hash=generate_password_hash("internal"),
            display_name="Coverage Network",
        )
        db.session.add(network_user)
        db.session.commit()

    rng = random.Random(42)
    created = 0

    for lat, lon, state, region, n_cams in demo_points:
        # Check if already seeded
        existing = Property.query.filter_by(
            name=f"Network — {region}",
            user_id=network_user.id,
        ).first()
        if existing:
            continue

        prop = Property(
            user_id=network_user.id,
            name=f"Network — {region}",
            state=state,
            county=region,
            acreage=rng.randint(200, 5000),
        )
        db.session.add(prop)
        db.session.flush()  # get prop.id

        for i in range(n_cams):
            cam = Camera(
                property_id=prop.id,
                camera_label=f"CAM-{chr(65 + i % 26)}{i // 26:02d}",
                name=f"Camera {i + 1}",
                lat=lat + rng.uniform(-0.05, 0.05),
                lon=lon + rng.uniform(-0.05, 0.05),
                placement_context=rng.choice([
                    "feeder", "trail", "food_plot", "water", "random"
                ]),
                is_active=True,
            )
            db.session.add(cam)

        # Create some uploads with timestamps spread over the past year
        n_uploads = rng.randint(1, 6)
        for j in range(n_uploads):
            days_ago = rng.randint(7, 365)
            upload_date = datetime(2026, 4, 11) - __import__("datetime").timedelta(days=days_ago)
            upload = Upload(
                property_id=prop.id,
                user_id=network_user.id,
                status="complete",
                photo_count=rng.randint(80, 2500),
                uploaded_at=upload_date,
                processed_at=upload_date,
            )
            db.session.add(upload)

        created += 1

    db.session.commit()

    return jsonify({
        "status": "seeded",
        "properties_created": created,
        "total_points": len(demo_points),
    })

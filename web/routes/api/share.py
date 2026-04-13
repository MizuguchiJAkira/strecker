"""Share Card API endpoints for the Basal Informatics web app.

Generate shareable species detection cards with Open Graph support.
"""

import os
import uuid

from flask import Blueprint, jsonify, render_template_string, request, url_for
from flask_login import current_user, login_required

from db.models import db, Camera, DetectionSummary, Property, Season, ShareCard

share_api_bp = Blueprint("share_api", __name__)

# ---------------------------------------------------------------------------
# Common-name mapping (shared with dashboard)
# ---------------------------------------------------------------------------

COMMON_NAMES = {
    "white_tailed_deer": "White-tailed Deer",
    "feral_hog": "Feral Hog",
    "turkey": "Wild Turkey",
    "raccoon": "Raccoon",
    "armadillo": "Nine-banded Armadillo",
    "coyote": "Coyote",
    "bobcat": "Bobcat",
    "cottontail_rabbit": "Eastern Cottontail",
    "axis_deer": "Axis Deer",
    "opossum": "Virginia Opossum",
    "red_fox": "Red Fox",
    "gray_fox": "Gray Fox",
}


def _confidence_grade(conf):
    """Convert a 0-1 confidence float to a letter grade."""
    if conf is None:
        return "N/A"
    if conf >= 0.95:
        return "A+"
    if conf >= 0.90:
        return "A"
    if conf >= 0.85:
        return "B+"
    if conf >= 0.80:
        return "B"
    if conf >= 0.70:
        return "C"
    if conf >= 0.60:
        return "D"
    return "F"


def _generate_share_card_image(
    species_name, events, cameras, grade, property_name, token
):
    """Generate a 600x315 PNG share card image using Pillow.

    Returns the saved file path relative to static/, or None on failure.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    width, height = 600, 315
    bg_color = (30, 41, 59)  # #1e293b

    img = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    # Try to load a good font, fall back to default
    font_large = None
    font_medium = None
    font_small = None
    font_tiny = None
    try:
        # Try common system font paths
        for font_path in [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/SFNSText.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]:
            if os.path.exists(font_path):
                font_large = ImageFont.truetype(font_path, 36)
                font_medium = ImageFont.truetype(font_path, 20)
                font_small = ImageFont.truetype(font_path, 16)
                font_tiny = ImageFont.truetype(font_path, 12)
                break
    except (OSError, IOError):
        pass

    if font_large is None:
        font_large = ImageFont.load_default()
        font_medium = font_large
        font_small = font_large
        font_tiny = font_large

    # Accent bar at top
    draw.rectangle([0, 0, width, 6], fill=(13, 115, 119))  # teal #0D7377

    # Species name
    draw.text((40, 40), species_name, fill=(255, 255, 255), font=font_large)

    # Stats row
    stats_y = 110
    # Events
    draw.text((40, stats_y), str(events), fill=(13, 115, 119), font=font_medium)
    draw.text((40, stats_y + 28), "events", fill=(148, 163, 184), font=font_small)

    # Cameras
    draw.text((200, stats_y), str(cameras), fill=(13, 115, 119), font=font_medium)
    draw.text((200, stats_y + 28), "cameras", fill=(148, 163, 184), font=font_small)

    # Confidence grade
    draw.text((360, stats_y), grade, fill=(13, 115, 119), font=font_medium)
    draw.text(
        (360, stats_y + 28), "confidence", fill=(148, 163, 184), font=font_small
    )

    # Divider line
    draw.line([(40, 200), (width - 40, 200)], fill=(51, 65, 85), width=1)

    # Property name
    draw.text(
        (40, 220), property_name, fill=(148, 163, 184), font=font_small
    )

    # Watermark
    draw.text(
        (40, 270),
        "basalinformatics.com",
        fill=(71, 85, 105),
        font=font_tiny,
    )

    # Basal Informatics branding (right side)
    draw.text(
        (width - 200, 270),
        "Basal Informatics",
        fill=(71, 85, 105),
        font=font_tiny,
    )

    # Save
    static_dir = os.path.join(
        os.path.dirname(__file__), "..", "..", "static", "shares"
    )
    os.makedirs(static_dir, exist_ok=True)
    filename = f"{token}.png"
    filepath = os.path.join(static_dir, filename)
    img.save(filepath, "PNG")

    return f"shares/{filename}"


# ---------------------------------------------------------------------------
# POST /api/share-cards
# ---------------------------------------------------------------------------

@share_api_bp.route("/api/share-cards", methods=["POST"])
@login_required
def create_share_card():
    """Generate a share card image for a species detection summary."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    species_key = data.get("species_key")
    property_id = data.get("property_id")
    season_id = data.get("season_id")

    if not all([species_key, property_id, season_id]):
        return jsonify({"error": "species_key, property_id, season_id required"}), 400

    # Validate ownership
    prop = Property.query.get(property_id)
    if not prop or prop.user_id != current_user.id:
        return jsonify({"error": "Property not found"}), 404

    season = Season.query.filter_by(id=season_id, property_id=property_id).first()
    if not season:
        return jsonify({"error": "Season not found"}), 404

    # Aggregate detection data for this species
    camera_ids = {
        r[0]
        for r in db.session.query(Camera.id).filter_by(property_id=property_id).all()
    }
    if not camera_ids:
        return jsonify({"error": "No cameras found"}), 404

    detections = DetectionSummary.query.filter(
        DetectionSummary.season_id == season_id,
        DetectionSummary.camera_id.in_(camera_ids),
        DetectionSummary.species_key == species_key,
    ).all()

    if not detections:
        return jsonify({"error": "No detections found for species"}), 404

    total_events = sum(d.independent_events or 0 for d in detections)
    camera_count = len({d.camera_id for d in detections})
    conf_vals = [d.avg_confidence for d in detections if d.avg_confidence is not None]
    avg_conf = sum(conf_vals) / len(conf_vals) if conf_vals else None
    grade = _confidence_grade(avg_conf)

    common_name = COMMON_NAMES.get(
        species_key, species_key.replace("_", " ").title()
    )

    # Generate token and image
    token = uuid.uuid4().hex

    image_rel_path = _generate_share_card_image(
        species_name=common_name,
        events=total_events,
        cameras=camera_count,
        grade=grade,
        property_name=prop.name,
        token=token,
    )

    # Create DB record
    card = ShareCard(
        user_id=current_user.id,
        species_key=species_key,
        property_id=property_id,
        season_id=season_id,
        card_image_path=image_rel_path,
        share_token=token,
    )
    db.session.add(card)
    db.session.commit()

    image_url = (
        url_for("static", filename=image_rel_path)
        if image_rel_path
        else None
    )

    return jsonify({
        "share_token": token,
        "image_url": image_url,
        "share_url": f"/share/{token}",
    }), 201


# ---------------------------------------------------------------------------
# GET /share/<token>  (public, no auth)
# ---------------------------------------------------------------------------

SHARE_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>

    <!-- Open Graph -->
    <meta property="og:title" content="{{ title }}">
    <meta property="og:description" content="{{ description }}">
    <meta property="og:image" content="{{ image_url }}">
    <meta property="og:type" content="website">

    <!-- Twitter Card -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:title" content="{{ title }}">
    <meta name="twitter:description" content="{{ description }}">
    <meta name="twitter:image" content="{{ image_url }}">

    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f172a;
            color: #e2e8f0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            padding: 20px;
        }
        .card-container {
            max-width: 620px;
            width: 100%;
            text-align: center;
        }
        .card-container img {
            width: 100%;
            border-radius: 12px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
        }
        .branding {
            margin-top: 24px;
            font-size: 14px;
            color: #64748b;
        }
        .branding a {
            color: #0D7377;
            text-decoration: none;
        }
        .branding a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <div class="card-container">
        {% if image_url %}
        <img src="{{ image_url }}" alt="{{ title }}">
        {% else %}
        <p style="color:#94a3b8;">Share card image not available.</p>
        {% endif %}
        <p class="branding">
            Powered by <a href="https://basalinformatics.com">Basal Informatics</a>
        </p>
    </div>
</body>
</html>"""


@share_api_bp.route("/share/<token>")
def view_share_card(token):
    """Public page to view a share card with Open Graph meta tags."""
    card = ShareCard.query.filter_by(share_token=token).first()
    if not card:
        return "Share card not found", 404

    prop = Property.query.get(card.property_id)
    season = Season.query.get(card.season_id)
    common_name = COMMON_NAMES.get(
        card.species_key, card.species_key.replace("_", " ").title()
    )
    property_name = prop.name if prop else "Unknown Property"

    # Build aggregate stats for description
    camera_ids = {
        r[0]
        for r in db.session.query(Camera.id).filter_by(
            property_id=card.property_id
        ).all()
    }
    detections = DetectionSummary.query.filter(
        DetectionSummary.season_id == card.season_id,
        DetectionSummary.camera_id.in_(camera_ids),
        DetectionSummary.species_key == card.species_key,
    ).all() if camera_ids else []

    total_events = sum(d.independent_events or 0 for d in detections)
    camera_count = len({d.camera_id for d in detections})

    title = f"{common_name} at {property_name}"
    description = f"{total_events} detections across {camera_count} cameras"

    image_url = (
        url_for("static", filename=card.card_image_path, _external=True)
        if card.card_image_path
        else ""
    )

    return render_template_string(
        SHARE_PAGE_TEMPLATE,
        title=title,
        description=description,
        image_url=image_url,
    )

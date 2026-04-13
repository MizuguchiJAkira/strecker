"""Feedback routes — hunter correction submission and review queue.

Flask endpoints for the human-in-the-loop feedback system:
  POST /feedback/correction      — submit a species correction
  POST /feedback/ecological      — submit ecological ground-truth note
  GET  /feedback/review-queue    — get uncertain detections for review
  GET  /feedback/accuracy/<hu>   — get regional accuracy stats
"""

from flask import Blueprint, jsonify, request

from strecker.feedback import (
    get_regional_accuracy,
    get_review_queue,
    submit_correction,
    submit_ecological_feedback,
)

feedback_bp = Blueprint("feedback", __name__, url_prefix="/feedback")


@feedback_bp.route("/correction", methods=["POST"])
def post_correction():
    """Submit a species classification correction.

    Request JSON:
        {
            "detection_id": 123,
            "corrected_species": "axis_deer",   // null for false_positive
            "user_id": "USER-01",               // optional, defaults to anonymous
            "correction_type": "misclassification"
                // one of: misclassification, false_positive, missed_detection
        }

    Returns:
        201 with correction details and updated regional accuracy.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    detection_id = data.get("detection_id")
    if detection_id is None:
        return jsonify({"error": "detection_id is required"}), 400

    corrected_species = data.get("corrected_species")
    user_id = data.get("user_id", "anonymous")
    correction_type = data.get("correction_type", "misclassification")

    try:
        result = submit_correction(
            detection_id=int(detection_id),
            corrected_species_key=corrected_species,
            user_id=user_id,
            correction_type=correction_type,
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@feedback_bp.route("/ecological", methods=["POST"])
def post_ecological():
    """Submit ecological ground-truth feedback.

    Request JSON:
        {
            "camera_id": "CAM-F01",
            "user_id": "USER-01",
            "ecological_note": "Heavy root damage despite low hog detection"
        }

    Returns:
        201 with feedback details.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    camera_id = data.get("camera_id")
    if not camera_id:
        return jsonify({"error": "camera_id is required"}), 400

    ecological_note = data.get("ecological_note")
    if not ecological_note:
        return jsonify({"error": "ecological_note is required"}), 400

    user_id = data.get("user_id", "anonymous")

    try:
        result = submit_ecological_feedback(
            camera_id=camera_id,
            user_id=user_id,
            ecological_note=ecological_note,
        )
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@feedback_bp.route("/review-queue", methods=["GET"])
def review_queue():
    """Get the next batch of uncertain detections for human review.

    Query params:
        habitat_unit_id — optional, filter by habitat unit
        limit           — max results, default 50

    Returns highest-entropy detections first (most uncertain = most
    value from human review).
    """
    habitat_unit_id = request.args.get("habitat_unit_id")
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 200)  # Cap at 200

    results = get_review_queue(
        habitat_unit_id=habitat_unit_id,
        limit=limit,
    )

    return jsonify({
        "count": len(results),
        "habitat_unit_id": habitat_unit_id,
        "detections": results,
    })


@feedback_bp.route("/accuracy/<habitat_unit_id>", methods=["GET"])
def regional_accuracy(habitat_unit_id):
    """Get regional accuracy stats for a habitat unit.

    Query params:
        species — optional, filter to one species

    Used by the Risk Synthesis Engine for insurer confidence grades.
    """
    species = request.args.get("species")

    results = get_regional_accuracy(
        habitat_unit_id=habitat_unit_id,
        species_key=species,
    )

    return jsonify({
        "habitat_unit_id": habitat_unit_id,
        "species_count": len(results),
        "species": results,
    })

"""Coverage Score calculator for Basal Informatics.

Grades a hunter's camera network A-F based on how well it produces
insurer-grade ecological data.  Four sub-scores (density, diversity,
distribution, temporal) are combined into a weighted overall score.

Phase 3 -- camera-network quality metric.
"""

import json
import math

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLACEMENT_CONTEXTS = ["feeder", "trail", "water", "food_plot", "random", "other"]
ACRES_TO_KM2 = 0.00404686

GRADE_THRESHOLDS = [
    (90, "A"),
    (85, "A-"),
    (80, "B+"),
    (75, "B"),
    (70, "B-"),
    (65, "C+"),
    (60, "C"),
    (55, "C-"),
    (50, "D"),
]


def _score_to_grade(score: float) -> str:
    """Map a 0-100 score to a letter grade."""
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


# ---------------------------------------------------------------------------
# Sub-score: Density  (30 %)
# ---------------------------------------------------------------------------

def _density_score(num_cameras: int, acreage: float) -> float:
    """Cameras per km^2 relative to ideal of 1.5/km^2."""
    if acreage <= 0:
        return 0.0
    area_km2 = acreage * ACRES_TO_KM2
    cameras_per_km2 = num_cameras / area_km2
    return min(100.0, (cameras_per_km2 / 1.5) * 100.0)


# ---------------------------------------------------------------------------
# Sub-score: Diversity  (20 %)
# ---------------------------------------------------------------------------

def _diversity_score(cameras) -> float:
    """Shannon entropy over placement_context distribution."""
    if not cameras:
        return 20.0

    # Count contexts
    context_counts: dict[str, int] = {}
    for cam in cameras:
        ctx = getattr(cam, "placement_context", None) or "other"
        ctx = ctx.lower().strip()
        if ctx not in PLACEMENT_CONTEXTS:
            ctx = "other"
        context_counts[ctx] = context_counts.get(ctx, 0) + 1

    total = sum(context_counts.values())
    if total == 0:
        return 20.0

    # Shannon entropy
    entropy = 0.0
    for count in context_counts.values():
        if count > 0:
            p = count / total
            entropy -= p * math.log(p)

    max_entropy = math.log(len(PLACEMENT_CONTEXTS))  # log(6)
    if max_entropy == 0:
        return 20.0

    score = 20.0 + 80.0 * (entropy / max_entropy)

    # Feeder penalty: if >70 % feeders, cap at 40
    feeder_count = context_counts.get("feeder", 0)
    if total > 0 and (feeder_count / total) > 0.70:
        score = min(score, 40.0)

    return score


# ---------------------------------------------------------------------------
# Sub-score: Distribution  (30 %)
# ---------------------------------------------------------------------------

def _convex_hull_area_shapely(points):
    """Calculate convex hull area using Shapely (returns area in deg^2)."""
    from shapely.geometry import MultiPoint  # noqa: delayed import

    if len(points) < 3:
        return 0.0
    mp = MultiPoint(points)
    hull = mp.convex_hull
    return hull.area


def _bounding_box_area(points):
    """Simple fallback: bounding box area from lat/lon points."""
    if len(points) < 2:
        return 0.0
    lats = [p[1] for p in points]
    lons = [p[0] for p in points]
    return (max(lats) - min(lats)) * (max(lons) - min(lons))


def _parse_boundary(boundary_geojson):
    """Parse boundary GeoJSON and return its area (using Shapely or bbox)."""
    if not boundary_geojson:
        return None

    try:
        geo = json.loads(boundary_geojson) if isinstance(boundary_geojson, str) else boundary_geojson
    except (json.JSONDecodeError, TypeError):
        return None

    try:
        from shapely.geometry import shape  # noqa
        geom = shape(geo if geo.get("type") != "FeatureCollection" else geo["features"][0]["geometry"])
        return geom.area
    except Exception:
        pass

    # Fallback: extract coordinates and compute bounding box
    try:
        coords = _extract_coords(geo)
        if coords:
            return _bounding_box_area(coords)
    except Exception:
        pass

    return None


def _extract_coords(geo):
    """Recursively extract coordinate pairs from GeoJSON."""
    if geo.get("type") == "FeatureCollection":
        for feat in geo.get("features", []):
            result = _extract_coords(feat)
            if result:
                return result
    elif geo.get("type") == "Feature":
        return _extract_coords(geo.get("geometry", {}))
    elif geo.get("type") in ("Polygon", "MultiPolygon"):
        coords = geo.get("coordinates", [])
        # Flatten to list of [lon, lat] pairs
        flat = []
        _flatten_coords(coords, flat)
        return flat
    return None


def _flatten_coords(coords, out):
    """Flatten nested coordinate arrays."""
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        out.append(coords[:2])
        return
    for item in coords:
        _flatten_coords(item, out)


def _max_distance(points):
    """Maximum Euclidean distance between any two points (in degrees)."""
    if len(points) < 2:
        return 0.0
    max_d = 0.0
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            dx = points[i][0] - points[j][0]
            dy = points[i][1] - points[j][1]
            d = math.sqrt(dx * dx + dy * dy)
            if d > max_d:
                max_d = d
    return max_d


def _distribution_score(cameras, acreage: float, boundary_geojson) -> float:
    """Spatial distribution of cameras over the property."""
    points = []
    for cam in cameras:
        lat = getattr(cam, "lat", None)
        lon = getattr(cam, "lon", None)
        if lat is not None and lon is not None:
            points.append((lon, lat))  # x, y convention

    if len(points) < 2:
        return 10.0  # can't evaluate spread with <2 cameras

    # Try convex hull area
    try:
        hull_area = _convex_hull_area_shapely(points)
    except Exception:
        hull_area = _bounding_box_area(points)

    boundary_area = _parse_boundary(boundary_geojson)

    if boundary_area and boundary_area > 0:
        ratio = hull_area / boundary_area
        return min(100.0, ratio * 120.0)

    # No boundary: use spread relative to expected property size
    # Convert acreage to approximate degree span
    # 1 acre ~ 4047 m^2, sqrt gives side length in meters
    # At mid-latitudes, 1 degree ~ 111,000 m
    if acreage > 0:
        side_m = math.sqrt(acreage * 4047)  # approx side in meters
        side_deg = side_m / 111000.0  # rough degree equivalent
        spread = _max_distance(points)
        if side_deg > 0:
            ratio = spread / side_deg
            return min(100.0, ratio * 100.0)

    return 50.0  # fallback


# ---------------------------------------------------------------------------
# Sub-score: Temporal  (20 %)
# ---------------------------------------------------------------------------

def _temporal_score(days_monitored: int) -> float:
    """Score based on monitoring duration, targeting 180 days."""
    if days_monitored <= 0:
        return 0.0
    if days_monitored < 30:
        return max(10.0, days_monitored * 2.0)
    return min(100.0, (days_monitored / 180.0) * 100.0)


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------

def _build_recommendations(
    density: float,
    diversity: float,
    distribution: float,
    temporal: float,
    num_cameras: int,
    acreage: float,
    cameras,
    days_monitored: int,
) -> list[str]:
    """Generate actionable recommendations based on weak sub-scores."""
    recs = []

    if density < 60:
        area_km2 = acreage * ACRES_TO_KM2 if acreage > 0 else 1.0
        ideal_cameras = math.ceil(1.5 * area_km2)
        needed = max(1, ideal_cameras - num_cameras)
        recs.append(f"Add {needed} more cameras for better coverage density")

    if diversity < 50:
        # Find dominant context
        context_counts: dict[str, int] = {}
        for cam in cameras:
            ctx = getattr(cam, "placement_context", None) or "other"
            context_counts[ctx] = context_counts.get(ctx, 0) + 1
        top_context = max(context_counts, key=context_counts.get) if context_counts else "feeder"
        recs.append(
            f"Your cameras are mostly at {top_context}s. "
            "Add cameras on trails, water sources, or random locations."
        )

    if distribution < 60:
        recs.append(
            "Your cameras are clustered. Spread them across different areas of your property."
        )

    if temporal < 70:
        recs.append(
            f"Keep cameras deployed longer. {days_monitored} days monitored "
            "— aim for 6+ months."
        )

    return recs


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def calculate_coverage(
    cameras: list,
    property_acreage: float,
    boundary_geojson: str | None,
    days_monitored: int,
) -> dict:
    """Calculate the coverage score for a camera network.

    Parameters
    ----------
    cameras : list
        Camera model instances (need .lat, .lon, .placement_context).
    property_acreage : float
        Property size in acres.
    boundary_geojson : str | None
        GeoJSON string of the property boundary, or None.
    days_monitored : int
        Total days cameras have been deployed.

    Returns
    -------
    dict with keys: overall_score, density_score, diversity_score,
    distribution_score, temporal_score, grade, recommendations.
    """
    num_cameras = len(cameras)
    acreage = property_acreage or 0.0

    density = _density_score(num_cameras, acreage)
    diversity = _diversity_score(cameras)
    distribution = _distribution_score(cameras, acreage, boundary_geojson)
    temporal = _temporal_score(days_monitored)

    overall = (
        density * 0.30
        + diversity * 0.20
        + distribution * 0.30
        + temporal * 0.20
    )

    grade = _score_to_grade(overall)

    recommendations = _build_recommendations(
        density, diversity, distribution, temporal,
        num_cameras, acreage, cameras, days_monitored,
    )

    return {
        "overall_score": round(overall, 1),
        "density_score": round(density, 1),
        "diversity_score": round(diversity, 1),
        "distribution_score": round(distribution, 1),
        "temporal_score": round(temporal, 1),
        "grade": grade,
        "recommendations": recommendations,
    }

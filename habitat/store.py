"""Shared spatial data store for the habitat module.

In production, all queries go against PostGIS tables via psycopg2.
In demo mode, this module provides an in-memory SQLite store with
plain-geometry columns (WKT text), using Haversine math instead of
PostGIS spatial functions. The SQL column names and table structure
match db/schema.sql exactly so the production swap is mechanical.

This store is shared across fingerprint, units, corridors, confidence,
and gaps modules — all operating on the same in-memory database during
a single pipeline run.
"""

import math
import sqlite3
import threading
from typing import List, Optional, Tuple

_lock = threading.Lock()
_db = None


def get_db() -> sqlite3.Connection:
    """Get or create the shared in-memory SQLite database."""
    global _db
    if _db is None:
        _db = sqlite3.connect(":memory:", check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _init_schema(_db)
    return _db


def reset_db():
    """Reset the store (for testing)."""
    global _db
    if _db is not None:
        _db.close()
    _db = None


def _init_schema(db: sqlite3.Connection):
    """Create tables matching the PostGIS schema."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS camera_stations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT UNIQUE NOT NULL,
            user_id TEXT,
            lat REAL,
            lon REAL,
            habitat_unit_id TEXT,
            placement_context TEXT,
            installed_date TEXT,
            last_active TEXT,
            camera_model TEXT
        );

        CREATE TABLE IF NOT EXISTS habitat_fingerprints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT UNIQUE,
            ecoregion_iii_code TEXT,
            ecoregion_iii_name TEXT,
            ecoregion_iv_code TEXT,
            ecoregion_iv_name TEXT,
            nlcd_code INTEGER,
            nlcd_class TEXT,
            huc10 TEXT,
            huc10_name TEXT,
            elevation_m REAL,
            slope_degrees REAL,
            distance_to_water_m REAL,
            stream_order INTEGER,
            soil_type TEXT,
            canopy_cover_pct REAL
        );

        CREATE TABLE IF NOT EXISTS habitat_units (
            id TEXT PRIMARY KEY,
            huc10 TEXT NOT NULL,
            huc10_name TEXT,
            ecoregion_iv_code TEXT NOT NULL,
            ecoregion_iv_name TEXT,
            ecoregion_iii_code TEXT,
            ecoregion_iii_name TEXT,
            nlcd_code INTEGER NOT NULL,
            nlcd_class TEXT,
            area_km2 REAL,
            camera_count INTEGER DEFAULT 0,
            total_camera_nights INTEGER DEFAULT 0,
            contributing_users INTEGER DEFAULT 0,
            monitoring_start TEXT,
            monitoring_end TEXT,
            monitoring_months INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS corridors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habitat_unit_id TEXT,
            corridor_type TEXT NOT NULL,
            length_km REAL NOT NULL,
            -- In PostGIS this is GEOMETRY(LineString, 4326)
            -- In SQLite demo, store as WKT or segment endpoints
            start_lat REAL,
            start_lon REAL,
            end_lat REAL,
            end_lon REAL
        );

        CREATE TABLE IF NOT EXISTS species_confidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habitat_unit_id TEXT NOT NULL,
            species_key TEXT NOT NULL,
            total_detections INTEGER DEFAULT 0,
            cameras_detected INTEGER DEFAULT 0,
            cameras_total INTEGER DEFAULT 0,
            detection_frequency_pct REAL,
            raw_detection_frequency_pct REAL,
            bias_correction_applied INTEGER DEFAULT 0,
            classification_confidence_pct REAL,
            corridor_coverage_pct REAL,
            overall_confidence_pct REAL,
            confidence_grade TEXT,
            monitoring_start TEXT,
            monitoring_end TEXT,
            monitoring_months INTEGER DEFAULT 0,
            UNIQUE(habitat_unit_id, species_key)
        );

        CREATE TABLE IF NOT EXISTS monitoring_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            habitat_unit_id TEXT NOT NULL,
            corridor_type TEXT NOT NULL,
            gap_start_lat REAL,
            gap_start_lon REAL,
            gap_end_lat REAL,
            gap_end_lon REAL,
            gap_length_m REAL,
            species_most_affected TEXT,
            projected_confidence_increase_pct REAL,
            cameras_needed INTEGER DEFAULT 1
        );
    """)
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Spatial math utilities (Haversine — replaces PostGIS ST_Distance)
# ═══════════════════════════════════════════════════════════════════════════

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float,
                lat2: float, lon2: float) -> float:
    """Haversine distance in meters between two lat/lon points."""
    r = EARTH_RADIUS_M
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2 * r * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def point_to_segment_distance_m(
        px: float, py: float,
        ax: float, ay: float,
        bx: float, by: float) -> float:
    """Approximate min distance from point (px,py) to segment (ax,ay)-(bx,by).

    Uses projected position along segment in lat/lon space, then Haversine
    for the distance. Good enough for corridor coverage at ranch scale.
    """
    # Vector math in lat/lon (small area approximation)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return haversine_m(px, py, ax, ay)

    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    nearest_lat = ax + t * dx
    nearest_lon = ay + t * dy
    return haversine_m(px, py, nearest_lat, nearest_lon)

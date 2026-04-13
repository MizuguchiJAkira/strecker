-- Basal Informatics PostGIS Schema
-- Run once: python manage.py db init

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- Camera stations registered by hunters
CREATE TABLE camera_stations (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50) UNIQUE NOT NULL,
    user_id VARCHAR(50),
    geom GEOMETRY(Point, 4326),
    habitat_unit_id VARCHAR(100),
    placement_context VARCHAR(20),          -- 'trail', 'feeder', 'food_plot', 'water', 'random', 'other'
                                            -- Kolowski & Forrester 2017: 9.7x detection inflation at trail/feeder vs random
    installed_date DATE,
    last_active DATE,
    camera_model VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Individual species detections from Strecker
CREATE TABLE detections (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50) REFERENCES camera_stations(camera_id),
    species_key VARCHAR(50) NOT NULL,
    confidence FLOAT NOT NULL,
    confidence_calibrated FLOAT,           -- after temperature scaling (Dussert et al. 2025)
    timestamp TIMESTAMP NOT NULL,
    image_filename VARCHAR(255),
    megadetector_confidence FLOAT,
    burst_group_id VARCHAR(100),           -- same trigger burst (<60s), for ensemble classification
    independent_event_id VARCHAR(100),     -- 30-min independence threshold, for ecological analysis
    review_required BOOLEAN DEFAULT FALSE, -- true when softmax entropy > threshold
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE habitat_fingerprints (
    id SERIAL PRIMARY KEY,
    camera_id VARCHAR(50) REFERENCES camera_stations(camera_id),
    ecoregion_iii_code VARCHAR(10),
    ecoregion_iii_name VARCHAR(100),
    ecoregion_iv_code VARCHAR(10),
    ecoregion_iv_name VARCHAR(100),
    nlcd_code INTEGER,
    nlcd_class VARCHAR(100),
    huc10 VARCHAR(20),
    huc10_name VARCHAR(100),
    elevation_m FLOAT,
    slope_degrees FLOAT,
    distance_to_water_m FLOAT,
    stream_order INTEGER,
    soil_type VARCHAR(100),
    canopy_cover_pct FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE habitat_units (
    id VARCHAR(100) PRIMARY KEY,           -- e.g. 'HU-1209020104-30a-41'
    huc10 VARCHAR(20) NOT NULL,
    huc10_name VARCHAR(100),
    ecoregion_iv_code VARCHAR(10) NOT NULL,
    ecoregion_iv_name VARCHAR(100),
    ecoregion_iii_code VARCHAR(10),
    ecoregion_iii_name VARCHAR(100),
    nlcd_code INTEGER NOT NULL,
    nlcd_class VARCHAR(100),
    area_km2 FLOAT,
    geom GEOMETRY(MultiPolygon, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE corridors (
    id SERIAL PRIMARY KEY,
    habitat_unit_id VARCHAR(100) REFERENCES habitat_units(id),
    corridor_type VARCHAR(50) NOT NULL,
    length_km FLOAT NOT NULL,
    geom GEOMETRY(LineString, 4326),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE species_confidence (
    id SERIAL PRIMARY KEY,
    habitat_unit_id VARCHAR(100) REFERENCES habitat_units(id),
    species_key VARCHAR(50) NOT NULL,
    total_detections INTEGER,
    cameras_detected INTEGER,
    cameras_total INTEGER,
    detection_frequency_pct FLOAT,
    raw_detection_frequency_pct FLOAT,
    bias_correction_applied BOOLEAN DEFAULT FALSE,
    classification_confidence_pct FLOAT,
    corridor_coverage_pct FLOAT,
    overall_confidence_pct FLOAT,
    confidence_grade CHAR(2),
    monitoring_start DATE,
    monitoring_end DATE,
    monitoring_months INTEGER,
    UNIQUE(habitat_unit_id, species_key)
);

CREATE TABLE risk_assessments (
    id SERIAL PRIMARY KEY,
    parcel_id VARCHAR(50) UNIQUE NOT NULL,
    parcel_boundary GEOMETRY(Polygon, 4326),
    acreage FLOAT,
    county VARCHAR(100),
    state VARCHAR(2),
    assessment_date DATE,
    risk_json JSONB NOT NULL,
    pdf_path VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Human feedback (per Sara Beery, MIT CSAIL: OOD performance estimation is impossible,
-- so build performance estimation into the system via user feedback)
CREATE TABLE feedback_corrections (
    id SERIAL PRIMARY KEY,
    detection_id INTEGER REFERENCES detections(id),
    camera_id VARCHAR(50) REFERENCES camera_stations(camera_id),
    original_species_key VARCHAR(50) NOT NULL,
    corrected_species_key VARCHAR(50),
    original_confidence FLOAT,
    user_id VARCHAR(50),
    habitat_unit_id VARCHAR(100),
    correction_type VARCHAR(30) NOT NULL,   -- 'misclassification', 'false_positive', 'missed_detection', 'ecological_mismatch'
    ecological_note TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE regional_performance (
    id SERIAL PRIMARY KEY,
    habitat_unit_id VARCHAR(100) REFERENCES habitat_units(id),
    species_key VARCHAR(50) NOT NULL,
    total_predictions INTEGER DEFAULT 0,
    total_corrections INTEGER DEFAULT 0,
    estimated_classification_accuracy_pct FLOAT,
    ecological_validation_status VARCHAR(20),  -- 'calibrated', 'partially_validated', 'unvalidated'
    calibration_source VARCHAR(50),
    last_updated TIMESTAMP DEFAULT NOW(),
    UNIQUE(habitat_unit_id, species_key)
);

-- Spatial indexes
CREATE INDEX idx_cameras_geom ON camera_stations USING GIST(geom);
CREATE INDEX idx_habitat_units_geom ON habitat_units USING GIST(geom);
CREATE INDEX idx_corridors_geom ON corridors USING GIST(geom);
CREATE INDEX idx_parcels_geom ON risk_assessments USING GIST(parcel_boundary);
CREATE INDEX idx_detections_camera ON detections(camera_id);
CREATE INDEX idx_detections_species ON detections(species_key);
CREATE INDEX idx_detections_timestamp ON detections(timestamp);

"""Pydantic models for the Risk Synthesis Engine.

Defines input/output schemas for parcel risk assessments. The output
JSON is consumed directly by the enterprise PDF report generator.

Every field is explicitly defined — no **kwargs, no Optional-by-default.
The insurer sees exactly what these schemas describe.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════
# Input schemas
# ═══════════════════════════════════════════════════════════════════════════

class ParcelQuery(BaseModel):
    """Input to the risk synthesis engine."""
    parcel_id: str = Field(..., description="Unique parcel identifier")
    acreage: float = Field(..., gt=0)
    county: str
    state: str
    ecoregion: str = Field(
        default="edwards_plateau",
        description="Level III ecoregion key for calibration")
    boundary_geojson: Optional[Dict] = Field(
        default=None,
        description="GeoJSON polygon of parcel boundary (for spatial overlay)")
    prepared_for: Optional[Dict] = Field(
        default=None,
        description="Client info: {company, contact}")


# ═══════════════════════════════════════════════════════════════════════════
# Species inventory
# ═══════════════════════════════════════════════════════════════════════════

class SpeciesInventoryEntry(BaseModel):
    """One species in the parcel inventory."""
    species_key: str
    common_name: str
    scientific_name: str
    native: bool
    invasive: bool
    esa_status: Optional[str] = None
    risk_flag: Optional[str] = Field(
        default=None,
        description="INVASIVE — HIGH/MODERATE/LOW or ESA — status")
    independent_events: int = Field(
        ..., description="Weighted by HU overlap fraction")
    detection_frequency_pct: float = Field(
        ..., description="IPW-adjusted if bias correction applied")
    raw_detection_frequency_pct: float
    confidence_grade: str
    confidence_pct: float
    cameras_detected: int
    cameras_total: int
    habitat_units: List[str] = Field(
        ..., description="HU IDs where this species was detected")


# ═══════════════════════════════════════════════════════════════════════════
# Damage projections
# ═══════════════════════════════════════════════════════════════════════════

class DamageProjection(BaseModel):
    """DCF damage model output for one invasive species."""
    species_key: str
    common_name: str
    base_cost_per_acre: float = Field(
        ..., description="USDA-APHIS base rate ($/acre/year)")
    ecoregion_calibration_factor: float
    frequency_scale: float = Field(
        ..., description="Logistic scaling from detection frequency")
    detection_frequency_pct: float = Field(
        ..., description="IPW-adjusted detection frequency used")
    acreage: float
    estimated_annual_loss: float
    ten_year_npv: float
    confidence_grade: str
    confidence_interval_pct: float = Field(
        ..., description="Plus/minus percentage based on confidence grade")
    confidence_interval_low: float
    confidence_interval_high: float
    methodology: str
    broadley_caveat: str = Field(
        default=("Detection frequency is a relative activity index, "
                 "not absolute density. Broadley et al. 2020 showed "
                 "density-dependent movement can cause cameras to "
                 "underestimate population declines by up to 30%."),
        description="Caveat on detection-density relationship")


class FHExposureScore(BaseModel):
    """Feral Hog Exposure Score (0-100) composite."""
    score: int = Field(..., ge=0, le=100)
    detection_frequency_component: float = Field(
        ..., description="0.4 weight")
    recency_component: float = Field(
        ..., description="0.3 weight — days since last detection")
    spatial_extent_component: float = Field(
        ..., description="0.3 weight — fraction of cameras detecting")
    interpretation: str


# ═══════════════════════════════════════════════════════════════════════════
# Regulatory risk
# ═══════════════════════════════════════════════════════════════════════════

class ESASpeciesRisk(BaseModel):
    """Regulatory risk for one ESA-listed species."""
    species_key: str
    common_name: str
    scientific_name: str
    esa_status: str
    estimated_habitat_overlap_acres: float
    section_7_required: bool = Field(
        ..., description="Federal nexus triggers Section 7 consultation")
    section_10_hcp: bool = Field(
        ..., description="Section 10 HCP if no federal nexus")
    estimated_compliance_cost_low: float
    estimated_compliance_cost_high: float
    notes: str


class RegulatoryRisk(BaseModel):
    """Aggregate ESA regulatory risk for the parcel."""
    esa_species_present: List[str]
    consultation_required: bool
    total_estimated_compliance_cost_low: float
    total_estimated_compliance_cost_high: float
    species_details: List[ESASpeciesRisk]


# ═══════════════════════════════════════════════════════════════════════════
# Data confidence
# ═══════════════════════════════════════════════════════════════════════════

class RegionalModelAccuracy(BaseModel):
    """Classification accuracy from user feedback loop."""
    source: str = "paired_field_survey + user_feedback"
    species_accuracies: Dict[str, float] = Field(
        ..., description="species_key -> accuracy_pct")
    ecological_validation_status: str
    calibration_note: str = (
        "Detection-to-density calibrated via paired surveys at "
        "Matagorda Bay. Classification accuracy from hunter "
        "corrections in Edwards Plateau habitat units.")


class DataGap(BaseModel):
    """One monitoring gap from gap analysis."""
    habitat_unit_id: str
    corridor_type: str
    gap_length_m: float
    species_most_affected: str
    cameras_needed: int
    projected_confidence_increase_pct: float


class DataConfidence(BaseModel):
    """Overall data confidence summary for the parcel."""
    overall_grade: str
    monitoring_months: int
    camera_density_per_km2: float
    regional_model_accuracy: RegionalModelAccuracy
    top_data_gaps: List[DataGap]


# ═══════════════════════════════════════════════════════════════════════════
# Full assessment output
# ═══════════════════════════════════════════════════════════════════════════

class ParcelRiskAssessment(BaseModel):
    """Complete parcel risk assessment — the product we sell.

    This JSON is consumed by the enterprise PDF generator and
    delivered to insurer/lender clients for TNFD compliance.
    """
    parcel_id: str
    acreage: float
    county: str
    state: str
    assessment_date: str
    species_inventory: List[SpeciesInventoryEntry]
    damage_projections: Dict[str, DamageProjection]
    feral_hog_exposure_score: Optional[FHExposureScore] = None
    regulatory_risk: RegulatoryRisk
    overall_risk_rating: str = Field(
        ..., description="LOW / MODERATE / ELEVATED / HIGH / CRITICAL")
    data_confidence: DataConfidence
    methodology_version: str = "1.0.0"
    bias_correction_applied: bool
    prepared_for: Optional[Dict] = None

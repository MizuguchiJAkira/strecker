"""ESA regulatory risk assessment.

For any species with esa_status not null, flags Section 7 (federal
nexus) or Section 10 HCP consultation requirements and estimates
conditional compliance cost ranges.

Section 7: Required when there's a federal nexus (federal funding,
federal permits, federal land). The agency must consult with USFWS.
Cost is typically borne by the project proponent.

Section 10 HCP: Required for "take" of listed species on private
land without a federal nexus. Habitat Conservation Plans can cost
$15K–$500K+ depending on species, acreage, and habitat quality.

For the Edwards Plateau demo, the golden-cheeked warbler
(Setophaga chrysoparia) is ESA Endangered and endemic to the
juniper-oak woodlands of the region. Any parcel with significant
Ashe juniper canopy triggers consultation.
"""

from typing import Dict, List, Optional

from config.species_reference import SPECIES_REFERENCE


# ESA species that could appear in Texas deployments
# These supplement SPECIES_REFERENCE for species that cameras may
# not detect but whose habitat overlaps the parcel.
_ESA_SPECIES_DATABASE = {
    "golden_cheeked_warbler": {
        "common_name": "Golden-cheeked Warbler",
        "scientific_name": "Setophaga chrysoparia",
        "esa_status": "Endangered",
        "habitat_description": (
            "Breeding endemic to mature Ashe juniper-oak woodland "
            "on the Edwards Plateau. Requires juniper bark strips "
            "for nest construction — cannot breed elsewhere."),
        "range_ecoregions": ["edwards_plateau"],
        # Compliance cost model (per-acre + fixed costs)
        "compliance_cost_model": {
            # HCP development: biological surveys, plan writing,
            # USFWS review, mitigation commitments
            "fixed_cost_range": (15000, 45000),
            # Per-acre mitigation (conservation easement, habitat
            # restoration, or in-lieu fee)
            "per_acre_cost_range": (0, 118),
            # Typical overlap fraction for Edwards Plateau parcels
            # with suitable habitat (~14.5% of juniper-oak woodland)
            "typical_overlap_fraction": 0.145,
        },
    },
    "black_capped_vireo": {
        "common_name": "Black-capped Vireo",
        "scientific_name": "Vireo atricapilla",
        "esa_status": "Delisted (Recovery)",
        "habitat_description": (
            "Low-growing scrubby vegetation on limestone slopes. "
            "Delisted in 2018 due to recovery, but monitoring "
            "obligations may persist for parcels with prior HCPs."),
        "range_ecoregions": ["edwards_plateau", "cross_timbers"],
        "compliance_cost_model": {
            "fixed_cost_range": (5000, 15000),
            "per_acre_cost_range": (10, 30),
            "typical_overlap_fraction": 0.08,
        },
    },
    "texas_blind_salamander": {
        "common_name": "Texas Blind Salamander",
        "scientific_name": "Eurycea rathbuni",
        "esa_status": "Endangered",
        "habitat_description": (
            "Aquifer-dependent species in the San Marcos pool of "
            "the Edwards Aquifer. Surface activities affecting "
            "groundwater recharge may require consultation."),
        "range_ecoregions": ["edwards_plateau"],
        "compliance_cost_model": {
            "fixed_cost_range": (20000, 60000),
            "per_acre_cost_range": (30, 80),
            "typical_overlap_fraction": 0.05,
        },
    },
}


def assess_regulatory_risk(
    species_inventory: List[Dict],
    acreage: float,
    ecoregion: str = "edwards_plateau",
    county: str = "",
    demo: bool = False,
) -> Dict:
    """Assess ESA regulatory risk for a parcel.

    Checks both:
    1. Species detected by cameras that have ESA status
    2. ESA species known to occur in the ecoregion (habitat-based)

    Args:
        species_inventory: From assemble_inventory().
        acreage: Parcel acreage.
        ecoregion: Level III ecoregion key.
        county: County name for range-specific checks.
        demo: If True, include golden-cheeked warbler for Edwards Plateau.

    Returns:
        RegulatoryRisk dict.
    """
    species_details = []
    esa_species_present = []

    # ── 1. Check detected species for ESA status ──
    for sp in species_inventory:
        if sp.get("esa_status"):
            risk = _assess_single_species(
                sp["species_key"], sp["esa_status"],
                acreage, ecoregion)
            if risk:
                species_details.append(risk)
                esa_species_present.append(sp["species_key"])

    # ── 2. Check ecoregion-based ESA species (not camera-detected) ──
    for sp_key, sp_data in _ESA_SPECIES_DATABASE.items():
        if sp_key in esa_species_present:
            continue  # Already handled from camera detections

        # Only include if in range for this ecoregion
        if ecoregion not in sp_data.get("range_ecoregions", []):
            continue

        # Skip delisted species unless there's specific county concern
        if "Delisted" in sp_data["esa_status"]:
            continue

        # In demo mode, include only golden-cheeked warbler for Edwards Plateau
        if demo and ecoregion == "edwards_plateau":
            if sp_key != "golden_cheeked_warbler":
                continue
        elif not demo:
            continue  # Production: would check actual habitat layers

        risk = _assess_esa_database_species(
            sp_key, sp_data, acreage, ecoregion)
        if risk:
            species_details.append(risk)
            esa_species_present.append(sp_key)

    # ── 3. Aggregate ──
    consultation_required = len(species_details) > 0
    total_low = sum(d["estimated_compliance_cost_low"]
                    for d in species_details)
    total_high = sum(d["estimated_compliance_cost_high"]
                     for d in species_details)

    return {
        "esa_species_present": esa_species_present,
        "consultation_required": consultation_required,
        "total_estimated_compliance_cost_low": round(total_low, 0),
        "total_estimated_compliance_cost_high": round(total_high, 0),
        "species_details": species_details,
    }


def _assess_single_species(
    species_key: str,
    esa_status: str,
    acreage: float,
    ecoregion: str,
) -> Optional[Dict]:
    """Assess regulatory risk for a single camera-detected ESA species."""
    ref = SPECIES_REFERENCE.get(species_key, {})
    db_entry = _ESA_SPECIES_DATABASE.get(species_key, {})

    cost_model = db_entry.get("compliance_cost_model", {
        "fixed_cost_range": (10000, 50000),
        "per_acre_cost_range": (20, 60),
        "typical_overlap_fraction": 0.10,
    })

    overlap_acres = acreage * cost_model["typical_overlap_fraction"]
    fixed_low, fixed_high = cost_model["fixed_cost_range"]
    per_acre_low, per_acre_high = cost_model["per_acre_cost_range"]

    cost_low = fixed_low + (per_acre_low * overlap_acres)
    cost_high = fixed_high + (per_acre_high * overlap_acres)

    # Federal nexus determination
    # Section 7 if federal funding/permits involved
    # Section 10 HCP for private land without federal nexus
    section_7 = False  # Conservative: assume private land
    section_10 = True  # Private land → HCP required for "take"

    return {
        "species_key": species_key,
        "common_name": ref.get("common_name",
                               db_entry.get("common_name", species_key)),
        "scientific_name": ref.get("scientific_name",
                                   db_entry.get("scientific_name", "")),
        "esa_status": esa_status,
        "estimated_habitat_overlap_acres": round(overlap_acres, 0),
        "section_7_required": section_7,
        "section_10_hcp": section_10,
        "estimated_compliance_cost_low": round(cost_low, 0),
        "estimated_compliance_cost_high": round(cost_high, 0),
        "notes": db_entry.get("habitat_description",
                              f"{esa_status} species detected on parcel."),
    }


def _assess_esa_database_species(
    species_key: str,
    sp_data: Dict,
    acreage: float,
    ecoregion: str,
) -> Optional[Dict]:
    """Assess regulatory risk for an ESA species from the database
    (not camera-detected, but habitat overlaps parcel)."""
    cost_model = sp_data["compliance_cost_model"]

    overlap_acres = acreage * cost_model["typical_overlap_fraction"]
    fixed_low, fixed_high = cost_model["fixed_cost_range"]
    per_acre_low, per_acre_high = cost_model["per_acre_cost_range"]

    cost_low = fixed_low + (per_acre_low * overlap_acres)
    cost_high = fixed_high + (per_acre_high * overlap_acres)

    return {
        "species_key": species_key,
        "common_name": sp_data["common_name"],
        "scientific_name": sp_data["scientific_name"],
        "esa_status": sp_data["esa_status"],
        "estimated_habitat_overlap_acres": round(overlap_acres, 0),
        "section_7_required": False,
        "section_10_hcp": True,
        "estimated_compliance_cost_low": round(cost_low, 0),
        "estimated_compliance_cost_high": round(cost_high, 0),
        "notes": sp_data["habitat_description"],
    }

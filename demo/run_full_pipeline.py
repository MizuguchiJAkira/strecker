"""Run the full Basal Informatics pipeline on demo data.

End-to-end execution: ingest -> classify -> sort -> hunter report ->
habitat analysis -> bias correction -> risk synthesis -> enterprise PDF.

This is the single script that proves everything works.
"""

import os
import sys
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))


def run_full_pipeline():
    """Execute the complete pipeline on Edwards Plateau demo data.

    Returns:
        dict with paths and key metrics.
    """
    start = time.time()
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)

    print("=" * 60)
    print("Basal Informatics — Full Pipeline Demo")
    print("=" * 60)

    # ── 1. Generate demo data ──
    print("\n1. Generating demo data...")
    from demo.generate_demo_data import generate
    generate()

    # ── 2. Strecker: ingest + classify ──
    print("\n2. Running Strecker pipeline...")
    from strecker.ingest import ingest
    from strecker.classify import classify
    photos = ingest(demo=True)
    detections = classify(photos, demo=True)
    print(f"   {len(detections):,} detections across "
          f"{len(set(d.species_key for d in detections))} species")

    # ── 3. Strecker: sort ──
    print("\n3. Sorting photos by species...")
    from strecker.sort import sort_detections
    manifest = sort_detections(detections, str(output_dir / "sorted"),
                               demo=True)
    print(f"   Manifest: {manifest}")

    # ── 4. Hunter-facing PDF ──
    print("\n4. Generating hunter Game Inventory Report...")
    from strecker.report import generate_report as generate_hunter_report
    hunter_pdf = generate_hunter_report(
        detections,
        output_path=str(output_dir / "game_inventory_report.pdf"),
        property_name="Edwards Plateau Ranch",
        demo=True,
    )
    hunter_size = os.path.getsize(hunter_pdf) / 1024
    print(f"   Hunter PDF: {hunter_pdf} ({hunter_size:.0f} KB)")

    # ── 5. Habitat analysis ──
    print("\n5. Running habitat analysis...")
    from habitat.fingerprint import fingerprint_cameras
    from habitat.units import delineate_units
    from habitat.corridors import generate_corridors
    from habitat.confidence import compute_confidence
    from habitat.gaps import analyze_gaps

    fingerprint_cameras(demo=True)
    units = delineate_units(demo=True)
    corridors = generate_corridors(demo=True)
    confidence = compute_confidence(detections=detections, demo=True)
    gaps = analyze_gaps(demo=True)
    print(f"   {len(units)} habitat units, {len(corridors)} corridors, "
          f"{len(gaps)} monitoring gaps")

    # ── 6. Bias correction ──
    print("\n6. Running IPW bias correction...")
    from bias.ipw import run_bias_correction
    bias_result = run_bias_correction(demo=True)
    auc = bias_result.get("propensity_auc", 0)
    print(f"   Propensity AUC: {auc:.4f}")
    hog_sp = bias_result.get("per_species", {}).get("feral_hog", {})
    if hog_sp:
        raw = hog_sp.get("raw_detection_frequency_pct", 0)
        adj = hog_sp.get("adjusted_detection_frequency_pct", 0)
        print(f"   Feral hog: {raw:.1f}% raw -> {adj:.1f}% adjusted "
              f"({adj - raw:+.1f}%)")

    # ── 7. Risk synthesis ──
    print("\n7. Running risk synthesis...")
    from risk.synthesis import run_risk_assessment
    assessment = run_risk_assessment(
        parcel_id="TX-KIM-2024-04817",
        acreage=2340,
        county="Kimble",
        state="TX",
        demo=True,
    )
    print(f"   Overall risk: {assessment['overall_risk_rating']}")

    # ── 8. Enterprise PDF ──
    print("\n8. Generating enterprise Nature Exposure Report...")
    from report.generator import generate_report as generate_enterprise_report
    enterprise_pdf = generate_enterprise_report(
        assessment=assessment,
        output_path=str(output_dir / "nature_exposure_TX-KIM-2024-04817.pdf"),
        detections=detections,
    )
    enterprise_size = os.path.getsize(enterprise_pdf) / 1024
    print(f"   Enterprise PDF: {enterprise_pdf} ({enterprise_size:.0f} KB)")

    elapsed = time.time() - start

    # ── Summary ──
    fh_score = assessment.get("feral_hog_exposure_score", {})
    hog_proj = assessment.get("damage_projections", {}).get("feral_hog", {})
    reg = assessment.get("regulatory_risk", {})

    print("\n" + "=" * 60)
    print("Pipeline Complete")
    print("=" * 60)
    print(f"\n  Hunter report:     {hunter_pdf}")
    print(f"  Enterprise report: {enterprise_pdf}")
    print(f"\n  Feral Hog Exposure Score: "
          f"{fh_score.get('score', 'N/A')}/100")
    print(f"  Estimated Annual Loss:    "
          f"${hog_proj.get('estimated_annual_loss', 0):,.0f}")
    print(f"  10-Year NPV:              "
          f"${hog_proj.get('ten_year_npv', 0):,.0f}")
    print(f"  Overall Risk Rating:      "
          f"{assessment['overall_risk_rating']}")
    print(f"  ESA Species Flagged:      "
          f"{', '.join(reg.get('esa_species_present', []))}")
    print(f"  Compliance Cost Range:    "
          f"${reg.get('total_estimated_compliance_cost_low', 0):,.0f}"
          f"–${reg.get('total_estimated_compliance_cost_high', 0):,.0f}")
    print(f"\n  Total time: {elapsed:.1f}s")

    return {
        "hunter_pdf": hunter_pdf,
        "enterprise_pdf": enterprise_pdf,
        "assessment": assessment,
        "elapsed": elapsed,
    }


if __name__ == "__main__":
    run_full_pipeline()

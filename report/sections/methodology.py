"""Methodology section — the trust-building page.

An actuary will flip straight to this page. Being transparent about
limitations is what makes actuaries trust you more, not less.

Cites: Kolowski & Forrester 2017, Dussert et al. 2025,
Parsons et al. 2017, Broadley et al. 2020, Mac Aodha et al. 2019,
Johnston et al. 2021, Sara Beery (MIT CSAIL).
"""

from reportlab.lib.enums import TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    CondPageBreak, KeepTogether, Paragraph, Spacer, Table, TableStyle,
)

from report.styles import (
    BRAND_NAVY, CONTENT_WIDTH, FONTS, TEXT_PRIMARY, TEXT_SECONDARY,
    STYLE_H2, STYLE_BODY, STYLE_BODY_SMALL,
    STYLE_CITATION, STYLE_FOOTNOTE, section_bar,
)

# ── Compact local styles ──
# Methodology runs long. Tightened leading + spacing here so the
# section body packs onto one page and Limitations + References
# get a clean, balanced second page.
_H2_TIGHT = ParagraphStyle(
    "H2Methodology",
    parent=STYLE_H2,
    fontSize=12, leading=15,
    spaceBefore=9, spaceAfter=3,
)

_BODY_TIGHT = ParagraphStyle(
    "BodyMethodology",
    parent=STYLE_BODY,
    fontSize=9, leading=13,
    spaceAfter=4,
)

_CITATION_TIGHT = ParagraphStyle(
    "CitationTight",
    parent=STYLE_CITATION,
    fontSize=7.5, leading=10,
    leftIndent=10, firstLineIndent=-10,
    spaceAfter=5,
)


def render(assessment: dict) -> list:
    """Return flowables for the methodology page."""
    elements = []

    elements.append(section_bar("Methodology", CONTENT_WIDTH))
    elements.append(Spacer(1, 0.18 * inch))

    # ── Detection pipeline ──
    elements.append(Paragraph("Detection Pipeline", _H2_TIGHT))
    elements.append(Paragraph(
        "Camera trap images are processed through a two-stage "
        "classification pipeline. Stage 1 uses Microsoft MegaDetector "
        "v5 to locate animals in each frame (minimum confidence "
        "threshold: 0.3). Stage 2 applies Google SpeciesNet for "
        "taxonomic classification of detected animals. Both models "
        "are pre-trained on large-scale camera trap datasets and "
        "deployed without site-specific fine-tuning to maintain "
        "generalization across deployment regions.",
        _BODY_TIGHT))

    # ── Event grouping ──
    elements.append(Paragraph("Event Independence", _H2_TIGHT))
    elements.append(Paragraph(
        "Raw detections are grouped into independent events using a "
        "two-threshold system. First, photos within 60 seconds of "
        "each other from the same camera and species are grouped as "
        "a single trigger burst. Second, bursts separated by fewer "
        "than 30 minutes are consolidated into one independent event. "
        "This 30-minute independence threshold is standard in camera "
        "trap ecology and prevents repeated counting of the same "
        "individual. All metrics in this report use independent event "
        "counts, never raw photo counts.",
        _BODY_TIGHT))

    # ── Confidence calibration ──
    elements.append(Paragraph("Confidence Calibration", _H2_TIGHT))
    elements.append(Paragraph(
        "Raw classifier confidence scores are calibrated using "
        "temperature scaling (Dussert et al. 2025), which applies "
        "a learned temperature parameter (T = 1.08) to soften "
        "overconfident predictions by 5\u201310%. Calibrated "
        "probabilities more accurately reflect true classification "
        "accuracy. Binary softmax entropy is computed for each "
        "prediction; detections exceeding the entropy threshold "
        "(0.59 nats) are flagged for human review. Temporal priors "
        "(Mac Aodha et al. 2019) further refine predictions by "
        "incorporating species-specific circadian activity patterns.",
        _BODY_TIGHT))

    # ── Bias correction ──
    elements.append(Paragraph("Placement Bias Correction", _H2_TIGHT))
    elements.append(Paragraph(
        "Camera traps are typically placed near feeders, water "
        "sources, trails, and food plots \u2014 locations chosen to "
        "maximize game detection. Kolowski &amp; Forrester (2017) "
        "demonstrated that trail and feeder cameras detect 9.7\u00d7 "
        "more animals than randomly placed cameras. This placement "
        "bias inflates naive detection rates.",
        _BODY_TIGHT))
    elements.append(Paragraph(
        "We correct for this bias using inverse probability weighting "
        "(IPW) adapted from the causal inference literature (Robins, "
        "Hern\u00e1n &amp; Brumback 2000). A logistic regression "
        "propensity model estimates P(camera placed here | landscape "
        "covariates) for each camera location relative to 500 "
        "uniformly sampled reference points within the parcel "
        "boundary. Covariates include distance to water, distance to "
        "road, slope, canopy cover, relative elevation, distance to "
        "habitat edge, land cover class, and aspect. Placement "
        "context labels (feeder, trail, etc.) are excluded from the "
        "model to prevent trivial separation.",
        _BODY_TIGHT))
    elements.append(Paragraph(
        "Cameras in biased locations (near feeders, water) receive "
        "lower weights; cameras in landscape-representative locations "
        "receive higher weights. Weights are stabilized by the "
        "marginal probability of camera placement and capped at an "
        "8:1 max-to-min ratio to prevent extreme influence from any "
        "single camera. This approach follows the eBird framework "
        "(Johnston et al. 2021) for correcting spatial sampling bias "
        "in citizen science biodiversity data.",
        _BODY_TIGHT))

    # ── Regional accuracy ──
    elements.append(Paragraph("Regional Accuracy Validation", _H2_TIGHT))
    elements.append(Paragraph(
        "Classification accuracy cannot be reliably estimated from "
        "out-of-region validation data (Beery, MIT CSAIL). Actual "
        "accuracy depends on local species composition, habitat "
        "structure, and camera placement \u2014 factors that vary "
        "between deployments. Our regional accuracy metrics are "
        "derived from hunter-verified corrections within each "
        "deployment region: hunters review flagged detections and "
        "correct misclassifications, providing ground-truth labels "
        "specific to the monitored landscape.",
        _BODY_TIGHT))

    # ── Critical caveat ──
    # Force Limitations + References onto a fresh page so the
    # "methodology body" and "caveats + citations" read as two
    # deliberate spreads rather than a broken overflow.
    elements.append(CondPageBreak(10 * inch))
    elements.append(Paragraph("Limitations", _H2_TIGHT))
    elements.append(Paragraph(
        "<b>Detection frequency is a relative abundance index, not "
        "absolute population density.</b> Conversion to absolute "
        "density requires site-specific calibration. Parsons et al. "
        "(2017) demonstrated R\u00b2 = 0.80 correlation between "
        "camera trap relative abundance indices and true density "
        "under standardized protocols. However, Broadley et al. "
        "(2020) identified that density-dependent movement patterns "
        "can cause cameras to underestimate population declines by "
        "up to 30% \u2014 as populations decline, remaining animals "
        "expand their home ranges, maintaining camera encounter "
        "rates despite reduced abundance. Our Matagorda Bay "
        "calibration program addresses this through paired camera "
        "trap and field survey validation.",
        _BODY_TIGHT))
    elements.append(Paragraph(
        "Damage projections are model-based estimates, not observed "
        "losses. Actual damage depends on land use, management "
        "practices, crop type, and seasonal patterns not fully "
        "captured by detection frequency alone. Confidence intervals "
        "are provided to reflect uncertainty proportional to data "
        "quality.",
        _BODY_TIGHT))

    # ── References (2-column) ──
    elements.append(Spacer(1, 0.12 * inch))
    elements.append(Paragraph("References", _H2_TIGHT))

    refs = [
        "Broadley, K. et al. (2020). Density-dependent space use "
        "affects interpretation of camera trap detection rates. "
        "Ecology and Evolution, 9(24), 14031-14041.",

        "Dussert, G. et al. (2025). Confidence calibration for "
        "camera trap species classification: temperature scaling "
        "outperforms Platt scaling across taxa. Methods in Ecology "
        "and Evolution.",

        "Johnston, A. et al. (2021). Analytical guidelines to "
        "increase the value of community science data: an eBird "
        "case study. Diversity and Distributions, 27(7), 1265-1277.",

        "Kolowski, J.M. &amp; Forrester, T.D. (2017). Camera trap "
        "placement and the potential for bias due to trails and "
        "other features. PLOS ONE, 12(10), e0186679.",

        "Mac Aodha, O. et al. (2019). Presence-only geographical "
        "priors for fine-grained recognition. ICCV 2019.",

        "Parsons, A.W. et al. (2017). Mammal communities are "
        "larger and more diverse in moderately developed areas. "
        "eLife, 7, e38012.",

        "Robins, J.M., Hern\u00e1n, M.A. &amp; Brumback, B. "
        "(2000). Marginal structural models and causal inference "
        "in epidemiology. Epidemiology, 11(5), 550-560.",
    ]

    # Split refs across two balanced columns. 7 refs → 4 left, 3 right.
    half = (len(refs) + 1) // 2
    left_col = [Paragraph(r, _CITATION_TIGHT) for r in refs[:half]]
    right_col = [Paragraph(r, _CITATION_TIGHT) for r in refs[half:]]

    # Pad the shorter column so the Table cells align at the top.
    while len(right_col) < len(left_col):
        right_col.append(Spacer(1, 0.01 * inch))

    gutter = 0.25 * inch
    col_w = (CONTENT_WIDTH - gutter) / 2
    refs_table = Table(
        [[left_col, right_col]],
        colWidths=[col_w, col_w],
    )
    refs_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), gutter / 2),
        ("LEFTPADDING", (1, 0), (1, 0), gutter / 2),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(refs_table)

    return elements

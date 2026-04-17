"""Hunter-facing Game Inventory Report — PDF generation.

Generates a concise (8-12 page) summary of species detected, activity
patterns, and photo counts for the hunter's camera station. This is the
free deliverable that drives Strecker adoption.

Uses ReportLab for PDF layout and Matplotlib for embedded charts.
Lunar illumination computed via ephem for moon/activity correlation.

Target: 8-12 pages total.
  Page 1:   Cover — property name, date range, summary stats
  Page 2:   Species breakdown table + all-species activity chart
  Pages 3-5: Top-3 species full-page detail (chart + deep stats)
  Pages 6-7: Remaining species compact (2-3 per page, mini charts)
  Page 8:   Moon/weather correlation

The full event-level appendix is exported as a separate CSV file
(events_appendix.csv) alongside the PDF — not embedded in it.
"""

import csv
import io
import json
import math
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import ephem  # lunar-illumination chart; optional dep
except ImportError:
    ephem = None
import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

from strecker.ingest import Detection
from config import settings
from config.species_reference import SPECIES_REFERENCE


# ═══════════════════════════════════════════════════════════════════════════
# Color palette from settings
# ═══════════════════════════════════════════════════════════════════════════

_C = settings.PDF_COLORS
BRAND_TEAL = colors.HexColor(_C["brand_teal"])
TEXT_PRIMARY = colors.HexColor(_C["text_primary"])
TEXT_SECONDARY = colors.HexColor(_C["text_secondary"])
RISK_HIGH = colors.HexColor(_C["risk_high"])
RISK_MODERATE = colors.HexColor(_C["risk_moderate"])
RISK_LOW = colors.HexColor(_C["risk_low"])
TABLE_HEADER_BG = colors.HexColor(_C["table_header_bg"])
TABLE_HEADER_TEXT = colors.HexColor(_C["table_header_text"])
TABLE_ALT_ROW = colors.HexColor(_C["table_alt_row"])

_F = settings.PDF_FONTS
FONT_HEADING = _F["heading"]
FONT_BODY = _F["body"]
FONT_MONO = _F["mono"]

# Chart colors — one per species, distinct
CHART_PALETTE = [
    "#0D7377", "#D4880F", "#C43B31", "#2A7D3F", "#5A6B7F",
    "#8B5CF6", "#EC4899", "#F97316", "#06B6D4", "#84CC16",
    "#EF4444", "#3B82F6", "#A855F7", "#14B8A6", "#F59E0B",
]


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _aggregate_species_stats(detections: List[Detection]) -> Dict:
    """Build per-species stats from classified detections."""
    stats = {}
    for det in detections:
        sp = det.species_key
        if sp not in stats:
            stats[sp] = {
                "photos": 0,
                "events": set(),
                "cameras": set(),
                "conf_sum": 0.0,
                "cal_sum": 0.0,
                "review_count": 0,
                "hourly_events": defaultdict(set),
                "hourly_photos": defaultdict(int),
                "buck_count": 0,
                "doe_count": 0,
                "timestamps": [],
            }
        s = stats[sp]
        s["photos"] += 1
        s["events"].add(det.independent_event_id)
        s["cameras"].add(det.camera_id)
        s["conf_sum"] += det.confidence
        s["cal_sum"] += det.confidence_calibrated or det.confidence
        if det.review_required:
            s["review_count"] += 1
        hour = det.timestamp.hour
        s["hourly_events"][hour].add(det.independent_event_id)
        s["hourly_photos"][hour] += 1
        s["timestamps"].append(det.timestamp)
        if det.antler_classification == "buck":
            s["buck_count"] += 1
        elif det.antler_classification == "doe":
            s["doe_count"] += 1

    for sp, s in stats.items():
        s["n_events"] = len(s["events"])
        s["n_cameras"] = len(s["cameras"])
        s["mean_conf"] = s["conf_sum"] / s["photos"]
        s["mean_cal"] = s["cal_sum"] / s["photos"]
        s["hourly_event_counts"] = {
            h: len(evts) for h, evts in s["hourly_events"].items()
        }

    return stats


def _get_date_range(detections: List[Detection]) -> Tuple[datetime, datetime]:
    ts = [d.timestamp for d in detections]
    return min(ts), max(ts)


def _classify_activity_pattern(hourly: Dict) -> Tuple[str, int, int]:
    """Return (pattern_label, night_pct, day_pct) from hourly event counts."""
    night_events = sum(hourly.get(h, 0)
                       for h in list(range(0, 5)) + list(range(21, 24)))
    day_events = sum(hourly.get(h, 0) for h in range(7, 18))
    total = sum(hourly.values())
    if total == 0:
        return "Unknown", 0, 0
    night_pct = int(night_events / total * 100)
    day_pct = int(day_events / total * 100)
    if night_pct > 65:
        return "Nocturnal", night_pct, day_pct
    elif day_pct > 65:
        return "Diurnal", night_pct, day_pct
    return "Crepuscular", night_pct, day_pct


# ═══════════════════════════════════════════════════════════════════════════
# Chart generation
# ═══════════════════════════════════════════════════════════════════════════

def _chart_to_image(fig, dpi=150) -> str:
    """Save matplotlib figure to a temp PNG and return the path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    fig.savefig(tmp.name, dpi=dpi, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    return tmp.name


def _add_diel_shading(ax):
    """Add dawn/dusk/night shading to a 24-hour axis."""
    ax.axvspan(5, 7, alpha=0.08, color="orange")
    ax.axvspan(17, 20, alpha=0.08, color="orange")
    ax.axvspan(-0.5, 5, alpha=0.05, color="navy")
    ax.axvspan(20, 23.5, alpha=0.05, color="navy")


def _make_activity_chart(detections: List[Detection],
                         title: str = "All-Species Activity Pattern"
                         ) -> str:
    """24-hour activity pattern bar chart (all species combined)."""
    hourly = defaultdict(set)
    for det in detections:
        hourly[det.timestamp.hour].add(det.independent_event_id)

    hours = list(range(24))
    counts = [len(hourly.get(h, set())) for h in hours]

    fig, ax = plt.subplots(figsize=(7, 2.8))
    bar_colors = ["#0D7377" if 5 <= h <= 20 else "#1a3a5c" for h in hours]
    ax.bar(hours, counts, color=bar_colors, edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Hour of Day", fontsize=9)
    ax.set_ylabel("Independent Events", fontsize=9)
    ax.set_title(title, fontsize=11, fontweight="bold", color="#1A1A1A")
    ax.set_xticks(hours)
    ax.set_xticklabels([f"{h:02d}" for h in hours], fontsize=7)
    ax.set_xlim(-0.5, 23.5)
    _add_diel_shading(ax)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    return _chart_to_image(fig)


def _make_species_activity_chart(species_key: str,
                                 stats: Dict,
                                 color: str = "#0D7377",
                                 compact: bool = False) -> str:
    """Per-species 24-hour activity chart.

    compact=True produces a smaller chart for the 2-3 per page layout.
    """
    ref = SPECIES_REFERENCE.get(species_key, {})
    common = ref.get("common_name", species_key.replace("_", " ").title())

    hourly = stats["hourly_event_counts"]
    hours = list(range(24))
    counts = [hourly.get(h, 0) for h in hours]

    if compact:
        fig, ax = plt.subplots(figsize=(4.2, 1.5))
        title_size, label_size, tick_size = 8, 6, 5
    else:
        fig, ax = plt.subplots(figsize=(6, 2.8))
        title_size, label_size, tick_size = 10, 8, 6

    ax.bar(hours, counts, color=color, edgecolor="white", linewidth=0.5)
    ax.set_xlabel("Hour", fontsize=label_size)
    ax.set_ylabel("Events", fontsize=label_size)
    ax.set_title(f"{common} — Hourly Activity", fontsize=title_size,
                 fontweight="bold")
    ax.set_xticks([0, 4, 8, 12, 16, 20] if compact else hours)
    if not compact:
        ax.set_xticklabels([f"{h:02d}" for h in hours], fontsize=tick_size)
    else:
        ax.tick_params(axis="both", labelsize=tick_size)
    ax.set_xlim(-0.5, 23.5)
    _add_diel_shading(ax)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    return _chart_to_image(fig)


def _make_moon_chart(detections: List[Detection],
                     camera_lat: float = 30.49,
                     camera_lon: float = -99.75) -> str:
    """Moon illumination vs nocturnal activity correlation chart."""
    nocturnal_species = {
        "feral_hog", "raccoon", "armadillo", "opossum",
        "gray_fox", "red_fox", "bobcat",
    }

    nightly_events = defaultdict(set)
    for det in detections:
        if det.species_key not in nocturnal_species:
            continue
        h = det.timestamp.hour
        if h >= 20 or h < 5:
            night_date = det.timestamp.date()
            if h < 5:
                night_date = (det.timestamp - timedelta(days=1)).date()
            nightly_events[night_date].add(det.independent_event_id)

    if not nightly_events or ephem is None:
        fig, ax = plt.subplots(figsize=(6, 3))
        msg = ("Insufficient nocturnal data" if not nightly_events
               else "Moon chart skipped (ephem not installed)")
        ax.text(0.5, 0.5, msg,
                ha="center", va="center", transform=ax.transAxes)
        fig.tight_layout()
        return _chart_to_image(fig)

    observer = ephem.Observer()
    observer.lat = str(camera_lat)
    observer.lon = str(camera_lon)
    observer.elevation = 550

    moon_data = []
    for night_date in sorted(nightly_events.keys()):
        observer.date = ephem.Date(
            datetime(night_date.year, night_date.month, night_date.day, 0, 0))
        moon = ephem.Moon(observer)
        illum = moon.phase / 100.0
        n_events = len(nightly_events[night_date])
        moon_data.append((illum, n_events))

    illums = [m[0] for m in moon_data]
    events = [m[1] for m in moon_data]

    bins = [0, 0.25, 0.50, 0.75, 1.01]
    bin_labels = ["New\n(0-25%)", "Crescent\n(25-50%)",
                  "Gibbous\n(50-75%)", "Full\n(75-100%)"]
    bin_means, bin_stds = [], []
    for i in range(len(bins) - 1):
        vals = [e for il, e in zip(illums, events)
                if bins[i] <= il < bins[i + 1]]
        bin_means.append(np.mean(vals) if vals else 0)
        bin_stds.append(np.std(vals) if vals else 0)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7, 2.8),
                                    gridspec_kw={"width_ratios": [1.3, 1]})

    ax1.scatter(illums, events, alpha=0.4, s=15, c="#0D7377", edgecolors="none")
    if len(illums) > 2:
        z = np.polyfit(illums, events, 1)
        p = np.poly1d(z)
        x_line = np.linspace(0, 1, 50)
        ax1.plot(x_line, p(x_line), "--", color="#C43B31", linewidth=1.5,
                 alpha=0.7, label=f"Trend (slope={z[0]:.1f})")
        ax1.legend(fontsize=7)
    ax1.set_xlabel("Moon Illumination", fontsize=8)
    ax1.set_ylabel("Nocturnal Events", fontsize=8)
    ax1.set_title("Moon Phase vs Activity", fontsize=10, fontweight="bold")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)

    x_pos = range(len(bin_labels))
    ax2.bar(x_pos, bin_means, yerr=bin_stds, capsize=3,
            color=["#1a3a5c", "#3a6a8c", "#6a9abc", "#aaccdd"],
            edgecolor="white")
    ax2.set_xticks(x_pos)
    ax2.set_xticklabels(bin_labels, fontsize=7)
    ax2.set_ylabel("Mean Events/Night", fontsize=8)
    ax2.set_title("By Moon Phase", fontsize=10, fontweight="bold")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))

    fig.tight_layout()
    return _chart_to_image(fig)


# ═══════════════════════════════════════════════════════════════════════════
# Appendix CSV export (replaces the in-PDF appendix)
# ═══════════════════════════════════════════════════════════════════════════

def export_events_appendix(detections: List[Detection],
                           output_path: str) -> str:
    """Export one-row-per-independent-event CSV alongside the PDF.

    This replaces the 130+ page in-PDF appendix. Hunters who want the
    raw data can open this in Excel; the PDF stays concise.
    """
    events_seen = set()
    rows = []
    for det in sorted(detections, key=lambda d: d.timestamp):
        eid = det.independent_event_id
        if eid in events_seen:
            continue
        events_seen.add(eid)
        ref = SPECIES_REFERENCE.get(det.species_key, {})
        rows.append({
            "event_id": eid,
            "timestamp": det.timestamp.isoformat(),
            "camera_id": det.camera_id,
            "species_key": det.species_key,
            "common_name": ref.get("common_name", ""),
            "confidence": round(det.confidence, 4),
            "confidence_calibrated": round(
                det.confidence_calibrated, 4) if det.confidence_calibrated else "",
            "temporal_prior": round(
                det.temporal_prior, 4) if det.temporal_prior else "",
            "softmax_entropy": round(
                det.softmax_entropy, 4) if det.softmax_entropy else "",
            "review_required": det.review_required,
            "burst_group_id": det.burst_group_id or "",
            "antler_classification": det.antler_classification or "",
        })

    # Empty-input guard: a probe upload with no valid detections lands
    # here with rows=[]. Write a header-only CSV so callers don't crash
    # on IndexError and downstream aggregation still runs.
    fieldnames = list(rows[0].keys()) if rows else [
        "species_key", "timestamp", "camera_id", "independent_event_id",
        "burst_group_id", "antler_classification",
    ]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


# ═══════════════════════════════════════════════════════════════════════════
# PDF builder
# ═══════════════════════════════════════════════════════════════════════════

def _header_footer(canvas, doc):
    """Draw header/footer on each page."""
    canvas.saveState()

    # Header bar
    canvas.setFillColor(BRAND_TEAL)
    canvas.rect(0, letter[1] - 45, letter[0], 45, fill=True, stroke=False)
    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_HEADING, 14)
    canvas.drawString(36, letter[1] - 30, "Strecker \u2014 Game Inventory Report")
    canvas.setFont(FONT_BODY, 8)
    canvas.drawRightString(letter[0] - 36, letter[1] - 30,
                           "Powered by Basal Informatics")

    # Footer
    canvas.setFillColor(TEXT_SECONDARY)
    canvas.setFont(FONT_BODY, 7)
    canvas.drawString(36, 20,
                      "Basal Informatics \u00b7 Ground-truth ecological verification")
    canvas.drawRightString(letter[0] - 36, 20, f"Page {doc.page}")

    canvas.restoreState()


def generate_report(detections: List[Detection],
                    output_path: str = "demo/output/game_inventory_report.pdf",
                    property_name: str = "Edwards Plateau Ranch",
                    demo: bool = False) -> str:
    """Generate the hunter-facing Game Inventory Report PDF.

    Target: 8-12 pages. Top-3 species get full pages; remaining species
    are packed 2-3 per page in compact layout. The event-level appendix
    is exported as events_appendix.csv alongside the PDF.

    Returns:
        Path to the generated PDF.
    """
    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Export appendix CSV ──
    appendix_csv_path = str(out_dir / "events_appendix.csv")
    export_events_appendix(detections, appendix_csv_path)

    # ── Load camera metadata ──
    camera_lat, camera_lon = 30.49, -99.75
    if demo:
        cam_path = (Path(__file__).parent.parent
                    / "demo" / "demo_data" / "cameras.json")
        if cam_path.exists():
            with open(cam_path) as f:
                cams = json.load(f)
            if cams:
                camera_lat = cams[0]["lat"]
                camera_lon = cams[0]["lon"]

    # ── Aggregate stats ──
    stats = _aggregate_species_stats(detections)
    date_min, date_max = _get_date_range(detections)
    n_cameras = len(set(d.camera_id for d in detections))
    n_species = len(stats)
    n_events = len(set(d.independent_event_id for d in detections))
    n_review = sum(1 for d in detections if d.review_required)

    # Sort by event count descending
    sorted_species = sorted(stats.keys(),
                            key=lambda s: stats[s]["n_events"],
                            reverse=True)
    top_3 = sorted_species[:3]
    rest = sorted_species[3:]

    # ── Styles ──
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "ReportTitle", parent=styles["Title"],
        fontName=FONT_HEADING, fontSize=22, textColor=TEXT_PRIMARY,
        spaceAfter=4, alignment=TA_LEFT)
    style_subtitle = ParagraphStyle(
        "Subtitle", parent=styles["Normal"],
        fontName=FONT_BODY, fontSize=11, textColor=TEXT_SECONDARY,
        spaceAfter=2)
    style_h2 = ParagraphStyle(
        "H2", parent=styles["Heading2"],
        fontName=FONT_HEADING, fontSize=14, textColor=BRAND_TEAL,
        spaceBefore=14, spaceAfter=6)
    style_h3 = ParagraphStyle(
        "H3", parent=styles["Heading3"],
        fontName=FONT_HEADING, fontSize=11, textColor=TEXT_PRIMARY,
        spaceBefore=8, spaceAfter=3)
    style_body = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontName=FONT_BODY, fontSize=9, textColor=TEXT_PRIMARY,
        leading=13)
    style_small = ParagraphStyle(
        "Small", parent=styles["Normal"],
        fontName=FONT_BODY, fontSize=7.5, textColor=TEXT_SECONDARY,
        leading=10)
    style_compact_body = ParagraphStyle(
        "CompactBody", parent=styles["Normal"],
        fontName=FONT_BODY, fontSize=8, textColor=TEXT_PRIMARY,
        leading=11)

    # ── Build document ──
    doc = BaseDocTemplate(
        output_path, pagesize=letter,
        leftMargin=36, rightMargin=36,
        topMargin=60, bottomMargin=40)

    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="main")
    doc.addPageTemplates([
        PageTemplate(id="main", frames=[frame], onPage=_header_footer),
    ])

    story = []
    _chart_files = []

    # ══════════════════════════════════════════════════════════════════════
    # PAGE 1: Cover / Summary
    # ══════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 20))
    story.append(Paragraph(property_name, style_title))
    story.append(Paragraph(
        f"Game Inventory Report \u2014 "
        f"{date_min.strftime('%B %d, %Y')} to "
        f"{date_max.strftime('%B %d, %Y')}",
        style_subtitle))
    story.append(Spacer(1, 16))

    # Summary stats grid
    summary_data = [
        ["Total Photos", "Independent Events", "Camera Stations",
         "Species Detected", "Flagged for Review"],
        [f"{len(detections):,}", f"{n_events:,}", str(n_cameras),
         str(n_species), f"{n_review:,} ({n_review/len(detections)*100:.1f}%)"],
    ]
    summary_table = Table(summary_data, colWidths=[1.4 * inch] * 5)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_TEAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), FONT_HEADING),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, 1), FONT_HEADING),
        ("FONTSIZE", (0, 1), (-1, 1), 14),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0E0E0")),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Note: All counts use <b>independent events</b> (30-min threshold "
        "per Cunningham et al. 2021), not raw photo triggers. A single "
        "animal visiting a feeder repeatedly counts as one event, preventing "
        "4-10\u00d7 inflation of activity estimates.",
        style_small))
    story.append(Spacer(1, 10))

    # ── Species Breakdown Table ──
    story.append(Paragraph("Species Breakdown", style_h2))

    table_header = ["Species", "Events", "Photos", "Cameras",
                    "Avg Conf.", "Status", "Notes"]
    table_data = [table_header]

    for sp in sorted_species:
        s = stats[sp]
        ref = SPECIES_REFERENCE.get(sp, {})
        common = ref.get("common_name", sp.replace("_", " ").title())

        if ref.get("invasive"):
            status = "INVASIVE"
        elif ref.get("esa_status"):
            status = f"ESA: {ref['esa_status']}"
        else:
            status = "Native"

        notes = []
        if sp == "white_tailed_deer" and (s["buck_count"] or s["doe_count"]):
            total_sexed = s["buck_count"] + s["doe_count"]
            buck_pct = s["buck_count"] / total_sexed * 100 if total_sexed else 0
            notes.append(f"Buck:Doe {s['buck_count']}:{s['doe_count']} "
                         f"({buck_pct:.0f}%)")
        if s["review_count"]:
            notes.append(f"{s['review_count']} flagged")

        table_data.append([
            common,
            str(s["n_events"]),
            f"{s['photos']:,}",
            str(s["n_cameras"]),
            f"{s['mean_cal']:.1%}",
            status,
            "; ".join(notes) if notes else "",
        ])

    col_widths = [1.6*inch, 0.65*inch, 0.7*inch, 0.7*inch,
                  0.7*inch, 0.8*inch, 1.85*inch]
    species_table = Table(table_data, colWidths=col_widths, repeatRows=1)

    table_style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), TABLE_HEADER_TEXT),
        ("FONTNAME", (0, 0), (-1, 0), FONT_HEADING),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, -1), FONT_BODY),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("ALIGN", (1, 0), (4, -1), "CENTER"),
        ("ALIGN", (5, 0), (5, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E0E0E0")),
    ]
    for i in range(1, len(table_data)):
        if i % 2 == 0:
            table_style_cmds.append(
                ("BACKGROUND", (0, i), (-1, i), TABLE_ALT_ROW))
    for i, sp in enumerate(sorted_species, start=1):
        ref = SPECIES_REFERENCE.get(sp, {})
        if ref.get("invasive"):
            table_style_cmds.append(("TEXTCOLOR", (5, i), (5, i), RISK_HIGH))
            table_style_cmds.append(("FONTNAME", (5, i), (5, i), FONT_HEADING))

    species_table.setStyle(TableStyle(table_style_cmds))
    story.append(species_table)

    # ══════════════════════════════════════════════════════════════════════
    # PAGE 2: All-Species Activity Chart
    # ══════════════════════════════════════════════════════════════════════
    story.append(Spacer(1, 10))
    story.append(Paragraph("Activity Patterns", style_h2))
    story.append(Paragraph(
        "Detection activity by hour of day across all species. "
        "Dawn/dusk highlighted in amber; nighttime in blue.",
        style_body))
    story.append(Spacer(1, 4))

    activity_chart_path = _make_activity_chart(detections)
    _chart_files.append(activity_chart_path)
    story.append(Image(activity_chart_path, width=6.5*inch, height=2.6*inch))

    # ══════════════════════════════════════════════════════════════════════
    # PAGES 3-5: Top-3 Species Full Detail
    # ══════════════════════════════════════════════════════════════════════
    for rank, sp in enumerate(top_3):
        story.append(PageBreak())
        s = stats[sp]
        ref = SPECIES_REFERENCE.get(sp, {})
        common = ref.get("common_name", sp.replace("_", " ").title())
        sci = ref.get("scientific_name", "")
        color = CHART_PALETTE[rank % len(CHART_PALETTE)]

        story.append(Paragraph(
            f"{common} "
            f"<font size=9 color='#5A6B7F'><i>{sci}</i></font>",
            style_h2))

        status_str = "INVASIVE" if ref.get("invasive") else "Native"
        story.append(Paragraph(
            f"<b>Independent Events:</b> {s['n_events']}  |  "
            f"<b>Photos:</b> {s['photos']:,}  |  "
            f"<b>Cameras:</b> {s['n_cameras']}  |  "
            f"<b>Avg Confidence:</b> {s['mean_cal']:.1%}  |  "
            f"<b>Status:</b> {status_str}",
            style_body))

        if sp == "white_tailed_deer" and (s["buck_count"] or s["doe_count"]):
            total_sexed = s["buck_count"] + s["doe_count"]
            buck_pct = (s["buck_count"] / total_sexed * 100
                        if total_sexed else 0)
            story.append(Paragraph(
                f"<b>Antler Classification (May-Nov):</b> "
                f"{s['buck_count']} bucks / {s['doe_count']} does "
                f"({buck_pct:.0f}% buck rate)",
                style_body))

        if s["review_count"]:
            story.append(Paragraph(
                f"<b>Review Flagged:</b> {s['review_count']} photos "
                f"({s['review_count']/s['photos']*100:.1f}% "
                f"\u2014 entropy > {settings.REVIEW_ENTROPY_THRESHOLD} nats)",
                style_body))

        story.append(Spacer(1, 6))
        chart_path = _make_species_activity_chart(sp, s, color=color,
                                                   compact=False)
        _chart_files.append(chart_path)
        story.append(Image(chart_path, width=6.0*inch, height=2.6*inch))
        story.append(Spacer(1, 6))

        # Activity pattern classification
        hourly = s["hourly_event_counts"]
        if hourly:
            pattern, night_pct, day_pct = _classify_activity_pattern(hourly)
            peak_hour = max(hourly, key=hourly.get)
            peak_count = hourly[peak_hour]
            story.append(Paragraph(
                f"<b>Activity Pattern:</b> {pattern} "
                f"(peak hour {peak_hour:02d}:00 with {peak_count} events; "
                f"nocturnal {night_pct}%, diurnal {day_pct}%)",
                style_body))

        # Camera distribution
        cam_events = defaultdict(int)
        for det in detections:
            if det.species_key == sp:
                cam_events[det.camera_id] += 1
        top_cams = sorted(cam_events.items(), key=lambda x: -x[1])[:5]
        cam_str = ", ".join(f"{c} ({n})" for c, n in top_cams)
        story.append(Paragraph(
            f"<b>Top Cameras:</b> {cam_str}", style_body))

        # First/last detection
        sp_ts = sorted(s["timestamps"])
        story.append(Paragraph(
            f"<b>First Detection:</b> {sp_ts[0].strftime('%Y-%m-%d %H:%M')}  |  "
            f"<b>Last:</b> {sp_ts[-1].strftime('%Y-%m-%d %H:%M')}  |  "
            f"<b>Survey Span:</b> {(sp_ts[-1] - sp_ts[0]).days} days",
            style_body))

    # ══════════════════════════════════════════════════════════════════════
    # PAGES 6-7: Remaining Species — Compact Layout (2-3 per page)
    # ══════════════════════════════════════════════════════════════════════
    if rest:
        story.append(PageBreak())
        story.append(Paragraph("Additional Species", style_h2))

        for idx, sp in enumerate(rest):
            s = stats[sp]
            ref = SPECIES_REFERENCE.get(sp, {})
            common = ref.get("common_name", sp.replace("_", " ").title())
            sci = ref.get("scientific_name", "")
            global_idx = sorted_species.index(sp)
            color = CHART_PALETTE[global_idx % len(CHART_PALETTE)]

            # ── Species header ──
            story.append(Paragraph(
                f"<b>{common}</b> "
                f"<font size=7 color='#5A6B7F'><i>{sci}</i></font>",
                style_h3))

            # ── One-line stats ──
            status_str = "INVASIVE" if ref.get("invasive") else "Native"
            hourly = s["hourly_event_counts"]
            pattern, night_pct, day_pct = _classify_activity_pattern(hourly)
            peak_hour = max(hourly, key=hourly.get) if hourly else 0

            review_str = ""
            if s["review_count"]:
                review_str = (f"  |  <b>Review:</b> {s['review_count']} "
                              f"({s['review_count']/s['photos']*100:.0f}%)")

            story.append(Paragraph(
                f"<b>Events:</b> {s['n_events']}  |  "
                f"<b>Photos:</b> {s['photos']:,}  |  "
                f"<b>Cameras:</b> {s['n_cameras']}  |  "
                f"<b>Conf:</b> {s['mean_cal']:.0%}  |  "
                f"<b>{status_str}</b>  |  "
                f"<b>{pattern}</b> (peak {peak_hour:02d}:00)"
                f"{review_str}",
                style_compact_body))

            # ── Compact activity chart ──
            chart_path = _make_species_activity_chart(
                sp, s, color=color, compact=True)
            _chart_files.append(chart_path)
            story.append(Spacer(1, 2))
            story.append(Image(chart_path, width=4.0*inch, height=1.4*inch))
            story.append(Spacer(1, 8))

    # ══════════════════════════════════════════════════════════════════════
    # FINAL PAGE: Moon/Weather Correlation
    # ══════════════════════════════════════════════════════════════════════
    story.append(PageBreak())
    story.append(Paragraph("Lunar Illumination & Nocturnal Activity",
                           style_h2))
    story.append(Paragraph(
        "Correlation between moon phase and nocturnal species activity "
        "(feral hog, raccoon, armadillo, opossum, foxes, bobcat). "
        "Lunar illumination computed via PyEphem for each survey night "
        f"at {camera_lat:.2f}\u00b0N, {abs(camera_lon):.2f}\u00b0W.",
        style_body))
    story.append(Spacer(1, 6))

    moon_chart_path = _make_moon_chart(
        detections, camera_lat=camera_lat, camera_lon=camera_lon)
    _chart_files.append(moon_chart_path)
    story.append(Image(moon_chart_path, width=6.5*inch, height=2.6*inch))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "Negative slope indicates reduced activity during brighter moon "
        "phases (lunar avoidance), documented in feral hogs "
        "(Podg\u00f3rski et al. 2013) and raccoons. Positive slope suggests "
        "moonlight-aided foraging (common in rabbits and small mammals).",
        style_small))
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "<b>Data Files:</b> Full event-level classification data exported "
        f"to <font name='Courier'>events_appendix.csv</font> ({n_events:,} "
        "events) and <font name='Courier'>sorted/manifest.csv</font> "
        f"({len(detections):,} photos). Open in Excel for filtering, "
        "pivot tables, and custom analysis.",
        style_body))

    # ── Build PDF ──
    doc.build(story)

    # Cleanup temp chart files
    for f in _chart_files:
        try:
            os.unlink(f)
        except OSError:
            pass

    return output_path

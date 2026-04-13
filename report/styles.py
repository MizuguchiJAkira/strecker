"""PDF styling constants and ReportLab style definitions.

Design language: insurer-grade documentation in the McKinsey idiom.
Libre Baskerville serif throughout. Disciplined use of color:
  * Navy  (#0F2847) — primary accent, header bars, category chips,
                      hog/primary chart series
  * Blue  (#3A8BD1) — secondary series, lighter category chips
  * Teal  (#2B9B94) — single vivid highlight, used sparingly (exposure
                      gauge arc, captured-value markers)
  * Gray  (#C7CDD4) — baselines, budget rails, outlines
  * Black (#0A0A0A) — body text, rules
  * White (#FFFFFF) — paper

The cover is near-black ground with cream type; content pages are
white paper with navy section bars and black body type. Color is
reserved for data — the voice stays restrained.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ═══════════════════════════════════════════════════════════════════════════
# Font registration (Libre Baskerville — entire report)
# ═══════════════════════════════════════════════════════════════════════════

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_BASKERVILLE_AVAILABLE = False
try:
    pdfmetrics.registerFont(TTFont(
        "LibreBaskerville",
        str(_FONT_DIR / "LibreBaskerville-Regular.ttf")))
    pdfmetrics.registerFont(TTFont(
        "LibreBaskerville-Bold",
        str(_FONT_DIR / "LibreBaskerville-Bold.ttf")))
    pdfmetrics.registerFont(TTFont(
        "LibreBaskerville-Italic",
        str(_FONT_DIR / "LibreBaskerville-Italic.ttf")))
    # Register a family so inline <b>/<i> markup in Paragraph resolves.
    pdfmetrics.registerFontFamily(
        "LibreBaskerville",
        normal="LibreBaskerville",
        bold="LibreBaskerville-Bold",
        italic="LibreBaskerville-Italic",
        boldItalic="LibreBaskerville-Bold",
    )
    _BASKERVILLE_AVAILABLE = True
except Exception:
    # Graceful fallback — if fonts are missing, fall back to Times-Roman
    pass

SERIF_REGULAR = "LibreBaskerville" if _BASKERVILLE_AVAILABLE else "Times-Roman"
SERIF_BOLD = "LibreBaskerville-Bold" if _BASKERVILLE_AVAILABLE else "Times-Bold"
SERIF_ITALIC = ("LibreBaskerville-Italic"
                if _BASKERVILLE_AVAILABLE else "Times-Italic")

# ═══════════════════════════════════════════════════════════════════════════
# Disciplined McKinsey palette — navy primary, teal accent
# ═══════════════════════════════════════════════════════════════════════════

COLORS = {
    # Surfaces
    "page_bg": "#FFFFFF",        # white paper
    "cover_bg": "#0A0A0A",       # near-black cover ground
    # Ink
    "text_primary": "#0A0A0A",   # near-black body text
    "text_secondary": "#525252", # neutral mid-gray
    "text_muted": "#8A8A8A",     # neutral light gray
    # Brand colors — the three hues that carry data
    "brand_navy": "#0F2847",       # header bars, primary data, category chips
    "brand_navy_deep": "#0A1E3A",  # cover bar / deepest navy
    "brand_blue": "#3A8BD1",       # secondary series
    "brand_blue_light": "#BBD5EC", # light category chip fill
    "brand_teal": "#2B9B94",       # single vivid accent — exposure gauge
    # Back-compat aliases
    "brand_accent": "#2B9B94",
    # Risk scale — navy descent, teal never used for risk
    "risk_critical": "#0F2847",  # deep navy
    "risk_high": "#1F4171",
    "risk_elevated": "#3A8BD1",
    "risk_moderate": "#6BA7D8",
    "risk_low": "#B5CADF",
    # Chart series — hog is the primary subject, always navy
    "chart_primary": "#0F2847",    # navy — hog / main subject
    "chart_secondary": "#3A8BD1",  # blue — secondary species
    "chart_tertiary": "#8FB5D7",   # pale blue — tertiary
    "chart_neutral": "#C7CDD4",    # gray baseline / budget rail
    # Back-compat aliases
    "chart_unmanaged": "#0F2847",
    "chart_managed": "#3A8BD1",
    "chart_avoidable": "#C7CDD4",
    # Structure
    "gridline": "#E1E4E8",       # hairline rule
    "border_light": "#C7CDD4",
    "table_header_bg": "#FFFFFF",
    "table_header_text": "#0A0A0A",
    "table_alt_row": "#F7F8FA",
    "section_bar_bg": "#0F2847", # navy band behind H1 section headers
    "section_bar_text": "#FFFFFF",
    "background": "#FFFFFF",
}

# ReportLab color objects
PAGE_BG = colors.HexColor(COLORS["page_bg"])
COVER_BG = colors.HexColor(COLORS["cover_bg"])
BRAND_NAVY = colors.HexColor(COLORS["brand_navy"])
BRAND_NAVY_DEEP = colors.HexColor(COLORS["brand_navy_deep"])
BRAND_BLUE = colors.HexColor(COLORS["brand_blue"])
BRAND_BLUE_LIGHT = colors.HexColor(COLORS["brand_blue_light"])
BRAND_TEAL = colors.HexColor(COLORS["brand_teal"])
BRAND_ACCENT = BRAND_TEAL
TEXT_PRIMARY = colors.HexColor(COLORS["text_primary"])
TEXT_SECONDARY = colors.HexColor(COLORS["text_secondary"])
TEXT_MUTED = colors.HexColor(COLORS["text_muted"])
RISK_CRITICAL = colors.HexColor(COLORS["risk_critical"])
RISK_HIGH = colors.HexColor(COLORS["risk_high"])
RISK_ELEVATED = colors.HexColor(COLORS["risk_elevated"])
RISK_LOW = colors.HexColor(COLORS["risk_low"])
TABLE_HEADER_BG = colors.HexColor(COLORS["table_header_bg"])
TABLE_HEADER_TEXT = colors.HexColor(COLORS["table_header_text"])
TABLE_ALT_ROW = colors.HexColor(COLORS["table_alt_row"])
GRIDLINE = colors.HexColor(COLORS["gridline"])
BORDER_LIGHT = colors.HexColor(COLORS["border_light"])
SECTION_BAR_BG = colors.HexColor(COLORS["section_bar_bg"])
SECTION_BAR_TEXT = colors.HexColor(COLORS["section_bar_text"])

# Cover palette — used by cover.py + generator._cover_page
COVER_TEXT = colors.HexColor("#F5F1E8")
COVER_MUTED = colors.HexColor("#9A958A")
COVER_RULE = colors.HexColor("#2A2A2A")

FONTS = {
    # All "body" and "heading" references resolve to Libre Baskerville
    # so the serif voice runs from cover to methodology.
    "heading": SERIF_BOLD,
    "body": SERIF_REGULAR,
    "italic": SERIF_ITALIC,
    "mono": "Courier",
    "serif_regular": SERIF_REGULAR,
    "serif_bold": SERIF_BOLD,
    "serif_italic": SERIF_ITALIC,
}

# ═══════════════════════════════════════════════════════════════════════════
# Page dimensions (US Letter)
# ═══════════════════════════════════════════════════════════════════════════

PAGE_WIDTH = 8.5 * inch
PAGE_HEIGHT = 11 * inch
MARGIN_LEFT = 0.85 * inch
MARGIN_RIGHT = 0.85 * inch
MARGIN_TOP = 0.85 * inch
MARGIN_BOTTOM = 0.85 * inch
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT

# ═══════════════════════════════════════════════════════════════════════════
# Paragraph styles — serif hierarchy
# ═══════════════════════════════════════════════════════════════════════════

STYLE_TITLE = ParagraphStyle(
    "ReportTitle",
    fontName=SERIF_BOLD, fontSize=30, leading=36,
    textColor=TEXT_PRIMARY, alignment=TA_LEFT, spaceAfter=6,
)

STYLE_SUBTITLE = ParagraphStyle(
    "ReportSubtitle",
    fontName=SERIF_ITALIC, fontSize=12, leading=17,
    textColor=TEXT_SECONDARY, alignment=TA_LEFT, spaceAfter=12,
)

STYLE_H1 = ParagraphStyle(
    "H1",
    fontName=SERIF_BOLD, fontSize=22, leading=26,
    textColor=TEXT_PRIMARY, spaceBefore=0, spaceAfter=6,
)

# Used inside the navy section-bar Table — white serif on navy
STYLE_H1_BAR = ParagraphStyle(
    "H1Bar",
    fontName=SERIF_BOLD, fontSize=14, leading=18,
    textColor=SECTION_BAR_TEXT, alignment=TA_CENTER,
    spaceBefore=0, spaceAfter=0,
)

STYLE_H2 = ParagraphStyle(
    "H2",
    fontName=SERIF_BOLD, fontSize=13, leading=17,
    textColor=colors.HexColor(COLORS["brand_navy"]),
    spaceBefore=14, spaceAfter=5,
)

STYLE_H3 = ParagraphStyle(
    "H3",
    fontName=SERIF_ITALIC, fontSize=11, leading=14,
    textColor=colors.HexColor(COLORS["brand_navy"]),
    spaceBefore=8, spaceAfter=3,
)

STYLE_EYEBROW = ParagraphStyle(
    "Eyebrow",
    fontName=SERIF_ITALIC, fontSize=9, leading=12,
    textColor=TEXT_SECONDARY, alignment=TA_LEFT,
    spaceBefore=0, spaceAfter=3,
)

STYLE_BODY = ParagraphStyle(
    "Body",
    fontName=SERIF_REGULAR, fontSize=9.5, leading=14,
    textColor=TEXT_PRIMARY, alignment=TA_JUSTIFY, spaceAfter=6,
)

STYLE_BODY_SMALL = ParagraphStyle(
    "BodySmall",
    fontName=SERIF_REGULAR, fontSize=8, leading=11,
    textColor=TEXT_SECONDARY, alignment=TA_JUSTIFY, spaceAfter=4,
)

STYLE_CAPTION = ParagraphStyle(
    "Caption",
    fontName=SERIF_ITALIC, fontSize=7.5, leading=10,
    textColor=TEXT_SECONDARY, alignment=TA_LEFT, spaceAfter=4, spaceBefore=2,
)

STYLE_METRIC_LARGE = ParagraphStyle(
    "MetricLarge",
    fontName=SERIF_BOLD, fontSize=42, leading=46,
    textColor=TEXT_PRIMARY, alignment=TA_CENTER,
)

STYLE_METRIC_LABEL = ParagraphStyle(
    "MetricLabel",
    fontName=SERIF_ITALIC, fontSize=9, leading=12,
    textColor=TEXT_SECONDARY, alignment=TA_CENTER,
)

STYLE_FOOTNOTE = ParagraphStyle(
    "Footnote",
    fontName=SERIF_ITALIC, fontSize=7.5, leading=10,
    textColor=TEXT_SECONDARY, alignment=TA_LEFT, spaceBefore=8,
)

STYLE_CITATION = ParagraphStyle(
    "Citation",
    fontName=SERIF_REGULAR, fontSize=8, leading=11,
    textColor=TEXT_SECONDARY, alignment=TA_LEFT,
    leftIndent=14, firstLineIndent=-14, spaceAfter=4,
)


# ═══════════════════════════════════════════════════════════════════════════
# Cover page styles (same family, inverted palette)
# ═══════════════════════════════════════════════════════════════════════════

STYLE_COVER_WORDMARK = ParagraphStyle(
    "CoverWordmark",
    fontName=SERIF_ITALIC, fontSize=11, leading=14,
    textColor=COVER_TEXT, alignment=TA_LEFT,
)

STYLE_COVER_LABEL = ParagraphStyle(
    "CoverLabel",
    fontName=SERIF_REGULAR, fontSize=8, leading=11,
    textColor=COVER_MUTED, alignment=TA_LEFT,
)

STYLE_COVER_EYEBROW = ParagraphStyle(
    "CoverEyebrow",
    fontName=SERIF_REGULAR, fontSize=9, leading=12,
    textColor=COVER_MUTED, alignment=TA_LEFT,
    spaceAfter=6,
)

STYLE_COVER_TITLE = ParagraphStyle(
    "CoverTitle",
    fontName=SERIF_BOLD, fontSize=42, leading=48,
    textColor=COVER_TEXT, alignment=TA_LEFT,
    spaceAfter=10,
)

STYLE_COVER_SUBTITLE = ParagraphStyle(
    "CoverSubtitle",
    fontName=SERIF_ITALIC, fontSize=13, leading=18,
    textColor=COVER_TEXT, alignment=TA_LEFT,
    spaceAfter=4,
)

STYLE_COVER_META_KEY = ParagraphStyle(
    "CoverMetaKey",
    fontName=SERIF_REGULAR, fontSize=8, leading=11,
    textColor=COVER_MUTED, alignment=TA_LEFT,
)

STYLE_COVER_META_VAL = ParagraphStyle(
    "CoverMetaVal",
    fontName=SERIF_REGULAR, fontSize=9.5, leading=12,
    textColor=COVER_TEXT, alignment=TA_LEFT,
)

STYLE_COVER_CAPTION = ParagraphStyle(
    "CoverCaption",
    fontName=SERIF_ITALIC, fontSize=7.5, leading=10,
    textColor=COVER_MUTED, alignment=TA_LEFT,
)

STYLE_COVER_FOOTER = ParagraphStyle(
    "CoverFooter",
    fontName=SERIF_REGULAR, fontSize=8, leading=11,
    textColor=COVER_MUTED, alignment=TA_LEFT,
)


# ═══════════════════════════════════════════════════════════════════════════
# Table style helpers — monochrome rule-driven
# ═══════════════════════════════════════════════════════════════════════════

def base_table_style(n_rows: int) -> list:
    """McKinsey-idiom table styling: navy header rule stack.

    Header is serif bold on white, opened by a thick navy rule and
    closed by a hairline navy rule. Body rows are separated by
    hairline gray rules. The table closes with a thick navy bottom
    rule — the thick-thin-thin-thick stack of a financial statement.
    """
    cmds = [
        # Header
        ("FONTNAME", (0, 0), (-1, 0), SERIF_BOLD),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), BRAND_NAVY),
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, BRAND_NAVY),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, BRAND_NAVY),
        # Body
        ("FONTNAME", (0, 1), (-1, -1), SERIF_REGULAR),
        ("FONTSIZE", (0, 1), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 1), (-1, -1), TEXT_PRIMARY),
        # Layout
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        # Hairline row separators, thick navy bottom rule
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, GRIDLINE),
        ("LINEBELOW", (0, -1), (-1, -1), 1.0, BRAND_NAVY),
    ]
    return cmds


def risk_color(rating: str) -> colors.HexColor:
    """Return a navy-descending color for a risk rating string.

    CRITICAL is deepest navy; LOW fades to pale blue. The ramp stays
    inside the blue family so it reads as one visual system.
    """
    r = (rating or "").upper()
    if "CRITICAL" in r:
        return RISK_CRITICAL
    if "HIGH" in r:
        return RISK_HIGH
    if "ELEVATED" in r or "MODERATE" in r:
        return RISK_ELEVATED
    return RISK_LOW


def grade_color(grade: str) -> str:
    """Return hex color for a confidence grade (navy ramp)."""
    if grade.startswith("A"):
        return COLORS["brand_navy"]
    if grade.startswith("B"):
        return COLORS["risk_high"]
    if grade.startswith("C"):
        return COLORS["brand_blue"]
    return COLORS["text_secondary"]


def section_bar(title: str, width: float):
    """Build a McKinsey-style navy section header bar.

    Returns a ReportLab flowable (Table) that paints a ~0.35-inch
    navy strip with centered white serif-bold text, to be used as
    the H1 equivalent on content pages.
    """
    from reportlab.platypus import Paragraph, Table, TableStyle
    p = Paragraph(title, STYLE_H1_BAR)
    t = Table([[p]], colWidths=[width], rowHeights=[0.36 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SECTION_BAR_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


# ═══════════════════════════════════════════════════════════════════════════
# Matplotlib monochrome style
# ═══════════════════════════════════════════════════════════════════════════

_MPL_CONFIGURED = False


def setup_chart_style() -> None:
    """Apply monochrome matplotlib rcParams — call before creating figures.

    Registers Libre Baskerville with matplotlib, switches font family
    to serif, and sets white facecolors + neutral axis/grid colors so
    charts feel like they belong in the same document as the serif
    body text.
    """
    global _MPL_CONFIGURED
    if _MPL_CONFIGURED:
        return

    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import font_manager, rcParams

    if _BASKERVILLE_AVAILABLE:
        try:
            for fname in (
                "LibreBaskerville-Regular.ttf",
                "LibreBaskerville-Bold.ttf",
                "LibreBaskerville-Italic.ttf",
            ):
                font_manager.fontManager.addfont(str(_FONT_DIR / fname))
            rcParams["font.family"] = "serif"
            rcParams["font.serif"] = ["Libre Baskerville"] + list(
                rcParams.get("font.serif", []))
        except Exception:
            rcParams["font.family"] = "serif"
    else:
        rcParams["font.family"] = "serif"

    rcParams["figure.facecolor"] = COLORS["page_bg"]
    rcParams["axes.facecolor"] = COLORS["page_bg"]
    rcParams["savefig.facecolor"] = COLORS["page_bg"]
    rcParams["savefig.edgecolor"] = COLORS["page_bg"]
    rcParams["text.color"] = COLORS["text_primary"]
    rcParams["axes.labelcolor"] = COLORS["text_secondary"]
    rcParams["axes.edgecolor"] = COLORS["text_primary"]
    rcParams["xtick.color"] = COLORS["text_secondary"]
    rcParams["ytick.color"] = COLORS["text_secondary"]
    rcParams["grid.color"] = COLORS["gridline"]
    rcParams["axes.titlecolor"] = COLORS["brand_navy"]
    rcParams["axes.titleweight"] = "bold"
    rcParams["legend.frameon"] = False

    _MPL_CONFIGURED = True


# Apply on import so any section that pulls from report.styles picks
# it up automatically; sections can still call setup_chart_style()
# defensively.
setup_chart_style()

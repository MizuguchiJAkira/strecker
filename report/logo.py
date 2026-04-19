"""Basal mark — the five-strata-with-three-peaks logo as a ReportLab flowable.

Rendered directly from the canonical SVG paths (viewBox 0 0 64 64) using
ReportLab's path primitive, so the logo is vector-sharp at any size and
stays in sync with web/static/marketing/basal-mark.svg without needing
an external SVG library.
"""

from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import Flowable


# Paths copied verbatim from web/static/marketing/basal-mark.svg.
# Each tuple is a closed polygon in 64-unit SVG viewBox coordinates
# (y-down). The render() method flips y at draw time for PDF (y-up).
_BARS = [
    # Bar 1 — top, with a V-notch at x=20 receiving peak from bar 2
    [(4, 2), (60, 2), (60, 10), (24, 10), (20, 6), (16, 10), (4, 10)],
    # Bar 2 — peak at x=20, notch at x=44
    [(4, 14), (16, 14), (20, 10), (24, 14), (60, 14), (60, 22),
     (48, 22), (44, 18), (40, 22), (4, 22)],
    # Bar 3 — peak at x=44, notch at x=32
    [(4, 26), (40, 26), (44, 22), (48, 26), (60, 26), (60, 34),
     (36, 34), (32, 30), (28, 34), (4, 34)],
    # Bar 4 — peak at x=32, no notch above
    [(4, 38), (28, 38), (32, 34), (36, 38), (60, 38), (60, 46), (4, 46)],
    # Bar 5 — base slab
    [(4, 50), (60, 50), (60, 58), (4, 58)],
]

_VIEWBOX = 64.0


class BasalMark(Flowable):
    """Vector logo flowable — the five-strata mark.

    Args:
        size: Rendered square size in points. Default is 0.45".
        color: Fill color (default: ink / near-black).
    """

    def __init__(self, size: float = 0.45 * inch,
                 color: colors.Color = colors.HexColor("#0c0d0a")):
        super().__init__()
        self.size = size
        self.color = color
        self.width = size
        self.height = size

    def wrap(self, availWidth, availHeight):
        return (self.size, self.size)

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFillColor(self.color)
        c.setStrokeColor(self.color)
        s = self.size / _VIEWBOX  # scale factor: svg unit → pt
        for bar in _BARS:
            p = c.beginPath()
            first = True
            for (x, y) in bar:
                # Flip y axis: PDF origin is bottom-left; SVG is top-left.
                px = x * s
                py = (_VIEWBOX - y) * s
                if first:
                    p.moveTo(px, py)
                    first = False
                else:
                    p.lineTo(px, py)
            p.close()
            c.drawPath(p, fill=1, stroke=0)
        c.restoreState()

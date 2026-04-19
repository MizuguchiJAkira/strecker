"""Season-window resolution for multi-year uploads.

A real hunter's SD card often spans multiple calendar years — 2013-2025 in
the TNDeer dump, for example. REM density math assumes a *single* survey
window per DetectionSummary row, so we must split one upload's detections
across (season, camera, species) buckets that each sit inside exactly one
Season's date range.

Resolution order for each detection timestamp:

1. If any existing ``Season(property_id=...)`` row covers the date
   (``start_date <= ts.date() <= end_date``), use it. Multiple overlapping
   seasons are rare but deterministic — we pick the one with the earliest
   ``start_date`` for stability.
2. Otherwise, fall back to a calendar-year bucket and auto-create (or look
   up) a ``Season`` named ``"Auto-detected YYYY deployment"`` spanning
   Jan 1 – Dec 31 of the detection's year. Keeps density math sane at
   calendar-year grain; upgradeable later by editing the Season row.

This module is intentionally dependency-light — it takes the SQLAlchemy
``db`` handle and the ``Season`` model by injection so it's easy to test
under the same SQLite fixture the rest of the suite uses.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, Iterable, List, Tuple


AUTO_SEASON_NAME_FMT = "Auto-detected {year} deployment"


def _ts_date(ts) -> date:
    """Accept datetime or date; return a date."""
    if isinstance(ts, datetime):
        return ts.date()
    return ts


def _find_covering_season(seasons, ts_date: date):
    """Return the existing Season whose window covers ``ts_date``.

    Pick the one with the earliest ``start_date`` so overlapping windows
    resolve deterministically. Seasons with a NULL bound are ignored —
    we only trust seasons with both bounds set.
    """
    best = None
    for s in seasons:
        if s.start_date is None or s.end_date is None:
            continue
        if s.start_date <= ts_date <= s.end_date:
            if best is None or s.start_date < best.start_date:
                best = s
    return best


def resolve_seasons_for_detections(
    db, Season, property_id: int, detections: Iterable
) -> Dict[int, object]:
    """Map each detection (by object id) to a ``Season`` row.

    Returns a dict ``{id(detection): season}``. Auto-creates a
    calendar-year Season for any detection whose date falls outside all
    existing Season windows for the property.

    Newly created Season rows are ``db.session.add``-ed and flushed so
    callers can reference ``season.id`` immediately. The caller is
    responsible for the final ``commit()``.
    """
    existing = Season.query.filter_by(property_id=property_id).all()

    # Cache auto-created year seasons within this call so a 10k-detection
    # batch doesn't query the DB 10k times.
    auto_by_year: Dict[int, object] = {}
    mapping: Dict[int, object] = {}

    for det in detections:
        ts = det.timestamp
        if ts is None:
            continue
        d = _ts_date(ts)

        covering = _find_covering_season(existing, d)
        if covering is not None:
            mapping[id(det)] = covering
            continue

        year = d.year
        season = auto_by_year.get(year)
        if season is None:
            # Look for an already-persisted auto-season from a prior upload.
            name = AUTO_SEASON_NAME_FMT.format(year=year)
            season = (Season.query
                      .filter_by(property_id=property_id, name=name)
                      .first())
            if season is None:
                season = Season(
                    property_id=property_id,
                    name=name,
                    start_date=date(year, 1, 1),
                    end_date=date(year, 12, 31),
                )
                db.session.add(season)
                db.session.flush()  # populate season.id
                # Include the new season in the existing-list so subsequent
                # detections whose date was already inside its range don't
                # get their own duplicate row (defensive — auto windows are
                # whole years so they shouldn't overlap, but existing user
                # seasons might be added in the middle of a batch).
                existing.append(season)
            auto_by_year[year] = season
        mapping[id(det)] = season

    return mapping


def group_detections_by_season(
    db, Season, property_id: int, detections: List
) -> List[Tuple[object, List]]:
    """Partition a detection list into ``[(season, [detections...]), ...]``.

    Preserves the single-season fast path: if every detection maps to
    the same Season, returns a single-tuple list with no per-detection
    dict overhead at the call site. Order of returned seasons is the
    order each season was first encountered in the input — deterministic
    for a deterministic input.
    """
    mapping = resolve_seasons_for_detections(db, Season, property_id, detections)

    if not mapping:
        return []

    # Fast path: all detections to one season.
    unique_seasons = {id(s): s for s in mapping.values()}
    if len(unique_seasons) == 1:
        only = next(iter(unique_seasons.values()))
        return [(only, [d for d in detections if id(d) in mapping])]

    # Multi-season path — preserve first-seen order of seasons.
    order: List[object] = []
    seen: set = set()
    buckets: Dict[int, List] = {}
    for d in detections:
        season = mapping.get(id(d))
        if season is None:
            continue
        sid = id(season)
        if sid not in seen:
            seen.add(sid)
            order.append(season)
            buckets[sid] = []
        buckets[sid].append(d)

    return [(s, buckets[id(s)]) for s in order]

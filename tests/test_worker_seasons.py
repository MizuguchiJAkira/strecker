"""Worker multi-year aggregation tests.

Guards the per-season slicing in ``strecker.worker._aggregate_to_property``
(via the ``strecker.seasons`` helper): a single upload whose detections
span multiple years must produce one DetectionSummary per
(detected-season-window, camera, species). REM density math assumes a
single survey window per summary row, so a multi-year SD card that
collapses into one Season would silently corrupt density.

Covers:
  - single-year upload (fast path still works)
  - multi-year upload (two seasons, two DetectionSummary rows per
    species per camera)
  - year-boundary detection (routed into the right year-bucket)
  - auto-created Season rows have sane names + Jan 1–Dec 31 dates
  - existing Season rows are preferred over auto-created ones
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime

import pytest


_TEST_DB = tempfile.NamedTemporaryFile(
    prefix="basal-test-workerseasons-", suffix=".db", delete=False).name
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import sys as _sys
for _mod in list(_sys.modules):
    if (_mod == "config" or _mod.startswith("config.")
            or _mod == "db" or _mod.startswith("db.")
            or _mod.startswith("web.")):
        _sys.modules.pop(_mod, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app_ctx():
    from web.app import create_app
    from db.models import db
    app = create_app(demo=True, site="strecker")
    with app.app_context():
        yield app, db


class _FakeDet:
    """Minimal stand-in for strecker.ingest.Detection.

    The worker's aggregation path only reads a handful of attributes, so
    we don't need to spin up the full dataclass (which pulls in torch
    etc. via the ingest module).
    """

    def __init__(self, camera_id, species_key, ts,
                 confidence=0.9, event_id=None,
                 antler=None, calibrated=None):
        self.camera_id = camera_id
        self.species_key = species_key
        self.timestamp = ts
        self.confidence = confidence
        self.confidence_calibrated = calibrated
        self.independent_event_id = event_id
        self.antler_classification = antler


def _make_parcel(db):
    from db.models import User, Property
    u = User.query.filter_by(email="owner@basal.eco").first()
    if u is None:
        u = User(email="owner@basal.eco", is_owner=True, password_hash="x")
        db.session.add(u); db.session.commit()
    p = Property(user_id=u.id, name="Season-slice test parcel",
                 county="Matagorda", state="TX", acreage=100,
                 created_at=datetime(2025, 1, 1))
    db.session.add(p); db.session.commit()
    return p


def _make_job(db, property_id):
    from db.models import ProcessingJob
    import uuid
    pj = ProcessingJob(
        job_id=uuid.uuid4().hex[:8],
        property_id=property_id,
        property_name="Season-slice test parcel",
        state="TX",
        status="processing",
    )
    db.session.add(pj); db.session.commit()
    return pj


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_single_year_upload_single_season(app_ctx):
    """All detections in one year → one Season, one summary per
    (camera, species). Fast path."""
    app, db = app_ctx
    from db.models import Season, DetectionSummary
    from strecker.worker import _aggregate_to_property

    p = _make_parcel(db)
    pj = _make_job(db, p.id)
    dets = [
        _FakeDet("CAM-A", "white_tailed_deer",
                 datetime(2024, 3, 1, 7, 0), event_id="e1"),
        _FakeDet("CAM-A", "white_tailed_deer",
                 datetime(2024, 6, 15, 8, 0), event_id="e2"),
        _FakeDet("CAM-A", "feral_hog",
                 datetime(2024, 10, 2, 22, 0), event_id="e3"),
    ]

    _aggregate_to_property(db, pj, dets)

    seasons = Season.query.filter_by(property_id=p.id).all()
    assert len(seasons) == 1
    assert seasons[0].start_date == date(2024, 1, 1)
    assert seasons[0].end_date == date(2024, 12, 31)
    summaries = DetectionSummary.query.filter_by(
        season_id=seasons[0].id).all()
    # 2 species × 1 camera = 2 summary rows
    assert {s.species_key for s in summaries} == {
        "white_tailed_deer", "feral_hog"}
    assert len(summaries) == 2


def test_multi_year_upload_splits_into_per_year_summaries(app_ctx):
    """2023 and 2025 detections → 2 seasons, 2 summaries per species
    per camera. No year-collapse."""
    app, db = app_ctx
    from db.models import Season, DetectionSummary
    from strecker.worker import _aggregate_to_property

    p = _make_parcel(db)
    pj = _make_job(db, p.id)
    dets = [
        # 2023
        _FakeDet("CAM-B", "white_tailed_deer",
                 datetime(2023, 5, 1, 7, 0), event_id="23a"),
        _FakeDet("CAM-B", "white_tailed_deer",
                 datetime(2023, 8, 1, 18, 0), event_id="23b"),
        _FakeDet("CAM-B", "feral_hog",
                 datetime(2023, 11, 1, 22, 0), event_id="23c"),
        # 2025
        _FakeDet("CAM-B", "white_tailed_deer",
                 datetime(2025, 2, 1, 7, 0), event_id="25a"),
        _FakeDet("CAM-B", "feral_hog",
                 datetime(2025, 6, 1, 22, 0), event_id="25b"),
    ]

    _aggregate_to_property(db, pj, dets)

    seasons = Season.query.filter_by(property_id=p.id).order_by(
        Season.start_date).all()
    years = [s.start_date.year for s in seasons]
    assert years == [2023, 2025], f"expected per-year seasons, got {years}"

    by_year = {s.start_date.year: s.id for s in seasons}
    # Deer in both years, hog in both years → 4 rows on CAM-B
    rows = DetectionSummary.query.filter(
        DetectionSummary.season_id.in_([s.id for s in seasons])).all()
    pairs = {(r.season_id, r.species_key) for r in rows}
    assert (by_year[2023], "white_tailed_deer") in pairs
    assert (by_year[2023], "feral_hog") in pairs
    assert (by_year[2025], "white_tailed_deer") in pairs
    assert (by_year[2025], "feral_hog") in pairs

    # Counts are not blended across years.
    deer_23 = next(r for r in rows
                   if r.season_id == by_year[2023]
                   and r.species_key == "white_tailed_deer")
    deer_25 = next(r for r in rows
                   if r.season_id == by_year[2025]
                   and r.species_key == "white_tailed_deer")
    assert deer_23.total_photos == 2
    assert deer_25.total_photos == 1


def test_year_boundary_routes_to_correct_season(app_ctx):
    """Dec 31 @ 23:59 stays in year N; Jan 1 @ 00:01 lands in year N+1."""
    app, db = app_ctx
    from db.models import Season, DetectionSummary
    from strecker.worker import _aggregate_to_property

    p = _make_parcel(db)
    pj = _make_job(db, p.id)
    dets = [
        _FakeDet("CAM-C", "white_tailed_deer",
                 datetime(2024, 12, 31, 23, 59), event_id="nye"),
        _FakeDet("CAM-C", "white_tailed_deer",
                 datetime(2025, 1, 1, 0, 1), event_id="nyd"),
    ]

    _aggregate_to_property(db, pj, dets)

    seasons = {s.start_date.year: s for s
               in Season.query.filter_by(property_id=p.id).all()}
    assert 2024 in seasons and 2025 in seasons

    s24 = DetectionSummary.query.filter_by(
        season_id=seasons[2024].id).first()
    s25 = DetectionSummary.query.filter_by(
        season_id=seasons[2025].id).first()
    assert s24 is not None and s25 is not None
    assert s24.total_photos == 1
    assert s25.total_photos == 1


def test_auto_created_season_names_and_dates(app_ctx):
    """Auto-created Season has the documented name format and spans
    the full calendar year."""
    app, db = app_ctx
    from db.models import Season
    from strecker.worker import _aggregate_to_property

    p = _make_parcel(db)
    pj = _make_job(db, p.id)
    dets = [
        _FakeDet("CAM-D", "feral_hog",
                 datetime(2019, 7, 4, 3, 14), event_id="e"),
    ]

    _aggregate_to_property(db, pj, dets)

    seasons = Season.query.filter_by(property_id=p.id).all()
    assert len(seasons) == 1
    s = seasons[0]
    assert s.name == "Auto-detected 2019 deployment"
    assert s.start_date == date(2019, 1, 1)
    assert s.end_date == date(2019, 12, 31)


def test_existing_season_is_preferred_over_auto_create(app_ctx):
    """A pre-existing Season whose window covers a detection date is
    used instead of an auto-created calendar-year row."""
    app, db = app_ctx
    from db.models import Season, DetectionSummary
    from strecker.worker import _aggregate_to_property

    p = _make_parcel(db)
    # Pre-existing user-defined season — narrow hunting window.
    existing = Season(
        property_id=p.id, name="TX Archery 2024",
        start_date=date(2024, 10, 1), end_date=date(2024, 11, 15))
    db.session.add(existing); db.session.commit()
    existing_id = existing.id

    pj = _make_job(db, p.id)
    dets = [
        # Inside the hunter's window → should attach to existing season
        _FakeDet("CAM-E", "white_tailed_deer",
                 datetime(2024, 10, 20, 7, 0), event_id="in1"),
        _FakeDet("CAM-E", "white_tailed_deer",
                 datetime(2024, 11, 5, 7, 0), event_id="in2"),
        # Outside the window → should auto-create a 2024 calendar-year season
        _FakeDet("CAM-E", "feral_hog",
                 datetime(2024, 3, 15, 22, 0), event_id="out"),
    ]

    _aggregate_to_property(db, pj, dets)

    # Existing season picked up the in-window detections.
    in_window = DetectionSummary.query.filter_by(
        season_id=existing_id, species_key="white_tailed_deer").first()
    assert in_window is not None
    assert in_window.total_photos == 2

    # An auto-season was created for the March detection.
    auto = Season.query.filter_by(
        property_id=p.id, name="Auto-detected 2024 deployment").first()
    assert auto is not None
    assert auto.id != existing_id
    hog = DetectionSummary.query.filter_by(
        season_id=auto.id, species_key="feral_hog").first()
    assert hog is not None
    assert hog.total_photos == 1

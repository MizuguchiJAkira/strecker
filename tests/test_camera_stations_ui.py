"""Owner-facing camera-stations mapping page.

Thin shell around the ``/api/properties/<pid>/camera-stations`` JSON
API (already covered by tests/test_camera_stations.py). These tests
guard the page route + template wiring.
"""

import json
import os
import tempfile

import pytest


_TEST_DB = tempfile.NamedTemporaryFile(
    prefix="basal-test-cam-ui-", suffix=".db", delete=False).name
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import sys as _sys
for _mod in list(_sys.modules):
    if (_mod == "config" or _mod.startswith("config.")
            or _mod == "db" or _mod.startswith("db.")
            or _mod.startswith("web.")):
        _sys.modules.pop(_mod, None)


@pytest.fixture(scope="module")
def ctx():
    from web.app import create_app
    from db.models import db, Property, User
    app = create_app(demo=True, site="strecker")
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        owner = User.query.filter_by(email="demo@strecker.app").first()
        if owner is None:
            owner = User(email="demo@strecker.app",
                         is_owner=True, password_hash="x")
            db.session.add(owner); db.session.commit()
        parcel = Property.query.filter_by(name="CamStations Parcel").first()
        if parcel is None:
            b = {"type": "Feature", "geometry": {"type": "Polygon",
                 "coordinates": [[[-96.5, 30.5], [-96.5, 30.6],
                                  [-96.4, 30.6], [-96.4, 30.5],
                                  [-96.5, 30.5]]]}}
            parcel = Property(
                user_id=owner.id, name="CamStations Parcel",
                county="Brazos", state="TX", acreage=400,
                boundary_geojson=json.dumps(b), crop_type="corn")
            db.session.add(parcel); db.session.commit()
        pid = parcel.id
    yield app, app.test_client(), pid


def test_page_renders_for_owner(ctx):
    _, c, pid = ctx
    r = c.get(f"/properties/{pid}/camera-stations")
    assert r.status_code == 200, r.data
    body = r.data.decode("utf-8")
    # Form + select populated with canonical contexts
    assert "station_code" in body
    assert "feeder" in body
    assert "random" in body
    assert "water" in body
    # Fetches from the right API base (JS concatenates PID + path)
    assert f"const PID = {pid};" in body
    assert '"/api/properties/" + PID + "/camera-stations"' in body


def test_page_404_for_non_owner(ctx):
    app, _, pid = ctx
    # Fresh client with no session — auto-login can't run without a
    # session at all for a different user, so we simulate a logged-in
    # non-owner by creating a second user and forcing login.
    from db.models import db, User
    from flask_login import login_user
    with app.app_context():
        intruder = User.query.filter_by(email="intruder@basal.test").first()
        if intruder is None:
            intruder = User(email="intruder@basal.test",
                            is_owner=False, password_hash="x")
            db.session.add(intruder); db.session.commit()
        intruder_id = intruder.id

    fresh = app.test_client()
    with fresh.session_transaction() as s:
        s["_user_id"] = str(intruder_id)
    r = fresh.get(f"/properties/{pid}/camera-stations")
    # Ownership check returns 404 rather than 403 (same pattern as
    # /upload and /upload-tokens).
    assert r.status_code == 404


def test_cameras_page_links_to_stations_page(ctx):
    _, c, pid = ctx
    r = c.get(f"/properties/{pid}/cameras")
    assert r.status_code == 200
    assert f"/properties/{pid}/camera-stations" in r.data.decode("utf-8")

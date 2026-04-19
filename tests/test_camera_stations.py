"""API tests for CameraStation — per-property station-code mapping
used by the IPW bias-correction layer.

Uses a real SQLite DB + the strecker Flask app in demo mode; demo mode
gives us an auto-login as the demo hunter so we don't need to POST to
/login. A second hunter is created to cover the auth-gating case (the
second hunter's property must be invisible to the first).
"""

import json
import os
import tempfile

import pytest


_TEST_DB = tempfile.NamedTemporaryFile(
    prefix="basal-test-camstations-", suffix=".db", delete=False).name
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
        assert owner is not None
        my_parcel = Property.query.filter_by(name="Stations Test Parcel").first()
        if my_parcel is None:
            my_parcel = Property(
                user_id=owner.id, name="Stations Test Parcel",
                county="Brazos", state="TX", acreage=400)
            db.session.add(my_parcel)
            db.session.commit()
        my_pid = my_parcel.id

        # A second hunter + their property — used for the ownership check.
        other = User.query.filter_by(email="other@strecker.app").first()
        if other is None:
            other = User(email="other@strecker.app", password_hash="x")
            db.session.add(other)
            db.session.commit()
        other_parcel = Property.query.filter_by(name="Other Hunter Parcel").first()
        if other_parcel is None:
            other_parcel = Property(
                user_id=other.id, name="Other Hunter Parcel",
                county="Kimble", state="TX", acreage=200)
            db.session.add(other_parcel)
            db.session.commit()
        other_pid = other_parcel.id
    yield app, app.test_client(), my_pid, other_pid


# ---------------------------------------------------------------------------
# Happy path: create / list / patch / delete
# ---------------------------------------------------------------------------

def test_create_station_happy_path(ctx):
    _, c, pid, _ = ctx
    r = c.post(
        f"/api/properties/{pid}/camera-stations",
        data=json.dumps({
            "station_code": "mh",
            "placement_context": "water",
            "label": "Moore House tank",
        }),
        content_type="application/json",
    )
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["station_code"] == "MH"  # normalized upper-case
    assert body["placement_context"] == "water"
    assert body["label"] == "Moore House tank"
    assert body["property_id"] == pid


def test_list_stations_returns_created(ctx):
    _, c, pid, _ = ctx
    r = c.get(f"/api/properties/{pid}/camera-stations")
    assert r.status_code == 200
    body = r.get_json()
    codes = {s["station_code"] for s in body}
    assert "MH" in codes


def test_patch_station_updates_context_and_label(ctx):
    _, c, pid, _ = ctx
    r = c.patch(
        f"/api/properties/{pid}/camera-stations/MH",
        data=json.dumps({"placement_context": "feeder",
                         "label": "relocated to feeder"}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["placement_context"] == "feeder"
    assert body["label"] == "relocated to feeder"


def test_patch_with_lowercase_path_still_finds_row(ctx):
    _, c, pid, _ = ctx
    r = c.patch(
        f"/api/properties/{pid}/camera-stations/mh",
        data=json.dumps({"placement_context": "trail"}),
        content_type="application/json",
    )
    assert r.status_code == 200
    assert r.get_json()["placement_context"] == "trail"


def test_delete_station_removes_it(ctx):
    _, c, pid, _ = ctx
    r = c.delete(f"/api/properties/{pid}/camera-stations/MH")
    assert r.status_code == 200
    # Follow-up list must not contain MH.
    r = c.get(f"/api/properties/{pid}/camera-stations")
    codes = {s["station_code"] for s in r.get_json()}
    assert "MH" not in codes


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_create_rejects_invalid_placement_context(ctx):
    _, c, pid, _ = ctx
    r = c.post(
        f"/api/properties/{pid}/camera-stations",
        data=json.dumps({"station_code": "XX",
                         "placement_context": "bogus"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_create_rejects_non_alpha_station_code(ctx):
    _, c, pid, _ = ctx
    r = c.post(
        f"/api/properties/{pid}/camera-stations",
        data=json.dumps({"station_code": "M9",
                         "placement_context": "water"}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_unique_constraint_returns_409_on_dup(ctx):
    _, c, pid, _ = ctx
    r1 = c.post(
        f"/api/properties/{pid}/camera-stations",
        data=json.dumps({"station_code": "BS",
                         "placement_context": "feeder"}),
        content_type="application/json",
    )
    assert r1.status_code == 201
    r2 = c.post(
        f"/api/properties/{pid}/camera-stations",
        data=json.dumps({"station_code": "BS",
                         "placement_context": "trail"}),
        content_type="application/json",
    )
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Auth gating — non-owner rejected
# ---------------------------------------------------------------------------

def test_non_owner_cannot_create_on_other_property(ctx):
    """Demo hunter hits the second hunter's parcel; must 404."""
    _, c, _, other_pid = ctx
    r = c.post(
        f"/api/properties/{other_pid}/camera-stations",
        data=json.dumps({"station_code": "CW",
                         "placement_context": "water"}),
        content_type="application/json",
    )
    assert r.status_code == 404


def test_non_owner_cannot_list_other_property(ctx):
    _, c, _, other_pid = ctx
    r = c.get(f"/api/properties/{other_pid}/camera-stations")
    assert r.status_code == 404


def test_requires_login_when_demo_off():
    """Demo mode off → @login_required must redirect to /login."""
    from web.app import create_app
    app = create_app(demo=False, site="strecker")
    app.config["WTF_CSRF_ENABLED"] = False
    c = app.test_client()
    r = c.get("/api/properties/1/camera-stations", follow_redirects=False)
    assert r.status_code in (302, 401)
    if r.status_code == 302:
        assert "/login" in r.headers.get("Location", "")

"""Public /methodology page — renders docs/METHODOLOGY.md at request
time on the basal site. 404 on the strecker site (hunter-facing).
"""

import os
import tempfile

import pytest


_TEST_DB = tempfile.NamedTemporaryFile(
    prefix="basal-test-methpg-", suffix=".db", delete=False).name
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret")

import sys as _sys
for _mod in list(_sys.modules):
    if (_mod == "config" or _mod.startswith("config.")
            or _mod == "db" or _mod.startswith("db.")
            or _mod.startswith("web.")):
        _sys.modules.pop(_mod, None)


@pytest.fixture(scope="module")
def basal_client():
    from web.app import create_app
    app = create_app(demo=True, site="basal")
    app.config["WTF_CSRF_ENABLED"] = False
    yield app.test_client()


@pytest.fixture(scope="module")
def strecker_client():
    from web.app import create_app
    app = create_app(demo=True, site="strecker")
    app.config["WTF_CSRF_ENABLED"] = False
    yield app.test_client()


def test_methodology_renders_on_basal_site(basal_client):
    r = basal_client.get("/methodology")
    assert r.status_code == 200, r.data
    body = r.data.decode("utf-8")
    # Sourced from docs/METHODOLOGY.md — check for unique landmarks
    # in that doc that the markdown renderer should carry through.
    assert "Random Encounter Model" in body or "REM" in body
    assert "Mayer" in body or "Rowcliffe" in body or "Kolowski" in body
    # Styled page chrome (editorial typography class hooks)
    assert "b-methodology" in body
    # Current-page nav link
    assert 'href="/methodology"' in body


def test_methodology_404_on_strecker_site(strecker_client):
    r = strecker_client.get("/methodology")
    assert r.status_code == 404


def test_landing_footer_links_to_methodology_page(basal_client):
    """The landing footer's Science column should link to the new
    public page, not the #methodology fragment only."""
    r = basal_client.get("/")
    assert r.status_code == 200
    body = r.data.decode("utf-8")
    # At least one anchor to the new page
    assert 'href="/methodology"' in body

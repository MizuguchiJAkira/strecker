"""Owner-only routes — Basal Informatics internal dashboard.

Coverage map, upload activity, and network health metrics.
Locked to users with is_owner=True.
"""

from functools import wraps

from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user

owner_bp = Blueprint(
    "owner", __name__,
    url_prefix="/owner",
    template_folder="../templates/owner",
)


def owner_required(f):
    """Decorator: 404 if current user is not an owner.
    In demo mode, skip the owner check so auto-login works seamlessly.
    """
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        from flask import current_app
        if current_app.config.get("DEMO_MODE"):
            return f(*args, **kwargs)
        if not getattr(current_user, "is_owner", False):
            abort(404)  # hide existence from non-owners
        return f(*args, **kwargs)
    return decorated


@owner_bp.route("/coverage")
@owner_required
def coverage_map():
    """Render the national coverage map."""
    return render_template("coverage.html")

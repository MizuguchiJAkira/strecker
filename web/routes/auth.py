"""Authentication routes — register, login, logout.

GET/POST /login    — render login form, validate credentials
GET/POST /register — render register form, create user
GET      /logout   — log out, redirect to login
"""

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from db.models import db, User

auth_bp = Blueprint("auth", __name__)


def _default_landing():
    """Return the appropriate post-login URL based on the active brand.

    Host-routed: hits against strecker.* land on /properties, hits
    against basal.* land on /owner/coverage. Falls back to the app's
    boot-time default if there's no request context.
    """
    active = getattr(current_app, "active_site", None)
    site = active() if callable(active) else current_app.config.get("SITE")
    if site == "basal":
        return "/owner/coverage"
    return url_for("properties.index")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login page."""
    if current_user.is_authenticated:
        return redirect(_default_landing())

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or _default_landing())
        else:
            flash("Invalid email or password.", "error")

    return render_template("auth/login.html")


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Registration page."""
    if current_user.is_authenticated:
        return redirect(_default_landing())

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        display_name = request.form.get("display_name", "").strip()

        if not email or not password:
            flash("Email and password are required.", "error")
            return render_template("auth/register.html")

        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template("auth/register.html")

        existing = User.query.filter_by(email=email).first()
        if existing:
            flash("An account with that email already exists.", "error")
            return render_template("auth/register.html")

        user = User(email=email, display_name=display_name or None)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        flash("Account created. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))

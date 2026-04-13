"""Results routes — display classification and sorting results.

GET /results/<job_id>  — Show processing summary, downloads, species breakdown
GET /download/<job_id>/<file_type> — Serve generated files
"""

import os

from flask import Blueprint, abort, render_template, send_file

results_bp = Blueprint("results", __name__)


@results_bp.route("/results/<job_id>")
def results(job_id):
    """Results page for a completed processing job."""
    from web.routes.upload import _get_job
    job = _get_job(job_id)
    species = job.get("species", []) if job else []
    return render_template("results.html", job=job, species=species)


@results_bp.route("/download/<job_id>/<file_type>")
def download(job_id, file_type):
    """Serve generated files (report PDF, appendix CSV)."""
    from web.routes.upload import _get_job
    job = _get_job(job_id)
    if not job:
        abort(404)

    if file_type == "report" and job.get("report_path"):
        path = job["report_path"]
        if os.path.exists(path):
            return send_file(path, as_attachment=True,
                             download_name="game_inventory_report.pdf")
    elif file_type == "appendix" and job.get("appendix_path"):
        path = job["appendix_path"]
        if os.path.exists(path):
            return send_file(path, as_attachment=True,
                             download_name="events_appendix.csv")

    abort(404)

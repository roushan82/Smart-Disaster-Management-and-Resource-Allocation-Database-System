from flask import Blueprint, redirect, render_template

from core import db
from os_engine.worker import is_worker_running

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def home():
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM disasters WHERE status = 'ACTIVE'")
        active_disasters = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM emergency_requests WHERE status = 'PENDING'")
        pending_requests = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM emergency_requests WHERE status = 'ALLOCATED'")
        allocated_requests = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM rescue_teams WHERE status = 'AVAILABLE'")
        available_teams = cursor.fetchone()[0]
        cursor.execute("SELECT COALESCE(SUM(available_quantity), 0) FROM resources")
        available_resources = cursor.fetchone()[0]
        worker_running = is_worker_running(cursor)
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "dashboard/page.html",
        active_disasters=active_disasters,
        pending_requests=pending_requests,
        allocated_requests=allocated_requests,
        available_teams=available_teams,
        available_resources=available_resources,
        worker_running=worker_running,
    )


@bp.route("/admin/dashboard")
def legacy_admin_dashboard():
    return redirect("/", code=301)

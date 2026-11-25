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
        active_disasters = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM emergency_requests WHERE status = 'PENDING'")
        pending_requests = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM emergency_requests WHERE status = 'PENDING' AND priority = 'CRITICAL'")
        pending_critical_requests = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM emergency_requests WHERE status = 'PENDING' AND priority = 'HIGH'")
        pending_high_requests = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM emergency_requests WHERE status = 'ALLOCATED'")
        allocated_requests = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM rescue_teams WHERE status = 'AVAILABLE'")
        available_teams = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM rescue_teams WHERE status = 'DEPLOYED'")
        deployed_teams = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("SELECT COALESCE(SUM(available_quantity), 0) FROM resources")
        available_resources = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("""
            SELECT COUNT(*)
            FROM (
                SELECT type_id
                FROM resources
                GROUP BY type_id
                HAVING SUM(available_quantity) = 0
            ) t
        """)
        depleted_resource_categories = int((cursor.fetchone() or (0,))[0] or 0)

        cursor.execute("""
            SELECT COUNT(*)
            FROM detection_results
            WHERE review_status IN ('PENDING_REVIEW', 'NEEDS_MORE_DATA')
        """)
        pending_historical_reviews = int((cursor.fetchone() or (0,))[0] or 0)

        worker_running = is_worker_running(cursor)
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "dashboard/page.html",
        active_disasters=active_disasters,
        pending_requests=pending_requests,
        pending_critical_requests=pending_critical_requests,
        pending_high_requests=pending_high_requests,
        allocated_requests=allocated_requests,
        available_teams=available_teams,
        deployed_teams=deployed_teams,
        available_resources=available_resources,
        depleted_resource_categories=depleted_resource_categories,
        pending_historical_reviews=pending_historical_reviews,
        worker_running=worker_running,
    )


@bp.route("/admin/dashboard")
def legacy_admin_dashboard():
    return redirect("/", code=301)

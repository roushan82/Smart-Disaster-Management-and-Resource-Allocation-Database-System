import mysql.connector
from flask import Blueprint, abort, redirect, render_template, request, url_for

from core import db
from modules.notifications.service import create_notification

bp = Blueprint("assignments", __name__)

ASSIGNMENT_STATUSES = ("ASSIGNED", "ACCEPTED", "IN_PROGRESS", "COMPLETED")


def _render_admin_assignments(error=None, message=None):
    connection = db.get_db_connection()
    cursor = connection.cursor(dictionary=True)
    assignments_available = True
    try:
        cursor.execute("""
            SELECT er.request_id, d.name AS disaster_name, er.priority
            FROM emergency_requests er JOIN disasters d ON d.disaster_id = er.disaster_id
            LEFT JOIN team_request_assignments ta ON ta.request_id = er.request_id
            WHERE er.status IN ('PENDING', 'ALLOCATED') AND ta.assignment_id IS NULL
            ORDER BY er.request_time, er.request_id
        """)
        eligible_requests = cursor.fetchall()
        cursor.execute("SELECT team_id, team_name, status FROM rescue_teams ORDER BY team_name")
        teams = cursor.fetchall()
        cursor.execute("""
            SELECT ta.assignment_id, ta.request_id, ta.team_id, ta.status, ta.instructions, ta.assigned_at,
                   d.name AS disaster_name, rt.team_name
            FROM team_request_assignments ta
            JOIN emergency_requests er ON er.request_id = ta.request_id
            JOIN disasters d ON d.disaster_id = er.disaster_id
            JOIN rescue_teams rt ON rt.team_id = ta.team_id
            ORDER BY ta.assigned_at DESC
        """)
        assignments = cursor.fetchall()
    except mysql.connector.Error as database_error:
        if database_error.errno != 1146:  # 1146 = table doesn't exist
            raise
        assignments_available = False
        eligible_requests, teams, assignments = [], [], []
        if error is None:
            error = (
                "Team assignments are unavailable because the team_request_assignments "
                "table is missing. Apply database/schema.sql to create it."
            )
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "assignments/page.html", eligible_requests=eligible_requests,
        teams=teams, assignments=assignments, error=error, message=message,
        assignments_available=assignments_available,
    )


@bp.route("/admin/assignments")
def page():
    return _render_admin_assignments(request.args.get("error"), request.args.get("message"))


@bp.route("/admin/assignments", methods=["POST"])
def create():
    # The table requires a real assigned_by_user_id; the app has no login, so none is fabricated.
    return _render_admin_assignments(
        error=(
            "Creating assignments is unavailable because the database requires a real "
            "assigned_by_user_id. Restoring creation requires an explicit, valid creator "
            "identity and the team_request_assignments schema; no user ID is fabricated."
        )
    ), 409


@bp.route("/team/assignments/<int:assignment_id>/status", methods=["POST"])
def update_status(assignment_id):
    status = request.form.get("status", "")
    if status not in ASSIGNMENT_STATUSES:
        abort(400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT request_id, team_id FROM team_request_assignments WHERE assignment_id = %s",
            (assignment_id,),
        )
        assignment = cursor.fetchone()
        if assignment is None:
            abort(404)
        request_id, team_id = assignment
        cursor.execute(
            "UPDATE team_request_assignments SET status = %s WHERE assignment_id = %s",
            (status, assignment_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            abort(404)
        create_notification(
            cursor,
            event_key=f"team-assignment-status:{assignment_id}:{status}",
            event_type="TEAM_ASSIGNMENT_STATUS",
            message=f"Team assignment for request #{request_id} changed to {status}.",
            related_request_id=request_id,
            related_team_id=team_id,
        )
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        abort(400)
    finally:
        cursor.close()
        connection.close()
    return redirect(url_for("assignments.page"))

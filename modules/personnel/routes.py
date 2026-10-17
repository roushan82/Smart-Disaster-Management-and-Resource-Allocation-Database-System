import mysql.connector
from flask import Blueprint, abort, redirect, render_template, request

from core import db
from core.validation import FormValidationError, validate_status
from modules.notifications.service import create_notification
from modules.personnel.validation import (
    PERSONNEL_ROLES,
    PERSONNEL_STATUSES,
    validate_personnel,
)

bp = Blueprint("personnel", __name__)


def _render_personnel(form_data=None, errors=None, status=200):
    connection = db.get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT p.personnel_id, p.name, p.role, p.contact, p.status,
                   rt.team_name
            FROM personnel p
            JOIN rescue_teams rt ON p.team_id = rt.team_id
            ORDER BY p.personnel_id DESC
        """)
        personnel_data = cursor.fetchall()
        cursor.execute("SELECT team_id, team_name FROM rescue_teams ORDER BY team_name")
        teams = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "personnel/page.html",
        personnel=personnel_data,
        teams=teams,
        personnel_roles=PERSONNEL_ROLES,
        personnel_statuses=PERSONNEL_STATUSES,
        form_data=form_data or {},
        errors=errors or {},
    ), status


@bp.route("/personnel")
def personnel():
    return _render_personnel()


@bp.route("/personnel/add", methods=["POST"])
def add_personnel():
    try:
        values = validate_personnel(request.form)
    except FormValidationError as error:
        return _render_personnel(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM rescue_teams WHERE team_id = %s", (values["team_id"],))
        if cursor.fetchone() is None:
            return _render_personnel(request.form, {"team_id": "Select an existing rescue team."}, 400)
        cursor.execute("""
            INSERT INTO personnel (team_id, name, role, contact, status)
            VALUES (%s, %s, %s, %s, 'AVAILABLE')
        """, (values["team_id"], values["name"], values["role"], values["contact"]))
        personnel_id = cursor.lastrowid
        create_notification(
            cursor,
            event_key=f"team-assignment:{personnel_id}",
            event_type="TEAM_ASSIGNMENT",
            message=f"A personnel member was assigned to rescue team #{values['team_id']}.",
            related_team_id=values["team_id"],
            related_personnel_id=personnel_id,
        )
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_personnel(
            request.form,
            {"_form": "The personnel record could not be saved. Check the submitted data and try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/personnel")


@bp.route("/personnel/status/<int:personnel_id>", methods=["POST"])
def update_personnel_status(personnel_id):
    try:
        personnel_status = validate_status(
            request.form, "status", "Personnel status", PERSONNEL_STATUSES
        )
    except FormValidationError as error:
        return _render_personnel(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM personnel WHERE personnel_id = %s", (personnel_id,))
        if cursor.fetchone() is None:
            abort(404)
        cursor.execute(
            "UPDATE personnel SET status = %s WHERE personnel_id = %s",
            (personnel_status, personnel_id),
        )
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_personnel(
            request.form,
            {"_form": "The personnel status could not be updated. Try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/personnel")

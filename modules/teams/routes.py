import mysql.connector
from flask import Blueprint, abort, redirect, render_template, request

from core import db
from core.validation import FormValidationError, validate_status
from modules.teams.validation import TEAM_STATUSES, TEAM_TYPES, validate_team

bp = Blueprint("teams", __name__)


def _render_teams(form_data=None, errors=None, status=200):
    connection = db.get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT rt.team_id, rt.team_name, rt.team_type, rt.status,
                   l.city, l.state
            FROM rescue_teams rt
            LEFT JOIN locations l ON rt.location_id = l.location_id
            ORDER BY rt.team_id DESC
        """)
        teams_data = cursor.fetchall()
        cursor.execute("SELECT location_id, city, state FROM locations")
        locations = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "teams/page.html",
        teams=teams_data,
        locations=locations,
        team_types=TEAM_TYPES,
        team_statuses=TEAM_STATUSES,
        form_data=form_data or {},
        errors=errors or {},
    ), status


@bp.route("/teams")
def teams():
    return _render_teams()


@bp.route("/teams/add", methods=["POST"])
def add_team():
    try:
        values = validate_team(request.form)
    except FormValidationError as error:
        return _render_teams(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM locations WHERE location_id = %s", (values["location_id"],))
        if cursor.fetchone() is None:
            return _render_teams(request.form, {"location_id": "Select an existing location."}, 400)
        cursor.execute("""
            INSERT INTO rescue_teams (team_name, team_type, location_id)
            VALUES (%s, %s, %s)
        """, (values["team_name"], values["team_type"], values["location_id"]))
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_teams(
            request.form,
            {"_form": "The rescue team could not be saved. Check the submitted data and try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/teams")


@bp.route("/teams/status/<int:team_id>", methods=["POST"])
def update_team_status(team_id):
    try:
        team_status = validate_status(request.form, "status", "Team status", TEAM_STATUSES)
    except FormValidationError as error:
        return _render_teams(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM rescue_teams WHERE team_id = %s", (team_id,))
        if cursor.fetchone() is None:
            abort(404)
        cursor.execute("UPDATE rescue_teams SET status = %s WHERE team_id = %s", (team_status, team_id))
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_teams(
            request.form,
            {"_form": "The team status could not be updated. Try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/teams")

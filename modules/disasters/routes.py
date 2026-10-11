import mysql.connector
from flask import Blueprint, abort, redirect, render_template, request

from core import db
from core.validation import FormValidationError
from modules.disasters.validation import DISASTER_TYPES, validate_disaster
from modules.notifications.service import create_notification

bp = Blueprint("disasters", __name__)


def _render_disasters(form_data=None, errors=None, status=200):
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT d.disaster_id, d.name, d.type, d.severity, l.city, d.status
            FROM disasters d
            JOIN locations l ON d.location_id = l.location_id
        """)
        disasters_data = cursor.fetchall()
        cursor.execute("SELECT location_id, city, state FROM locations")
        locations_data = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    return render_template(
        "disasters/page.html",
        disasters=disasters_data,
        locations=locations_data,
        disaster_types=DISASTER_TYPES,
        form_data=form_data or {},
        errors=errors or {},
    ), status


@bp.route("/disasters")
def disasters():
    return _render_disasters()


@bp.route("/disasters/add", methods=["POST"])
def add_disaster():
    try:
        values = validate_disaster(request.form)
    except FormValidationError as error:
        return _render_disasters(request.form, error.errors, 400)

    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM locations WHERE location_id = %s", (values["location_id"],))
        if cursor.fetchone() is None:
            return _render_disasters(request.form, {"location_id": "Select an existing location."}, 400)
        cursor.execute("""
            INSERT INTO disasters (name, type, severity, description, location_id)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            values["name"], values["type"], values["severity"],
            values["description"] or None, values["location_id"],
        ))
        disaster_id = cursor.lastrowid
        create_notification(
            cursor,
            event_key=f"disaster-created:{disaster_id}",
            event_type="NEW_EMERGENCY",
            message=f"New disaster registered: {values['name']}.",
            related_disaster_id=disaster_id,
        )
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_disasters(
            request.form,
            {"_form": "The disaster could not be saved. Check the submitted data and try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/disasters")


@bp.route("/disasters/resolve/<int:disaster_id>", methods=["POST"])
def resolve_disaster(disaster_id):
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE disasters SET status = 'RESOLVED' WHERE disaster_id = %s",
            (disaster_id,),
        )
        if cursor.rowcount == 0:
            abort(404)
        create_notification(
            cursor,
            event_key=f"disaster-resolved:{disaster_id}",
            event_type="EMERGENCY_STATUS_CHANGED",
            message=f"Disaster #{disaster_id} was marked resolved.",
            related_disaster_id=disaster_id,
        )
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return "Unable to update the disaster status.", 400
    finally:
        cursor.close()
        connection.close()
    return redirect("/disasters")

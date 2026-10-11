import mysql.connector
from flask import Blueprint, abort, redirect, render_template, request

from core import db
from core.validation import MAX_MYSQL_INT, FormValidationError
from modules.resources.validation import validate_replenishment, validate_resource

bp = Blueprint("resources", __name__)


def _render_resources(form_data=None, errors=None, status=200):
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT r.resource_id, rt.type_name, l.city, r.total_quantity,
                   r.available_quantity, r.status
            FROM resources r
            JOIN resource_types rt ON r.type_id = rt.type_id
            JOIN locations l ON r.location_id = l.location_id
        """)
        resources_data = cursor.fetchall()
        cursor.execute("SELECT type_id, type_name, unit FROM resource_types")
        resource_types_data = cursor.fetchall()
        cursor.execute("SELECT location_id, city, state FROM locations")
        locations_data = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "resources/page.html",
        resources=resources_data,
        resource_types=resource_types_data,
        locations=locations_data,
        form_data=form_data or {},
        errors=errors or {},
    ), status


@bp.route("/resources")
def resources():
    return _render_resources()


@bp.route("/resources/add", methods=["POST"])
def add_resource():
    try:
        values = validate_resource(request.form)
    except FormValidationError as error:
        return _render_resources(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM resource_types WHERE type_id = %s", (values["type_id"],))
        type_exists = cursor.fetchone() is not None
        cursor.execute("SELECT 1 FROM locations WHERE location_id = %s", (values["location_id"],))
        location_exists = cursor.fetchone() is not None
        errors = {}
        if not type_exists:
            errors["type_id"] = "Select an existing resource type."
        if not location_exists:
            errors["location_id"] = "Select an existing location."
        if errors:
            return _render_resources(request.form, errors, 400)
        quantity = values["total_quantity"]
        resource_status = "AVAILABLE" if quantity > 0 else "UNAVAILABLE"
        cursor.execute("""
            INSERT INTO resources
                (type_id, location_id, total_quantity, available_quantity, status)
            VALUES (%s, %s, %s, %s, %s)
        """, (values["type_id"], values["location_id"], quantity, quantity, resource_status))
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_resources(
            request.form,
            {"_form": "The resource could not be saved. Check the submitted data and try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/resources")


@bp.route("/resources/replenish/<int:resource_id>", methods=["POST"])
def replenish_resource(resource_id):
    if resource_id < 1:
        abort(404)
    try:
        quantity = validate_replenishment(request.form)["quantity"]
    except FormValidationError as error:
        return _render_resources(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        connection.start_transaction()
        cursor.execute("""
            SELECT total_quantity, available_quantity
            FROM resources
            WHERE resource_id = %s
            FOR UPDATE
        """, (resource_id,))
        resource = cursor.fetchone()
        if resource is None:
            connection.rollback()
            abort(404)
        total_quantity, available_quantity = resource
        if total_quantity + quantity > MAX_MYSQL_INT or available_quantity + quantity > MAX_MYSQL_INT:
            connection.rollback()
            return _render_resources(
                request.form,
                {"quantity": "The replenished quantity exceeds the supported maximum."},
                400,
            )
        cursor.execute("""
            UPDATE resources
            SET total_quantity = total_quantity + %s,
                available_quantity = available_quantity + %s,
                status = 'AVAILABLE'
            WHERE resource_id = %s
        """, (quantity, quantity, resource_id))
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_resources(
            request.form,
            {"_form": "The resource could not be replenished. Try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    return redirect("/resources")

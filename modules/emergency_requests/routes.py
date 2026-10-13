import mysql.connector
from flask import Blueprint, current_app, redirect, render_template, request

from core import db
from core.validation import FormValidationError
from modules.emergency_requests.validation import validate_request
from modules.notifications.service import create_notification
from os_engine.allocator import allocate_request
from os_engine.policies import ALGORITHMS
from os_engine.scheduler import (
    get_automatic_schedule_preview,
    schedule_pending_requests,
    signal_automatic_scheduler,
)
from os_engine.worker import is_worker_running

bp = Blueprint("emergency_requests", __name__)


def _render_requests(form_data=None, errors=None, status=200, message=None):
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT er.request_id, d.name, rt.type_name, er.requested_quantity,
                   er.priority, er.status, er.request_time
            FROM emergency_requests er
            JOIN disasters d ON er.disaster_id = d.disaster_id
            JOIN resource_types rt ON er.resource_type_id = rt.type_id
            ORDER BY er.request_id DESC
        """)
        requests_data = cursor.fetchall()
        cursor.execute("""
            SELECT disaster_id, name, status
            FROM disasters
            ORDER BY disaster_id DESC
        """)
        disasters_data = cursor.fetchall()
        cursor.execute("SELECT type_id, type_name, unit FROM resource_types")
        resource_types_data = cursor.fetchall()
        cursor.execute("""
            SELECT sr.algorithm, ri.queue_position, ri.request_id, sr.reason,
                   ri.request_status, ri.outcome, ri.result_message,
                   ri.executed_at
            FROM scheduling_run_items ri
            JOIN scheduling_runs sr ON sr.run_id = ri.run_id
            WHERE ri.run_id IN (
                SELECT MAX(run_id)
                FROM scheduling_runs
                GROUP BY algorithm
            )
            ORDER BY sr.algorithm ASC, ri.queue_position ASC
        """)
        scheduling_log = cursor.fetchall()
        automatic_preview = get_automatic_schedule_preview(cursor)
        worker_running = is_worker_running(cursor)
    finally:
        cursor.close()
        connection.close()
    return render_template(
        "emergency_requests/page.html",
        requests=requests_data,
        disasters=disasters_data,
        has_active_disasters=any(disaster[2] == "ACTIVE" for disaster in disasters_data),
        resource_types=resource_types_data,
        scheduling_log=scheduling_log,
        automatic_preview=automatic_preview,
        worker_running=worker_running,
        form_data=form_data or {},
        errors=errors or {},
        message=message,
    ), status


@bp.route("/requests")
def requests():
    return _render_requests()


@bp.route("/requests/add", methods=["POST"])
def add_request():
    try:
        values = validate_request(request.form)
    except FormValidationError as error:
        return _render_requests(request.form, error.errors, 400)
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT 1 FROM disasters WHERE disaster_id = %s AND status = 'ACTIVE'",
            (values["disaster_id"],),
        )
        disaster_exists = cursor.fetchone() is not None
        cursor.execute("SELECT 1 FROM resource_types WHERE type_id = %s", (values["resource_type_id"],))
        resource_exists = cursor.fetchone() is not None
        errors = {}
        if not disaster_exists:
            errors["disaster_id"] = "Select an existing active disaster."
        if not resource_exists:
            errors["resource_type_id"] = "Select an existing resource type."
        if errors:
            return _render_requests(request.form, errors, 400)
        cursor.execute("""
            INSERT INTO emergency_requests
                (disaster_id, resource_type_id, requested_quantity, priority)
            VALUES (%s, %s, %s, %s)
        """, (
            values["disaster_id"], values["resource_type_id"],
            values["requested_quantity"], values["priority"],
        ))
        request_id = cursor.lastrowid
        create_notification(
            cursor,
            event_key=f"request-created:{request_id}",
            event_type="NEW_REQUEST",
            message=(
                f"New emergency request #{request_id} was created "
                f"with {values['priority']} priority."
            ),
            related_request_id=request_id,
            related_disaster_id=values["disaster_id"],
        )
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        return _render_requests(
            request.form,
            {"_form": "The request could not be saved. Check the submitted data and try again."},
            400,
        )
    finally:
        cursor.close()
        connection.close()
    try:
        signal_automatic_scheduler()
    except mysql.connector.Error:
        current_app.logger.exception(
            "Could not signal the automatic scheduler; its polling will retry."
        )
    return redirect("/requests")


@bp.route("/requests/allocate/<int:request_id>", methods=["POST"])
def allocate_request_route(request_id):
    _, message = allocate_request(request_id)
    return _render_requests(message=message)


@bp.route("/requests/schedule", methods=["POST"])
def schedule_requests_route():
    algorithm = request.form.get("algorithm", "FCFS")
    if algorithm not in ALGORITHMS:
        return _render_requests(message="Select FCFS or Priority Scheduling.", status=400)
    try:
        result = schedule_pending_requests(algorithm)
    except mysql.connector.Error:
        return _render_requests(
            message=f"{algorithm} scheduling could not be completed because of a database error."
        )
    return _render_requests(message=result["message"])

import secrets

import mysql.connector
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from core import db
from core.reviewer_auth import (
    configured_disaster_officer,
    configured_reviewer,
    establish_role_session,
    operational_officer_required,
    verify_disaster_officer_credentials,
)
from core.validation import FormValidationError
from modules.emergency_requests.service import (
    DisasterNotActiveError,
    DuplicateRequestError,
    IdempotencyCollisionError,
    PriorAllocationRequiresEscalationError,
    create_manual_request_atomic,
    create_operational_request_atomic,
    get_disaster_resource_demand,
)
from modules.emergency_requests.validation import (
    REQUEST_PRIORITIES,
    validate_operational_generation,
    validate_request,
)
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
        if hasattr(connection, "start_transaction"):
            connection.start_transaction()
        create_manual_request_atomic(
            cursor,
            disaster_id=values["disaster_id"],
            resource_type_id=values["resource_type_id"],
            requested_quantity=values["requested_quantity"],
            priority=values["priority"],
        )
        connection.commit()
    except (DisasterNotActiveError, ValueError) as error:
        connection.rollback()
        return _render_requests(request.form, {"_form": str(error)}, 400)
    except DuplicateRequestError as error:
        connection.rollback()
        return _render_requests(request.form, {"_form": str(error)}, 409)
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


def _render_operational_generate(form_data=None, errors=None, status=200, message=None, demand=None):
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT disaster_id, name, type, severity FROM disasters WHERE status = 'ACTIVE' ORDER BY disaster_id DESC"
        )
        active_disasters = cursor.fetchall()
        cursor.execute("SELECT type_id, type_name, unit FROM resource_types ORDER BY type_name")
        resource_types = cursor.fetchall()
    finally:
        cursor.close()
        connection.close()

    submission_token = (form_data or {}).get("submission_token") or secrets.token_hex(16)
    return render_template(
        "emergency_requests/operational_generate.html",
        active_disasters=active_disasters,
        resource_types=resource_types,
        priorities=REQUEST_PRIORITIES,
        submission_token=submission_token,
        form_data=form_data or {},
        errors=errors or {},
        message=message,
        demand=demand,
        officer=session.get("disaster_officer"),
    ), status


@bp.route("/officer/login", methods=["GET", "POST"])
def officer_login():
    login_enabled = configured_disaster_officer() is not None
    if not login_enabled:
        return render_template(
            "emergency_requests/officer_login.html",
            error="Operational officer authentication is not configured in environment.",
            login_enabled=False,
        ), 503

    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        officer = verify_disaster_officer_credentials(username, password)
        if officer is None:
            return render_template(
                "emergency_requests/officer_login.html",
                error="Invalid disaster officer credentials.",
                login_enabled=True,
            ), 401
        establish_role_session(
            session,
            "disaster_officer",
            officer,
            "historical_reviewer",
            configured_reviewer(),
        )
        return redirect(url_for("emergency_requests.operational_generate_page"))

    return render_template(
        "emergency_requests/officer_login.html",
        error=None,
        login_enabled=True,
    )


@bp.route("/officer/logout", methods=["POST"])
@operational_officer_required
def officer_logout():
    session.pop("disaster_officer", None)
    return redirect(url_for("emergency_requests.officer_login"))


@bp.route("/requests/operational-generate", methods=["GET"])
@operational_officer_required
def operational_generate_page():
    disaster_id = request.args.get("disaster_id")
    resource_type_id = request.args.get("resource_type_id")
    demand = None
    if disaster_id and resource_type_id and disaster_id.isdigit() and resource_type_id.isdigit():
        connection = db.get_db_connection()
        cursor = connection.cursor()
        try:
            demand = get_disaster_resource_demand(cursor, int(disaster_id), int(resource_type_id))
        finally:
            cursor.close()
            connection.close()

    return _render_operational_generate(
        form_data={"disaster_id": disaster_id, "resource_type_id": resource_type_id},
        demand=demand,
    )


@bp.route("/requests/operational-generate", methods=["POST"])
@operational_officer_required
def operational_generate_submit():
    disaster_id = request.form.get("disaster_id", "").strip()
    resource_type_id = request.form.get("resource_type_id", "").strip()
    has_prior = False
    demand = None

    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        if disaster_id.isdigit() and resource_type_id.isdigit():
            demand = get_disaster_resource_demand(cursor, int(disaster_id), int(resource_type_id))
            has_prior = demand["has_prior_allocation"]
    finally:
        cursor.close()
        connection.close()

    try:
        values = validate_operational_generation(request.form, has_prior_allocation=has_prior)
    except FormValidationError as error:
        return _render_operational_generate(request.form, error.errors, 400, demand=demand)

    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        if hasattr(connection, "start_transaction"):
            connection.start_transaction()
        create_operational_request_atomic(
            cursor,
            disaster_id=values["disaster_id"],
            resource_type_id=values["resource_type_id"],
            requested_quantity=values["requested_quantity"],
            priority=values["priority"],
            justification=values["justification"],
            submission_token=values["submission_token"],
            officer_name=session["disaster_officer"],
            escalation_acknowledged=values["escalation_acknowledged"],
            escalation_justification=values["escalation_justification"],
        )
        connection.commit()
    except (DisasterNotActiveError, ValueError) as error:
        connection.rollback()
        return _render_operational_generate(request.form, {"_form": str(error)}, 400, demand=demand)
    except (DuplicateRequestError, IdempotencyCollisionError, PriorAllocationRequiresEscalationError) as error:
        connection.rollback()
        return _render_operational_generate(request.form, {"_form": str(error)}, 409, demand=demand)
    except mysql.connector.Error:
        connection.rollback()
        return _render_operational_generate(
            request.form,
            {"_form": "The operational request could not be saved due to a database error."},
            400,
            demand=demand,
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
@operational_officer_required
def allocate_request_route(request_id):
    _, message = allocate_request(request_id)
    return _render_requests(message=message)


@bp.route("/requests/schedule", methods=["POST"])
@operational_officer_required
def schedule_requests_route():
    algorithm = request.form.get("algorithm", "FCFS")
    if algorithm not in ALGORITHMS:
        return _render_requests(
            message="Select FCFS, Priority Scheduling, or Round Robin.",
            status=400,
        )
    try:
        result = schedule_pending_requests(algorithm)
    except mysql.connector.Error:
        return _render_requests(
            message=f"{algorithm} scheduling could not be completed because of a database error."
        )
    return _render_requests(message=result["message"])

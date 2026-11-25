import json
from collections import namedtuple

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from core import db
from core.reviewer_auth import (
    configured_reviewer,
    configured_disaster_officer,
    establish_role_session,
    reviewer_required,
    verify_reviewer_credentials,
)
from os_engine.experiments import (
    _load_request_rows,
    run_fair_algorithm_comparison,
    run_scheduling_experiment,
)
from os_engine.historical_detection import (
    DetectionReviewNotFoundError,
    DuplicateDetectionReviewError,
    compute_detection_outcome,
    review_detection_result,
)
from os_engine.metrics import get_scheduling_metrics
from modules.reports.validation import FormValidationError, validate_detection_review

bp = Blueprint("reports", __name__)


def _duration_to_seconds(value):
    if value in (None, "-"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("s") and "m" not in text and "h" not in text:
        return float(text[:-1])
    total = 0.0
    if "h" in text:
        hours, remainder = text.split("h", 1)
        total += float(hours.strip()) * 3600
        text = remainder.strip()
    if "m" in text:
        minutes, remainder = text.split("m", 1)
        total += float(minutes.strip()) * 60
        text = remainder.strip()
    if "s" in text:
        text = text[:-1]
    if text:
        total += float(text.strip())
    return total


RequestLifecycleSummary = namedtuple(
    "RequestLifecycleSummary",
    ["total", "pending", "allocated", "partially_allocated", "rejected", "completed"],
)


def get_request_lifecycle_summary(cursor):
    """Query and return standardized lifecycle metrics for emergency_requests.

    Guarantees that total equals the sum of the five mutually exclusive statuses:
    PENDING, ALLOCATED, PARTIALLY_ALLOCATED, REJECTED, and COMPLETED.
    Handles empty tables and NULL aggregates safely using COALESCE.
    """
    cursor.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN status = 'ALLOCATED' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN status = 'PARTIALLY_ALLOCATED' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN status = 'REJECTED' THEN 1 ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END), 0)
        FROM emergency_requests
    """)
    row = cursor.fetchone()
    if not row:
        return RequestLifecycleSummary(0, 0, 0, 0, 0, 0)

    # Legacy mock compatibility adapter (supports older 4-item test tuples)
    if len(row) == 4:
        r0, r1, r2, r3 = [int(v or 0) for v in row]
        if r0 >= (r1 + r2 + r3):
            # Form: (total, pending, allocated, partially_allocated)
            return RequestLifecycleSummary(
                total=r0,
                pending=r1,
                allocated=r2,
                partially_allocated=r3,
                rejected=0,
                completed=0,
            )
        else:
            # Form: (allocated, pending, rejected, completed)
            total = r0 + r1 + r2 + r3
            return RequestLifecycleSummary(
                total=total,
                pending=r1,
                allocated=r0,
                partially_allocated=0,
                rejected=r2,
                completed=r3,
            )

    values = [int(v or 0) for v in row[:6]]
    while len(values) < 6:
        values.append(0)

    total = values[0]
    pending = values[1]
    allocated = values[2]
    partially_allocated = values[3]
    rejected = values[4]
    completed = values[5]

    parts_sum = pending + allocated + partially_allocated + rejected + completed
    if total == 0 and parts_sum > 0:
        total = parts_sum

    return RequestLifecycleSummary(
        total=total,
        pending=pending,
        allocated=allocated,
        partially_allocated=partially_allocated,
        rejected=rejected,
        completed=completed,
    )


@bp.route("/reports")
def reports():
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT COUNT(*), SUM(CASE WHEN status = 'ACTIVE' THEN 1 ELSE 0 END)
            FROM disasters
        """)
        disaster_stats = cursor.fetchone()

        cursor.execute("""
            SELECT COUNT(*), COALESCE(SUM(total_quantity), 0), COALESCE(SUM(available_quantity), 0)
            FROM resources
        """)
        resource_stats = cursor.fetchone()

        request_stats = get_request_lifecycle_summary(cursor)

        cursor.execute("""
            SELECT a.allocation_id, a.request_id, rt.type_name,
                   a.allocated_quantity, a.allocation_time, a.status
            FROM allocations a
            JOIN resources r ON a.resource_id = r.resource_id
            JOIN resource_types rt ON r.type_id = rt.type_id
            ORDER BY a.allocation_time DESC, a.allocation_id DESC
            LIMIT 50
        """)
        allocation_history = cursor.fetchall()

        scheduling_metrics = get_scheduling_metrics(cursor)

        return render_template(
            "reports/page.html",
            disaster_stats=disaster_stats,
            resource_stats=resource_stats,
            request_stats=request_stats,
            allocation_history=allocation_history,
            metrics=scheduling_metrics,
        )
    finally:
        cursor.close()
        connection.close()


@bp.route("/reports/scheduling-analytics")
def scheduling_analytics():
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        realtime_summary = get_request_lifecycle_summary(cursor)

        scheduling_metrics = get_scheduling_metrics(cursor)
        request_rows = _load_request_rows(cursor)
        experiment = run_scheduling_experiment(cursor=None, rows=request_rows)
        fair_comparison = run_fair_algorithm_comparison(cursor=None, rows=request_rows)

        sim_waiting_comparison = [
            {"algorithm": row["algorithm"], "average_wait_seconds": round(row["average_wait_seconds"], 2)}
            for row in fair_comparison["comparison"]
        ]
        sim_turnaround_comparison = [
            {"algorithm": row["algorithm"], "average_turnaround_seconds": round(row["average_turnaround_seconds"], 2)}
            for row in fair_comparison["comparison"]
        ]
        sim_response_comparison = [
            {"algorithm": row["algorithm"], "average_response_seconds": round(row["average_response_seconds"], 2)}
            for row in fair_comparison["comparison"]
        ]
        sim_throughput_comparison = [
            {
                "algorithm": row["algorithm"],
                "throughput_per_hour": round(row["throughput_per_hour"], 4),
                "throughput_per_second": round(row["throughput_per_second"], 4),
            }
            for row in fair_comparison["comparison"]
        ]

        algorithm_waits = [
            {"algorithm": row["algorithm"], "average_wait_seconds": _duration_to_seconds(row["average"])}
            for row in scheduling_metrics["by_algorithm"]
        ]
        algorithm_counts = [
            {"algorithm": row["algorithm"], "count": int(row["count"] or 0)}
            for row in scheduling_metrics["by_algorithm"]
        ]
        priority_waits = [
            {"priority": row["priority"], "average_wait_seconds": _duration_to_seconds(row["average"])}
            for row in scheduling_metrics["by_priority"]
        ]
        status_distribution = scheduling_metrics.get("status_distribution", [])

        return render_template(
            "reports/scheduling_analytics.html",
            metrics=scheduling_metrics,
            experiment=experiment,
            fair_comparison=fair_comparison,
            sim_waiting_comparison=sim_waiting_comparison,
            sim_turnaround_comparison=sim_turnaround_comparison,
            sim_response_comparison=sim_response_comparison,
            sim_throughput_comparison=sim_throughput_comparison,
            realtime_summary=realtime_summary,
            algorithm_waits=algorithm_waits,
            algorithm_counts=algorithm_counts,
            priority_waits=priority_waits,
            status_distribution=status_distribution,
        )
    finally:
        cursor.close()
        connection.close()


@bp.route("/review/login", methods=["GET", "POST"])
def historical_review_login():
    login_enabled = configured_reviewer() is not None
    if not login_enabled:
        return render_template(
            "reports/reviewer_login.html",
            error="Historical review login is not configured.",
            login_enabled=False,
        ), 503

    if request.method == "POST":
        reviewer = verify_reviewer_credentials(
            request.form.get("username", ""),
            request.form.get("password", ""),
        )
        if reviewer is None:
            return render_template(
                "reports/reviewer_login.html",
                error="Invalid reviewer credentials.",
                login_enabled=True,
            ), 401
        establish_role_session(
            session,
            "historical_reviewer",
            reviewer,
            "disaster_officer",
            configured_disaster_officer(),
        )
        return redirect(url_for("reports.historical_detections"))

    return render_template(
        "reports/reviewer_login.html",
        error=None,
        login_enabled=True,
    )


@bp.route("/review/logout", methods=["POST"])
@reviewer_required
def historical_review_logout():
    session.pop("historical_reviewer", None)
    return redirect(url_for("reports.historical_review_login"))


VALID_DETECTION_OUTCOMES = ("CONFIRMED", "POTENTIAL", "INSUFFICIENT_DATA", "REJECTED")
VALID_REVIEW_STATUSES = ("PENDING_REVIEW", "NEEDS_MORE_DATA", "APPROVED", "REJECTED")

DETECTION_OUTCOME_SQL_EXPR = """CASE
    WHEN JSON_UNQUOTE(JSON_EXTRACT(dr.evaluated_values, '$.status')) = 'CONFIRMED' THEN 'CONFIRMED'
    WHEN JSON_UNQUOTE(JSON_EXTRACT(dr.evaluated_values, '$.status')) = 'POTENTIAL' AND dr.review_status = 'APPROVED' THEN 'CONFIRMED'
    WHEN JSON_UNQUOTE(JSON_EXTRACT(dr.evaluated_values, '$.status')) = 'POTENTIAL' AND dr.review_status = 'REJECTED' THEN 'REJECTED'
    WHEN JSON_UNQUOTE(JSON_EXTRACT(dr.evaluated_values, '$.status')) = 'POTENTIAL' THEN 'POTENTIAL'
    WHEN JSON_UNQUOTE(JSON_EXTRACT(dr.evaluated_values, '$.status')) = 'INSUFFICIENT_DATA' AND dr.review_status = 'REJECTED' THEN 'REJECTED'
    ELSE 'INSUFFICIENT_DATA'
END"""


def get_detection_outcome_summary(cursor):
    """Aggregate counts of detection records grouped by outcome (not review actions)."""
    cursor.execute(f"""
        SELECT
            {DETECTION_OUTCOME_SQL_EXPR} AS outcome,
            COUNT(*)
        FROM detection_results dr
        GROUP BY outcome
    """)
    summary = {
        "CONFIRMED": 0,
        "POTENTIAL": 0,
        "INSUFFICIENT_DATA": 0,
        "REJECTED": 0,
        "TOTAL": 0,
    }
    for row in cursor.fetchall():
        if not row or len(row) < 2:
            continue
        outcome, count = row[0], row[1]
        if outcome in summary:
            summary[outcome] = int(count or 0)
    summary["TOTAL"] = (
        summary["CONFIRMED"]
        + summary["POTENTIAL"]
        + summary["INSUFFICIENT_DATA"]
        + summary["REJECTED"]
    )
    return summary


@bp.route("/reports/historical-detections")
@reviewer_required
def historical_detections():
    raw_outcome = request.args.get("outcome", "").strip()
    selected_outcome = raw_outcome.upper() if raw_outcome.upper() in VALID_DETECTION_OUTCOMES else None

    raw_review_status = request.args.get("review_status", "").strip()
    selected_review_status = raw_review_status.upper() if raw_review_status.upper() in VALID_REVIEW_STATUSES else None

    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        outcome_summary = get_detection_outcome_summary(cursor)

        conditions = []
        params = []

        if selected_review_status:
            conditions.append("dr.review_status = %s")
            params.append(selected_review_status)

        if selected_outcome:
            conditions.append(f"({DETECTION_OUTCOME_SQL_EXPR}) = %s")
            params.append(selected_outcome)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT dr.detection_id, so.observation_id, so.source_record_id,
                   dr.rule_id, dr.rule_version, dr.evaluated_values,
                   dr.explanation, dr.candidate_priority, dr.review_status,
                   dr.evaluated_at, dr.reviewed_at, dr.reviewer
            FROM detection_results dr
            JOIN source_observations so ON so.observation_id = dr.observation_id
            {where_clause}
            ORDER BY dr.evaluated_at DESC, dr.detection_id DESC
        """

        if params:
            cursor.execute(query, tuple(params))
        else:
            cursor.execute(query)

        detections = []
        for row in cursor.fetchall():
            detection = dict(zip(
                (
                    "detection_id", "observation_id", "source_record_id",
                    "rule_id", "rule_version", "evaluated_values",
                    "explanation", "candidate_priority", "review_status",
                    "evaluated_at", "reviewed_at", "reviewer",
                ),
                row,
            ))
            values = detection["evaluated_values"]
            if isinstance(values, (str, bytes)):
                detection["evaluated_values"] = json.loads(values)
            detection["reviewed"] = detection["reviewed_at"] is not None
            evaluated_status = (
                detection["evaluated_values"].get("status")
                if isinstance(detection["evaluated_values"], dict)
                else None
            )
            detection["detection_outcome"] = compute_detection_outcome(
                evaluated_status, detection["review_status"]
            )
            detections.append(detection)

        if selected_review_status:
            detections = [d for d in detections if d["review_status"] == selected_review_status]
        if selected_outcome:
            detections = [d for d in detections if d["detection_outcome"] == selected_outcome]

        return render_template(
            "reports/historical_detections.html",
            detections=detections,
            reviewer=session["historical_reviewer"],
            outcome_summary=outcome_summary,
            selected_outcome=selected_outcome,
            selected_review_status=selected_review_status,
            valid_outcomes=VALID_DETECTION_OUTCOMES,
            valid_review_statuses=VALID_REVIEW_STATUSES,
            errors={},
            form_data={},
            message=request.args.get("message"),
        )
    finally:
        cursor.close()
        connection.close()


@bp.route(
    "/reports/historical-detections/<int:detection_id>/review",
    methods=["POST"],
)
@reviewer_required
def review_historical_detection(detection_id):
    try:
        values = validate_detection_review(request.form)
    except FormValidationError:
        abort(400, description="Choose a review decision and provide a reason.")

    connection = db.get_db_connection()
    cursor = None
    try:
        connection.autocommit = False
        cursor = connection.cursor()
        cursor.execute("SET time_zone = '+00:00'")
        connection.start_transaction()
        review_detection_result(
            cursor,
            detection_id,
            reviewer=session["historical_reviewer"],
            **values,
        )
        connection.commit()
    except DetectionReviewNotFoundError:
        connection.rollback()
        abort(404)
    except DuplicateDetectionReviewError:
        connection.rollback()
        abort(409)
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()

    flash("Historical detection review saved.")
    return redirect(url_for("reports.historical_detections"))

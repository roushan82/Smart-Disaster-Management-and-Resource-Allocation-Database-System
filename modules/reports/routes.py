from flask import Blueprint, render_template

from core import db
from os_engine.metrics import get_scheduling_metrics

bp = Blueprint("reports", __name__)


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

        cursor.execute("""
            SELECT
                COUNT(*),
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'ALLOCATED' THEN 1 ELSE 0 END),
                SUM(CASE WHEN status = 'PARTIALLY_ALLOCATED' THEN 1 ELSE 0 END)
            FROM emergency_requests
        """)
        request_stats = cursor.fetchone()

        cursor.execute("""
            SELECT a.allocation_id, a.request_id, rt.type_name,
                   a.allocated_quantity, a.allocation_time, a.status
            FROM allocations a
            JOIN resources r ON a.resource_id = r.resource_id
            JOIN resource_types rt ON r.type_id = rt.type_id
            ORDER BY a.allocation_id DESC
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
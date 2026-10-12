import mysql.connector

from core import db
from os_engine.allocator import allocate_request
from os_engine.policies import (
    AGING_THRESHOLD_SECONDS,
    ALGORITHMS,
    order_by_clause,
    select_automatic_algorithm,
)

SCHEDULER_LOCK_NAME = "smart_disaster_request_scheduler"


def signal_automatic_scheduler():
    """Tell the background worker that a new request arrived (bumps a counter)."""
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            INSERT INTO scheduler_control (control_id, generation)
            VALUES (1, 1)
            ON DUPLICATE KEY UPDATE generation = generation + 1
        """)
        connection.commit()
    except mysql.connector.Error:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def get_automatic_schedule_preview(cursor):
    """What the automatic policy would do right now (read-only, for the UI)."""
    algorithm, reason = select_automatic_algorithm(cursor)
    order_clause, parameters = order_by_clause(algorithm, alias="er")
    cursor.execute("""
        SELECT er.request_id, d.name, er.priority, er.request_time,
               TIMESTAMPDIFF(SECOND, er.request_time, CURRENT_TIMESTAMP)
        FROM emergency_requests er
        JOIN disasters d ON d.disaster_id = er.disaster_id
        WHERE er.status = 'PENDING'
        ORDER BY """ + order_clause, parameters)
    queue = [
        {
            "request_id": row[0],
            "disaster_name": row[1],
            "priority": row[2],
            "request_time": row[3],
            "waiting_seconds": row[4],
            "aged": (
                algorithm == "PRIORITY"
                and row[4] is not None
                and row[4] >= AGING_THRESHOLD_SECONDS
            ),
        }
        for row in cursor.fetchall()
    ]
    if not queue:
        algorithm = "IDLE"
        reason = "No pending requests are waiting for scheduling."
    return {
        "algorithm": algorithm,
        "reason": reason,
        "queue": queue,
        "aging_threshold_seconds": AGING_THRESHOLD_SECONDS,
    }


def schedule_pending_requests_fcfs():
    return schedule_pending_requests("FCFS", reason="Manual FCFS fallback")


def schedule_pending_requests_priority():
    return schedule_pending_requests("PRIORITY", reason="Manual Priority fallback")


def schedule_automatically():
    return schedule_pending_requests(None, automatic=True)


def schedule_pending_requests(algorithm, automatic=False, reason=None):
    if algorithm is not None and algorithm not in ALGORITHMS:
        raise ValueError("Unsupported scheduling algorithm.")

    connection = db.get_db_connection()
    cursor = connection.cursor()
    lock_acquired = False
    run_id = None

    try:
        cursor.execute("SELECT GET_LOCK(%s, 0)", (SCHEDULER_LOCK_NAME,))
        lock_acquired = cursor.fetchone()[0] == 1
        if not lock_acquired:
            return {
                "run_id": None,
                "results": [],
                "message": "Another scheduling run is already in progress.",
            }

        if automatic:
            algorithm, reason = select_automatic_algorithm(cursor)

        order_clause, order_parameters = order_by_clause(algorithm)

        cursor.execute("""
            INSERT INTO scheduling_runs (algorithm, reason, automatic, status)
            VALUES (%s, %s, %s, 'RUNNING')
        """, (algorithm, reason or "Manual scheduler selection.", automatic))
        run_id = cursor.lastrowid
        connection.commit()

        cursor.execute("""
            SELECT request_id
            FROM emergency_requests
            WHERE status = 'PENDING'
            ORDER BY """ + order_clause,
            order_parameters,
        )
        request_ids = [row[0] for row in cursor.fetchall()]
        results = []

        for queue_position, request_id in enumerate(request_ids, start=1):
            cursor.execute("""
                INSERT INTO scheduling_run_items
                    (run_id, queue_position, request_id, request_status,
                     outcome, result_message)
                VALUES (%s, %s, %s, 'PENDING', 'PROCESSING', 'Allocation in progress.')
            """, (run_id, queue_position, request_id))
            connection.commit()

            success, result_message = allocate_request(request_id)

            cursor.execute(
                "SELECT status FROM emergency_requests WHERE request_id = %s",
                (request_id,),
            )
            request_row = cursor.fetchone()
            request_status = request_row[0] if request_row else "UNKNOWN"
            if success:
                outcome = "ALLOCATED"
            elif request_status != "PENDING":
                outcome = "SKIPPED"
            else:
                outcome = "FAILED"

            cursor.execute("""
                UPDATE scheduling_run_items
                SET request_status = %s, outcome = %s, result_message = %s,
                    executed_at = CURRENT_TIMESTAMP
                WHERE run_id = %s AND queue_position = %s
            """, (request_status, outcome, result_message, run_id, queue_position))
            connection.commit()
            results.append({
                "queue_position": queue_position,
                "request_id": request_id,
                "request_status": request_status,
                "outcome": outcome,
                "result_message": result_message,
            })

        cursor.execute("""
            UPDATE scheduling_runs
            SET status = 'COMPLETED', finished_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
        """, (run_id,))
        connection.commit()

        if not results:
            message = f"No pending requests to schedule with {algorithm}."
        else:
            message = f"{algorithm} run {run_id} processed {len(results)} pending request(s)."
        return {
            "run_id": run_id,
            "algorithm": algorithm,
            "reason": reason,
            "results": results,
            "message": message,
        }

    except mysql.connector.Error:
        connection.rollback()
        if run_id is not None:
            try:
                cursor.execute("""
                    UPDATE scheduling_runs
                    SET status = 'FAILED', finished_at = CURRENT_TIMESTAMP
                    WHERE run_id = %s
                """, (run_id,))
                connection.commit()
            except mysql.connector.Error:
                connection.rollback()
        raise

    finally:
        if lock_acquired:
            try:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (SCHEDULER_LOCK_NAME,))
                cursor.fetchone()
            except mysql.connector.Error:
                pass
        cursor.close()
        connection.close()

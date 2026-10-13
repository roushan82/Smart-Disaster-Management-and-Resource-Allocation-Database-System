from os_engine.policies import AGING_THRESHOLD_SECONDS

PRIORITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

_BY_ALGORITHM_SQL = """
    SELECT t.algorithm, COUNT(*), AVG(t.wait_seconds),
           MIN(t.wait_seconds), MAX(t.wait_seconds)
    FROM (
        SELECT COALESCE(sr.algorithm, 'MANUAL') AS algorithm,
               GREATEST(TIMESTAMPDIFF(SECOND, er.request_time, a.allocation_time), 0)
                   AS wait_seconds
        FROM allocations a
        JOIN emergency_requests er ON er.request_id = a.request_id
        LEFT JOIN scheduling_run_items sri
               ON sri.request_id = a.request_id AND sri.outcome = 'ALLOCATED'
        LEFT JOIN scheduling_runs sr ON sr.run_id = sri.run_id
    ) t
    GROUP BY t.algorithm
    ORDER BY t.algorithm
"""

_BY_PRIORITY_SQL = """
    SELECT er.priority, COUNT(*),
           AVG(GREATEST(TIMESTAMPDIFF(SECOND, er.request_time, a.allocation_time), 0)),
           MAX(GREATEST(TIMESTAMPDIFF(SECOND, er.request_time, a.allocation_time), 0))
    FROM allocations a
    JOIN emergency_requests er ON er.request_id = a.request_id
    GROUP BY er.priority
"""

_PENDING_SQL = """
    SELECT COUNT(*),
           AVG(TIMESTAMPDIFF(SECOND, request_time, CURRENT_TIMESTAMP)),
           MAX(TIMESTAMPDIFF(SECOND, request_time, CURRENT_TIMESTAMP)),
           SUM(CASE WHEN TIMESTAMPDIFF(SECOND, request_time, CURRENT_TIMESTAMP) >= %s
                    THEN 1 ELSE 0 END)
    FROM emergency_requests
    WHERE status = 'PENDING'
"""


def format_duration(seconds):
    """1 -> '1s', 125 -> '2m 05s', 7260 -> '2h 01m'; None -> '-'."""
    if seconds is None:
        return "-"
    total = int(float(seconds) + 0.5)  # round half up
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m {total % 60:02d}s"
    return f"{total // 3600}h {(total % 3600) // 60:02d}m"


def get_scheduling_metrics(cursor):
    cursor.execute(_BY_ALGORITHM_SQL)
    by_algorithm = [
        {
            "algorithm": algorithm,
            "count": count,
            "average": format_duration(average),
            "shortest": format_duration(shortest),
            "longest": format_duration(longest),
        }
        for algorithm, count, average, shortest, longest in cursor.fetchall()
    ]

    cursor.execute(_BY_PRIORITY_SQL)
    priority_rows = {
        priority: (count, average, longest)
        for priority, count, average, longest in cursor.fetchall()
    }
    by_priority = [
        {
            "priority": priority,
            "count": priority_rows[priority][0],
            "average": format_duration(priority_rows[priority][1]),
            "longest": format_duration(priority_rows[priority][2]),
        }
        for priority in PRIORITY_ORDER
        if priority in priority_rows
    ]

    cursor.execute(_PENDING_SQL, (AGING_THRESHOLD_SECONDS,))
    count, average, longest, aged = cursor.fetchone()
    pending = {
        "count": count or 0,
        "average": format_duration(average),
        "longest": format_duration(longest),
        "aged": int(aged or 0),
    }

    return {
        "by_algorithm": by_algorithm,
        "by_priority": by_priority,
        "pending": pending,
        "aging_threshold_minutes": AGING_THRESHOLD_SECONDS // 60,
    }
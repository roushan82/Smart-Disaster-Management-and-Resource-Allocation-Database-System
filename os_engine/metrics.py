from os_engine.policies import AGING_THRESHOLD_SECONDS

PRIORITY_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")

_BY_ALGORITHM_SQL = """
    SELECT t.algorithm, COUNT(*), AVG(t.wait_seconds),
           MIN(t.wait_seconds), MAX(t.wait_seconds)
    FROM (
        SELECT COALESCE((
                   SELECT sr.algorithm
                   FROM scheduling_run_items sri
                   JOIN scheduling_runs sr ON sr.run_id = sri.run_id
                   WHERE sri.request_id = a.request_id
                     AND sri.outcome = 'ALLOCATED'
                   ORDER BY
                       CASE WHEN sri.executed_at IS NULL THEN 1 ELSE 0 END,
                       ABS(TIMESTAMPDIFF(MICROSECOND, a.allocation_time, sri.executed_at)),
                       CASE WHEN sri.executed_at >= a.allocation_time THEN 0 ELSE 1 END,
                       sr.run_id ASC
                   LIMIT 1
               ), 'MANUAL') AS algorithm,
               TIMESTAMPDIFF(SECOND, er.request_time, a.allocation_time)
                   AS wait_seconds
        FROM allocations a
        JOIN emergency_requests er ON er.request_id = a.request_id
        WHERE a.status = 'ALLOCATED'
          AND er.request_time IS NOT NULL
          AND a.allocation_time IS NOT NULL
          AND a.allocation_time >= er.request_time
    ) t
    GROUP BY t.algorithm
    ORDER BY t.algorithm
"""

_BY_PRIORITY_SQL = """
    SELECT er.priority, COUNT(*),
           AVG(TIMESTAMPDIFF(SECOND, er.request_time, a.allocation_time)),
           MAX(TIMESTAMPDIFF(SECOND, er.request_time, a.allocation_time))
    FROM allocations a
    JOIN emergency_requests er ON er.request_id = a.request_id
    WHERE a.status = 'ALLOCATED'
      AND er.request_time IS NOT NULL
      AND a.allocation_time IS NOT NULL
      AND a.allocation_time >= er.request_time
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
      AND request_time IS NOT NULL
"""

_STATUS_DISTRIBUTION_SQL = """
    SELECT status, COUNT(*)
    FROM emergency_requests
    GROUP BY status
    ORDER BY status
"""

_SCHEDULER_RUNS_SQL = """
    SELECT sr.run_id, sr.algorithm, sr.reason, sr.automatic, sr.status,
           sr.started_at, sr.finished_at, sr.rr_start_request_id,
           COUNT(CASE
               WHEN sri.outcome IN ('ALLOCATED', 'FAILED', 'SKIPPED') THEN 1
           END) AS processed_count
    FROM scheduling_runs sr
    LEFT JOIN scheduling_run_items sri ON sri.run_id = sr.run_id
    GROUP BY sr.run_id, sr.algorithm, sr.reason, sr.automatic, sr.status,
             sr.started_at, sr.finished_at, sr.rr_start_request_id
    ORDER BY sr.started_at DESC
    LIMIT 20
"""

_REQUEST_ARRIVALS_SQL = """
    SELECT DATE(request_time) AS day, COUNT(*) AS request_count
    FROM emergency_requests
    WHERE request_time IS NOT NULL
    GROUP BY DATE(request_time)
    ORDER BY day ASC
"""


def _query_all(cursor, sql, params=()):
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    except StopIteration:
        return []


def _query_one(cursor, sql, params=()):
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    except StopIteration:
        return None


def _normalize_row(row, expected_size):
    values = tuple(row or ())
    if len(values) < expected_size:
        values += (None,) * (expected_size - len(values))
    return values[:expected_size]


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
    by_algorithm_rows = _query_all(cursor, _BY_ALGORITHM_SQL)
    by_algorithm = []
    for row in by_algorithm_rows:
        algorithm, count, average, shortest, longest = _normalize_row(row, 5)
        by_algorithm.append({
            "algorithm": algorithm,
            "count": count or 0,
            "average": format_duration(average),
            "shortest": format_duration(shortest),
            "longest": format_duration(longest),
        })

    priority_rows = _query_all(cursor, _BY_PRIORITY_SQL)
    priority_lookup = {}
    for row in priority_rows:
        priority, count, average, longest = _normalize_row(row, 4)
        if priority is None:
            continue
        priority_lookup[priority] = (count or 0, average, longest)
    by_priority = [
        {
            "priority": priority,
            "count": priority_lookup[priority][0],
            "average": format_duration(priority_lookup[priority][1]),
            "longest": format_duration(priority_lookup[priority][2]),
        }
        for priority in PRIORITY_ORDER
        if priority in priority_lookup
    ]

    status_rows = _query_all(cursor, _STATUS_DISTRIBUTION_SQL)
    status_distribution = []
    for row in status_rows:
        status, count = _normalize_row(row, 2)
        if status is None:
            continue
        status_distribution.append({"status": status, "count": count or 0})

    scheduler_runs = _query_all(cursor, _SCHEDULER_RUNS_SQL)
    scheduler_history = []
    for row in scheduler_runs:
        (
            run_id, algorithm, reason, automatic, status, started_at,
            finished_at, rr_start_request_id, processed_count,
        ) = _normalize_row(row, 9)
        scheduler_history.append({
            "run_id": run_id,
            "algorithm": algorithm,
            "reason": reason,
            "automatic": bool(automatic),
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "rr_start_request_id": rr_start_request_id,
            "processed_count": processed_count or 0,
        })

    arrival_rows = _query_all(cursor, _REQUEST_ARRIVALS_SQL)
    request_arrivals = []
    for row in arrival_rows:
        day, request_count = _normalize_row(row, 2)
        request_arrivals.append({
            "day": day,
            "request_count": request_count or 0,
        })

    pending_row = _query_one(cursor, _PENDING_SQL, (AGING_THRESHOLD_SECONDS,)) or (0, None, None, 0)
    count, average, longest, aged = _normalize_row(pending_row, 4)
    pending = {
        "count": count or 0,
        "average": format_duration(average),
        "longest": format_duration(longest),
        "aged": int(aged or 0),
        "aged_count": int(aged or 0),
    }
    aging_breakdown = {
        "total_pending": count or 0,
        "aged": int(aged or 0),
        "non_aged": max((count or 0) - int(aged or 0), 0),
    }

    return {
        "by_algorithm": by_algorithm,
        "by_priority": by_priority,
        "pending": pending,
        "aging_threshold_minutes": AGING_THRESHOLD_SECONDS // 60,
        "status_distribution": status_distribution,
        "scheduler_history": scheduler_history,
        "request_arrivals": request_arrivals,
        "aging_breakdown": aging_breakdown,
    }
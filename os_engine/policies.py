AGING_THRESHOLD_SECONDS = 900

ALGORITHMS = ("FCFS", "PRIORITY", "RR")
PRIORITY_VALUES = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def _normalize_algorithm_name(algorithm):
    """Normalize scheduler names from request data or UI input."""
    if not isinstance(algorithm, str):
        raise ValueError("Unsupported scheduling algorithm.")
    normalized = algorithm.strip().upper()
    if normalized not in ALGORITHMS:
        raise ValueError("Unsupported scheduling algorithm.")
    return normalized


def _normalize_alias(alias):
    """Preserve the table alias while tolerating a trailing dot."""
    if alias is None:
        return ""
    text = str(alias).strip()
    return f"{text.rstrip('.')}." if text else ""


def order_by_clause(algorithm, alias="", rr_cursor=None):
    """Return (sql_order_by, params) for the pending-request queue."""
    algorithm = _normalize_algorithm_name(algorithm)
    prefix = _normalize_alias(alias)
    arrival = f"{prefix}request_time ASC, {prefix}request_id ASC"
    if algorithm == "FCFS":
        return arrival, ()
    if algorithm == "PRIORITY":
        clause = (
            f"CASE WHEN TIMESTAMPDIFF(SECOND, {prefix}request_time, CURRENT_TIMESTAMP) "
            f">= %s THEN 0 ELSE CASE {prefix}priority "
            "WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 "
            "WHEN 'MEDIUM' THEN 3 WHEN 'LOW' THEN 4 END END, "
            + arrival
        )
        return clause, (AGING_THRESHOLD_SECONDS,)
    if algorithm == "RR":
        clause = (
            f"CASE WHEN {prefix}request_id > %s THEN 0 ELSE 1 END, "
            f"{prefix}request_id ASC"
        )
        return clause, (rr_cursor,)
    raise ValueError("Unsupported scheduling algorithm.")


def select_automatic_algorithm(cursor):
    """Rule: any pending CRITICAL request -> PRIORITY, otherwise FCFS."""
    if cursor is None:
        return "FCFS", "Automatic policy: no pending CRITICAL requests; using FCFS."

    cursor.execute("""
        SELECT COUNT(*)
        FROM emergency_requests
        WHERE status = 'PENDING' AND priority = 'CRITICAL'
    """)
    row = cursor.fetchone()
    if row and row[0]:
        return "PRIORITY", (
            "Automatic policy: pending CRITICAL request(s) detected; "
            f"requests waiting at least {AGING_THRESHOLD_SECONDS} seconds "
            "are promoted ahead of standard priority order."
        )
    return "FCFS", "Automatic policy: no pending CRITICAL requests; using FCFS."

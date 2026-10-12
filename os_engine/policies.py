AGING_THRESHOLD_SECONDS = 900

ALGORITHMS = ("FCFS", "PRIORITY")
PRIORITY_VALUES = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def order_by_clause(algorithm, alias=""):
    """Return (sql_order_by, params) for the pending-request queue."""
    prefix = f"{alias}." if alias else ""
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
    raise ValueError("Unsupported scheduling algorithm.")


def select_automatic_algorithm(cursor):
    """Rule: any pending CRITICAL request -> PRIORITY, otherwise FCFS."""
    cursor.execute("""
        SELECT COUNT(*)
        FROM emergency_requests
        WHERE status = 'PENDING' AND priority = 'CRITICAL'
    """)
    if cursor.fetchone()[0]:
        return "PRIORITY", (
            "Automatic policy: pending CRITICAL request(s) detected; "
            f"requests waiting at least {AGING_THRESHOLD_SECONDS} seconds "
            "are promoted ahead of standard priority order."
        )
    return "FCFS", "Automatic policy: no pending CRITICAL requests; using FCFS."

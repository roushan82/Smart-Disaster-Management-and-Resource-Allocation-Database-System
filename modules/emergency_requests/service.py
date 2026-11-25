"""Operational and manual emergency request creation service with row-locked concurrency control."""

from modules.notifications.service import create_notification


class DisasterNotActiveError(ValueError):
    """Raised when an operation targets a non-existent or inactive disaster."""


class DuplicateRequestError(ValueError):
    """Raised when request creation violates duplicate prevention rules."""


class PriorAllocationRequiresEscalationError(ValueError):
    """Raised when prior allocations exist but escalation acknowledgement is missing."""


class IdempotencyCollisionError(ValueError):
    """Raised when a duplicate submission token is detected."""


def lock_and_verify_active_disaster(cursor, disaster_id):
    """Acquire an exclusive lock on the disaster row and verify it is ACTIVE."""
    cursor.execute(
        """
        SELECT disaster_id, name, status, location_id
        FROM disasters
        WHERE disaster_id = %s AND status = 'ACTIVE'
        FOR UPDATE
        """,
        (disaster_id,),
    )
    row = cursor.fetchone()
    if not row:
        raise DisasterNotActiveError("Select an existing active disaster.")
    return row


def get_disaster_resource_demand(cursor, disaster_id, resource_type_id):
    """Query current demand and allocations for a specific disaster and resource type."""
    cursor.execute(
        """
        SELECT er.request_id, er.status, er.requested_quantity, er.priority, er.request_time,
               COALESCE(SUM(CASE WHEN a.status = 'ALLOCATED' THEN a.allocated_quantity ELSE 0 END), 0) AS allocated_quantity,
               MAX(CASE WHEN a.status = 'ALLOCATED' THEN a.allocation_time ELSE NULL END) AS latest_allocation_time
        FROM emergency_requests er
        LEFT JOIN allocations a ON a.request_id = er.request_id
        WHERE er.disaster_id = %s AND er.resource_type_id = %s
        GROUP BY er.request_id, er.status, er.requested_quantity, er.priority, er.request_time
        ORDER BY er.request_time DESC, er.request_id DESC
        """,
        (disaster_id, resource_type_id),
    )
    rows = cursor.fetchall()
    pending = []
    allocated = []
    total_pending = 0
    total_allocated = 0

    for r in rows:
        item = {
            "request_id": r[0],
            "status": r[1],
            "requested_quantity": r[2],
            "priority": r[3],
            "request_time": r[4],
            "allocated_quantity": int(r[5] or 0),
            "latest_allocation_time": r[6],
        }
        if r[1] == "PENDING":
            pending.append(item)
            total_pending += r[2]
        elif r[1] in ("ALLOCATED", "PARTIALLY_ALLOCATED"):
            allocated.append(item)
            total_allocated += int(r[5] or 0)

    return {
        "rows": rows,
        "pending_requests": pending,
        "allocated_requests": allocated,
        "total_pending_quantity": total_pending,
        "total_allocated_quantity": total_allocated,
        "has_pending": len(pending) > 0,
        "has_prior_allocation": total_allocated > 0,
    }


def create_manual_request_atomic(
    cursor,
    *,
    disaster_id,
    resource_type_id,
    requested_quantity,
    priority,
):
    """Create a manual request serialized by disaster-row locking.

    Manual duplicate policy: Rejects only if an identical unallocated request
    (same disaster, resource type, quantity, and priority) is already PENDING.
    Allows differing quantities or priorities to preserve existing manual flexibility.
    """
    lock_and_verify_active_disaster(cursor, disaster_id)

    cursor.execute(
        "SELECT 1 FROM resource_types WHERE type_id = %s",
        (resource_type_id,),
    )
    if not cursor.fetchone():
        raise ValueError("Select an existing resource type.")

    cursor.execute(
        """
        SELECT request_id
        FROM emergency_requests
        WHERE disaster_id = %s
          AND resource_type_id = %s
          AND requested_quantity = %s
          AND priority = %s
          AND status = 'PENDING'
        """,
        (disaster_id, resource_type_id, requested_quantity, priority),
    )
    existing_identical = cursor.fetchall()
    if existing_identical:
        raise DuplicateRequestError(
            f"An identical unallocated request (#{existing_identical[0][0]}) for "
            f"{requested_quantity} unit(s) with {priority} priority is already PENDING."
        )

    cursor.execute(
        """
        INSERT INTO emergency_requests
            (disaster_id, resource_type_id, requested_quantity, priority, status)
        VALUES (%s, %s, %s, %s, 'PENDING')
        """,
        (disaster_id, resource_type_id, requested_quantity, priority),
    )
    request_id = cursor.lastrowid

    create_notification(
        cursor,
        event_key=f"request-created:{request_id}",
        event_type="NEW_REQUEST",
        message=(
            f"New emergency request #{request_id} was created "
            f"with {priority} priority."
        ),
        related_request_id=request_id,
        related_disaster_id=disaster_id,
    )
    return request_id


def create_operational_request_atomic(
    cursor,
    *,
    disaster_id,
    resource_type_id,
    requested_quantity,
    priority,
    justification,
    submission_token,
    officer_name,
    escalation_acknowledged=False,
    escalation_justification="",
):
    """Create an officer-assisted operational request serialized by disaster-row locking.

    Strict duplicate policy:
    1. Blocks if ANY request for this resource on this disaster is currently PENDING.
    2. Blocks if ANY request was previously ALLOCATED, unless escalation acknowledgement
       and justification are explicitly provided.
    3. Blocks duplicate form submission tokens via notification idempotency key.
    """
    disaster = lock_and_verify_active_disaster(cursor, disaster_id)
    disaster_name = disaster[1] if len(disaster) > 1 else f"#{disaster_id}"

    cursor.execute(
        "SELECT type_name FROM resource_types WHERE type_id = %s",
        (resource_type_id,),
    )
    res_row = cursor.fetchone()
    if not res_row:
        raise ValueError("Select an existing resource type.")

    demand = get_disaster_resource_demand(cursor, disaster_id, resource_type_id)

    if demand["has_pending"]:
        pending_ids = ", ".join(str(r["request_id"]) for r in demand["pending_requests"])
        raise DuplicateRequestError(
            f"An unallocated request ({pending_ids}) for this resource is currently PENDING. "
            "New requests are blocked until pending allocations complete."
        )

    if demand["has_prior_allocation"]:
        if not escalation_acknowledged:
            raise PriorAllocationRequiresEscalationError(
                f"Disaster '{disaster_name}' has already received {demand['total_allocated_quantity']} "
                "allocated unit(s) of this resource. You must acknowledge prior allocations to request additional resources."
            )
        if not escalation_justification or len(escalation_justification.strip()) < 15:
            raise PriorAllocationRequiresEscalationError(
                "Escalation justification of at least 15 characters is required when requesting additional resources."
            )

    cursor.execute(
        """
        INSERT INTO emergency_requests
            (disaster_id, resource_type_id, requested_quantity, priority, status)
        VALUES (%s, %s, %s, %s, 'PENDING')
        """,
        (disaster_id, resource_type_id, requested_quantity, priority),
    )
    request_id = cursor.lastrowid

    clean_message = (
        f"Operational request #{request_id} ({requested_quantity} unit(s), {priority}) "
        f"generated for active disaster #{disaster_id} by authorized officer {officer_name}."
    )[:500]

    token_inserted = create_notification(
        cursor,
        event_key=f"op-req-token:{disaster_id}:{resource_type_id}:{submission_token}",
        event_type="OPERATIONAL_REQUEST_GENERATED",
        message=clean_message,
        related_request_id=request_id,
        related_disaster_id=disaster_id,
    )
    if not token_inserted:
        raise IdempotencyCollisionError(
            "Duplicate submission token detected. This request was already submitted."
        )

    return request_id

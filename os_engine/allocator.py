import random
import time

import mysql.connector

from core import db
from modules.notifications.service import create_notification
from os_engine.policies import PRIORITY_VALUES


DEADLOCK_ERRNO = 1213  # ER_LOCK_DEADLOCK
MAX_ATTEMPTS = 3
DATABASE_ERROR_MESSAGE = "Allocation failed due to a database error. No stock was changed."


def _pause_before_retry(attempt):
    # Small random delay so two deadlocked callers do not collide again in lockstep.
    time.sleep(0.05 * attempt + random.uniform(0, 0.05))


def allocate_request(request_id):
    if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id < 1:
        return False, "Invalid request ID."

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return _allocate_once(request_id)
        except mysql.connector.Error as error:
            if getattr(error, "errno", None) == DEADLOCK_ERRNO and attempt < MAX_ATTEMPTS:
                _pause_before_retry(attempt)
                continue
            return False, DATABASE_ERROR_MESSAGE


def _allocate_once(request_id):
    """One transaction attempt. Raises mysql.connector.Error so the caller can retry."""
    connection = db.get_db_connection()
    cursor = connection.cursor()

    try:
        connection.start_transaction()

        cursor.execute("""
            SELECT request_id, resource_type_id, requested_quantity, priority, status
            FROM emergency_requests
            WHERE request_id = %s
            FOR UPDATE
        """, (request_id,))
        request_data = cursor.fetchone()
        if not request_data:
            raise Exception("Request not found.")

        _, resource_type_id, requested_quantity, priority, status = request_data

        if not isinstance(requested_quantity, int) or requested_quantity < 1:
            raise Exception("Request quantity must be a positive whole number.")
        if priority not in PRIORITY_VALUES:
            raise Exception("Request priority is invalid.")
        if status != "PENDING":
            raise Exception(f"Request {request_id} is not PENDING.")

        cursor.execute("""
            SELECT resource_id, available_quantity, total_quantity
            FROM resources
            WHERE type_id = %s
              AND status = 'AVAILABLE'
              AND available_quantity > 0
            ORDER BY available_quantity DESC
            LIMIT 1
            FOR UPDATE
        """, (resource_type_id,))
        resource_data = cursor.fetchone()
        if not resource_data:
            connection.rollback()
            return False, "No available resource found."

        resource_id, available_quantity, total_quantity = resource_data
        if available_quantity < 0 or available_quantity > total_quantity:
            raise Exception("Resource quantities are inconsistent.")

        if available_quantity < requested_quantity:
            connection.rollback()
            return False, (
                f"Insufficient quantity. "
                f"Required: {requested_quantity}, "
                f"Available: {available_quantity}"
            )

        cursor.execute("""
            UPDATE resources
            SET status = CASE
                    WHEN available_quantity = %s THEN 'UNAVAILABLE'
                    ELSE 'AVAILABLE'
                END,
                available_quantity = available_quantity - %s
            WHERE resource_id = %s
        """, (requested_quantity, requested_quantity, resource_id))

        cursor.execute("""
            INSERT INTO allocations (request_id, resource_id, allocated_quantity, status)
            VALUES (%s, %s, %s, 'ALLOCATED')
        """, (request_id, resource_id, requested_quantity))

        cursor.execute("""
            UPDATE emergency_requests SET status = 'ALLOCATED' WHERE request_id = %s
        """, (request_id,))

        create_notification(
            cursor,
            event_key=f"request-allocated:{request_id}",
            event_type="REQUEST_STATUS_CHANGED",
            message=f"Emergency request #{request_id} was allocated.",
            related_request_id=request_id,
        )
        create_notification(
            cursor,
            event_key=f"resource-assigned:{request_id}",
            event_type="RESOURCE_ASSIGNMENT",
            message=f"Resources were assigned to emergency request #{request_id}.",
            related_request_id=request_id,
        )

        connection.commit()
        return True, f"Request {request_id} allocated successfully."

    except mysql.connector.Error:
        connection.rollback()
        raise

    except Exception as error:
        connection.rollback()
        return False, str(error)

    finally:
        cursor.close()
        connection.close()
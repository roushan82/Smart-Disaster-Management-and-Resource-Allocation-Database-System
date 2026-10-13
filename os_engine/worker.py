import logging
import threading
import time

import mysql.connector

from core import db
from os_engine.scheduler import schedule_automatically


LOGGER = logging.getLogger(__name__)
WORKER_LOCK_NAME = "smart_disaster_automatic_worker"
POLL_INTERVAL_SECONDS = 2
RETRY_INTERVAL_SECONDS = 30

# True if at least one PENDING request could be satisfied by a single resource row
# right now. Mirrors the allocator's own test (available_quantity >= requested_quantity).
RUNNABLE_PENDING_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM emergency_requests er
        JOIN resources r ON r.type_id = er.resource_type_id
        WHERE er.status = 'PENDING'
          AND r.status = 'AVAILABLE'
          AND r.available_quantity > 0
          AND r.available_quantity >= er.requested_quantity
    )
"""


def run_automatic_worker(
    stop_event=None,
    poll_interval=POLL_INTERVAL_SECONDS,
    retry_interval=RETRY_INTERVAL_SECONDS,
):
    stop_event = stop_event or threading.Event()
    lock_connection = db.get_db_connection()
    lock_connection.autocommit = True
    lock_cursor = lock_connection.cursor()
    lock_acquired = False

    try:
        lock_cursor.execute("SELECT GET_LOCK(%s, 0)", (WORKER_LOCK_NAME,))
        lock_acquired = lock_cursor.fetchone()[0] == 1
        if not lock_acquired:
            LOGGER.warning("Another automatic scheduler worker is already active.")
            return False

        LOGGER.info("Automatic scheduler worker started.")
        last_generation = None
        next_retry_at = 0

        while not stop_event.is_set():
            try:
                lock_cursor.execute(
                    "SELECT IS_USED_LOCK(%s), CONNECTION_ID()",
                    (WORKER_LOCK_NAME,),
                )
                lock_owner, connection_id = lock_cursor.fetchone()
                if lock_owner != connection_id:
                    LOGGER.error("Automatic worker lost its singleton lock; stopping.")
                    return False

                poll_connection = db.get_db_connection()
                poll_cursor = poll_connection.cursor()
                try:
                    poll_cursor.execute(
                        "SELECT generation FROM scheduler_control WHERE control_id = 1"
                    )
                    signal_row = poll_cursor.fetchone()
                    generation = signal_row[0] if signal_row else 0
                    poll_cursor.execute("""
                        SELECT COUNT(*)
                        FROM emergency_requests
                        WHERE status = 'PENDING'
                    """)
                    pending_count = poll_cursor.fetchone()[0]
                    runnable = False
                    if pending_count:
                        poll_cursor.execute(RUNNABLE_PENDING_SQL)
                        runnable = bool(poll_cursor.fetchone()[0])
                finally:
                    poll_cursor.close()
                    poll_connection.close()

                now = time.monotonic()
                signaled = generation != last_generation
                # A new-request signal always gets one run (so it is logged). The
                # periodic retry only runs when some pending request fits the stock
                # available right now; otherwise it would just add FAILED rows to the
                # scheduling log every retry_interval seconds.
                retry_due = now >= next_retry_at and runnable
                if pending_count and (signaled or retry_due):
                    last_generation = generation
                    result = schedule_automatically()
                    LOGGER.info(
                        "Automatic scheduler selected %s: %s",
                        result.get("algorithm", "BUSY"),
                        result["message"],
                    )
                    if result.get("run_id") is None:
                        next_retry_at = time.monotonic() + poll_interval
                    else:
                        next_retry_at = time.monotonic() + retry_interval
                else:
                    last_generation = generation
            except mysql.connector.Error:
                LOGGER.exception("Automatic scheduler polling failed; will retry.")
                if not lock_connection.is_connected():
                    LOGGER.error("Automatic worker lock connection was lost; stopping.")
                    return False
                next_retry_at = time.monotonic() + poll_interval

            stop_event.wait(poll_interval)

        return True
    finally:
        if lock_acquired:
            try:
                lock_cursor.execute("SELECT RELEASE_LOCK(%s)", (WORKER_LOCK_NAME,))
                lock_cursor.fetchone()
            except mysql.connector.Error:
                LOGGER.exception("Could not explicitly release the worker lock.")
        lock_cursor.close()
        lock_connection.close()


def is_worker_running(cursor):
    """True only if a live worker currently holds the singleton lock.

    This is the real proof that the worker is running (a policy shown on
    screen is not).
    """
    cursor.execute("SELECT IS_USED_LOCK(%s)", (WORKER_LOCK_NAME,))
    return cursor.fetchone()[0] is not None
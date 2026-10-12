import mysql.connector

from core import db


def create_notification(
    cursor,
    event_key,
    event_type,
    message,
    related_request_id=None,
    related_disaster_id=None,
    related_team_id=None,
    related_personnel_id=None,
):
    """Insert inside the caller's transaction. event_key is UNIQUE, so retries never duplicate."""
    cursor.execute("""
        INSERT IGNORE INTO notifications
            (event_key, recipient_user_id, event_type, message,
             related_request_id, related_disaster_id, related_team_id,
             related_personnel_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        event_key, None, event_type, message,
        related_request_id, related_disaster_id, related_team_id,
        related_personnel_id,
    ))
    return cursor.rowcount == 1


def get_unread_notification_count():
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM notifications WHERE recipient_user_id IS NULL AND read_at IS NULL"
        )
        return cursor.fetchone()[0]
    finally:
        cursor.close()
        connection.close()


def get_notifications(limit=100):
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > 500:
        raise ValueError("Notification limit must be between 1 and 500.")

    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            SELECT notification_id, event_type, message, created_at, read_at,
                   related_request_id, related_disaster_id, related_team_id,
                   related_personnel_id
            FROM notifications
            WHERE recipient_user_id IS NULL
            ORDER BY created_at DESC, notification_id DESC
            LIMIT %s
        """, (limit,))
        return cursor.fetchall()
    finally:
        cursor.close()
        connection.close()


def mark_notification_read(notification_id):
    if not isinstance(notification_id, int) or isinstance(notification_id, bool) or notification_id < 1:
        return False

    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("""
            UPDATE notifications
            SET read_at = CURRENT_TIMESTAMP
            WHERE notification_id = %s AND recipient_user_id IS NULL
              AND read_at IS NULL
        """, (notification_id,))
        connection.commit()
        if cursor.rowcount == 1:
            return True
        cursor.execute("""
            SELECT 1 FROM notifications
            WHERE notification_id = %s AND recipient_user_id IS NULL
        """, (notification_id,))
        return cursor.fetchone() is not None
    except mysql.connector.Error:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def mark_all_notifications_read():
    connection = db.get_db_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE "
            "recipient_user_id IS NULL AND read_at IS NULL"
        )
        connection.commit()
        return cursor.rowcount
    except mysql.connector.Error:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

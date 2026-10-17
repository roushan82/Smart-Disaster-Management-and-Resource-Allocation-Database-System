import unittest
from unittest.mock import patch

from modules.notifications.service import (
    create_notification,
    get_notifications,
    get_unread_notification_count,
    mark_all_notifications_read,
    mark_notification_read,
)


class NotificationCursor:
    def __init__(self, database):
        self.database = database
        self.rowcount = 0
        self.row = None
        self.rows = []
        self.statements = []

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.statements.append((normalized, parameters))
        self.row = None
        self.rows = []
        if normalized.startswith("INSERT IGNORE INTO notifications"):
            (event_key, recipient, event_type, message, request_id,
             disaster_id, team_id, personnel_id) = parameters
            if event_key in self.database.events:
                self.rowcount = 0
            else:
                self.database.events[event_key] = {
                    "id": len(self.database.events) + 1,
                    "event_key": event_key,
                    "recipient": recipient,
                    "event_type": event_type,
                    "message": message,
                    "created_at": "2026-10-10 12:00:00",
                    "read_at": None,
                    "request_id": request_id,
                    "disaster_id": disaster_id,
                    "team_id": team_id,
                    "personnel_id": personnel_id,
                }
                self.rowcount = 1
        elif normalized.startswith("SELECT COUNT(*) FROM notifications"):
            recipient = parameters[0] if "recipient_user_id = %s" in normalized else None
            self.row = (sum(
                event["recipient"] == recipient and event["read_at"] is None
                for event in self.database.events.values()
            ),)
        elif normalized.startswith("SELECT notification_id, event_type"):
            recipient = parameters[0] if "recipient_user_id = %s" in normalized else None
            self.rows = [
                (
                    event["id"], event["event_type"], event["message"],
                    event["created_at"], event["read_at"], event["request_id"],
                    event["disaster_id"], event["team_id"], event["personnel_id"],
                )
                for event in sorted(
                    self.database.events.values(),
                    key=lambda item: item["id"],
                    reverse=True,
                )
                if event["recipient"] == recipient
            ][:parameters[-1]]
        elif normalized.startswith("UPDATE notifications"):
            recipient = parameters[1] if "recipient_user_id = %s" in normalized else None
            if "notification_id = %s" in normalized:
                event = next((
                    event for event in self.database.events.values()
                    if event["id"] == parameters[0] and event["recipient"] == recipient
                ), None)
                if event is None:
                    self.rowcount = 0
                elif event["read_at"] is None:
                    event["read_at"] = "2026-10-10 12:05:00"
                    self.rowcount = 1
                else:
                    self.rowcount = 0
            else:
                unread = [
                    event for event in self.database.events.values()
                    if event["recipient"] == recipient and event["read_at"] is None
                ]
                for event in unread:
                    event["read_at"] = "2026-10-10 12:05:00"
                self.rowcount = len(unread)
        elif normalized.startswith("SELECT 1 FROM notifications"):
            recipient = parameters[1] if "recipient_user_id = %s" in normalized else None
            event = next((
                event for event in self.database.events.values()
                if event["id"] == parameters[0] and event["recipient"] == recipient
            ), None)
            self.row = (1,) if event else None

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class NotificationDatabase:
    def __init__(self):
        self.events = {}
        self.cursor_instance = NotificationCursor(self)
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class NotificationServiceTests(unittest.TestCase):
    def test_event_key_prevents_duplicate_notifications_on_retry(self):
        database = NotificationDatabase()
        cursor = database.cursor()
        arguments = {
            "cursor": cursor,
            "event_key": "request-allocated:31",
            "event_type": "REQUEST_STATUS_CHANGED",
            "message": "Request allocated.",
            "related_request_id": 31,
        }

        self.assertTrue(create_notification(**arguments))
        self.assertFalse(create_notification(**arguments))
        self.assertEqual(len(database.events), 1)

    def test_notifications_persist_and_global_reads_exclude_other_recipients(self):
        database = NotificationDatabase()
        database.events = {
            "global": {
                "id": 1, "recipient": None, "event_type": "NEW_REQUEST",
                "message": "Request created.", "created_at": "today",
                "read_at": None, "request_id": 7, "disaster_id": 2,
                "team_id": None, "personnel_id": None,
            },
            "private": {
                "id": 2, "recipient": 99, "event_type": "TEAM_ASSIGNMENT",
                "message": "Private assignment.", "created_at": "today",
                "read_at": None, "request_id": None, "disaster_id": None,
                "team_id": 3, "personnel_id": 4,
            },
        }
        with patch("core.db.get_db_connection", return_value=database):
            self.assertEqual(get_unread_notification_count(), 1)
            notifications = get_notifications()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][5], 7)
        self.assertNotIn("Private assignment.", str(notifications))

    def test_global_notifications_ignore_recipient_scoped_events(self):
        database = NotificationDatabase()
        database.events = {
            "team-a": {
                "id": 1, "recipient": 99, "event_type": "TEAM_ASSIGNMENT",
                "message": "Team A only", "created_at": "today", "read_at": None,
                "request_id": 7, "disaster_id": None, "team_id": 1, "personnel_id": None,
            },
            "team-b": {
                "id": 2, "recipient": 100, "event_type": "TEAM_ASSIGNMENT",
                "message": "Team B private", "created_at": "today", "read_at": None,
                "request_id": 8, "disaster_id": None, "team_id": 2, "personnel_id": None,
            },
        }
        with patch("core.db.get_db_connection", return_value=database):
            scoped = get_notifications()
            unread = get_unread_notification_count()
            self.assertFalse(mark_notification_read(1))
            self.assertFalse(mark_notification_read(2))

        self.assertEqual(len(scoped), 0)
        self.assertEqual(unread, 0)
        self.assertIsNone(database.events["team-a"]["read_at"])
        self.assertIsNone(database.events["team-b"]["read_at"])

    def test_mark_read_and_mark_all_read_are_global_scope_only(self):
        database = NotificationDatabase()
        database.events = {
            "global1": {
                "id": 1, "recipient": None, "event_type": "NEW_REQUEST",
                "message": "One", "created_at": "today", "read_at": None,
                "request_id": 1, "disaster_id": None, "team_id": None,
                "personnel_id": None,
            },
            "private": {
                "id": 2, "recipient": 12, "event_type": "TEAM_ASSIGNMENT",
                "message": "Private", "created_at": "today", "read_at": None,
                "request_id": None, "disaster_id": None, "team_id": 3,
                "personnel_id": 4,
            },
            "global2": {
                "id": 3, "recipient": None, "event_type": "NEW_REQUEST",
                "message": "Two", "created_at": "today", "read_at": None,
                "request_id": 3, "disaster_id": None, "team_id": None,
                "personnel_id": None,
            },
        }
        with patch("core.db.get_db_connection", return_value=database):
            self.assertTrue(mark_notification_read(1))
            self.assertTrue(mark_notification_read(1))
            self.assertFalse(mark_notification_read(2))
            self.assertEqual(mark_all_notifications_read(), 1)

        self.assertIsNotNone(database.events["global1"]["read_at"])
        self.assertIsNotNone(database.events["global2"]["read_at"])
        self.assertIsNone(database.events["private"]["read_at"])


    def test_notification_limit_is_bounded(self):
        with self.assertRaises(ValueError):
            get_notifications(limit=1000)


if __name__ == "__main__":
    unittest.main()

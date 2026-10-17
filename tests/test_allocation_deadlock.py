import unittest
from unittest.mock import patch

import mysql.connector

from os_engine import allocator
from os_engine.allocator import (
    DATABASE_ERROR_MESSAGE,
    MAX_ATTEMPTS,
    allocate_request,
)


def deadlock():
    return mysql.connector.Error(msg="Deadlock found", errno=1213)


class RetryWrapperTests(unittest.TestCase):
    def setUp(self):
        pause = patch("os_engine.allocator._pause_before_retry")
        self.pause = pause.start()
        self.addCleanup(pause.stop)

    def test_deadlock_is_retried_and_second_attempt_can_succeed(self):
        with patch(
            "os_engine.allocator._allocate_once",
            side_effect=[deadlock(), (True, "Request 5 allocated successfully.")],
        ) as attempt:
            result = allocate_request(5)

        self.assertEqual(result, (True, "Request 5 allocated successfully."))
        self.assertEqual(attempt.call_count, 2)
        self.assertEqual(self.pause.call_count, 1)

    def test_gives_up_after_max_attempts_with_the_usual_database_error(self):
        with patch(
            "os_engine.allocator._allocate_once",
            side_effect=[deadlock() for _ in range(MAX_ATTEMPTS)],
        ) as attempt:
            result = allocate_request(5)

        self.assertEqual(result, (False, DATABASE_ERROR_MESSAGE))
        self.assertEqual(attempt.call_count, MAX_ATTEMPTS)
        self.assertEqual(self.pause.call_count, MAX_ATTEMPTS - 1)

    def test_other_database_errors_are_not_retried(self):
        for error in (
            mysql.connector.Error(msg="Duplicate entry", errno=1062),
            mysql.connector.Error(msg="Lock wait timeout", errno=1205),
            mysql.connector.Error(msg="no errno"),
        ):
            with self.subTest(errno=getattr(error, "errno", None)):
                with patch(
                    "os_engine.allocator._allocate_once", side_effect=error
                ) as attempt:
                    result = allocate_request(5)
                self.assertEqual(result, (False, DATABASE_ERROR_MESSAGE))
                self.assertEqual(attempt.call_count, 1)
        self.assertEqual(self.pause.call_count, 0)

    def test_business_failures_are_returned_without_retry(self):
        with patch(
            "os_engine.allocator._allocate_once",
            return_value=(False, "Insufficient quantity. Required: 5, Available: 2"),
        ) as attempt:
            result = allocate_request(5)

        self.assertFalse(result[0])
        self.assertEqual(attempt.call_count, 1)

    def test_invalid_request_id_never_reaches_the_database(self):
        with patch("os_engine.allocator._allocate_once") as attempt:
            self.assertEqual(allocate_request(0), (False, "Invalid request ID."))
            self.assertEqual(allocate_request(True), (False, "Invalid request ID."))
        attempt.assert_not_called()


class DeadlockingCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        raise deadlock()

    def close(self):
        pass


class DeadlockingConnection:
    def __init__(self):
        self.fake_cursor = DeadlockingCursor()
        self.started = False
        self.rolled_back = False
        self.committed = False
        self.closed = False

    def start_transaction(self):
        self.started = True

    def cursor(self, **kwargs):
        return self.fake_cursor

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class SingleAttemptTests(unittest.TestCase):
    def test_deadlock_inside_a_transaction_rolls_back_and_is_reraised(self):
        connection = DeadlockingConnection()
        with patch("core.db.get_db_connection", return_value=connection):
            with self.assertRaises(mysql.connector.Error) as raised:
                allocator._allocate_once(5)

        self.assertEqual(raised.exception.errno, 1213)
        self.assertTrue(connection.rolled_back)
        self.assertFalse(connection.committed)
        self.assertTrue(connection.closed)

    def test_full_flow_opens_a_fresh_connection_for_every_attempt(self):
        connections = [DeadlockingConnection() for _ in range(MAX_ATTEMPTS)]
        with patch("os_engine.allocator._pause_before_retry"):
            with patch("core.db.get_db_connection", side_effect=connections):
                result = allocate_request(5)

        self.assertEqual(result, (False, DATABASE_ERROR_MESSAGE))
        self.assertTrue(all(c.rolled_back and c.closed for c in connections))
        self.assertTrue(all(not c.committed for c in connections))


if __name__ == "__main__":
    unittest.main()
import unittest
from datetime import datetime
from unittest.mock import patch

from os_engine.policies import AGING_THRESHOLD_SECONDS, order_by_clause
from os_engine.scheduler import (
    SCHEDULER_LOCK_NAME,
    schedule_automatically,
    schedule_pending_requests_fcfs,
    schedule_pending_requests_priority,
)
from os_engine.allocator import allocate_request


class SchedulerDatabase:
    def __init__(self, requests=(), lock_acquired=True):
        self.requests = list(requests)
        self.statuses = {request[0]: "PENDING" for request in requests}
        self.lock_acquired = lock_acquired
        self.algorithm = None
        self.automatic = False
        self.statements = []
        self.items = []
        self.runs = []


class SchedulerCursor:
    def __init__(self, database):
        self.database = database
        self.row = None
        self.rows = []
        self.lastrowid = None
        self.release_result_pending = False
        self.rowcount = 1

    def execute(self, statement, parameters=None):
        self.database.statements.append((statement, parameters))
        normalized = " ".join(statement.split())
        self.row = None
        self.rows = []
        if normalized.startswith("SELECT RELEASE_LOCK"):
            self.release_result_pending = True

        if "GET_LOCK" in normalized:
            self.row = (1 if self.database.lock_acquired else 0,)
        elif normalized.startswith("SELECT COUNT(*) FROM emergency_requests"):
            self.row = (sum(
                self.database.statuses[request[0]] == "PENDING"
                and request[2] == "CRITICAL"
                for request in self.database.requests
            ),)
        elif normalized.startswith("INSERT INTO scheduling_runs"):
            self.database.algorithm = parameters[0]
            self.database.automatic = parameters[2]
            self.lastrowid = len(self.database.runs) + 1
            self.database.runs.append({
                "run_id": self.lastrowid,
                "algorithm": self.database.algorithm,
                "reason": parameters[1],
                "automatic": self.database.automatic,
                "status": "RUNNING",
            })
        elif normalized.startswith("SELECT request_id FROM emergency_requests"):
            priority_order = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
            if self.database.algorithm == "PRIORITY":
                order_key = lambda request: (
                    0 if self.database.automatic
                    and len(request) > 3
                    and request[3] >= AGING_THRESHOLD_SECONDS
                    else priority_order[request[2]],
                    request[1],
                    request[0],
                )
            else:
                order_key = lambda request: (request[1], request[0])
            self.rows = [
                (request[0],)
                for request in sorted(
                    self.database.requests,
                    key=order_key,
                )
                if self.database.statuses[request[0]] == "PENDING"
            ]
        elif normalized.startswith("INSERT INTO scheduling_run_items"):
            run_id, position, request_id = parameters
            self.database.items.append({
                "run_id": run_id,
                "queue_position": position,
                "request_id": request_id,
                "request_status": "PENDING",
                "outcome": "PROCESSING",
                "result_message": "Allocation in progress.",
            })
        elif normalized.startswith("SELECT status FROM emergency_requests"):
            self.row = (self.database.statuses[parameters[0]],)
        elif normalized.startswith("INSERT IGNORE INTO notifications"):
            self.rowcount = 1
        elif normalized.startswith("UPDATE scheduling_run_items"):
            status, outcome, result, run_id, position = parameters
            item = next(
                item for item in self.database.items
                if item["run_id"] == run_id and item["queue_position"] == position
            )
            item.update({
                "request_status": status,
                "outcome": outcome,
                "result_message": result,
            })
        elif normalized.startswith("UPDATE scheduling_runs"):
            run_id = parameters[0]
            status = "FAILED" if "SET status = 'FAILED'" in normalized else "COMPLETED"
            next(run for run in self.database.runs if run["run_id"] == run_id)["status"] = status

    def fetchone(self):
        if self.release_result_pending:
            self.release_result_pending = False
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        if self.release_result_pending:
            raise AssertionError("Cursor closed with unread lock-release result")


class SchedulerConnection:
    def __init__(self, database):
        self.database = database
        self.fake_cursor = SchedulerCursor(database)
        self.commit_count = 0
        self.rollback_count = 0

    def cursor(self):
        return self.fake_cursor

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        pass


class FcfsSchedulingTests(unittest.TestCase):
    def test_orders_by_arrival_then_request_id_and_records_queue(self):
        earlier = datetime(2026, 10, 1, 10, 0)
        later = datetime(2026, 10, 1, 10, 5)
        database = SchedulerDatabase([
            (13, later, "CRITICAL"),
            (9, earlier, "LOW"),
            (4, earlier, "HIGH"),
        ])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            return True, f"Request {request_id} allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocate_mock:
                result = schedule_pending_requests_fcfs()

        self.assertEqual(allocate_mock.call_args_list, [unittest.mock.call(4), unittest.mock.call(9), unittest.mock.call(13)])
        self.assertEqual(result["algorithm"], "FCFS")
        self.assertEqual(
            [(item["queue_position"], item["request_id"]) for item in result["results"]],
            [(1, 4), (2, 9), (3, 13)],
        )
        queue_query = next(
            " ".join(statement.split()) for statement, _ in database.statements
            if "SELECT request_id FROM emergency_requests" in " ".join(statement.split())
        )
        self.assertIn("ORDER BY request_time ASC, request_id ASC", queue_query)
        self.assertEqual([item["outcome"] for item in database.items], ["ALLOCATED"] * 3)
        self.assertTrue(connection.commit_count >= 8)

    def test_insufficient_stock_is_logged_as_failure_and_later_requests_run(self):
        timestamp = datetime(2026, 10, 1, 10, 0)
        database = SchedulerDatabase([(1, timestamp, "HIGH"), (2, timestamp, "LOW")])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            if request_id == 1:
                return False, "Insufficient quantity. Required: 10, Available: 2"
            database.statuses[request_id] = "ALLOCATED"
            return True, "Request 2 allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocate_mock:
                result = schedule_pending_requests_fcfs()

        allocate_mock.assert_has_calls([unittest.mock.call(1), unittest.mock.call(2)])
        self.assertEqual(result["results"][0]["outcome"], "FAILED")
        self.assertEqual(result["results"][0]["request_status"], "PENDING")
        self.assertEqual(result["results"][1]["outcome"], "ALLOCATED")
        self.assertEqual(database.items[0]["request_status"], "PENDING")

    def test_insufficient_stock_rolls_back_without_mutating_resources(self):
        class InsufficientStockCursor(SchedulerCursor):
            def __init__(self, database):
                super().__init__(database)
                self.read_count = 0

            def fetchone(self):
                self.read_count += 1
                if self.read_count == 1:
                    return (1, 3, 10, "HIGH", "PENDING")
                return (7, 2, 10)

        class AllocationConnection(SchedulerConnection):
            def __init__(self, database):
                super().__init__(database)
                self.fake_cursor = InsufficientStockCursor(database)

            def start_transaction(self):
                pass

        database = SchedulerDatabase()
        connection = AllocationConnection(database)
        with patch("core.db.get_db_connection", return_value=connection):
            success, message = allocate_request(1)

        self.assertFalse(success)
        self.assertIn("Insufficient quantity", message)
        self.assertTrue(connection.rollback_count)
        self.assertFalse(any(
            "UPDATE resources" in statement or "INSERT INTO allocations" in statement
            for statement, _ in database.statements
        ))

    def test_second_run_only_processes_still_pending_requests(self):
        timestamp = datetime(2026, 10, 1, 10, 0)
        database = SchedulerDatabase([(8, timestamp, "HIGH")])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            database.requests = []
            return True, "Request allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocate_mock:
                first_run = schedule_pending_requests_fcfs()
                second_run = schedule_pending_requests_fcfs()

        self.assertEqual(first_run["results"][0]["request_id"], 8)
        self.assertEqual(second_run["results"], [])
        self.assertIn("No pending requests", second_run["message"])
        self.assertEqual(allocate_mock.call_count, 1)
        self.assertEqual(len(database.runs), 2)

    def test_scheduler_lock_blocks_a_simultaneous_run(self):
        database = SchedulerDatabase(
            [(3, datetime(2026, 10, 1, 10, 0), "HIGH")],
            lock_acquired=False,
        )
        connection = SchedulerConnection(database)

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request") as allocate_mock:
                result = schedule_pending_requests_priority()

        self.assertIsNone(result["run_id"])
        self.assertIn("already in progress", result["message"])
        self.assertEqual(database.runs, [])
        lock_parameters = [
            parameters for statement, parameters in database.statements
            if "GET_LOCK" in statement
        ]
        self.assertEqual(lock_parameters, [(SCHEDULER_LOCK_NAME,)])
        allocate_mock.assert_not_called()

    def test_priority_order_then_arrival_and_request_id(self):
        earlier = datetime(2026, 10, 1, 10, 0)
        later = datetime(2026, 10, 1, 10, 5)
        database = SchedulerDatabase([
            (12, later, "CRITICAL"),
            (20, earlier, "HIGH"),
            (15, earlier, "HIGH"),
            (5, earlier, "LOW"),
            (19, earlier, "CRITICAL"),
        ])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            return True, f"Request {request_id} allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocate_mock:
                result = schedule_pending_requests_priority()

        expected = [19, 12, 15, 20, 5]
        self.assertEqual(allocate_mock.call_args_list, [
            unittest.mock.call(request_id) for request_id in expected
        ])
        self.assertEqual(result["algorithm"], "PRIORITY")
        self.assertEqual(
            [item["request_id"] for item in result["results"]], expected
        )
        queue_query = next(
            " ".join(statement.split()) for statement, _ in database.statements
            if "SELECT request_id FROM emergency_requests" in " ".join(statement.split())
        )
        self.assertIn("WHEN 'CRITICAL' THEN 1", queue_query)
        self.assertIn("request_time ASC, request_id ASC", queue_query)

    def test_automatic_uses_fcfs_without_pending_critical_requests(self):
        early = datetime(2026, 10, 1, 10, 0)
        late = datetime(2026, 10, 1, 10, 1)
        database = SchedulerDatabase([
            (2, late, "HIGH"),
            (3, early, "LOW"),
        ])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            return True, "Allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocator:
                result = schedule_automatically()

        self.assertEqual(result["algorithm"], "FCFS")
        self.assertIn("no pending CRITICAL", result["reason"])
        self.assertEqual(
            allocator.call_args_list,
            [unittest.mock.call(3), unittest.mock.call(2)],
        )
        self.assertTrue(database.runs[0]["automatic"])

    def test_automatic_selects_priority_after_critical_request_arrives(self):
        timestamp = datetime(2026, 10, 1, 10, 0)
        database = SchedulerDatabase([
            (10, timestamp, "HIGH"),
            (11, timestamp, "CRITICAL"),
        ])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            return True, "Allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocator:
                result = schedule_automatically()

        self.assertEqual(result["algorithm"], "PRIORITY")
        self.assertIn("CRITICAL request(s) detected", result["reason"])
        self.assertEqual(
            allocator.call_args_list,
            [unittest.mock.call(11), unittest.mock.call(10)],
        )

    def test_automatic_priority_aging_promotes_waiting_low_request(self):
        timestamp = datetime(2026, 10, 1, 10, 0)
        database = SchedulerDatabase([
            (1, timestamp, "CRITICAL", 30),
            (2, timestamp, "LOW", AGING_THRESHOLD_SECONDS + 1),
        ])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            return True, "Allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocator:
                result = schedule_automatically()

        self.assertEqual(result["algorithm"], "PRIORITY")
        self.assertIn("promoted", result["reason"])
        self.assertEqual(
            allocator.call_args_list,
            [unittest.mock.call(2), unittest.mock.call(1)],
        )
        queue_query = next(
            " ".join(statement.split()) for statement, _ in database.statements
            if "SELECT request_id FROM emergency_requests" in " ".join(statement.split())
        )
        self.assertIn("TIMESTAMPDIFF(SECOND", queue_query)
        self.assertIn(">= %s THEN 0", queue_query)

    def test_aged_high_request_precedes_new_critical_request(self):
        earlier = datetime(2026, 10, 1, 10, 0)
        newer = datetime(2026, 10, 1, 10, 16)
        database = SchedulerDatabase([
            (12, newer, "CRITICAL", 60),
            (8, earlier, "HIGH", AGING_THRESHOLD_SECONDS + 1),
        ])
        connection = SchedulerConnection(database)

        def allocate(request_id):
            database.statuses[request_id] = "ALLOCATED"
            return True, f"Request {request_id} allocated."

        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", side_effect=allocate) as allocator:
                result = schedule_automatically()

        self.assertEqual(result["algorithm"], "PRIORITY")
        self.assertEqual(
            allocator.call_args_list,
            [unittest.mock.call(8), unittest.mock.call(12)],
        )
        self.assertEqual(
            [item["request_id"] for item in result["results"]],
            [8, 12],
        )
        self.assertIn("900 seconds", database.runs[0]["reason"])


class PolicyTests(unittest.TestCase):
    def test_fcfs_order_has_no_priority_or_aging(self):
        clause, params = order_by_clause("FCFS")
        self.assertEqual(clause, "request_time ASC, request_id ASC")
        self.assertEqual(params, ())

    def test_priority_order_always_includes_aging_even_for_manual_runs(self):
        timestamp = datetime(2026, 10, 1, 10, 0)
        database = SchedulerDatabase([(1, timestamp, "LOW", AGING_THRESHOLD_SECONDS + 1)])
        connection = SchedulerConnection(database)
        with patch("core.db.get_db_connection", return_value=connection):
            with patch("os_engine.scheduler.allocate_request", return_value=(True, "ok")):
                schedule_pending_requests_priority()
        queue_query, params = next(
            (" ".join(statement.split()), parameters)
            for statement, parameters in database.statements
            if "SELECT request_id FROM emergency_requests" in " ".join(statement.split())
        )
        self.assertIn("TIMESTAMPDIFF(SECOND", queue_query)
        self.assertEqual(params, (AGING_THRESHOLD_SECONDS,))

    def test_unknown_algorithm_is_rejected(self):
        with self.assertRaises(ValueError):
            order_by_clause("ROUND_ROBIN")


if __name__ == "__main__":
    unittest.main()
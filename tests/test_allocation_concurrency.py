import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

import mysql.connector
from unittest.mock import patch

from os_engine.allocator import allocate_request


class InMemoryAllocationDatabase:
    """Isolated row-lock/transaction model for testing allocator interleavings."""

    def __init__(self, requests, resource_quantity, start_barrier=None,
                 fail_allocation_insert=False):
        self.requests = {
            request_id: {
                "request_id": request_id,
                "resource_type_id": 1,
                "requested_quantity": quantity,
                "priority": "HIGH",
                "status": "PENDING",
            }
            for request_id, quantity in requests.items()
        }
        self.resources = {
            1: {
                "resource_id": 1,
                "type_id": 1,
                "available_quantity": resource_quantity,
                "total_quantity": resource_quantity,
                "status": "AVAILABLE",
            }
        }
        self.request_locks = {request_id: threading.Lock() for request_id in requests}
        self.resource_locks = {1: threading.Lock()}
        self.start_barrier = start_barrier
        self.fail_allocation_insert = fail_allocation_insert
        self.allocations = []
        self.notifications = []
        self.state_lock = threading.Lock()

    def connect(self):
        return InMemoryAllocationConnection(self)


class InMemoryAllocationConnection:
    def __init__(self, database):
        self.database = database
        self.held_locks = []
        self.request_id = None
        self.resource_id = None
        self.pending_resource = None
        self.pending_request_status = None
        self.pending_allocation = None
        self.pending_notifications = []
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return InMemoryAllocationCursor(self)

    def start_transaction(self):
        if self.database.start_barrier is not None:
            self.database.start_barrier.wait(timeout=5)

    def acquire_request_lock(self, request_id):
        lock = self.database.request_locks[request_id]
        if not lock.acquire(timeout=5):
            raise TimeoutError("Timed out waiting for request row lock")
        self.held_locks.append(lock)
        self.request_id = request_id

    def acquire_resource_lock(self, resource_id):
        lock = self.database.resource_locks[resource_id]
        if not lock.acquire(timeout=5):
            raise TimeoutError("Timed out waiting for resource row lock")
        self.held_locks.append(lock)
        self.resource_id = resource_id

    def commit(self):
        with self.database.state_lock:
            if self.pending_resource is not None:
                resource_id, available, status = self.pending_resource
                self.database.resources[resource_id]["available_quantity"] = available
                self.database.resources[resource_id]["status"] = status
            if self.pending_request_status is not None:
                self.database.requests[self.request_id]["status"] = self.pending_request_status
            if self.pending_allocation is not None:
                self.database.allocations.append(self.pending_allocation)
            self.database.notifications.extend(self.pending_notifications)
        self.committed = True
        self._release_locks()

    def rollback(self):
        self.pending_resource = None
        self.pending_request_status = None
        self.pending_allocation = None
        self.pending_notifications = []
        self.rolled_back = True
        self._release_locks()

    def close(self):
        self._release_locks()

    def _release_locks(self):
        while self.held_locks:
            self.held_locks.pop().release()


class InMemoryAllocationCursor:
    def __init__(self, connection):
        self.connection = connection
        self.row = None
        self.rowcount = 0

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.row = None
        self.rowcount = 0

        if normalized.startswith("SELECT request_id,") and "FOR UPDATE" in normalized:
            request_id = parameters[0]
            self.connection.acquire_request_lock(request_id)
            request = self.connection.database.requests[request_id]
            self.row = (
                request["request_id"],
                request["resource_type_id"],
                request["requested_quantity"],
                request["priority"],
                request["status"],
            )
        elif normalized.startswith("SELECT resource_id,") and "FOR UPDATE" in normalized:
            resource_type_id = parameters[0]
            candidates = [
                resource for resource in self.connection.database.resources.values()
                if resource["type_id"] == resource_type_id
                and resource["status"] == "AVAILABLE"
                and resource["available_quantity"] > 0
            ]
            if candidates:
                resource = max(candidates, key=lambda candidate: candidate["available_quantity"])
                self.connection.acquire_resource_lock(resource["resource_id"])
                resource = self.connection.database.resources[resource["resource_id"]]
                if resource["status"] == "AVAILABLE" and resource["available_quantity"] > 0:
                    self.row = (
                        resource["resource_id"],
                        resource["available_quantity"],
                        resource["total_quantity"],
                    )
        elif normalized.startswith("UPDATE resources"):
            requested_for_status, requested_quantity, resource_id = parameters
            resource = self.connection.database.resources[resource_id]
            self.connection.pending_resource = (
                resource_id,
                resource["available_quantity"] - requested_quantity,
                "UNAVAILABLE"
                if resource["available_quantity"] == requested_for_status
                else "AVAILABLE",
            )
        elif normalized.startswith("INSERT INTO allocations"):
            if self.connection.database.fail_allocation_insert:
                raise mysql.connector.Error("simulated allocation insert failure")
            request_id, resource_id, allocated_quantity = parameters
            self.connection.pending_allocation = {
                "request_id": request_id,
                "resource_id": resource_id,
                "allocated_quantity": allocated_quantity,
            }
            self.rowcount = 1
        elif normalized.startswith("INSERT IGNORE INTO notifications"):
            event_key, recipient_id, event_type, message, request_id, disaster_id, team_id, personnel_id = parameters
            self.connection.pending_notifications.append({
                "event_key": event_key,
                "event_type": event_type,
                "message": message,
                "request_id": request_id,
            })
            self.rowcount = 1
        elif normalized.startswith("UPDATE emergency_requests"):
            self.connection.pending_request_status = "ALLOCATED"

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []

    def close(self):
        pass


class AllocationConcurrencyTests(unittest.TestCase):
    def run_concurrently(self, database, request_ids):
        with patch(
            "core.db.get_db_connection",
            side_effect=database.connect,
        ):
            with ThreadPoolExecutor(max_workers=len(request_ids)) as executor:
                futures = [
                    executor.submit(allocate_request, request_id)
                    for request_id in request_ids
                ]
                return [future.result(timeout=10) for future in futures]

    def test_two_requests_cannot_overallocate_one_limited_resource(self):
        database = InMemoryAllocationDatabase(
            requests={101: 7, 102: 7},
            resource_quantity=10,
            start_barrier=threading.Barrier(2),
        )

        results = self.run_concurrently(database, [101, 102])

        self.assertEqual(sum(success for success, _ in results), 1)
        allocated_total = sum(
            allocation["allocated_quantity"] for allocation in database.allocations
        )
        self.assertEqual(allocated_total, 7)
        self.assertLessEqual(allocated_total, 10)
        self.assertEqual(database.resources[1]["available_quantity"], 3)
        self.assertEqual(
            sum(request["status"] == "ALLOCATED" for request in database.requests.values()),
            1,
        )

    def test_same_emergency_request_is_not_allocated_twice_concurrently(self):
        database = InMemoryAllocationDatabase(
            requests={201: 6},
            resource_quantity=10,
            start_barrier=threading.Barrier(2),
        )

        results = self.run_concurrently(database, [201, 201])

        self.assertEqual(sum(success for success, _ in results), 1)
        self.assertEqual(len(database.allocations), 1)
        self.assertEqual(database.allocations[0]["request_id"], 201)
        self.assertEqual(database.resources[1]["available_quantity"], 4)
        self.assertEqual(database.requests[201]["status"], "ALLOCATED")

    def test_failed_allocation_rolls_back_staged_stock_and_status_changes(self):
        database = InMemoryAllocationDatabase(
            requests={301: 6},
            resource_quantity=10,
            fail_allocation_insert=True,
        )

        success, message = self.run_concurrently(database, [301])[0]

        self.assertFalse(success)
        self.assertIn("database error", message)
        self.assertEqual(database.resources[1]["available_quantity"], 10)
        self.assertEqual(database.resources[1]["status"], "AVAILABLE")
        self.assertEqual(database.requests[301]["status"], "PENDING")
        self.assertEqual(database.allocations, [])


if __name__ == "__main__":
    unittest.main()
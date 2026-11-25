import os
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash

from app import app
from modules.emergency_requests.service import (
    DisasterNotActiveError,
    DuplicateRequestError,
    IdempotencyCollisionError,
    PriorAllocationRequiresEscalationError,
    create_manual_request_atomic,
    create_operational_request_atomic,
)


class MockCursor:
    def __init__(self):
        self.disasters = {
            1: {"disaster_id": 1, "name": "Flood Alpha", "status": "ACTIVE", "location_id": 10},
            2: {"disaster_id": 2, "name": "Landslide Beta", "status": "RESOLVED", "location_id": 20},
        }
        self.resource_types = {
            1: {"type_id": 1, "type_name": "Food Packets", "unit": "packets"},
            2: {"type_id": 2, "type_name": "Medicine Kits", "unit": "kits"},
        }
        self.emergency_requests = []
        self.allocations = []
        self.notifications = []
        self.statements = []
        self.parameters = []
        self.lastrowid = 100
        self.rowcount = 1
        self._current_result = None
        self.fail_notification = False
        self.locked_disasters = set()

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        self.parameters.append(parameters)
        self._current_result = None

        if "SELECT disaster_id, name, status, location_id FROM disasters" in normalized:
            disaster_id = parameters[0]
            d = self.disasters.get(disaster_id)
            if "status = 'ACTIVE'" in normalized:
                if d and d["status"] == "ACTIVE":
                    if "FOR UPDATE" in normalized:
                        self.locked_disasters.add(disaster_id)
                    self._current_result = (d["disaster_id"], d["name"], d["status"], d["location_id"])
                else:
                    self._current_result = None
            else:
                if d:
                    if "FOR UPDATE" in normalized:
                        self.locked_disasters.add(disaster_id)
                    self._current_result = (d["disaster_id"], d["name"], d["status"], d["location_id"])
                else:
                    self._current_result = None

        elif "SELECT type_name FROM resource_types" in normalized or "SELECT 1 FROM resource_types" in normalized:
            type_id = parameters[0]
            rt = self.resource_types.get(type_id)
            self._current_result = (rt["type_name"],) if rt else None

        elif "FROM emergency_requests er LEFT JOIN allocations a" in normalized:
            disaster_id, resource_type_id = parameters[:2]
            matching = [
                r for r in self.emergency_requests
                if r["disaster_id"] == disaster_id and r["resource_type_id"] == resource_type_id
            ]
            rows = []
            for r in matching:
                alloc_qty = sum(
                    a["allocated_quantity"] for a in self.allocations
                    if a["request_id"] == r["request_id"] and a["status"] == "ALLOCATED"
                )
                latest_time = "2026-11-16 10:00:00" if alloc_qty > 0 else None
                rows.append((r["request_id"], r["status"], r["requested_quantity"], r["priority"], r.get("request_time"), alloc_qty, latest_time))
            self._current_result = rows

        elif "SELECT request_id FROM emergency_requests" in normalized:
            disaster_id, resource_type_id, qty, priority = parameters[:4]
            matching = [
                r for r in self.emergency_requests
                if r["disaster_id"] == disaster_id
                and r["resource_type_id"] == resource_type_id
                and r["requested_quantity"] == qty
                and r["priority"] == priority
                and r["status"] == "PENDING"
            ]
            self._current_result = [(m["request_id"],) for m in matching]

        elif "INSERT INTO emergency_requests" in normalized:
            disaster_id, resource_type_id, qty, priority = parameters[:4]
            self.lastrowid = len(self.emergency_requests) + 1
            self.emergency_requests.append({
                "request_id": self.lastrowid,
                "disaster_id": disaster_id,
                "resource_type_id": resource_type_id,
                "requested_quantity": qty,
                "priority": priority,
                "status": "PENDING",
            })
            self.rowcount = 1

        elif "INSERT IGNORE INTO notifications" in normalized:
            if self.fail_notification:
                self.rowcount = 0
                return
            event_key = parameters[0]
            if any(n["event_key"] == event_key for n in self.notifications):
                self.rowcount = 0
            else:
                self.notifications.append({
                    "event_key": event_key,
                    "event_type": parameters[2],
                    "message": parameters[3],
                    "related_request_id": parameters[4],
                    "related_disaster_id": parameters[5],
                })
                self.rowcount = 1

    def fetchone(self):
        if isinstance(self._current_result, list):
            return self._current_result[0] if self._current_result else None
        return self._current_result

    def fetchall(self):
        if isinstance(self._current_result, list):
            return self._current_result
        return [self._current_result] if self._current_result else []

    def close(self):
        pass


class MockConnection:
    def __init__(self, cursor=None):
        self.fake_cursor = cursor or MockCursor()
        self.committed = False
        self.rolled_back = False
        self.transaction_started = False
        self.autocommit = True
        self.closed = False

    def cursor(self, **kwargs):
        return self.fake_cursor

    def start_transaction(self):
        self.transaction_started = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class OperationalOfficerAuthenticationTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "test-secret-key-at-least-32-bytes-long-0001"
        self.notification_count = patch(
            "modules.notifications.service.get_unread_notification_count",
            return_value=0,
        )
        self.notification_count.start()
        self.officer_password_hash = generate_password_hash("officer-secret-pwd")
        self.reviewer_password_hash = generate_password_hash("reviewer-secret-pwd")
        self.client = app.test_client()
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "valid-csrf-token"

    def tearDown(self):
        self.notification_count.stop()

    def test_operational_generation_disabled_when_officer_env_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get("/officer/login")
            self.assertEqual(response.status_code, 503)
            self.assertIn(b"not configured", response.data)

    def test_historical_reviewer_cannot_access_operational_generation_without_fallback(self):
        env = {
            "REVIEWER_USERNAME": "historical@test.org",
            "REVIEWER_PASSWORD_HASH": self.reviewer_password_hash,
            "DISASTER_OFFICER_USERNAME": "officer@test.org",
            "DISASTER_OFFICER_PASSWORD_HASH": self.officer_password_hash,
        }
        with patch.dict(os.environ, env):
            with self.client.session_transaction() as sess:
                sess["historical_reviewer"] = "historical@test.org"
            # Attempt to access operational generation route while signed in only as historical reviewer
            response = self.client.get("/requests/operational-generate")
            # Must NOT grant access; redirects to officer login
            self.assertEqual(response.status_code, 302)
            self.assertIn("/officer/login", response.headers["Location"])

    def test_officer_login_success_and_logout(self):
        env = {
            "DISASTER_OFFICER_USERNAME": "officer@test.org",
            "DISASTER_OFFICER_PASSWORD_HASH": self.officer_password_hash,
            "REVIEWER_USERNAME": "historical@test.org",
            "REVIEWER_PASSWORD_HASH": self.reviewer_password_hash,
        }
        with patch.dict(os.environ, env):
            with self.client.session_transaction() as sess:
                sess["historical_reviewer"] = "historical@test.org"
            post_data = {
                "_csrf_token": "valid-csrf-token",
                "username": "officer@test.org",
                "password": "officer-secret-pwd",
            }
            res = self.client.post("/officer/login", data=post_data)
            self.assertEqual(res.status_code, 302)
            self.assertIn("/requests/operational-generate", res.headers["Location"])
            with self.client.session_transaction() as sess:
                self.assertEqual(sess.get("disaster_officer"), "officer@test.org")
                self.assertEqual(sess.get("historical_reviewer"), "historical@test.org")
                self.assertEqual(sess.get("_csrf_token"), "valid-csrf-token")

            with self.client.session_transaction() as sess:
                sess.pop("historical_reviewer", None)
            reviewer_only = self.client.get("/reports/historical-detections")
            self.assertEqual(reviewer_only.status_code, 302)
            self.assertIn("/review/login", reviewer_only.headers["Location"])
            with self.client.session_transaction() as sess:
                sess["historical_reviewer"] = "historical@test.org"

            # Sign out
            out_res = self.client.post("/officer/logout", data={"_csrf_token": "valid-csrf-token"})
            self.assertEqual(out_res.status_code, 302)
            with self.client.session_transaction() as sess:
                self.assertIsNone(sess.get("disaster_officer"))
                self.assertEqual(sess.get("historical_reviewer"), "historical@test.org")
                self.assertEqual(sess.get("_csrf_token"), "valid-csrf-token")

            reviewer_logout = self.client.post(
                "/review/logout", data={"_csrf_token": "valid-csrf-token"}
            )
            self.assertEqual(reviewer_logout.status_code, 302)
            self.assertIn("/review/login", reviewer_logout.headers["Location"])


class OperationalRequestValidationAndServiceTests(unittest.TestCase):
    def setUp(self):
        self.cursor = MockCursor()

    def test_manual_duplicate_policy_blocks_identical_pending(self):
        # Insert initial pending manual request
        create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="HIGH",
        )
        # Attempt identical request: same disaster, resource, quantity, priority
        with self.assertRaises(DuplicateRequestError):
            create_manual_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=10,
                priority="HIGH",
            )

    def test_manual_duplicate_policy_permits_different_quantity_or_priority(self):
        # Insert initial pending manual request
        id1 = create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="HIGH",
        )
        # Different quantity: permitted
        id2 = create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=20,
            priority="HIGH",
        )
        # Different priority: permitted
        id3 = create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="CRITICAL",
        )
        self.assertEqual(len(self.cursor.emergency_requests), 3)
        self.assertNotEqual(id1, id2)
        self.assertNotEqual(id1, id3)

    def test_operational_policy_blocks_any_pending_request(self):
        # Manual request of 5 units PENDING
        create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=5,
            priority="LOW",
        )
        # Operational generation for different quantity (50) and priority (CRITICAL)
        # Must still be blocked by operational policy because an unallocated request is PENDING
        with self.assertRaises(DuplicateRequestError) as ctx:
            create_operational_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=50,
                priority="CRITICAL",
                justification="Valid operational justification statement.",
                submission_token="token-001",
                officer_name="officer@test.org",
            )
        self.assertIn("currently PENDING", str(ctx.exception))

    def test_operational_policy_requires_escalation_when_prior_allocation_exists(self):
        # Simulate previous request that is ALLOCATED
        self.cursor.emergency_requests.append({
            "request_id": 1,
            "disaster_id": 1,
            "resource_type_id": 1,
            "requested_quantity": 25,
            "priority": "HIGH",
            "status": "ALLOCATED",
        })
        self.cursor.allocations.append({
            "request_id": 1,
            "allocated_quantity": 25,
            "status": "ALLOCATED",
        })

        # Without escalation acknowledgement
        with self.assertRaises(PriorAllocationRequiresEscalationError) as ctx:
            create_operational_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=10,
                priority="HIGH",
                justification="Valid operational justification statement.",
                submission_token="token-002",
                officer_name="officer@test.org",
                escalation_acknowledged=False,
            )
        self.assertIn("already received 25 allocated unit(s)", str(ctx.exception))

        # With escalation acknowledgement but missing justification (<15 chars)
        with self.assertRaises(PriorAllocationRequiresEscalationError):
            create_operational_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=10,
                priority="HIGH",
                justification="Valid operational justification statement.",
                submission_token="token-002",
                officer_name="officer@test.org",
                escalation_acknowledged=True,
                escalation_justification="Too short",
            )

        # With valid escalation acknowledgement and justification
        req_id = create_operational_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="HIGH",
            justification="Primary requirement based on survey.",
            submission_token="token-002",
            officer_name="officer@test.org",
            escalation_acknowledged=True,
            escalation_justification="Severe embankment rupture expanded flooded area to Sector 5.",
        )
        self.assertTrue(req_id > 0)
        new_req = next(r for r in self.cursor.emergency_requests if r["request_id"] == req_id)
        self.assertEqual(new_req["status"], "PENDING")

    def test_operational_generation_blocks_resolved_disaster(self):
        # Disaster 2 is RESOLVED
        with self.assertRaises(DisasterNotActiveError):
            create_operational_request_atomic(
                self.cursor,
                disaster_id=2,
                resource_type_id=1,
                requested_quantity=10,
                priority="HIGH",
                justification="Valid operational justification statement.",
                submission_token="token-003",
                officer_name="officer@test.org",
            )

    def test_idempotency_token_collision_detected(self):
        # First submission with token-abc succeeds
        req_id = create_operational_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=2,
            requested_quantity=15,
            priority="MEDIUM",
            justification="Medical kit deployment for camp.",
            submission_token="token-abc",
            officer_name="officer@test.org",
        )
        self.assertTrue(req_id > 0)

        # Simulate that this request was allocated so the pending guard does not block
        req = next(r for r in self.cursor.emergency_requests if r["request_id"] == req_id)
        req["status"] = "ALLOCATED"
        self.cursor.allocations.append({
            "request_id": req_id,
            "allocated_quantity": 15,
            "status": "ALLOCATED",
        })

        # Resubmission with escalation acknowledgement but SAME token triggers idempotency collision
        with self.assertRaises(IdempotencyCollisionError):
            create_operational_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=2,
                requested_quantity=15,
                priority="MEDIUM",
                justification="Medical kit deployment for camp.",
                submission_token="token-abc",
                officer_name="officer@test.org",
                escalation_acknowledged=True,
                escalation_justification="Secondary influx of patients required additional kits.",
            )


class ConcurrencySerializationTests(unittest.TestCase):
    def setUp(self):
        self.cursor = MockCursor()

    def test_ordering_1_manual_locks_first_then_operational(self):
        # 1. Manual transaction locks disaster row and inserts
        req_id1 = create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="HIGH",
        )
        self.assertIn(1, self.cursor.locked_disasters)
        self.assertTrue(req_id1 > 0)

        # 2. Operational transaction arrives next, acquires lock on same disaster row.
        # It must see the unallocated manual request in PENDING status and block.
        with self.assertRaises(DuplicateRequestError):
            create_operational_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=10,
                priority="HIGH",
                justification="Operational justification text.",
                submission_token="token-order1",
                officer_name="officer@test.org",
            )

    def test_ordering_2_operational_locks_first_then_manual(self):
        # 1. Operational transaction locks disaster row and inserts
        req_id1 = create_operational_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=30,
            priority="CRITICAL",
            justification="Operational justification text.",
            submission_token="token-order2",
            officer_name="officer@test.org",
        )
        self.assertIn(1, self.cursor.locked_disasters)
        self.assertTrue(req_id1 > 0)

        # 2. Manual transaction arrives next, acquires lock on same disaster row.
        # If manual request has IDENTICAL quantity and priority, manual policy blocks:
        with self.assertRaises(DuplicateRequestError):
            create_manual_request_atomic(
                self.cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=30,
                priority="CRITICAL",
            )

        # But if manual request has DIFFERENT quantity, manual policy allows:
        req_id2 = create_manual_request_atomic(
            self.cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=5,
            priority="LOW",
        )
        self.assertTrue(req_id2 > 0)
        self.assertNotEqual(req_id1, req_id2)


if __name__ == "__main__":
    unittest.main()

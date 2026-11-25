"""Phase 4: Resource Allocation Integration Tests.

Verifies end-to-end integration between Phase 3 reviewer-assisted operational
request generation and the existing OS engine (allocator, scheduler policies,
automatic policy selection, and background scheduler signaling).
All tests run against mock database fixtures without connecting to live MySQL.
"""

import unittest
from copy import deepcopy
from unittest.mock import patch

from modules.emergency_requests.service import (
    DuplicateRequestError,
    PriorAllocationRequiresEscalationError,
    create_manual_request_atomic,
    create_operational_request_atomic,
    get_disaster_resource_demand,
)
from os_engine.allocator import allocate_request
from os_engine.policies import select_automatic_algorithm
from os_engine.scheduler import (
    schedule_automatically,
    schedule_pending_requests,
    signal_automatic_scheduler,
)


class IntegratedAllocationMockDB:
    """Mock database simulating disasters, resource types, resources, requests,

    allocations, notifications, scheduler control, and scheduling runs.
    """

    def __init__(self):
        self.disasters = {
            1: {"disaster_id": 1, "name": "Flood Alpha", "status": "ACTIVE", "location_id": 10},
            2: {"disaster_id": 2, "name": "Landslide Beta", "status": "RESOLVED", "location_id": 20},
        }
        self.resource_types = {
            1: {"type_id": 1, "type_name": "Food Packets", "unit": "packets"},
            2: {"type_id": 2, "type_name": "Medicine Kits", "unit": "kits"},
        }
        self.resources = {
            10: {
                "resource_id": 10,
                "type_id": 1,
                "available_quantity": 100,
                "total_quantity": 100,
                "status": "AVAILABLE",
            },
            20: {
                "resource_id": 20,
                "type_id": 2,
                "available_quantity": 20,
                "total_quantity": 50,
                "status": "AVAILABLE",
            },
        }
        self.emergency_requests = {}
        self.allocations = []
        self.notifications = []
        self.scheduling_runs = []
        self.scheduling_run_items = []
        self.scheduler_generation = 0
        self.rr_start_request_id = None
        self.next_request_id = 1
        self.next_run_id = 1
        self.lock_acquired = True
        self.statements = []

    def save_state(self):
        return {
            "disasters": deepcopy(self.disasters),
            "resources": deepcopy(self.resources),
            "emergency_requests": deepcopy(self.emergency_requests),
            "allocations": deepcopy(self.allocations),
            "notifications": deepcopy(self.notifications),
            "scheduling_runs": deepcopy(self.scheduling_runs),
            "scheduling_run_items": deepcopy(self.scheduling_run_items),
            "scheduler_generation": self.scheduler_generation,
            "rr_start_request_id": self.rr_start_request_id,
            "next_request_id": self.next_request_id,
            "next_run_id": self.next_run_id,
        }

    def restore_state(self, state):
        self.disasters = deepcopy(state["disasters"])
        self.resources = deepcopy(state["resources"])
        self.emergency_requests = deepcopy(state["emergency_requests"])
        self.allocations = deepcopy(state["allocations"])
        self.notifications = deepcopy(state["notifications"])
        self.scheduling_runs = deepcopy(state["scheduling_runs"])
        self.scheduling_run_items = deepcopy(state["scheduling_run_items"])
        self.scheduler_generation = state["scheduler_generation"]
        self.rr_start_request_id = state["rr_start_request_id"]
        self.next_request_id = state["next_request_id"]
        self.next_run_id = state["next_run_id"]


class IntegratedAllocationCursor:
    def __init__(self, connection):
        self.connection = connection
        self.db = connection.db
        self.row = None
        self.rows = []
        self.lastrowid = None
        self.rowcount = 1

    def execute(self, statement, parameters=None):
        parameters = parameters or ()
        normalized = " ".join(statement.split())
        self.db.statements.append((normalized, parameters))
        self.row = None
        self.rows = []

        # Lock active disaster
        if "FROM disasters" in normalized and "FOR UPDATE" in normalized:
            disaster_id = parameters[0]
            d = self.db.disasters.get(disaster_id)
            if "status = 'ACTIVE'" in normalized:
                if d and d["status"] == "ACTIVE":
                    self.row = (d["disaster_id"], d["name"], d["status"], d["location_id"])
                else:
                    self.row = None
            else:
                self.row = (d["disaster_id"], d["name"], d["status"], d["location_id"]) if d else None

        elif "SELECT type_name FROM resource_types" in normalized or "SELECT 1 FROM resource_types" in normalized:
            type_id = parameters[0]
            rt = self.db.resource_types.get(type_id)
            self.row = (rt["type_name"],) if rt else None

        # get_disaster_resource_demand query
        elif "FROM emergency_requests er LEFT JOIN allocations a" in normalized:
            disaster_id, resource_type_id = parameters[:2]
            matching = [
                r for r in self.db.emergency_requests.values()
                if r["disaster_id"] == disaster_id and r["resource_type_id"] == resource_type_id
            ]
            rows = []
            for r in matching:
                alloc_qty = sum(
                    a["allocated_quantity"] for a in self.db.allocations
                    if a["request_id"] == r["request_id"] and a["status"] == "ALLOCATED"
                )
                latest_time = "2026-11-16 10:00:00" if alloc_qty > 0 else None
                rows.append((
                    r["request_id"],
                    r["status"],
                    r["requested_quantity"],
                    r["priority"],
                    r.get("request_time", "2026-11-16 09:00:00"),
                    alloc_qty,
                    latest_time,
                ))
            self.rows = rows

        # Duplicate check in create_manual_request_atomic
        elif normalized.startswith("SELECT request_id FROM emergency_requests WHERE disaster_id = %s"):
            disaster_id, resource_type_id, qty, priority = parameters[:4]
            matching = [
                r for r in self.db.emergency_requests.values()
                if r["disaster_id"] == disaster_id
                and r["resource_type_id"] == resource_type_id
                and r["requested_quantity"] == qty
                and r["priority"] == priority
                and r["status"] == "PENDING"
            ]
            self.rows = [(m["request_id"],) for m in matching]

        # Insert emergency request
        elif normalized.startswith("INSERT INTO emergency_requests"):
            disaster_id, resource_type_id, qty, priority = parameters[:4]
            req_id = self.db.next_request_id
            self.db.next_request_id += 1
            self.lastrowid = req_id
            self.db.emergency_requests[req_id] = {
                "request_id": req_id,
                "disaster_id": disaster_id,
                "resource_type_id": resource_type_id,
                "requested_quantity": qty,
                "priority": priority,
                "status": "PENDING",
                "request_time": "2026-11-16 09:00:00",
            }
            self.rowcount = 1

        # Allocator: Lock emergency request
        elif normalized.startswith("SELECT request_id, resource_type_id, requested_quantity, priority, status FROM emergency_requests") and "FOR UPDATE" in normalized:
            req_id = parameters[0]
            req = self.db.emergency_requests.get(req_id)
            if req:
                self.row = (
                    req["request_id"],
                    req["resource_type_id"],
                    req["requested_quantity"],
                    req["priority"],
                    req["status"],
                )
            else:
                self.row = None

        # Allocator: Lock available resource
        elif normalized.startswith("SELECT resource_id, available_quantity, total_quantity FROM resources") and "FOR UPDATE" in normalized:
            type_id = parameters[0]
            candidates = [
                res for res in self.db.resources.values()
                if res["type_id"] == type_id
                and res["status"] == "AVAILABLE"
                and res["available_quantity"] > 0
            ]
            if candidates:
                best = max(candidates, key=lambda c: c["available_quantity"])
                self.row = (best["resource_id"], best["available_quantity"], best["total_quantity"])
            else:
                self.row = None

        # Allocator: Update resources
        elif normalized.startswith("UPDATE resources"):
            req_qty1, req_qty2, resource_id = parameters
            res = self.db.resources[resource_id]
            res["available_quantity"] -= req_qty2
            if res["available_quantity"] == 0:
                res["status"] = "UNAVAILABLE"
            self.rowcount = 1

        # Allocator: Insert allocation
        elif normalized.startswith("INSERT INTO allocations"):
            req_id, res_id, alloc_qty = parameters[:3]
            self.db.allocations.append({
                "request_id": req_id,
                "resource_id": res_id,
                "allocated_quantity": alloc_qty,
                "status": "ALLOCATED",
            })
            self.rowcount = 1

        # Allocator: Update emergency request to ALLOCATED
        elif normalized.startswith("UPDATE emergency_requests SET status = 'ALLOCATED'"):
            req_id = parameters[0]
            if req_id in self.db.emergency_requests:
                self.db.emergency_requests[req_id]["status"] = "ALLOCATED"
            self.rowcount = 1

        # Notifications: Insert
        elif normalized.startswith("INSERT IGNORE INTO notifications"):
            event_key = parameters[0]
            if any(n["event_key"] == event_key for n in self.db.notifications):
                self.rowcount = 0
            else:
                self.db.notifications.append({
                    "event_key": event_key,
                    "event_type": parameters[2],
                    "message": parameters[3],
                    "related_request_id": parameters[4] if len(parameters) > 4 else None,
                    "related_disaster_id": parameters[5] if len(parameters) > 5 else None,
                })
                self.rowcount = 1

        # Scheduler GET_LOCK / RELEASE_LOCK
        elif "GET_LOCK" in normalized:
            self.row = (1 if self.db.lock_acquired else 0,)
        elif "RELEASE_LOCK" in normalized:
            self.row = (1,)

        # Scheduler count pending critical requests for automatic policy
        elif normalized.startswith("SELECT COUNT(*) FROM emergency_requests WHERE status = 'PENDING' AND priority = 'CRITICAL'"):
            count = sum(
                1 for r in self.db.emergency_requests.values()
                if r["status"] == "PENDING" and r["priority"] == "CRITICAL"
            )
            self.row = (count,)

        # Scheduler count all pending
        elif normalized.startswith("SELECT COUNT(*) FROM emergency_requests WHERE status = 'PENDING'"):
            count = sum(
                1 for r in self.db.emergency_requests.values()
                if r["status"] == "PENDING"
            )
            self.row = (count,)

        # Scheduler RR start request query
        elif normalized.startswith("SELECT rr_start_request_id FROM scheduling_runs"):
            self.row = (self.db.rr_start_request_id,) if self.db.rr_start_request_id is not None else None

        # Scheduler insert run
        elif normalized.startswith("INSERT INTO scheduling_runs"):
            run_id = self.db.next_run_id
            self.db.next_run_id += 1
            self.lastrowid = run_id
            self.db.scheduling_runs.append({
                "run_id": run_id,
                "algorithm": parameters[0],
                "reason": parameters[1],
                "automatic": parameters[2],
                "status": "RUNNING",
            })
            self.rowcount = 1

        # Scheduler queue selection
        elif normalized.startswith("SELECT request_id FROM emergency_requests WHERE status = 'PENDING'"):
            pending = [
                r for r in self.db.emergency_requests.values()
                if r["status"] == "PENDING"
            ]
            # Handle sorting
            if "WHEN 'CRITICAL'" in normalized:
                priority_weights = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}
                pending.sort(key=lambda r: (priority_weights.get(r["priority"], 5), r["request_id"]))
            elif "request_id >" in normalized:
                anchor = parameters[0] if parameters else None
                pending.sort(key=lambda r: (0 if anchor and r["request_id"] > anchor else 1, r["request_id"]))
            else:
                pending.sort(key=lambda r: r["request_id"])
            self.rows = [(r["request_id"],) for r in pending]

        # Scheduler RR start request update
        elif normalized.startswith("UPDATE scheduling_runs SET rr_start_request_id = %s"):
            self.db.rr_start_request_id = parameters[0]
            self.rowcount = 1

        # Scheduler insert run items (values are run_id, queue_position, request_id)
        elif normalized.startswith("INSERT INTO scheduling_run_items"):
            self.db.scheduling_run_items.append({
                "run_id": parameters[0],
                "queue_position": parameters[1],
                "request_id": parameters[2],
                "request_status": "PENDING",
                "outcome": "PROCESSING",
                "result_message": "Allocation in progress.",
            })
            self.rowcount = 1

        # Scheduler check request status after allocation
        elif normalized.startswith("SELECT status FROM emergency_requests WHERE request_id = %s"):
            req = self.db.emergency_requests.get(parameters[0])
            self.row = (req["status"],) if req else ("UNKNOWN",)

        # Scheduler update run item outcome
        elif normalized.startswith("UPDATE scheduling_run_items"):
            status, outcome, result_message, run_id, queue_pos = parameters[:5]
            for it in self.db.scheduling_run_items:
                if it["run_id"] == run_id and it["queue_position"] == queue_pos:
                    it["request_status"] = status
                    it["outcome"] = outcome
                    it["result_message"] = result_message
            self.rowcount = 1

        # Scheduler update run completed
        elif normalized.startswith("UPDATE scheduling_runs SET status = 'COMPLETED'"):
            run_id = parameters[0]
            for run in self.db.scheduling_runs:
                if run["run_id"] == run_id:
                    run["status"] = "COMPLETED"
            self.rowcount = 1

        # Scheduler control bump (signal_automatic_scheduler)
        elif "scheduler_control" in normalized:
            self.db.scheduler_generation += 1
            self.rowcount = 1

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        pass


class IntegratedAllocationConnection:
    def __init__(self, db):
        self.db = db
        self.snapshot = db.save_state()
        self.committed = False
        self.rolled_back = False

    def cursor(self, **kwargs):
        return IntegratedAllocationCursor(self)

    def start_transaction(self):
        self.snapshot = self.db.save_state()

    def commit(self):
        self.committed = True
        self.snapshot = self.db.save_state()

    def rollback(self):
        self.rolled_back = True
        self.db.restore_state(self.snapshot)

    def close(self):
        pass


class OperationalAllocationIntegrationTests(unittest.TestCase):
    """Integration test suite proving that operational requests flow seamlessly into the

    existing allocation and scheduling engine without modifying OS algorithms.
    """

    def setUp(self):
        self.db = IntegratedAllocationMockDB()
        self.connection = IntegratedAllocationConnection(self.db)

    def test_direct_allocation_of_operational_request_success(self):
        """Verify that an operational request is allocated cleanly by allocate_request()."""
        cursor = self.connection.cursor()
        req_id = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=1,  # Food Packets (100 in stock)
            requested_quantity=25,
            priority="HIGH",
            justification="Camp shelter food requirement.",
            submission_token="token-alloc-01",
            officer_name="officer@disaster.gov",
        )
        self.connection.commit()

        # Request begins in PENDING status
        self.assertEqual(self.db.emergency_requests[req_id]["status"], "PENDING")

        with patch("core.db.get_db_connection", return_value=IntegratedAllocationConnection(self.db)):
            success, message = allocate_request(req_id)

        self.assertTrue(success)
        self.assertIn("allocated successfully", message)

        # Emergency request status updated to ALLOCATED
        self.assertEqual(self.db.emergency_requests[req_id]["status"], "ALLOCATED")

        # Inventory decremented by requested quantity
        res = self.db.resources[10]
        self.assertEqual(res["available_quantity"], 75)
        self.assertEqual(res["status"], "AVAILABLE")

        # Allocation record inserted
        alloc = next(a for a in self.db.allocations if a["request_id"] == req_id)
        self.assertEqual(alloc["allocated_quantity"], 25)
        self.assertEqual(alloc["status"], "ALLOCATED")

        # Notification events created
        keys = [n["event_key"] for n in self.db.notifications]
        self.assertIn(f"request-allocated:{req_id}", keys)
        self.assertIn(f"resource-assigned:{req_id}", keys)

    def test_operational_request_insufficient_stock_remains_pending(self):
        """Verify that an operational request with insufficient inventory fails allocation

        and remains PENDING without altering stock or recording allocations.
        """
        cursor = self.connection.cursor()
        req_id = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=2,  # Medicine Kits (only 20 in stock)
            requested_quantity=50,  # Request exceeds stock
            priority="HIGH",
            justification="Field hospital trauma kits.",
            submission_token="token-alloc-02",
            officer_name="officer@disaster.gov",
        )
        self.connection.commit()

        initial_available = self.db.resources[20]["available_quantity"]

        with patch("core.db.get_db_connection", return_value=IntegratedAllocationConnection(self.db)):
            success, message = allocate_request(req_id)

        self.assertFalse(success)
        self.assertIn("Insufficient quantity", message)

        # Request remains in PENDING status
        self.assertEqual(self.db.emergency_requests[req_id]["status"], "PENDING")

        # Stock remains completely untouched
        self.assertEqual(self.db.resources[20]["available_quantity"], initial_available)

        # Zero allocations created
        self.assertEqual(len(self.db.allocations), 0)

    def test_fcfs_scheduling_processes_operational_requests_in_queue_order(self):
        """Verify that FCFS scheduler discovers both manual and operational requests

        and allocates them in queue arrival order.
        """
        cursor = self.connection.cursor()
        # 1. Manual request arrives first (Resource 1: Food Packets)
        manual_id = create_manual_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="LOW",
        )
        # 2. Operational request arrives second (Resource 2: Medicine Kits)
        op_id = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=2,
            requested_quantity=15,
            priority="HIGH",
            justification="Supplementary medicine supplies for Sector 1.",
            submission_token="token-fcfs-01",
            officer_name="officer@disaster.gov",
        )
        self.connection.commit()

        with patch("core.db.get_db_connection", side_effect=lambda: IntegratedAllocationConnection(self.db)):
            result = schedule_pending_requests("FCFS")

        self.assertEqual(result["algorithm"], "FCFS")
        self.assertEqual(len(result["results"]), 2)

        # FCFS processes manual request first, operational request second
        self.assertEqual(result["results"][0]["request_id"], manual_id)
        self.assertEqual(result["results"][0]["outcome"], "ALLOCATED")
        self.assertEqual(result["results"][1]["request_id"], op_id)
        self.assertEqual(result["results"][1]["outcome"], "ALLOCATED")

        # Both requests are ALLOCATED in database
        self.assertEqual(self.db.emergency_requests[manual_id]["status"], "ALLOCATED")
        self.assertEqual(self.db.emergency_requests[op_id]["status"], "ALLOCATED")
        self.assertEqual(self.db.resources[10]["available_quantity"], 90)  # 100 - 10
        self.assertEqual(self.db.resources[20]["available_quantity"], 5)   # 20 - 15

    def test_operational_critical_request_triggers_automatic_priority_switching(self):
        """Verify that an operational request with CRITICAL priority causes the

        automatic policy to switch from FCFS to PRIORITY and promotes it to queue position 1.
        """
        cursor = self.connection.cursor()
        # Non-critical request in queue
        create_manual_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=10,
            priority="LOW",
        )
        self.connection.commit()

        # Before CRITICAL request: automatic policy selects FCFS
        algo_before, _ = select_automatic_algorithm(cursor)
        self.assertEqual(algo_before, "FCFS")

        # Officer generates CRITICAL operational request
        crit_id = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=2,
            requested_quantity=5,
            priority="CRITICAL",
            justification="Immediate casualty life-support requirement.",
            submission_token="token-crit-01",
            officer_name="officer@disaster.gov",
        )
        self.connection.commit()

        # After CRITICAL request: automatic policy immediately selects PRIORITY
        algo_after, reason_after = select_automatic_algorithm(cursor)
        self.assertEqual(algo_after, "PRIORITY")
        self.assertIn("CRITICAL", reason_after)

        # Run automatic scheduler
        with patch("core.db.get_db_connection", side_effect=lambda: IntegratedAllocationConnection(self.db)):
            run_result = schedule_automatically()

        self.assertEqual(run_result["algorithm"], "PRIORITY")
        # Position 1 must be the CRITICAL operational request
        self.assertEqual(run_result["results"][0]["request_id"], crit_id)
        self.assertEqual(run_result["results"][0]["outcome"], "ALLOCATED")

    def test_round_robin_rotation_processes_operational_requests(self):
        """Verify that Round Robin cyclic rotation operates consistently with operational requests."""
        cursor = self.connection.cursor()
        id1 = create_manual_request_atomic(cursor, disaster_id=1, resource_type_id=1, requested_quantity=5, priority="LOW")
        id2 = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=2,
            requested_quantity=5,
            priority="MEDIUM",
            justification="First aid kit supplies.",
            submission_token="token-rr-01",
            officer_name="officer@disaster.gov",
        )
        self.connection.commit()

        with patch("core.db.get_db_connection", side_effect=lambda: IntegratedAllocationConnection(self.db)):
            rr_result = schedule_pending_requests("RR")

        self.assertEqual(rr_result["algorithm"], "RR")
        self.assertEqual(len(rr_result["results"]), 2)
        # Verify rr_start_request_id was tracked
        self.assertEqual(self.db.rr_start_request_id, id1)

    def test_closed_loop_operational_lifecycle_and_escalation_enforcement(self):
        """Verify the complete Phase 3 + Phase 4 closed-loop lifecycle:

        1. Operational request generated as PENDING.
        2. Duplicate operational request blocked while PENDING.
        3. Allocator allocates the request to ALLOCATED.
        4. Subsequent operational request blocked by prior allocation guard.
        5. Escalation acknowledgement + justification successfully creates escalation request.
        6. Scheduler allocates the escalation request.
        """
        cursor = self.connection.cursor()

        # 1. Operational request generated as PENDING (20 units of Food Packets)
        req1 = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=20,
            priority="HIGH",
            justification="Initial food distribution for Sector 1.",
            submission_token="token-loop-01",
            officer_name="officer@disaster.gov",
        )
        self.connection.commit()

        # 2. Duplicate operational request blocked while req1 is PENDING
        with self.assertRaises(DuplicateRequestError) as ctx:
            create_operational_request_atomic(
                cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=10,
                priority="HIGH",
                justification="Attempted second request while first is pending.",
                submission_token="token-loop-02",
                officer_name="officer@disaster.gov",
            )
        self.assertIn("currently PENDING", str(ctx.exception))

        # 3. Allocator processes req1
        with patch("core.db.get_db_connection", return_value=IntegratedAllocationConnection(self.db)):
            success, msg = allocate_request(req1)
        self.assertTrue(success)
        self.assertEqual(self.db.emergency_requests[req1]["status"], "ALLOCATED")

        # Demand check reflects 20 units allocated
        demand = get_disaster_resource_demand(cursor, 1, 1)
        self.assertTrue(demand["has_prior_allocation"])
        self.assertEqual(demand["total_allocated_quantity"], 20)
        self.assertFalse(demand["has_pending"])

        # 4. Subsequent operational request blocked without escalation acknowledgement
        with self.assertRaises(PriorAllocationRequiresEscalationError) as ctx:
            create_operational_request_atomic(
                cursor,
                disaster_id=1,
                resource_type_id=1,
                requested_quantity=15,
                priority="HIGH",
                justification="Additional rations needed after headcount increase.",
                submission_token="token-loop-03",
                officer_name="officer@disaster.gov",
                escalation_acknowledged=False,
            )
        self.assertIn("already received 20 allocated unit(s)", str(ctx.exception))

        # 5. Escalation acknowledgement + justification succeeds
        req2 = create_operational_request_atomic(
            cursor,
            disaster_id=1,
            resource_type_id=1,
            requested_quantity=15,
            priority="CRITICAL",
            justification="Additional rations needed after headcount increase.",
            submission_token="token-loop-04",
            officer_name="officer@disaster.gov",
            escalation_acknowledged=True,
            escalation_justification="Sector 2 influx added 150 refugees requiring immediate emergency provisions.",
        )
        self.connection.commit()
        self.assertEqual(self.db.emergency_requests[req2]["status"], "PENDING")

        # 6. Automatic scheduler prioritizes the CRITICAL escalation request
        with patch("core.db.get_db_connection", side_effect=lambda: IntegratedAllocationConnection(self.db)):
            schedule_result = schedule_automatically()

        self.assertEqual(schedule_result["algorithm"], "PRIORITY")
        self.assertEqual(schedule_result["results"][0]["request_id"], req2)
        self.assertEqual(schedule_result["results"][0]["outcome"], "ALLOCATED")
        self.assertEqual(self.db.emergency_requests[req2]["status"], "ALLOCATED")

        # Total allocated quantity is now 35 (20 + 15)
        demand_final = get_disaster_resource_demand(cursor, 1, 1)
        self.assertEqual(demand_final["total_allocated_quantity"], 35)

    def test_scheduler_signaling_occurs_post_commit(self):
        """Verify that signal_automatic_scheduler() increments scheduler_control generation."""
        initial_gen = self.db.scheduler_generation
        with patch("core.db.get_db_connection", return_value=IntegratedAllocationConnection(self.db)):
            signal_automatic_scheduler()

        self.assertEqual(self.db.scheduler_generation, initial_gen + 1)


if __name__ == "__main__":
    unittest.main()

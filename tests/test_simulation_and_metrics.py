import unittest
from datetime import datetime, timedelta, timezone

from os_engine.experiments import (
    SIMULATION_ALGORITHMS,
    _simulate_requests,
    _simulate_round_robin,
    run_fair_algorithm_comparison,
)


class SJFOrderingAndTieBreakingTests(unittest.TestCase):
    def test_sjf_orders_by_synthetic_duration_shortest_first(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "HIGH", "duration_seconds": 600},
            {"request_id": 2, "request_time": t0, "priority": "CRITICAL", "duration_seconds": 120},
            {"request_id": 3, "request_time": t0, "priority": "LOW", "duration_seconds": 300},
        ]
        result = _simulate_requests(rows, "SJF", t0)
        ordered_ids = [r["request_id"] for r in result["results"]]
        self.assertEqual(ordered_ids, [2, 3, 1])
        self.assertEqual(result["results"][0]["duration_seconds"], 120)
        self.assertEqual(result["results"][1]["duration_seconds"], 300)
        self.assertEqual(result["results"][2]["duration_seconds"], 600)

    def test_sjf_deterministic_tie_breaking_by_arrival_time(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 10, 5, tzinfo=timezone.utc)
        rows = [
            {"request_id": 10, "request_time": t1, "priority": "MEDIUM", "duration_seconds": 300},
            {"request_id": 20, "request_time": t0, "priority": "MEDIUM", "duration_seconds": 300},
        ]
        result = _simulate_requests(rows, "SJF", t0)
        ordered_ids = [r["request_id"] for r in result["results"]]
        # Identical duration (300s): earlier arrival (t0) must precede later arrival (t1)
        self.assertEqual(ordered_ids, [20, 10])

    def test_sjf_deterministic_tie_breaking_by_request_id(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 45, "request_time": t0, "priority": "LOW", "duration_seconds": 250},
            {"request_id": 12, "request_time": t0, "priority": "CRITICAL", "duration_seconds": 250},
            {"request_id": 89, "request_time": t0, "priority": "HIGH", "duration_seconds": 250},
        ]
        result = _simulate_requests(rows, "SJF", t0)
        ordered_ids = [r["request_id"] for r in result["results"]]
        # Identical duration (250s) and identical arrival (t0): tie-break deterministically on request_id ASC
        self.assertEqual(ordered_ids, [12, 45, 89])

    def test_sjf_selects_shortest_available_not_future_request(self):
        """Job 1 arrives at t=0 (600s). Job 2 arrives at t=100s (120s). Job 3 arrives at t=10,000s (60s).

        SJF must NOT execute Job 3 before its arrival on Day 0 or force Job 1 and Job 2 to wait 10,000s.
        Correct order: Job 1 (0..600s), Job 2 (600..720s), Job 3 (10,000..10,060s).
        """
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=100)
        t2 = t0 + timedelta(seconds=10000)
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "LOW", "duration_seconds": 600},
            {"request_id": 2, "request_time": t1, "priority": "LOW", "duration_seconds": 120},
            {"request_id": 3, "request_time": t2, "priority": "LOW", "duration_seconds": 60},
        ]
        result = _simulate_requests(rows, "SJF", t0)
        ordered_ids = [r["request_id"] for r in result["results"]]
        self.assertEqual(ordered_ids, [1, 2, 3])

        r1 = next(r for r in result["results"] if r["request_id"] == 1)
        r2 = next(r for r in result["results"] if r["request_id"] == 2)
        r3 = next(r for r in result["results"] if r["request_id"] == 3)

        self.assertEqual(r1["start_time"], t0)
        self.assertEqual(r1["waiting_seconds"], 0.0)

        self.assertEqual(r2["start_time"], t0 + timedelta(seconds=600))
        self.assertEqual(r2["waiting_seconds"], 500.0)  # waited 600 - 100

        self.assertEqual(r3["start_time"], t2)
        self.assertEqual(r3["waiting_seconds"], 0.0)  # zero wait; CPU was idle until t2

        self.assertAlmostEqual(result["average_wait_seconds"], (0.0 + 500.0 + 0.0) / 3)
        self.assertLess(result["average_wait_seconds"], 200.0)
        self.assertAlmostEqual(result["total_simulation_seconds"], 10060.0)


class PreemptiveRoundRobinTests(unittest.TestCase):
    def test_rr_preemption_and_time_slicing(self):
        """Job 1 (200s) and Job 2 (300s) arrive at t=0 with quantum q=120s.

        Timeline:
        0 - 120: Job 1 runs (starts at 0). Rem: 80s.
        120 - 240: Job 2 runs (starts at 120). Rem: 180s.
        240 - 320: Job 1 runs 80s and completes at 320. Rem: 0s.
        320 - 440: Job 2 runs 120s. Rem: 60s.
        440 - 500: Job 2 runs 60s and completes at 500. Rem: 0s.
        """
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "MEDIUM", "duration_seconds": 200},
            {"request_id": 2, "request_time": t0, "priority": "MEDIUM", "duration_seconds": 300},
        ]
        result = _simulate_round_robin(rows, t0, time_quantum=120)

        r1 = next(r for r in result["results"] if r["request_id"] == 1)
        r2 = next(r for r in result["results"] if r["request_id"] == 2)

        # First start times (Response Time = First Start - Arrival)
        self.assertEqual(r1["start_time"], t0)
        self.assertEqual(r1["response_time_seconds"], 0.0)
        self.assertEqual(r2["start_time"], t0 + timedelta(seconds=120))
        self.assertEqual(r2["response_time_seconds"], 120.0)

        # Completion times
        self.assertEqual(r1["completion_time"], t0 + timedelta(seconds=320))
        self.assertEqual(r2["completion_time"], t0 + timedelta(seconds=500))

        # Turnaround Times (Completion - Arrival)
        self.assertEqual(r1["turnaround_seconds"], 320.0)
        self.assertEqual(r2["turnaround_seconds"], 500.0)

        # Waiting Times (Turnaround - Duration)
        # r1: 320 - 200 = 120s (waited 120s while r2 ran its first slice)
        self.assertEqual(r1["waiting_seconds"], 120.0)
        # r2: 500 - 300 = 200s (waited 120s initially + 80s while r1 finished)
        self.assertEqual(r2["waiting_seconds"], 200.0)

        # Averages
        self.assertEqual(result["average_turnaround_seconds"], (320.0 + 500.0) / 2)
        self.assertEqual(result["average_wait_seconds"], (120.0 + 200.0) / 2)
        self.assertEqual(result["average_response_seconds"], (0.0 + 120.0) / 2)
        self.assertEqual(result["total_simulation_seconds"], 500.0)
        self.assertAlmostEqual(result["throughput_per_second"], 2 / 500.0)

    def test_rr_response_time_less_than_or_equal_to_waiting_time(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "LOW", "duration_seconds": 360},
            {"request_id": 2, "request_time": t0, "priority": "LOW", "duration_seconds": 240},
            {"request_id": 3, "request_time": t0, "priority": "LOW", "duration_seconds": 120},
        ]
        result = _simulate_round_robin(rows, t0, time_quantum=60)
        for r in result["results"]:
            self.assertLessEqual(r["response_time_seconds"], r["waiting_seconds"])
            self.assertEqual(
                r["turnaround_seconds"],
                r["waiting_seconds"] + r["duration_seconds"],
            )

    def test_rr_interleaved_arrival_joins_ready_queue(self):
        t0 = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 10, 1, 0, tzinfo=timezone.utc)  # arrives 60s later
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "LOW", "duration_seconds": 300},
            {"request_id": 2, "request_time": t1, "priority": "LOW", "duration_seconds": 100},
        ]
        # Quantum = 120s. Job 1 runs 0..120. At 60s, Job 2 arrives.
        # At 120s Job 1 preempted; Job 2 should run next.
        result = _simulate_round_robin(rows, t0, time_quantum=120)
        r2 = next(r for r in result["results"] if r["request_id"] == 2)
        # Job 2 runs from 120 to 220 (100s duration) and finishes at 220
        self.assertEqual(r2["start_time"], t0 + timedelta(seconds=120))
        self.assertEqual(r2["completion_time"], t0 + timedelta(seconds=220))
        self.assertEqual(r2["response_time_seconds"], 60.0)  # waited from 60s to 120s


class MathematicalMetricsConsistencyTests(unittest.TestCase):
    def test_all_algorithms_satisfy_turnaround_identity(self):
        """For all algorithms: Turnaround Time = Waiting Time + Processing Duration."""
        t0 = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "HIGH", "duration_seconds": 200},
            {"request_id": 2, "request_time": t0, "priority": "CRITICAL", "duration_seconds": 400},
            {"request_id": 3, "request_time": t0, "priority": "LOW", "duration_seconds": 100},
        ]
        for algo in SIMULATION_ALGORITHMS:
            result = _simulate_requests(rows, algo, t0, default_processing_time=300)
            self.assertEqual(result["request_count"], 3)
            for r in result["results"]:
                self.assertAlmostEqual(
                    r["turnaround_seconds"],
                    r["waiting_seconds"] + r["duration_seconds"],
                    places=5,
                    msg=f"Failed identity for algorithm {algo} on request {r['request_id']}",
                )
                self.assertAlmostEqual(
                    r["waiting_seconds"],
                    r["turnaround_seconds"] - r["duration_seconds"],
                    places=5,
                )
                self.assertGreaterEqual(r["waiting_seconds"], 0.0)
                self.assertGreaterEqual(r["response_time_seconds"], 0.0)

    def test_sjf_minimizes_average_turnaround_and_waiting_time(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": t0, "priority": "LOW", "duration_seconds": 900},
            {"request_id": 2, "request_time": t0, "priority": "LOW", "duration_seconds": 100},
            {"request_id": 3, "request_time": t0, "priority": "LOW", "duration_seconds": 300},
        ]
        fcfs = _simulate_requests(rows, "FCFS", t0)
        sjf = _simulate_requests(rows, "SJF", t0)

        # FCFS processes [1 (900s), 2 (100s), 3 (300s)]:
        # C1=900, C2=1000, C3=1300. Avg Turnaround = (900 + 1000 + 1300)/3 = 1066.67
        # SJF processes [2 (100s), 3 (300s), 1 (900s)]:
        # C2=100, C3=400, C1=1300. Avg Turnaround = (100 + 400 + 1300)/3 = 600.0
        self.assertLess(sjf["average_turnaround_seconds"], fcfs["average_turnaround_seconds"])
        self.assertLess(sjf["average_wait_seconds"], fcfs["average_wait_seconds"])
        self.assertAlmostEqual(sjf["average_turnaround_seconds"], 600.0)
        self.assertAlmostEqual(fcfs["average_turnaround_seconds"], 3200.0 / 3)

    def test_priority_aging_behavior_at_899_900_901_seconds(self):
        """Aging threshold is 900s.

        A LOW request at 899s must remain behind CRITICAL.
        A LOW request at 900s or 901s must age to group 0 and precede CRITICAL.
        """
        ref_899 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_899 = ref_899 - timedelta(seconds=899)
        # Case 1: 899s -> NOT aged, CRITICAL runs before LOW
        rows_899 = [
            {"request_id": 1, "request_time": t_899, "priority": "LOW", "duration_seconds": 100},
            {"request_id": 2, "request_time": t_899, "priority": "CRITICAL", "duration_seconds": 100},
        ]
        res_899 = _simulate_requests(rows_899, "PRIORITY", ref_899)
        self.assertEqual([r["request_id"] for r in res_899["results"]], [2, 1])

        # Case 2: 900s -> AGED, LOW promoted to group 0 and precedes CRITICAL by request_id tie-break
        ref_900 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_900 = ref_900 - timedelta(seconds=900)
        rows_900 = [
            {"request_id": 1, "request_time": t_900, "priority": "LOW", "duration_seconds": 100},
            {"request_id": 2, "request_time": t_900, "priority": "CRITICAL", "duration_seconds": 100},
        ]
        res_900 = _simulate_requests(rows_900, "PRIORITY", ref_900)
        self.assertEqual([r["request_id"] for r in res_900["results"]], [1, 2])

        # Case 3: 901s -> AGED, LOW promoted to group 0 and precedes CRITICAL
        ref_901 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t_901 = ref_901 - timedelta(seconds=901)
        rows_901 = [
            {"request_id": 1, "request_time": t_901, "priority": "LOW", "duration_seconds": 100},
            {"request_id": 2, "request_time": t_901, "priority": "CRITICAL", "duration_seconds": 100},
        ]
        res_901 = _simulate_requests(rows_901, "PRIORITY", ref_901)
        self.assertEqual([r["request_id"] for r in res_901["results"]], [1, 2])


class EdgeCaseSafetyTests(unittest.TestCase):
    def test_empty_workload_safety(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        for algo in SIMULATION_ALGORITHMS:
            res = _simulate_requests([], algo, t0)
            self.assertEqual(res["request_count"], 0)
            self.assertEqual(res["average_wait_seconds"], 0.0)
            self.assertEqual(res["average_turnaround_seconds"], 0.0)
            self.assertEqual(res["average_response_seconds"], 0.0)
            self.assertEqual(res["throughput_per_second"], 0.0)
            self.assertEqual(res["results"], [])
            self.assertTrue(res["is_simulated"])

    def test_missing_and_invalid_timestamps_are_safely_excluded(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": None, "priority": "HIGH", "duration_seconds": 100},
            {"request_id": 2, "request_time": "invalid-timestamp", "priority": "LOW", "duration_seconds": 100},
            {"request_id": 3, "request_time": t0, "priority": "MEDIUM", "duration_seconds": 100},
        ]
        for algo in SIMULATION_ALGORITHMS:
            res = _simulate_requests(rows, algo, t0)
            self.assertEqual(res["request_count"], 1)
            self.assertEqual([r["request_id"] for r in res["results"]], [3])

    def test_zero_duration_observation_window_handles_zero_division(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        # If duration is 0, duration defaults to default_processing_time (safe)
        rows = [{"request_id": 1, "request_time": t0, "priority": "LOW", "duration_seconds": 0}]
        res = _simulate_requests(rows, "FCFS", t0, default_processing_time=600)
        self.assertGreater(res["total_simulation_seconds"], 0.0)
        self.assertGreater(res["throughput_per_second"], 0.0)


class FairComparisonRunnerTests(unittest.TestCase):
    class SingleReadCursor:
        def __init__(self, rows):
            self.rows = rows
            self.read_count = 0

        def execute(self, statement, parameters=None):
            pass

        def fetchall(self):
            self.read_count += 1
            if self.read_count > 1:
                raise AssertionError(f"Cursor read more than once! (read_count={self.read_count})")
            return self.rows

    def test_synthetic_duration_deterministic_and_varied(self):
        from os_engine.experiments import calculate_synthetic_duration

        # 1. Deterministic repeatability
        self.assertEqual(calculate_synthetic_duration(1), calculate_synthetic_duration(1))
        self.assertEqual(calculate_synthetic_duration(2), calculate_synthetic_duration(2))

        # 2. Varied durations across different request IDs
        d1 = calculate_synthetic_duration(1)
        d2 = calculate_synthetic_duration(2)
        d3 = calculate_synthetic_duration(3)
        self.assertNotEqual(d1, d2)
        self.assertNotEqual(d2, d3)

        # 3. All durations strictly in [120, 1200]
        for req_id in range(1, 25):
            dur = calculate_synthetic_duration(req_id)
            self.assertGreaterEqual(dur, 120.0)
            self.assertLessEqual(dur, 1200.0)

    def test_sjf_selects_shorter_available_job_before_longer_job(self):
        t0 = "2026-01-01 10:00:00"
        # request_id=1 gets 960s, request_id=3 gets 240s
        cursor = self.SingleReadCursor([
            (1, t0, "HIGH"),
            (3, t0, "LOW"),
        ])
        result = run_fair_algorithm_comparison(cursor)

        sjf = next(a for a in result["algorithms"] if a["algorithm"] == "SJF")
        fcfs = next(a for a in result["algorithms"] if a["algorithm"] == "FCFS")

        # FCFS processes in request order (1 then 3)
        self.assertEqual([r["request_id"] for r in fcfs["results"]], [1, 3])
        # SJF processes shorter job first (request 3 has 240s, request 1 has 960s)
        self.assertEqual([r["request_id"] for r in sjf["results"]], [3, 1])
        self.assertEqual(sjf["results"][0]["duration_seconds"], 240.0)
        self.assertEqual(sjf["results"][1]["duration_seconds"], 960.0)
        # SJF has lower average waiting time than FCFS
        self.assertLess(sjf["average_wait_seconds"], fcfs["average_wait_seconds"])

    def test_four_algorithms_receive_identical_workload_and_durations(self):
        t0 = "2026-01-01 10:00:00"
        t1 = "2026-01-01 10:05:00"
        t2 = "2026-01-01 10:10:00"
        cursor = self.SingleReadCursor([
            (1, t0, "HIGH"),
            (2, t1, "CRITICAL"),
            (3, t2, "LOW"),
        ])
        result = run_fair_algorithm_comparison(cursor)
        self.assertEqual(len(result["algorithms"]), 4)

        base_requests = {r["request_id"]: (r["arrival"], r["duration_seconds"]) for r in result["algorithms"][0]["results"]}
        for algo in result["algorithms"][1:]:
            algo_requests = {r["request_id"]: (r["arrival"], r["duration_seconds"]) for r in algo["results"]}
            self.assertEqual(
                base_requests,
                algo_requests,
                f"Workload mismatch in algorithm {algo['algorithm']}",
            )

    def test_run_fair_algorithm_comparison_evaluates_all_four_policies(self):
        t0 = "2026-01-01 10:00:00"
        t1 = "2026-01-01 10:05:00"
        cursor = self.SingleReadCursor([
            (1, t0, "HIGH"),
            (2, t1, "CRITICAL"),
        ])
        result = run_fair_algorithm_comparison(cursor)
        self.assertEqual(len(result["algorithms"]), 4)
        algo_names = [a["algorithm"] for a in result["algorithms"]]
        self.assertEqual(algo_names, ["FCFS", "PRIORITY", "RR", "SJF"])
        self.assertEqual(len(result["comparison"]), 4)
        for comp in result["comparison"]:
            self.assertEqual(comp["request_count"], 2)
            self.assertTrue(comp["is_simulated"])
            self.assertIn("average_wait_seconds", comp)
            self.assertIn("average_turnaround_seconds", comp)
            self.assertIn("average_response_seconds", comp)
            self.assertIn("throughput_per_second", comp)
            self.assertIn("throughput_per_hour", comp)

    def test_workload_consistency_and_custom_duration_preservation(self):
        """Regression test: exactly one cursor read, custom durations preserved across all 4 algorithms."""
        t0 = "2026-01-01 10:00:00"
        t1 = "2026-01-01 10:05:00"
        cursor = self.SingleReadCursor([
            (101, t0, "HIGH"),
            (102, t1, "LOW"),
        ])
        custom_durations = {101: 150.0, 102: 450.0}
        result = run_fair_algorithm_comparison(cursor, durations=custom_durations)

        # 1. Cursor was read exactly once
        self.assertEqual(cursor.read_count, 1)

        # 2. All 4 algorithms evaluated
        self.assertEqual(len(result["algorithms"]), 4)

        # 3. Every algorithm received the exact same workload with custom durations
        for algo in result["algorithms"]:
            self.assertEqual(len(algo["results"]), 2)
            r101 = next(item for item in algo["results"] if item["request_id"] == 101)
            r102 = next(item for item in algo["results"] if item["request_id"] == 102)
            self.assertEqual(r101["duration_seconds"], 150.0)
            self.assertEqual(r102["duration_seconds"], 450.0)

    def test_run_fair_algorithm_comparison_accepts_preloaded_rows(self):
        t0 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        preloaded = [
            {"request_id": 1, "request_time": t0, "priority": "HIGH", "duration_seconds": 200},
            {"request_id": 2, "request_time": t0, "priority": "CRITICAL", "duration_seconds": 300},
        ]
        # cursor is None: must not raise error and must not query database
        result = run_fair_algorithm_comparison(cursor=None, rows=preloaded)
        self.assertEqual(len(result["algorithms"]), 4)
        for algo in result["algorithms"]:
            self.assertEqual(algo["request_count"], 2)


class AnalyticsPageAndAuthTests(unittest.TestCase):
    def setUp(self):
        from app import app
        from unittest.mock import patch

        app.config["TESTING"] = True
        app.secret_key = "unit-test-secret-key-32-chars-long!!"
        self.count_patcher = patch(
            "modules.notifications.service.get_unread_notification_count", return_value=0
        )
        self.count_patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.count_patcher.stop()

    def test_scheduling_analytics_renders_four_graphs_and_summary_table(self):
        from unittest.mock import patch
        from tests.test_metrics import SequenceConnection, SequenceCursor
        from decimal import Decimal

        cursor = SequenceCursor(
            fetchall_results=[
                [
                    ("FCFS", 2, Decimal("120"), 10, 230),
                    ("PRIORITY", 3, Decimal("80"), 20, 180),
                    ("RR", 1, Decimal("45"), 45, 45),
                ],
                [("CRITICAL", 1, Decimal("30"), 30)],
                [("PENDING", 2), ("ALLOCATED", 1)],
                [
                    (1, "FCFS", "manual", 0, "COMPLETED", "2026-01-01 10:00:00", "2026-01-01 10:05:00", None, 2),
                ],
                [("2026-01-01", 3)],
                [(1, "2026-01-01 09:00:00", "HIGH"), (2, "2026-01-01 09:30:00", "CRITICAL")],
            ],
            fetchone_results=[
                (1, 0, 0, 0),
                (2, Decimal("90"), 120, Decimal("1")),
                (3, 1, 1, 1),
            ],
        )

        with patch("core.db.get_db_connection", return_value=SequenceConnection(cursor)):
            response = self.client.get("/reports/scheduling-analytics")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # 1. Four comparison graph canvas elements
        self.assertIn('id="simWaitChart"', html)
        self.assertIn('id="simTurnaroundChart"', html)
        self.assertIn('id="simResponseChart"', html)
        self.assertIn('id="simThroughputChart"', html)

        # 2. Unified 4-algorithm summary table
        self.assertIn("Unified Four-Algorithm Performance Summary", html)
        self.assertIn("<th>Sample Size (N)</th>", html)
        self.assertIn("<th>Avg Waiting Time</th>", html)
        self.assertIn("<th>Avg Turnaround Time</th>", html)
        self.assertIn("<th>Avg Response Time</th>", html)
        self.assertIn("<th>Throughput (req/h)</th>", html)
        self.assertIn("<th>Observation Window</th>", html)
        self.assertIn("<th>Classification</th>", html)
        self.assertIn("req/h", html)

        # 3. All 4 algorithms present with simulation classification
        self.assertIn("<strong>FCFS</strong>", html)
        self.assertIn("<strong>PRIORITY</strong>", html)
        self.assertIn("<strong>RR</strong>", html)
        self.assertIn("<strong>SJF</strong>", html)
        self.assertIn("Controlled Simulation", html)

        # 4. JSON payloads populated in script
        self.assertIn("simWaitingComparison", html)
        self.assertIn("simTurnaroundComparison", html)
        self.assertIn("simResponseComparison", html)
        self.assertIn("simThroughputComparison", html)

    def test_scheduling_analytics_renders_empty_state_gracefully(self):
        from unittest.mock import patch
        from tests.test_metrics import SequenceConnection, SequenceCursor

        cursor = SequenceCursor(
            fetchall_results=[
                [], [], [], [], [], [],
            ],
            fetchone_results=[
                (0, 0, 0, 0),
                (0, None, None, 0),
                (0, None, None, None),
            ],
        )

        with patch("core.db.get_db_connection", return_value=SequenceConnection(cursor)):
            response = self.client.get("/reports/scheduling-analytics")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("No simulation data is available because there are no historical requests to replay.", html)

    def test_unauthorized_user_cannot_trigger_schedule(self):
        """Security check: unauthenticated user without officer session or CSRF cannot trigger /requests/schedule."""
        # 1. Missing CSRF rejected with 400
        res_no_csrf = self.client.post("/requests/schedule", data={"algorithm": "FCFS"})
        self.assertEqual(res_no_csrf.status_code, 400)

        # 2. Valid CSRF but missing officer session is redirected to officer login (302)
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "valid-csrf-token-1234"
        res_unauth = self.client.post(
            "/requests/schedule",
            data={"algorithm": "FCFS", "_csrf_token": "valid-csrf-token-1234"},
        )
        self.assertEqual(res_unauth.status_code, 302)
        self.assertIn("/officer/login", res_unauth.headers.get("Location", ""))


if __name__ == "__main__":
    unittest.main()

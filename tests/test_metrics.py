import unittest
import re
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from app import app
from modules.reports.routes import _duration_to_seconds
from os_engine.experiments import (
    SIMULATION_PROCESSING_TIME_SECONDS,
    _simulate_requests,
    run_scheduling_experiment,
)
from os_engine.metrics import format_duration, get_scheduling_metrics


class SequenceCursor:
    """Returns pre-set results in the order the code asks for them."""

    def __init__(self, fetchall_results, fetchone_results):
        self.fetchall_results = iter(fetchall_results)
        self.fetchone_results = iter(fetchone_results)
        self.statements = []

    def execute(self, statement, parameters=None):
        self.statements.append((" ".join(statement.split()), parameters))

    def fetchall(self):
        return next(self.fetchall_results)

    def fetchone(self):
        return next(self.fetchone_results)

    def close(self):
        pass


class SequenceConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, **kwargs):
        return self.fake_cursor

    def close(self):
        pass


class FormatDurationTests(unittest.TestCase):
    def test_formats_seconds_minutes_and_hours(self):
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(125), "2m 05s")
        self.assertEqual(format_duration(7260), "2h 01m")

    def test_accepts_decimal_from_mysql_avg_and_rounds(self):
        self.assertEqual(format_duration(Decimal("59.6")), "1m 00s")

    def test_none_means_no_data(self):
        self.assertEqual(format_duration(None), "-")

    def test_missing_duration_stays_missing_for_chart_data(self):
        self.assertIsNone(_duration_to_seconds(None))
        self.assertIsNone(_duration_to_seconds("-"))
        self.assertIsNone(_duration_to_seconds(""))


class SchedulingMetricsTests(unittest.TestCase):
    def test_builds_all_three_tables_from_query_results(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [("FCFS", 3, Decimal("10.5"), 2, 30), ("MANUAL", 1, Decimal("0"), 0, 0)],
                # deliberately out of order: output must follow CRITICAL..LOW
                [("LOW", 1, Decimal("300"), 300), ("CRITICAL", 2, Decimal("5"), 8)],
            ],
            fetchone_results=[(2, Decimal("125"), 300, Decimal("1"))],
        )

        metrics = get_scheduling_metrics(cursor)

        self.assertEqual(metrics["by_algorithm"][0], {
            "algorithm": "FCFS", "count": 3,
            "average": "11s", "shortest": "2s", "longest": "30s",
        })
        self.assertEqual(
            [row["priority"] for row in metrics["by_priority"]], ["CRITICAL", "LOW"]
        )
        self.assertEqual(metrics["by_priority"][1]["average"], "5m 00s")
        self.assertEqual(metrics["pending"], {
            "count": 2, "average": "2m 05s", "longest": "5m 00s",
            "aged": 1, "aged_count": 1,
        })
        self.assertEqual(metrics["aging_threshold_minutes"], 15)
        # the aging threshold (900 s) is passed as a query parameter
        self.assertEqual(cursor.statements[-1][1], (900,))
        self.assertIn("WHERE status = 'PENDING'", cursor.statements[-1][0])
        self.assertIn("AND request_time IS NOT NULL", cursor.statements[-1][0])

    def test_no_data_gives_empty_tables_and_zero_pending(self):
        cursor = SequenceCursor(
            fetchall_results=[[], []],
            fetchone_results=[(0, None, None, None)],
        )
        metrics = get_scheduling_metrics(cursor)
        self.assertEqual(metrics["by_algorithm"], [])
        self.assertEqual(metrics["by_priority"], [])
        self.assertEqual(metrics["pending"], {
            "count": 0, "average": "-", "longest": "-",
            "aged": 0, "aged_count": 0,
        })

    def test_allocation_wait_queries_exclude_unsuccessful_and_missing_timestamps(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [("FCFS", 1, Decimal("20"), 20, 20)],
                [("HIGH", 1, Decimal("20"), 20)],
            ],
            fetchone_results=[],
        )

        get_scheduling_metrics(cursor)

        algorithm_sql = cursor.statements[0][0]
        priority_sql = cursor.statements[1][0]
        for sql in (algorithm_sql, priority_sql):
            self.assertIn("WHERE a.status = 'ALLOCATED'", sql)
            self.assertIn("er.request_time IS NOT NULL", sql)
            self.assertIn("a.allocation_time IS NOT NULL", sql)
            self.assertIn("a.allocation_time >= er.request_time", sql)
            self.assertNotIn("GREATEST", sql)

    def test_duplicate_run_items_are_attributed_to_one_nearest_run(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [
                    ("RR", 1, Decimal("30"), 30, 30),
                    ("MANUAL", 1, Decimal("45"), 45, 45),
                ],
                [],
            ],
            fetchone_results=[],
        )

        metrics = get_scheduling_metrics(cursor)

        algorithm_sql = cursor.statements[0][0]
        self.assertIn("SELECT sr.algorithm", algorithm_sql)
        self.assertIn("ABS(TIMESTAMPDIFF(MICROSECOND", algorithm_sql)
        self.assertIn("LIMIT 1", algorithm_sql)
        self.assertIn("sr.run_id ASC", algorithm_sql)
        self.assertNotIn("LEFT JOIN scheduling_run_items", algorithm_sql)
        self.assertEqual(
            [(row["algorithm"], row["count"]) for row in metrics["by_algorithm"]],
            [("RR", 1), ("MANUAL", 1)],
        )

    def test_valid_allocations_report_wait_samples_and_correct_durations(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [
                    ("FCFS", 2, Decimal("15"), 10, 20),
                    ("RR", 1, Decimal("45"), 45, 45),
                ],
                [("HIGH", 3, Decimal("25"), 45)],
            ],
            fetchone_results=[],
        )

        metrics = get_scheduling_metrics(cursor)

        self.assertEqual(metrics["by_algorithm"], [
            {
                "algorithm": "FCFS", "count": 2,
                "average": "15s", "shortest": "10s", "longest": "20s",
            },
            {
                "algorithm": "RR", "count": 1,
                "average": "45s", "shortest": "45s", "longest": "45s",
            },
        ])
        self.assertEqual(metrics["by_priority"], [{
            "priority": "HIGH", "count": 3,
            "average": "25s", "longest": "45s",
        }])

    def test_zero_second_allocation_wait_is_valid(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [("FCFS", 1, Decimal("0"), 0, 0)],
                [("HIGH", 1, Decimal("0"), 0)],
            ],
            fetchone_results=[],
        )

        metrics = get_scheduling_metrics(cursor)

        self.assertEqual(metrics["by_algorithm"][0]["count"], 1)
        self.assertEqual(metrics["by_algorithm"][0]["average"], "0s")
        self.assertEqual(metrics["by_priority"][0]["count"], 1)
        self.assertEqual(metrics["by_priority"][0]["average"], "0s")
        self.assertIn("a.allocation_time >= er.request_time", cursor.statements[0][0])
        self.assertIn("a.allocation_time >= er.request_time", cursor.statements[1][0])


class AnalyticsExtensionTests(unittest.TestCase):
    def test_metrics_include_aging_and_status_breakdown(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [("FCFS", 2, Decimal("120"), 10, 230), ("PRIORITY", 3, Decimal("80"), 20, 180)],
                [("CRITICAL", 2, Decimal("50"), 90), ("LOW", 1, Decimal("300"), 300)],
                [("PENDING", 2), ("ALLOCATED", 3)],
                [
                    (1, "FCFS", "manual", 0, "COMPLETED",
                     "2026-01-01 10:00:00", "2026-01-01 10:05:00", None, 3),
                    (2, "RR", "empty queue", 0, "COMPLETED",
                     "2026-01-01 10:10:00", "2026-01-01 10:10:01", 0, 0),
                ],
                [("2026-01-01", 4)],
            ],
            fetchone_results=[
                (2, Decimal("90"), 120, Decimal("1")),
            ],
        )

        metrics = get_scheduling_metrics(cursor)

        self.assertIn("status_distribution", metrics)
        self.assertIn("scheduler_history", metrics)
        self.assertEqual(metrics["request_arrivals"], [{
            "day": "2026-01-01",
            "request_count": 4,
        }])
        arrivals_sql = cursor.statements[4][0]
        self.assertIn("COUNT(*) AS request_count", arrivals_sql)
        self.assertIn("WHERE request_time IS NOT NULL", arrivals_sql)
        self.assertNotIn("status = 'PENDING'", arrivals_sql)
        self.assertIn("aging_breakdown", metrics)
        self.assertEqual(metrics["aging_breakdown"]["aged"], 1)
        self.assertEqual(metrics["scheduler_history"][0]["algorithm"], "FCFS")
        self.assertEqual(metrics["scheduler_history"][0]["processed_count"], 3)
        self.assertEqual(metrics["scheduler_history"][0]["rr_start_request_id"], None)
        self.assertEqual(metrics["scheduler_history"][1]["processed_count"], 0)
        self.assertEqual(metrics["scheduler_history"][1]["rr_start_request_id"], 0)
        runs_sql = next(
            sql for sql, _ in cursor.statements if "FROM scheduling_runs" in sql
        )
        self.assertIn("LEFT JOIN scheduling_run_items sri ON sri.run_id = sr.run_id", runs_sql)
        self.assertIn("sri.outcome IN ('ALLOCATED', 'FAILED', 'SKIPPED')", runs_sql)
        self.assertNotIn("'PROCESSING'", runs_sql)
        self.assertIn("sr.rr_start_request_id", runs_sql)
        self.assertIn("ORDER BY sr.started_at DESC", runs_sql)
        self.assertIn("LIMIT 20", runs_sql)
        self.assertIn("GROUP BY sr.run_id", runs_sql)

    def test_scheduling_experiment_uses_same_request_set_without_mutating_db(self):
        class ExperimentCursor:
            def __init__(self, rows):
                self.rows = rows
                self.executed = False

            def execute(self, statement, parameters=None):
                self.executed = True

            def fetchall(self):
                return self.rows

        rows = [
            (1, "2026-01-01 09:00:00", "HIGH"),
            (2, "2026-01-01 09:05:00", "CRITICAL"),
            (3, "2026-01-01 09:10:00", "LOW"),
        ]
        cursor = ExperimentCursor(rows)

        result = run_scheduling_experiment(cursor)

        self.assertTrue(cursor.executed)
        self.assertEqual(len(result["algorithms"]), 2)
        self.assertEqual(result["comparison"][0]["algorithm"], "FCFS")
        self.assertEqual(result["comparison"][1]["algorithm"], "PRIORITY")
        self.assertEqual(
            [algorithm["request_count"] for algorithm in result["algorithms"]],
            [3, 3],
        )
        self.assertEqual(
            [
                [item["request_id"] for item in algorithm["results"]]
                for algorithm in result["algorithms"]
            ],
            [[1, 2, 3], [1, 2, 3]],
        )
        self.assertEqual(
            result["algorithms"][0]["reference_time"],
            result["algorithms"][1]["reference_time"],
        )
        self.assertEqual(result["reference_time"], result["algorithms"][0]["reference_time"])
        self.assertIn("Illustrative", result["label"])
        self.assertIn("not actual production performance", result["label"])
        self.assertEqual(result["processing_time_seconds"], SIMULATION_PROCESSING_TIME_SECONDS)
        self.assertEqual(SIMULATION_PROCESSING_TIME_SECONDS, 600)
        self.assertNotIn("RR", [algorithm["algorithm"] for algorithm in result["algorithms"]])
        self.assertTrue(all(
            "response_seconds" not in request
            for algorithm in result["algorithms"]
            for request in algorithm["results"]
        ))

    def test_scheduling_experiment_excludes_null_request_timestamps(self):
        class ExperimentCursor:
            def __init__(self):
                self.rows = [
                    (1, "2026-01-01 09:00:00", "HIGH"),
                    (2, None, "CRITICAL"),
                    (3, "not-a-timestamp", "LOW"),
                ]
                self.statement = None

            def execute(self, statement, parameters=None):
                self.statement = " ".join(statement.split())

            def fetchall(self):
                return self.rows

        cursor = ExperimentCursor()
        result = run_scheduling_experiment(cursor)

        self.assertIn("WHERE request_time IS NOT NULL", cursor.statement)
        self.assertEqual(
            [
                [item["request_id"] for item in algorithm["results"]]
                for algorithm in result["algorithms"]
            ],
            [[1], [1]],
        )

    def test_experiment_passes_one_reference_timestamp_to_both_algorithms(self):
        class ExperimentCursor:
            def execute(self, statement, parameters=None):
                pass

            def fetchall(self):
                return [
                    (1, "2026-01-01 09:00:00", "HIGH"),
                    (2, "2026-01-01 09:05:00", "LOW"),
                ]

        observed = []
        original_simulate = _simulate_requests

        def capture_reference(rows, algorithm, reference_time):
            observed.append((algorithm, reference_time))
            return original_simulate(rows, algorithm, reference_time)

        with patch("os_engine.experiments._simulate_requests", side_effect=capture_reference):
            result = run_scheduling_experiment(ExperimentCursor())

        self.assertEqual([algorithm for algorithm, _ in observed], ["FCFS", "PRIORITY"])
        self.assertEqual(observed[0][1], observed[1][1])
        self.assertEqual(observed[0][1], result["reference_time"])

    def test_simulation_wait_and_turnaround_formulas(self):
        arrival = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
        reference_time = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        rows = [
            {"request_id": 1, "request_time": arrival, "priority": "HIGH"},
            {"request_id": 2, "request_time": arrival, "priority": "LOW"},
        ]

        result = _simulate_requests(rows, "FCFS", reference_time)

        first, second = result["results"]
        self.assertEqual(first["start_time"], arrival)
        self.assertEqual(first["waiting_seconds"], (first["start_time"] - first["arrival"]).total_seconds())
        self.assertEqual(first["turnaround_seconds"], (first["completion_time"] - first["arrival"]).total_seconds())
        self.assertEqual(first["turnaround_seconds"], SIMULATION_PROCESSING_TIME_SECONDS)
        self.assertEqual(second["waiting_seconds"], SIMULATION_PROCESSING_TIME_SECONDS)
        self.assertEqual(second["turnaround_seconds"], 2 * SIMULATION_PROCESSING_TIME_SECONDS)

    def test_experiment_normalizes_z_offset_and_mixed_timestamps_to_utc(self):
        class ExperimentCursor:
            def execute(self, statement, parameters=None):
                pass

            def fetchall(self):
                return [
                    (1, "2026-01-01T09:00:00Z", "HIGH"),
                    (2, "2026-01-01T12:00:00+02:00", "LOW"),
                    (3, "2026-01-01 10:00:00", "MEDIUM"),
                ]

        result = run_scheduling_experiment(ExperimentCursor())

        # All three timestamps represent 09:00, 10:00, and 10:00 UTC respectively.
        arrivals = [
            item["arrival"]
            for item in result["algorithms"][0]["results"]
        ]
        self.assertEqual(arrivals, [
            datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        ])
        self.assertEqual(result["reference_time"].utcoffset().total_seconds(), 0)
        self.assertEqual(
            result["algorithms"][0]["reference_time"],
            result["algorithms"][1]["reference_time"],
        )
        self.assertEqual(
            [algorithm["request_count"] for algorithm in result["algorithms"]],
            [3, 3],
        )


class ReportsPageTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "unit-test-only-session-secret-value-0001"
        self.count_patcher = patch(
            "modules.notifications.service.get_unread_notification_count", return_value=0
        )
        self.count_patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.count_patcher.stop()

    def test_reports_page_shows_scheduling_metrics(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [],  # allocation history
                [("PRIORITY", 4, Decimal("125"), 10, 400)],
                [("CRITICAL", 4, Decimal("125"), 400)],
            ],
            fetchone_results=[
                (3, 1),            # disasters
                (4, 100, 80),      # resources
                (5, 1, 4, 0),      # requests
                (1, Decimal("60"), 60, Decimal("0")),  # pending
            ],
        )
        with patch("core.db.get_db_connection", return_value=SequenceConnection(cursor)):
            response = self.client.get("/reports")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Scheduling Metrics", page)
        self.assertIn("PRIORITY", page)
        self.assertIn("2m 05s", page)      # average wait of 125 s
        self.assertIn("CRITICAL", page)
        self.assertIn("Aged (over 15 min)", page)

    def test_scheduling_analytics_page_renders(self):
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
                    (2, "PRIORITY", "automatic", 1, "COMPLETED", "2026-01-01 10:10:00", "2026-01-01 10:15:00", None, 1),
                    (3, "RR", "manual", 0, "RUNNING", "2026-01-01 10:20:00", None, 17, 3),
                    (4, "RR", "empty queue", 0, "COMPLETED", "2026-01-01 10:25:00", "2026-01-01 10:25:01", None, 0),
                    (5, "FCFS", "failed run", 0, "FAILED", "2026-01-01 10:30:00", None, None, 0),
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
        page = response.get_data(as_text=True)
        self.assertIn("Scheduling Analytics", page)
        self.assertIn("Recent Scheduling Runs", page)
        self.assertIn("Latest 20 recorded runs.", page)
        self.assertIn("<th>Algorithm</th>", page)
        self.assertIn("<th>Started At</th>", page)
        self.assertIn("<th>Finished At</th>", page)
        self.assertIn("<th>Status</th>", page)
        self.assertIn("<th>Processed Requests</th>", page)
        self.assertIn("<th>RR Start Request ID</th>", page)
        self.assertIn("<td>FCFS</td>", page)
        self.assertIn("<td>PRIORITY</td>", page)
        self.assertIn("<td>RR</td>", page)
        self.assertRegex(page, r"<td>\s*In Progress\s*</td>")
        self.assertRegex(
            page,
            r"<td>FCFS</td>\s*<td>2026-01-01 10:30:00</td>\s*"
            r"<td>\s*&mdash;\s*</td>\s*<td>FAILED</td>",
        )
        self.assertRegex(
            page,
            r"<td>FCFS</td>\s*<td>2026-01-01 10:00:00</td>\s*"
            r"<td>\s*2026-01-01 10:05:00\s*</td>\s*<td>COMPLETED</td>",
        )
        self.assertIn("<td>COMPLETED</td>", page)
        self.assertIn("<td>RUNNING</td>", page)
        self.assertRegex(page, r"<td>\s*17\s*</td>")
        self.assertRegex(page, r"<td>\s*0\s*</td>")
        self.assertRegex(page, r"<td>\s*2\s*</td>")
        self.assertRegex(page, r"<td>\s*1\s*</td>")
        self.assertEqual(page.count("&mdash;"), 5)
        self.assertIn("Illustrative simulation only", page)
        self.assertIn("Priority aging uses the single UTC reference timestamp", page)
        self.assertIn("timestamps are normalized to UTC", page)
        self.assertIn("This illustrates queue behavior and is not actual production performance", page)
        self.assertIn("Metric Definitions", page)
        self.assertIn("Requests Created per Day", page)
        self.assertIn("Requests created", page)
        self.assertIn(
            "Pending Requests Aged (>= 900 seconds / 15 minutes)",
            page,
        )
        aged_display = re.search(
            r"<h3>Pending Requests Aged.*?<div class=\"number\">(\d+)</div>"
            r".*?Total pending requests: (\d+)",
            page,
            re.DOTALL,
        )
        self.assertEqual(aged_display.groups(), ("1", "2"))
        self.assertIn("Average pending age: 1m 30s", page)
        self.assertIn("Maximum pending age: 2m 00s", page)
        self.assertIn("Aged pending threshold", page)
        self.assertIn("&gt;= 900 seconds (15 minutes)", page)
        self.assertIn(
            "Allocation Wait (Request Creation &rarr; Allocation)",
            page,
        )
        self.assertIn(
            "FCFS Allocation Wait (Request Creation &rarr; Allocation)",
            page,
        )
        self.assertIn(
            "Allocation Wait (Request Creation &rarr; Allocation) by Priority",
            page,
        )
        self.assertIn(
            "Elapsed time from request creation to allocation; not processing-start wait.",
            page,
        )
        fcfs_wait_display = re.search(
            r"<h3>FCFS Allocation Wait.*?<div class=\"number\">(.*?)</div>",
            page,
            re.DOTALL,
        )
        self.assertEqual(fcfs_wait_display.group(1).strip(), "2m 00s")
        self.assertIn("Allocation Events Measured", page)
        self.assertIn("Allocation events measured", page)
        self.assertIn(
            "These are observed operational results, not a controlled or fair benchmark "
            "of FCFS, PRIORITY, and RR.",
            page,
        )
        self.assertIn(
            "Different request mixes and inventory conditions can affect the results.",
            page,
        )
        self.assertIn("MANUAL denotes manual allocations, not a scheduling algorithm.", page)

    def test_scheduling_analytics_shows_na_and_null_chart_value_without_fcfs_sample(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [("FCFS", 0, None, None, None)],
                [],
                [],
                [],
                [],
                [],
            ],
            fetchone_results=[
                (0, 0, 0, 0),
                (0, None, None, 0),
            ],
        )
        with patch("core.db.get_db_connection", return_value=SequenceConnection(cursor)):
            response = self.client.get("/reports/scheduling-analytics")

        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIsNotNone(re.search(
            r"<h3>FCFS Allocation Wait.*?<div class=\"number\">\s*N/A\s*</div>",
            page,
            re.DOTALL,
        ))
        self.assertIn('"average_wait_seconds": null', page)

    def test_scheduling_analytics_empty_arrivals_message(self):
        cursor = SequenceCursor(
            fetchall_results=[
                [],
                [],
                [],
                [],
                [],
                [],
                [],
                [],
                [],
                [],
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
        self.assertIn(
            "No dated requests are available to chart.",
            response.get_data(as_text=True),
        )
        page = response.get_data(as_text=True)
        aged_display = re.search(
            r"<h3>Pending Requests Aged.*?<div class=\"number\">(\d+)</div>"
            r".*?Total pending requests: (\d+)",
            page,
            re.DOTALL,
        )
        self.assertEqual(aged_display.groups(), ("0", "0"))

    def test_scheduling_analytics_empty_run_history_message(self):
        cursor = SequenceCursor(
            fetchall_results=[[], [], [], [], [], []],
            fetchone_results=[
                (0, 0, 0, 0),
                (0, None, None, 0),
                (0, None, None, None),
            ],
        )
        with patch("core.db.get_db_connection", return_value=SequenceConnection(cursor)):
            response = self.client.get("/reports/scheduling-analytics")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "No scheduling runs have been recorded.",
            response.get_data(as_text=True),
        )


if __name__ == "__main__":
    unittest.main()
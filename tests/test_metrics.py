import unittest
from decimal import Decimal
from unittest.mock import patch

from app import app
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
            "count": 2, "average": "2m 05s", "longest": "5m 00s", "aged": 1,
        })
        self.assertEqual(metrics["aging_threshold_minutes"], 15)
        # the aging threshold (900 s) is passed as a query parameter
        self.assertEqual(cursor.statements[-1][1], (900,))

    def test_no_data_gives_empty_tables_and_zero_pending(self):
        cursor = SequenceCursor(
            fetchall_results=[[], []],
            fetchone_results=[(0, None, None, None)],
        )
        metrics = get_scheduling_metrics(cursor)
        self.assertEqual(metrics["by_algorithm"], [])
        self.assertEqual(metrics["by_priority"], [])
        self.assertEqual(metrics["pending"], {
            "count": 0, "average": "-", "longest": "-", "aged": 0,
        })


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


if __name__ == "__main__":
    unittest.main()
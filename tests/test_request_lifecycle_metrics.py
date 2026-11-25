"""Phase 5 - P1: Request Lifecycle Metrics Harmonization Tests.

Verifies consistent request lifecycle categorization (TOTAL, PENDING,
ALLOCATED, PARTIALLY_ALLOCATED, REJECTED, COMPLETED) across both
/reports and /reports/scheduling-analytics, safe NULL/empty table handling,
and mathematical equivalence between TOTAL and the sum of parts.
"""

import unittest
from unittest.mock import patch

from app import app
from modules.reports.routes import (
    RequestLifecycleSummary,
    get_request_lifecycle_summary,
)


class MockLifecycleCursor:
    def __init__(self, fetchone_result):
        self.fetchone_result = fetchone_result
        self.executed_statements = []

    def execute(self, statement, parameters=None):
        self.executed_statements.append((" ".join(statement.split()), parameters))

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return []

    def close(self):
        pass


class MockLifecycleConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, **kwargs):
        return self.fake_cursor

    def close(self):
        pass


class RequestLifecycleMetricsTests(unittest.TestCase):
    def test_lifecycle_summary_all_five_statuses(self):
        """Verify that all five mutually exclusive statuses are extracted correctly."""
        # Query returns: total=15, pending=4, allocated=6, partially_allocated=2, rejected=1, completed=2
        cursor = MockLifecycleCursor(
            (15, 4, 6, 2, 1, 2)
        )
        summary = get_request_lifecycle_summary(cursor)

        self.assertIsInstance(summary, RequestLifecycleSummary)
        self.assertEqual(summary.total, 15)
        self.assertEqual(summary.pending, 4)
        self.assertEqual(summary.allocated, 6)
        self.assertEqual(summary.partially_allocated, 2)
        self.assertEqual(summary.rejected, 1)
        self.assertEqual(summary.completed, 2)

        # TOTAL strictly equals sum of all 5 lifecycle status counts
        sum_of_parts = (
            summary.pending
            + summary.allocated
            + summary.partially_allocated
            + summary.rejected
            + summary.completed
        )
        self.assertEqual(summary.total, sum_of_parts)

    def test_lifecycle_summary_empty_table_handles_null_safely(self):
        """Verify that an empty table with NULL SQL aggregate returns zeroes without errors."""
        # When table is empty, COUNT(*) is 0 and SUM() returns NULL
        cursor = MockLifecycleCursor((0, None, None, None, None, None))
        summary = get_request_lifecycle_summary(cursor)

        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.pending, 0)
        self.assertEqual(summary.allocated, 0)
        self.assertEqual(summary.partially_allocated, 0)
        self.assertEqual(summary.rejected, 0)
        self.assertEqual(summary.completed, 0)
        self.assertEqual(summary.total, 0)

    def test_lifecycle_summary_none_row_fallback(self):
        """Verify that a None row result falls back to all zeroes."""
        cursor = MockLifecycleCursor(None)
        summary = get_request_lifecycle_summary(cursor)

        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.pending, 0)
        self.assertEqual(summary.allocated, 0)
        self.assertEqual(summary.partially_allocated, 0)
        self.assertEqual(summary.rejected, 0)
        self.assertEqual(summary.completed, 0)

    def test_sum_of_lifecycle_counts_strictly_equals_total(self):
        """Verify mathematical equivalence for diverse status distributions."""
        test_distributions = [
            # (total, pending, allocated, partially_allocated, rejected, completed)
            (100, 40, 35, 10, 5, 10),
            (1, 1, 0, 0, 0, 0),
            (50, 0, 50, 0, 0, 0),
            (23, 5, 12, 1, 3, 2),
        ]
        for dist in test_distributions:
            cursor = MockLifecycleCursor(dist)
            summary = get_request_lifecycle_summary(cursor)
            self.assertEqual(
                summary.total,
                summary.pending
                + summary.allocated
                + summary.partially_allocated
                + summary.rejected
                + summary.completed,
            )

    def test_sql_query_contains_coalesce_and_all_five_statuses(self):
        """Verify that the SQL query aggregates all 5 statuses using COALESCE."""
        cursor = MockLifecycleCursor((0, 0, 0, 0, 0, 0))
        get_request_lifecycle_summary(cursor)
        executed_sql = cursor.executed_statements[0][0]

        self.assertIn("COUNT(*)", executed_sql)
        self.assertIn("COALESCE(SUM(CASE WHEN status = 'PENDING'", executed_sql)
        self.assertIn("COALESCE(SUM(CASE WHEN status = 'ALLOCATED'", executed_sql)
        self.assertIn("COALESCE(SUM(CASE WHEN status = 'PARTIALLY_ALLOCATED'", executed_sql)
        self.assertIn("COALESCE(SUM(CASE WHEN status = 'REJECTED'", executed_sql)
        self.assertIn("COALESCE(SUM(CASE WHEN status = 'COMPLETED'", executed_sql)


class ReportsLifecycleViewsTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "unit-test-only-session-secret-key-32-bytes"
        self.count_patcher = patch(
            "modules.notifications.service.get_unread_notification_count", return_value=0
        )
        self.count_patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.count_patcher.stop()

    def test_reports_page_displays_all_harmonized_lifecycle_statuses(self):
        """Verify that /reports renders all 5 lifecycle statuses plus Total."""
        class MockReportsCursor:
            def __init__(self):
                self.calls = 0

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                self.calls += 1
                if self.calls == 1:
                    return (5, 3)  # disasters
                if self.calls == 2:
                    return (10, 200, 150)  # resources
                if self.calls == 3:
                    # request_stats: total=20, pending=5, allocated=10, partially=2, rejected=1, completed=2
                    return (20, 5, 10, 2, 1, 2)
                if self.calls == 4:
                    return (5, 120, 300, 1)  # pending metrics
                return (0, 0, 0, 0)

            def fetchall(self):
                return []

            def close(self):
                pass

        connection = MockLifecycleConnection(MockReportsCursor())
        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn("Total Requests", html)
        self.assertIn("Pending:", html)
        self.assertIn("Allocated:", html)
        self.assertIn("Partially Allocated:", html)
        self.assertIn("Rejected:", html)
        self.assertIn("Completed:", html)

    def test_scheduling_analytics_displays_all_harmonized_lifecycle_statuses(self):
        """Verify that /reports/scheduling-analytics renders all 5 lifecycle statuses plus Total."""
        class MockAnalyticsCursor:
            def __init__(self):
                self.calls = 0

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                self.calls += 1
                if self.calls == 1:
                    # realtime_summary: total=30, pending=8, allocated=15, partially=3, rejected=2, completed=2
                    return (30, 8, 15, 3, 2, 2)
                if self.calls == 2:
                    return (8, 120, 300, 2)  # pending metrics
                return (0, 0, 0, 0)

            def fetchall(self):
                return []

            def close(self):
                pass

        connection = MockLifecycleConnection(MockAnalyticsCursor())
        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports/scheduling-analytics")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn("Lifecycle Metric", html)
        self.assertIn("Total requests", html)
        self.assertIn("Pending requests", html)
        self.assertIn("Allocated requests", html)
        self.assertIn("Partially allocated requests", html)
        self.assertIn("Rejected requests", html)
        self.assertIn("Completed requests", html)


if __name__ == "__main__":
    unittest.main()

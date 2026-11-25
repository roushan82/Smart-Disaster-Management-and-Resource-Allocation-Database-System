"""Phase 5 - P3: Bound Allocation History Query Tests.

Verifies:
1. The allocation history SQL query on /reports includes LIMIT 50.
2. The ordering sorts by newest allocation_time DESC with allocation_id DESC tie-breaker.
3. The report template heading displays 'Showing latest 50 allocations'.
4. Existing table columns and rendering remain intact.
"""

import unittest
from unittest.mock import patch

from app import app


class MockReportsCursor:
    def __init__(self, allocations=None):
        self.allocations = allocations or []
        self.executed_statements = []
        self.calls = 0

    def execute(self, statement, parameters=None):
        self.executed_statements.append((" ".join(statement.split()), parameters))

    def fetchone(self):
        self.calls += 1
        if self.calls == 1:
            return (5, 2)  # disasters: total, active
        if self.calls == 2:
            return (10, 500, 350)  # resources: count, total, available
        if self.calls == 3:
            # request_stats: total, pending, allocated, partially, rejected, completed
            return (20, 5, 10, 2, 1, 2)
        if self.calls == 4:
            return (5, 120, 300, 1)  # pending wait metrics
        return (0, 0, 0, 0)

    def fetchall(self):
        last_stmt = self.executed_statements[-1][0] if self.executed_statements else ""
        if "JOIN resource_types rt" in last_stmt:
            return self.allocations
        return []

    def close(self):
        pass


class MockReportsConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, **kwargs):
        return self.fake_cursor

    def close(self):
        pass


class BoundedAllocationHistoryTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "test-secret-key-allocation-history"
        self.notification_patcher = patch(
            "modules.notifications.service.get_unread_notification_count",
            return_value=0,
        )
        self.notification_patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.notification_patcher.stop()

    def test_allocation_history_query_includes_limit_50_and_tie_breaker(self):
        """Verify allocation history SQL query includes LIMIT 50 and stable tie-breaker."""
        cursor = MockReportsCursor()
        connection = MockReportsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            self.client.get("/reports")

        # Find the allocations query among executed statements
        alloc_stmt = next(
            s[0] for s in cursor.executed_statements
            if "FROM allocations a" in s[0]
        )

        self.assertIn("LIMIT 50", alloc_stmt)
        self.assertIn("ORDER BY a.allocation_time DESC, a.allocation_id DESC", alloc_stmt)

    def test_report_template_displays_latest_50_wording(self):
        """Verify the report template section heading displays 'Showing latest 50 allocations'."""
        cursor = MockReportsCursor()
        connection = MockReportsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        self.assertIn("Allocation History (Showing latest 50 allocations)", html)
        self.assertIn("Showing latest 50 allocations", html)

    def test_allocation_history_rendering_intact(self):
        """Verify that allocation history rows render all 6 table columns accurately."""
        sample_allocations = [
            (501, 101, "Food Packets", 25, "2026-11-16 14:00:00", "ALLOCATED"),
            (500, 100, "Medicine Kits", 10, "2026-11-16 13:30:00", "ALLOCATED"),
        ]
        cursor = MockReportsCursor(allocations=sample_allocations)
        connection = MockReportsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Check table headers
        self.assertIn("<th>Allocation ID</th>", html)
        self.assertIn("<th>Request ID</th>", html)
        self.assertIn("<th>Resource</th>", html)
        self.assertIn("<th>Quantity Allocated</th>", html)
        self.assertIn("<th>Allocation Time</th>", html)
        self.assertIn("<th>Status</th>", html)

        # Check rendered data cells
        self.assertIn("<td>501</td>", html)
        self.assertIn("<td>101</td>", html)
        self.assertIn("<td>Food Packets</td>", html)
        self.assertIn("<td>25</td>", html)
        self.assertIn("<td>2026-11-16 14:00:00</td>", html)
        self.assertIn("<td>ALLOCATED</td>", html)

    def test_empty_allocation_history_rendering(self):
        """Verify empty state message renders when no allocation records exist."""
        cursor = MockReportsCursor(allocations=[])
        connection = MockReportsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("No allocation records found.", html)


if __name__ == "__main__":
    unittest.main()

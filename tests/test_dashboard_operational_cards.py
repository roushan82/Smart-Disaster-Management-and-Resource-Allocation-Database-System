"""Phase 5 - P2: Executive Dashboard Operational Cards Tests.

Verifies operational metrics added to the main control dashboard:
- Pending CRITICAL requests
- Pending HIGH requests
- Deployed rescue teams
- Depleted resource categories (available stock = 0)
- Pending historical detection reviews
- Safe NULL and empty table handling
- Proper template card rendering and styling
"""

import unittest
from unittest.mock import patch

from app import app


class MockDashboardCursor:
    def __init__(self, query_mapping=None, default_fetch=(0,)):
        self.query_mapping = query_mapping or {}
        self.default_fetch = default_fetch
        self.executed_statements = []
        self._current_result = None

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.executed_statements.append((normalized, parameters))

        # Match against predefined mapping
        for key, result in self.query_mapping.items():
            if key in normalized:
                self._current_result = result
                return
        self._current_result = self.default_fetch

    def fetchone(self):
        return self._current_result

    def fetchall(self):
        return []

    def close(self):
        pass


class MockDashboardConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, **kwargs):
        return self.fake_cursor

    def close(self):
        pass


class DashboardOperationalCardsTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "test-dashboard-operational-cards-secret-key"
        self.notification_patcher = patch(
            "modules.notifications.service.get_unread_notification_count",
            return_value=3,
        )
        self.notification_patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.notification_patcher.stop()

    def test_dashboard_renders_normal_operational_metrics(self):
        """Verify dashboard displays populated operational metric values across all summary cards."""
        mapping = {
            "FROM disasters WHERE status = 'ACTIVE'": (4,),
            "FROM emergency_requests WHERE status = 'PENDING' AND priority = 'CRITICAL'": (2,),
            "FROM emergency_requests WHERE status = 'PENDING' AND priority = 'HIGH'": (5,),
            "FROM emergency_requests WHERE status = 'PENDING'": (9,),
            "FROM emergency_requests WHERE status = 'ALLOCATED'": (14,),
            "FROM rescue_teams WHERE status = 'AVAILABLE'": (6,),
            "FROM rescue_teams WHERE status = 'DEPLOYED'": (3,),
            "COALESCE(SUM(available_quantity), 0) FROM resources": (320,),
            "HAVING SUM(available_quantity) = 0": (1,),
            "FROM detection_results WHERE review_status IN": (7,),
            "SELECT IS_USED_LOCK('smart_disaster_automatic_worker')": (1,),
        }
        cursor = MockDashboardCursor(query_mapping=mapping)
        connection = MockDashboardConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Pre-existing metrics
        self.assertIn("Active disasters", html)
        self.assertIn("<h2>4</h2><p>Active disasters</p>", html)
        self.assertIn("Pending requests", html)
        self.assertIn("<h2>9</h2><p>Pending requests</p>", html)
        self.assertIn("Allocated requests", html)
        self.assertIn("<h2>14</h2><p>Allocated requests</p>", html)
        self.assertIn("Available rescue teams", html)
        self.assertIn("<h2>6</h2><p>Available rescue teams</p>", html)
        self.assertIn("Available resource units", html)
        self.assertIn("<h2>320</h2><p>Available resource units</p>", html)
        self.assertIn("<h2>RUNNING</h2><p>Automatic scheduler worker</p>", html)

        # New Phase 5 P2 operational metrics
        self.assertIn("Pending CRITICAL requests", html)
        self.assertIn("<h2>2</h2><p>Pending CRITICAL requests</p>", html)
        self.assertIn("Pending HIGH requests", html)
        self.assertIn("<h2>5</h2><p>Pending HIGH requests</p>", html)
        self.assertIn("Deployed rescue teams", html)
        self.assertIn("<h2>3</h2><p>Deployed rescue teams</p>", html)
        self.assertIn("Depleted resource categories", html)
        self.assertIn("<h2>1</h2><p>Depleted resource categories</p>", html)
        self.assertIn("Pending historical reviews", html)
        self.assertIn("<h2>7</h2><p>Pending historical reviews</p>", html)

    def test_dashboard_empty_tables_and_null_safe_handling(self):
        """Verify dashboard renders zero values gracefully when all tables are empty or return NULL."""
        # Default fetch returns (None,) or (0,)
        cursor = MockDashboardCursor(default_fetch=(None,))
        connection = MockDashboardConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # All counters must render 0 without errors or None text
        self.assertIn("<h2>0</h2><p>Active disasters</p>", html)
        self.assertIn("<h2>0</h2><p>Pending requests</p>", html)
        self.assertIn("<h2>0</h2><p>Pending CRITICAL requests</p>", html)
        self.assertIn("<h2>0</h2><p>Pending HIGH requests</p>", html)
        self.assertIn("<h2>0</h2><p>Allocated requests</p>", html)
        self.assertIn("<h2>0</h2><p>Available rescue teams</p>", html)
        self.assertIn("<h2>0</h2><p>Deployed rescue teams</p>", html)
        self.assertIn("<h2>0</h2><p>Available resource units</p>", html)
        self.assertIn("<h2>0</h2><p>Depleted resource categories</p>", html)
        self.assertIn("<h2>0</h2><p>Pending historical reviews</p>", html)
        self.assertIn("<h2>STOPPED</h2><p>Automatic scheduler worker</p>", html)

    def test_dashboard_sql_queries_use_accurate_filters(self):
        """Verify exact SQL filters for each operational metric."""
        cursor = MockDashboardCursor(default_fetch=(0,))
        connection = MockDashboardConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            self.client.get("/")

        executed_statements = [s[0] for s in cursor.executed_statements]

        # 1. Pending CRITICAL requests
        self.assertTrue(any(
            "FROM emergency_requests WHERE status = 'PENDING' AND priority = 'CRITICAL'" in stmt
            for stmt in executed_statements
        ))

        # 2. Pending HIGH requests
        self.assertTrue(any(
            "FROM emergency_requests WHERE status = 'PENDING' AND priority = 'HIGH'" in stmt
            for stmt in executed_statements
        ))

        # 3. Deployed rescue teams
        self.assertTrue(any(
            "FROM rescue_teams WHERE status = 'DEPLOYED'" in stmt
            for stmt in executed_statements
        ))

        # 4. Depleted resource categories (grouped by type_id where sum of available quantity is 0)
        self.assertTrue(any(
            "FROM resources GROUP BY type_id HAVING SUM(available_quantity) = 0" in stmt
            for stmt in executed_statements
        ))

        # 5. Pending historical reviews (PENDING_REVIEW and NEEDS_MORE_DATA only)
        self.assertTrue(any(
            "FROM detection_results WHERE review_status IN ('PENDING_REVIEW', 'NEEDS_MORE_DATA')" in stmt
            for stmt in executed_statements
        ))

    def test_worker_status_toggle_stopped_when_lock_none(self):
        """Verify worker card indicates STOPPED when singleton lock is not held."""
        mapping = {
            "IS_USED_LOCK": (None,),
        }
        cursor = MockDashboardCursor(query_mapping=mapping, default_fetch=(0,))
        connection = MockDashboardConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("<h2>STOPPED</h2><p>Automatic scheduler worker</p>", html)


if __name__ == "__main__":
    unittest.main()

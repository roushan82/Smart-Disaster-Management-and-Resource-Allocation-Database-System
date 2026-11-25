import os
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash

from app import app


class DashboardMockConnection:
    def cursor(self, **kwargs):
        class DashboardCursor:
            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                return (0,)

            def fetchall(self):
                return []

            def close(self):
                pass

        return DashboardCursor()

    def close(self):
        pass


class MockDisastersConnection:
    def __init__(self):
        self.calls = 0

    def cursor(self, **kwargs):
        class DisastersCursor:
            def __init__(self, parent):
                self.parent = parent

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                return (None,)

            def fetchall(self):
                self.parent.calls += 1
                if self.parent.calls == 1:
                    return [(1, "Active Flood", "Flood", "HIGH", "Dehradun", "ACTIVE")]
                return [(10, "Dehradun", "Uttarakhand")]

            def close(self):
                pass

        return DisastersCursor(self)

    def close(self):
        pass


class MockResourcesConnection:
    def __init__(self):
        self.calls = 0

    def cursor(self, **kwargs):
        class ResourcesCursor:
            def __init__(self, parent):
                self.parent = parent

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                return (None,)

            def fetchall(self):
                self.parent.calls += 1
                if self.parent.calls == 1:
                    return [(1, "Food Packets", "Dehradun", 100, 75, "AVAILABLE")]
                elif self.parent.calls == 2:
                    return [(1, "Food Packets", "packets")]
                return [(10, "Dehradun", "Uttarakhand")]

            def close(self):
                pass

        return ResourcesCursor(self)

    def close(self):
        pass


class MockTeamsConnection:
    def __init__(self):
        self.calls = 0

    def cursor(self, **kwargs):
        class TeamsCursor:
            def __init__(self, parent):
                self.parent = parent

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                return (None,)

            def fetchall(self):
                self.parent.calls += 1
                if self.parent.calls == 1:
                    return [{
                        "team_id": 1,
                        "team_name": "Rapid Rescue",
                        "team_type": "Flood Rescue",
                        "status": "AVAILABLE",
                        "city": "Dehradun",
                        "state": "Uttarakhand",
                    }]
                return [{"location_id": 10, "city": "Dehradun", "state": "Uttarakhand"}]

            def close(self):
                pass

        return TeamsCursor(self)

    def close(self):
        pass


class MockPersonnelConnection:
    def __init__(self):
        self.calls = 0

    def cursor(self, **kwargs):
        class PersonnelCursor:
            def __init__(self, parent):
                self.parent = parent

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                return (None,)

            def fetchall(self):
                self.parent.calls += 1
                if self.parent.calls == 1:
                    return [{
                        "personnel_id": 1,
                        "name": "Dr. Sarah",
                        "role": "Doctor",
                        "contact": "9876543210",
                        "status": "AVAILABLE",
                        "team_name": "Rapid Rescue",
                    }]
                return [{"team_id": 1, "team_name": "Rapid Rescue"}]

            def close(self):
                pass

        return PersonnelCursor(self)

    def close(self):
        pass


class MockRequestsConnection:
    def __init__(self):
        self.query_results = iter([
            [(101, "Active Flood", "Food Packets", 15, "HIGH", "PENDING", "2026-11-16 12:00:00")],  # requests
            [(1, "Active Flood", "ACTIVE")],  # disasters
            [(1, "Food Packets", "packets")],  # resource_types
            [],  # scheduling_log
            [(101, "Active Flood", "HIGH", None, 0)],  # automatic_preview.queue
        ])

    def cursor(self, **kwargs):
        class RequestsCursor:
            def __init__(self, parent):
                self.parent = parent

            def execute(self, statement, parameters=None):
                pass

            def fetchone(self):
                return (None,)

            def fetchall(self):
                try:
                    return next(self.parent.query_results)
                except StopIteration:
                    return []

            def close(self):
                pass

        return RequestsCursor(self)

    def close(self):
        pass


class OfficerTemplateAccessControlTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "unit-test-only-session-secret-value-0001"
        self.count_patcher = patch(
            "modules.notifications.service.get_unread_notification_count", return_value=0
        )
        self.count_patcher.start()
        self.officer_username = "officer@disaster.test"
        self.reviewer_username = "reviewer@review.test"
        self.env_patcher = patch.dict(os.environ, {
            "DISASTER_OFFICER_USERNAME": self.officer_username,
            "DISASTER_OFFICER_PASSWORD_HASH": generate_password_hash("officer-password-123"),
            "REVIEWER_USERNAME": self.reviewer_username,
            "REVIEWER_PASSWORD_HASH": generate_password_hash("reviewer-password-123"),
        }, clear=False)
        self.env_patcher.start()
        self.client = app.test_client()

    def tearDown(self):
        self.count_patcher.stop()
        self.env_patcher.stop()

    def authenticate_officer(self):
        with self.client.session_transaction() as sess:
            sess["disaster_officer"] = self.officer_username
            sess["_csrf_token"] = "valid-test-csrf-token"

    def clear_officer(self):
        with self.client.session_transaction() as sess:
            sess.pop("disaster_officer", None)
            sess["_csrf_token"] = "valid-test-csrf-token"

    def authenticate_reviewer(self):
        with self.client.session_transaction() as sess:
            sess["historical_reviewer"] = self.reviewer_username
            sess["_csrf_token"] = "valid-test-csrf-token"

    def test_navigation_bar_displays_login_when_unauthenticated(self):
        self.clear_officer()
        with patch("core.db.get_db_connection", return_value=DashboardMockConnection()):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Officer Login", html)
        self.assertIn("/officer/login", html)
        self.assertNotIn("Officer Logout", html)

    def test_navigation_bar_displays_logout_when_authenticated_as_officer(self):
        self.authenticate_officer()
        with patch("core.db.get_db_connection", return_value=DashboardMockConnection()):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Officer Logout", html)
        self.assertIn("/officer/logout", html)
        self.assertNotIn("Officer Login", html)

    def test_disasters_template_hides_admin_actions_for_unauthenticated_users(self):
        self.clear_officer()
        with patch("core.db.get_db_connection", return_value=MockDisastersConnection()):
            response = self.client.get("/disasters")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # Unauthenticated: form is hidden and resolve action is hidden
        self.assertNotIn('action="/disasters/add"', html)
        self.assertNotIn('<button type="submit">Add Disaster</button>', html)
        self.assertNotIn('action="/disasters/resolve/1"', html)
        self.assertNotIn('<button type="submit" class="resolve-button">', html)
        # Clear message indicating login required
        self.assertIn("Officer login is required to register new disasters or resolve active disasters", html)
        # Public data remains visible
        self.assertIn("Active Flood", html)
        self.assertIn("Dehradun", html)

    def test_disasters_template_shows_admin_actions_for_authenticated_officer(self):
        self.authenticate_officer()
        with patch("core.db.get_db_connection", return_value=MockDisastersConnection()):
            response = self.client.get("/disasters")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/disasters/add"', html)
        self.assertIn('<button type="submit">Add Disaster</button>', html)
        self.assertIn('action="/disasters/resolve/1"', html)
        self.assertIn("Resolve", html)

    def test_resources_template_hides_admin_actions_for_unauthenticated_users(self):
        self.clear_officer()
        with patch("core.db.get_db_connection", return_value=MockResourcesConnection()):
            response = self.client.get("/resources")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('action="/resources/add"', html)
        self.assertNotIn('<button type="submit">Add Resource</button>', html)
        self.assertNotIn('action="/resources/replenish/1"', html)
        self.assertNotIn('<button type="submit" class="replenish-button">', html)
        self.assertIn("Officer login is required to add resources or replenish stock", html)
        self.assertIn("Food Packets", html)

    def test_resources_template_shows_admin_actions_for_authenticated_officer(self):
        self.authenticate_officer()
        with patch("core.db.get_db_connection", return_value=MockResourcesConnection()):
            response = self.client.get("/resources")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/resources/add"', html)
        self.assertIn('<button type="submit">Add Resource</button>', html)
        self.assertIn('action="/resources/replenish/1"', html)
        self.assertIn("Replenish", html)

    def test_teams_template_hides_admin_actions_for_unauthenticated_users(self):
        self.clear_officer()
        with patch("core.db.get_db_connection", return_value=MockTeamsConnection()):
            response = self.client.get("/teams")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('action="/teams/add"', html)
        self.assertNotIn('action="/teams/status/1"', html)
        self.assertIn("Officer login is required to register rescue teams or update team deployment status", html)
        self.assertIn("Rapid Rescue", html)

    def test_teams_template_shows_admin_actions_for_authenticated_officer(self):
        self.authenticate_officer()
        with patch("core.db.get_db_connection", return_value=MockTeamsConnection()):
            response = self.client.get("/teams")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/teams/add"', html)
        self.assertIn('action="/teams/status/1"', html)
        self.assertIn("Update", html)

    def test_personnel_template_hides_admin_actions_for_unauthenticated_users(self):
        self.clear_officer()
        with patch("core.db.get_db_connection", return_value=MockPersonnelConnection()):
            response = self.client.get("/personnel")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn('action="/personnel/add"', html)
        self.assertNotIn('action="/personnel/status/1"', html)
        self.assertIn("Officer login is required to add personnel or update personnel status", html)
        self.assertIn("Dr. Sarah", html)

    def test_personnel_template_shows_admin_actions_for_authenticated_officer(self):
        self.authenticate_officer()
        with patch("core.db.get_db_connection", return_value=MockPersonnelConnection()):
            response = self.client.get("/personnel")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/personnel/add"', html)
        self.assertIn('action="/personnel/status/1"', html)
        self.assertIn("Update", html)

    def test_emergency_requests_preserves_public_intake_and_hides_officer_controls(self):
        self.clear_officer()
        with patch("core.db.get_db_connection", return_value=MockRequestsConnection()):
            response = self.client.get("/requests")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        # Public citizen intake form MUST remain visible
        self.assertIn('action="/requests/add"', html)
        self.assertIn('<button type="submit">Create Request</button>', html)
        # Officer-only controls are hidden
        self.assertNotIn("Manual admin fallback", html)
        self.assertNotIn("Run FCFS manually", html)
        self.assertNotIn("Run Priority manually", html)
        self.assertNotIn("Run Round Robin manually", html)
        self.assertNotIn('action="/requests/allocate/101"', html)
        self.assertIn("Officer login is required to run manual scheduling or allocate requests", html)
        self.assertIn("Active Flood", html)

    def test_emergency_requests_shows_officer_controls_for_authenticated_officer(self):
        self.authenticate_officer()
        with patch("core.db.get_db_connection", return_value=MockRequestsConnection()):
            response = self.client.get("/requests")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('action="/requests/add"', html)
        self.assertIn("Manual admin fallback", html)
        self.assertIn("Run FCFS manually", html)
        self.assertIn("Run Priority manually", html)
        self.assertIn("Run Round Robin manually", html)
        self.assertIn('action="/requests/allocate/101"', html)
        self.assertIn("Allocate", html)

    def test_historical_reviewer_functionality_remains_unchanged(self):
        # Reviewer login page is accessible
        response = self.client.get("/review/login")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Historical Reviewer Sign In", response.get_data(as_text=True))
        self.assertIn("Reviewer username", response.get_data(as_text=True))

        # Accessing protected review page without reviewer credentials redirects to reviewer login
        self.clear_officer()
        review_response = self.client.get("/reports/historical-detections")
        self.assertEqual(review_response.status_code, 302)
        self.assertIn("/review/login", review_response.headers.get("Location", ""))

        # Accessing with reviewer credentials succeeds
        self.authenticate_reviewer()
        with patch("core.db.get_db_connection") as mock_conn:
            mock_cur = mock_conn.return_value.cursor.return_value
            mock_cur.fetchall.return_value = []
            mock_cur.fetchone.return_value = (0, 0, 0, 0, 0)
            authed_response = self.client.get("/reports/historical-detections")
            self.assertEqual(authed_response.status_code, 200)
            self.assertIn("Historical Detection Review", authed_response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch

from app import app
from os_engine.allocator import allocate_request


class FakeCursor:
    def __init__(self, fetched=(1,)):
        self.fetched = fetched
        self.statements = []
        self.lastrowid = 100
        self.rowcount = 1

    def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))

    def fetchone(self):
        return self.fetched

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConnection:
    def __init__(self, fetched=(1,)):
        self.fake_cursor = FakeCursor(fetched)
        self.committed = False
        self.rolled_back = False

    def cursor(self, **kwargs):
        return self.fake_cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


class RequestPageCursor(FakeCursor):
    def __init__(self, query_results):
        super().__init__()
        self.query_results = iter(query_results)

    def fetchall(self):
        return next(self.query_results)


class RequestPageConnection(FakeConnection):
    def __init__(self, query_results):
        super().__init__()
        self.fake_cursor = RequestPageCursor(query_results)


class RouteValidationTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "unit-test-only-session-secret-value-0001"
        self.count_patcher = patch("modules.notifications.service.get_unread_notification_count", return_value=0)
        self.count_patcher.start()
        self.client = app.test_client()
        with self.client.session_transaction() as session_data:
            session_data["_csrf_token"] = "unit-test-csrf-token"

    def tearDown(self):
        self.count_patcher.stop()

    def post(self, path, data=None, **kwargs):
        form = dict(data or {})
        form.setdefault("_csrf_token", "unit-test-csrf-token")
        return self.client.post(path, data=form, **kwargs)

    def assert_rejected_without_database(self, path, form, renderer):
        with patch("core.db.get_db_connection", side_effect=AssertionError("database must not be reached")):
            with patch(renderer, return_value=("Rejected", 400)):
                response = self.post(path, data=form)
        self.assertEqual(response.status_code, 400)

    def test_direct_posts_reject_invalid_categories_and_typed_values(self):
        self.assert_rejected_without_database(
            "/disasters/add",
            {"name": "Storm", "type": "Arbitrary", "severity": "HIGH", "location_id": "1"},
            "modules.disasters.routes._render_disasters",
        )
        self.assert_rejected_without_database(
            "/teams/add",
            {"team_name": "Team", "team_type": "Medical", "location_id": "1"},
            "modules.teams.routes._render_teams",
        )
        self.assert_rejected_without_database(
            "/personnel/add",
            {"name": "Asha", "role": "9876543210", "contact": "9876543210", "team_id": "1"},
            "modules.personnel.routes._render_personnel",
        )
        self.assert_rejected_without_database(
            "/personnel/add",
            {"name": "Asha", "role": "Doctor", "contact": "Doctor", "team_id": "1"},
            "modules.personnel.routes._render_personnel",
        )

    def test_direct_posts_reject_missing_ids_and_negative_quantities(self):
        self.assert_rejected_without_database(
            "/disasters/add",
            {"name": "", "type": "Flood", "severity": "HIGH", "location_id": "1"},
            "modules.disasters.routes._render_disasters",
        )
        self.assert_rejected_without_database(
            "/resources/add",
            {"type_id": "1", "location_id": "1", "total_quantity": "-2"},
            "modules.resources.routes._render_resources",
        )
        self.assert_rejected_without_database(
            "/requests/add",
            {"disaster_id": "1", "resource_type_id": "1", "requested_quantity": "0", "priority": "HIGH"},
            "modules.emergency_requests.routes._render_requests",
        )
        self.assert_rejected_without_database(
            "/teams/status/1",
            {"status": "ONLINE"},
            "modules.teams.routes._render_teams",
        )
        self.assert_rejected_without_database(
            "/personnel/status/1",
            {"status": "ON_CALL"},
            "modules.personnel.routes._render_personnel",
        )

    def test_existing_fk_is_checked_before_insert(self):
        connection = FakeConnection(fetched=None)
        with patch("core.db.get_db_connection", return_value=connection):
            with patch("modules.personnel.routes._render_personnel", return_value=("Rejected", 400)):
                response = self.post("/personnel/add", data={
                    "name": "Asha",
                    "role": "Doctor",
                    "contact": "9876543210",
                    "team_id": "987654321",
                })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(any("INSERT INTO personnel" in sql for sql, _ in connection.fake_cursor.statements))
        self.assertFalse(connection.committed)

    def test_valid_direct_create_requests_reach_parameterized_inserts(self):
        cases = (
            (
                "/disasters/add",
                {"name": "River flood", "type": "Flood", "severity": "HIGH", "description": "", "location_id": "1"},
                "INSERT INTO disasters",
            ),
            (
                "/teams/add",
                {"team_name": "Flood Team", "team_type": "Flood Rescue", "location_id": "1"},
                "INSERT INTO rescue_teams",
            ),
            (
                "/personnel/add",
                {"name": "Asha", "role": "Doctor", "contact": "9876543210", "team_id": "1"},
                "INSERT INTO personnel",
            ),
        )
        for path, form, insert_fragment in cases:
            with self.subTest(path=path):
                connection = FakeConnection()
                with patch("core.db.get_db_connection", return_value=connection):
                    response = self.post(path, data=form)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(connection.committed)
                inserts = [
                    (sql, params)
                    for sql, params in connection.fake_cursor.statements
                    if insert_fragment in sql
                ]
                self.assertEqual(len(inserts), 1)
                self.assertIn("%s", inserts[0][0])

    def test_valid_status_update_commits(self):
        connection = FakeConnection()
        with patch("core.db.get_db_connection", return_value=connection):
            response = self.post(
                "/teams/status/1",
                data={"status": "DEPLOYED", "team_id": "1"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(connection.committed)
        self.assertTrue(
            any("UPDATE rescue_teams" in sql for sql, _ in connection.fake_cursor.statements)
        )

    def test_active_disasters_render_as_selectable_options_with_database_ids(self):
        connection = RequestPageConnection([
            [],
            [(42, "Current flood", "ACTIVE"), (41, "Resolved quake", "RESOLVED")],
            [],
            [
                ("FCFS", "Automatic fallback", 1, 42, "ALLOCATED", "ALLOCATED", "Request 42 allocated.", None),
                ("PRIORITY", "Critical request", 1, 42, "ALLOCATED", "ALLOCATED", "Request 42 allocated.", None),
            ],
            [(42, "Current flood", "CRITICAL", None, 90)],
        ])
        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/requests")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<option value="42"', html)
        self.assertIn("Current flood", html)
        self.assertIn('<option value="41" disabled>', html)
        self.assertIn("Resolved quake (RESOLVED)", html)
        self.assertIn("Latest FCFS vs Priority Execution", html)
        self.assertIn("Manual admin fallback", html)
        self.assertIn("Run FCFS manually", html)
        self.assertIn("Run Priority manually", html)
        self.assertIn("FCFS", html)
        self.assertIn("PRIORITY", html)
        self.assertIn("Request 42 allocated.", html)
        self.assertIn("Selected policy:", html)
        self.assertIn("FROM disasters", connection.fake_cursor.statements[1][0])
        self.assertNotIn("WHERE status = 'ACTIVE'", connection.fake_cursor.statements[1][0])
        self.assertIn("GROUP BY algorithm", connection.fake_cursor.statements[3][0])

    def test_empty_disaster_table_prompts_user_to_add_disaster(self):
        connection = RequestPageConnection([[], [], [], [], []])
        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/requests")

        self.assertEqual(response.status_code, 200)
        self.assertIn("No disasters are recorded. Add a disaster first.", response.get_data(as_text=True))

    def test_new_request_notifies_automatic_scheduler_after_commit(self):
        connection = FakeConnection()
        with patch("core.db.get_db_connection", return_value=connection):
            with patch("modules.emergency_requests.routes.signal_automatic_scheduler") as notify_scheduler:
                response = self.post("/requests/add", data={
                    "disaster_id": "1",
                    "resource_type_id": "1",
                    "requested_quantity": "3",
                    "priority": "LOW",
                })

        self.assertEqual(response.status_code, 302)
        self.assertTrue(connection.committed)
        notify_scheduler.assert_called_once_with()

    def test_scheduler_route_uses_selected_algorithm_and_rejects_unknown_choice(self):
        scheduler_result = {"message": "PRIORITY run completed."}
        with patch("modules.emergency_requests.routes.schedule_pending_requests", return_value=scheduler_result) as scheduler:
            with patch("modules.emergency_requests.routes._render_requests", return_value=("Scheduled", 200)):
                response = self.post(
                    "/requests/schedule",
                    data={"algorithm": "PRIORITY"},
                )
        self.assertEqual(response.status_code, 200)
        scheduler.assert_called_once_with("PRIORITY")

        with patch("modules.emergency_requests.routes.schedule_pending_requests") as scheduler:
            with patch("modules.emergency_requests.routes._render_requests", return_value=("Rejected", 400)):
                response = self.post(
                    "/requests/schedule",
                    data={"algorithm": "PRIORITY; DROP TABLE"},
                )
        self.assertEqual(response.status_code, 400)
        scheduler.assert_not_called()

    def test_manual_allocation_route_remains_connected(self):
        with patch("modules.emergency_requests.routes.allocate_request", return_value=(True, "Allocated.")) as allocate:
            with patch("modules.emergency_requests.routes._render_requests", return_value=("Allocated", 200)):
                response = self.post("/requests/allocate/21")
        self.assertEqual(response.status_code, 200)
        allocate.assert_called_once_with(21)

    def test_notification_routes_only_mark_global_notifications(self):
        with patch("modules.notifications.routes.service.mark_notification_read", return_value=True) as mark_one:
            response = self.post("/notifications/9/read")
        self.assertEqual(response.status_code, 302)
        mark_one.assert_called_once_with(9)

        with patch("modules.notifications.routes.service.mark_notification_read", return_value=False):
            response = self.post("/notifications/999/read")
        self.assertEqual(response.status_code, 404)

        with patch("modules.notifications.routes.service.mark_all_notifications_read", return_value=3) as mark_all:
            response = self.post("/notifications/read-all")
        self.assertEqual(response.status_code, 302)
        mark_all.assert_called_once_with()

    def test_notifications_inbox_route_and_bell_endpoint_are_registered(self):
        with patch("modules.notifications.routes.service.get_notifications", return_value=[]):
            with patch("modules.notifications.service.get_unread_notification_count", return_value=0):
                response = self.client.get("/notifications")
                with patch("core.db.get_db_connection", return_value=FakeConnection()):
                    dashboard = self.client.get("/")
                legacy_dashboard = self.client.get("/admin/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Notifications", response.get_data(as_text=True))
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"Manage Disasters", dashboard.data)
        self.assertIn(b'href="/notifications"', dashboard.data)
        self.assertNotIn(b"Sign in", dashboard.data)
        self.assertEqual(legacy_dashboard.status_code, 301)

    def test_login_route_is_absent_and_public_pages_do_not_redirect(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 404)

    def test_post_requires_a_valid_csrf_token(self):
        self.client.delete_cookie("session")
        for submitted in (None, "wrong-token"):
            data = {} if submitted is None else {"_csrf_token": submitted}
            with self.subTest(token=submitted):
                with patch("core.db.get_db_connection", side_effect=AssertionError("database must not be reached")):
                    response = self.client.post("/disasters/add", data=data)
                self.assertEqual(response.status_code, 400)

    def test_assignment_create_is_blocked_without_fabricating_creator(self):
        with patch("modules.assignments.routes._render_admin_assignments", return_value="Assignment unavailable") as render:
            response = self.post("/admin/assignments", data={"request_id": "2", "team_id": "3"})
        self.assertEqual(response.status_code, 409)
        render.assert_called_once()

    def test_assignment_page_explains_missing_optional_table(self):
        import mysql.connector

        connection = FakeConnection()
        connection.fake_cursor.execute = lambda statement, parameters=None: (_ for _ in ()).throw(
            mysql.connector.Error(errno=1146, msg="Table does not exist")
        ) if "team_request_assignments" in statement else None
        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/admin/assignments")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"team_request_assignments", response.data)

    def test_business_create_notification_shares_business_commit(self):
        connection = FakeConnection()
        connection.fake_cursor.lastrowid = 73
        connection.fake_cursor.rowcount = 1
        with patch("modules.emergency_requests.routes.validate_request", return_value={
            "disaster_id": 4,
            "resource_type_id": 2,
            "requested_quantity": 5,
            "priority": "HIGH",
        }):
            with patch("core.db.get_db_connection", return_value=connection):
                with patch("modules.emergency_requests.routes.signal_automatic_scheduler"):
                    response = self.post("/requests/add", data={"ignored": "valid"})

        self.assertEqual(response.status_code, 302)
        insert_positions = [
            index for index, (statement, _) in enumerate(connection.fake_cursor.statements)
            if "INSERT INTO emergency_requests" in statement
            or "INSERT IGNORE INTO notifications" in statement
        ]
        self.assertEqual(len(insert_positions), 2)
        self.assertLess(insert_positions[0], insert_positions[1])
        self.assertTrue(connection.committed)

    def test_invalid_allocation_quantity_does_not_change_stock(self):
        class InvalidRequestCursor(FakeCursor):
            def fetchone(self):
                return (1, 1, 0, "HIGH", "PENDING")

        class InvalidRequestConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.fake_cursor = InvalidRequestCursor()

            def start_transaction(self):
                pass

        connection = InvalidRequestConnection()
        with patch("core.db.get_db_connection", return_value=connection):
            success, message = allocate_request(1)
        self.assertFalse(success)
        self.assertIn("positive whole number", message)
        self.assertFalse(any("UPDATE resources" in sql for sql, _ in connection.fake_cursor.statements))
        self.assertFalse(any("INSERT INTO allocations" in sql for sql, _ in connection.fake_cursor.statements))
        self.assertTrue(connection.rolled_back)

    def test_valid_allocation_uses_locked_stock_and_commits(self):
        class ValidRequestCursor(FakeCursor):
            def __init__(self):
                super().__init__()
                self.read_count = 0

            def fetchone(self):
                self.read_count += 1
                if self.read_count == 1:
                    return (7, 5, 3, "HIGH", "PENDING")
                return (4, 5, 8)

        class ValidRequestConnection(FakeConnection):
            def __init__(self):
                super().__init__()
                self.fake_cursor = ValidRequestCursor()

            def start_transaction(self):
                pass

        connection = ValidRequestConnection()
        with patch("core.db.get_db_connection", return_value=connection):
            success, message = allocate_request(7)
        self.assertTrue(success, message)
        self.assertTrue(connection.committed)
        resource_update = next(
            (sql, params)
            for sql, params in connection.fake_cursor.statements
            if "UPDATE resources" in sql
        )
        self.assertLess(resource_update[0].index("SET status"), resource_update[0].index("available_quantity ="))
        self.assertEqual(resource_update[1], (3, 3, 4))
        executed_sql = " ".join(sql for sql, _ in connection.fake_cursor.statements).lower()
        self.assertNotIn("users", executed_sql)
        self.assertNotIn("team_request_assignments", executed_sql)
        self.assertEqual(
            sum("INSERT IGNORE INTO notifications" in sql for sql, _ in connection.fake_cursor.statements),
            2,
        )


if __name__ == "__main__":
    unittest.main()

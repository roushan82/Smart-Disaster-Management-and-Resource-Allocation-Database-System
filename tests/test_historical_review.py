import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from app import app
from modules.reports.validation import (
    FormValidationError,
    validate_detection_review,
)
from os_engine.historical_detection import (
    DuplicateDetectionReviewError,
    review_detection_result,
)
from werkzeug.security import generate_password_hash


class ReviewCursor:
    def __init__(self, initial_review=None, listing=()):
        self.initial_review = initial_review
        self.listing = list(listing)
        self.result = None
        self.statements = []
        self.parameters = []
        self.rowcount = 0
        self.lastrowid = 41
        self.audit_rows = []
        self.updated = None
        self.fail_audit = False
        self._savepoint_state = None

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        self.parameters.append(parameters)
        if normalized.startswith("SAVEPOINT "):
            self._savepoint_state = (
                deepcopy(self.audit_rows),
                deepcopy(self.updated),
            )
        elif normalized.startswith("ROLLBACK TO SAVEPOINT "):
            self.audit_rows, self.updated = deepcopy(self._savepoint_state)
        elif normalized.startswith("RELEASE SAVEPOINT "):
            self._savepoint_state = None
        elif normalized.startswith("SET time_zone"):
            return
        elif "SELECT dr.review_status" in normalized:
            self.result = self.initial_review
        elif "UPDATE detection_results" in normalized:
            self.updated = parameters
            self.rowcount = 1
        elif "INSERT INTO detection_audit" in normalized:
            if self.fail_audit:
                raise RuntimeError("audit failure")
            self.audit_rows.append(parameters)
        elif "GROUP BY outcome" in normalized:
            self.result = [("INSUFFICIENT_DATA", len(self.listing))]
        elif "FROM detection_results dr" in normalized:
            self.result = self.listing
        else:
            raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result

    def close(self):
        pass


class ReviewConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
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


class HistoricalReviewValidationTests(unittest.TestCase):
    def test_requires_allowed_decision_and_nonempty_reason(self):
        self.assertEqual(
            validate_detection_review(
                {"review_status": "APPROVED", "reason": "Evidence checked."}
            ),
            {"review_status": "APPROVED", "reason": "Evidence checked."},
        )
        for form in (
            {"review_status": "APPROVED", "reason": " "},
            {"review_status": "PENDING_REVIEW", "reason": "A reason"},
            {"review_status": "REJECTED", "reason": "x" * 2001},
        ):
            with self.subTest(form=form):
                with self.assertRaises(FormValidationError):
                    validate_detection_review(form)


class HistoricalReviewPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.cursor = ReviewCursor(
            initial_review=("NEEDS_MORE_DATA", None, None, 7, 1)
        )

    def test_updates_result_and_appends_reason_audit(self):
        review_detection_result(
            self.cursor,
            12,
            reviewer="reviewer@example.test",
            review_status="APPROVED",
            reason="Source narrative reviewed.",
        )

        self.assertIn("UPDATE detection_results", " ".join(self.cursor.statements))
        update_params = next(
            params for sql, params in zip(self.cursor.statements, self.cursor.parameters)
            if "UPDATE detection_results" in sql
        )
        self.assertEqual(update_params, ("APPROVED", "reviewer@example.test", 12))
        self.assertEqual(len(self.cursor.audit_rows), 1)
        audit = self.cursor.audit_rows[0]
        self.assertEqual(audit[0:4], (7, 1, 12, "DETECTION_REVIEW"))
        self.assertEqual(audit[4], "reviewer@example.test")
        self.assertIn("Source narrative reviewed.", audit[5])

    def test_rejects_duplicate_review(self):
        self.cursor.initial_review = (
            "APPROVED",
            "2026-11-09 12:00:00",
            "reviewer@example.test",
            7,
            1,
        )

        with self.assertRaises(DuplicateDetectionReviewError):
            review_detection_result(
                self.cursor,
                12,
                reviewer="reviewer@example.test",
                review_status="REJECTED",
                reason="Second decision.",
            )
        self.assertFalse(any("UPDATE detection_results" in sql for sql in self.cursor.statements))
        self.assertEqual(self.cursor.audit_rows, [])

    def test_audit_failure_rolls_back_status_update(self):
        self.cursor.fail_audit = True

        with self.assertRaisesRegex(RuntimeError, "audit failure"):
            review_detection_result(
                self.cursor,
                12,
                reviewer="reviewer@example.test",
                review_status="REJECTED",
                reason="Insufficient evidence.",
            )

        self.assertIsNone(self.cursor.updated)
        self.assertEqual(self.cursor.audit_rows, [])
        self.assertTrue(any(
            sql.startswith("ROLLBACK TO SAVEPOINT")
            for sql in self.cursor.statements
        ))


class HistoricalReviewRouteTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "unit-test-only-session-secret-value-0001"
        self.password_hash = generate_password_hash("correct test password")
        self.environment = patch.dict(
            os.environ,
            {
                "REVIEWER_USERNAME": "reviewer@example.test",
                "REVIEWER_PASSWORD_HASH": self.password_hash,
            },
            clear=False,
        )
        self.environment.start()
        self.notification_count = patch(
            "modules.notifications.service.get_unread_notification_count",
            return_value=0,
        )
        self.notification_count.start()
        self.client = app.test_client()
        with self.client.session_transaction() as session_data:
            session_data["_csrf_token"] = "review-test-token"

    def tearDown(self):
        self.environment.stop()
        self.notification_count.stop()

    def post(self, path, data):
        return self.client.post(
            path,
            data={"_csrf_token": "review-test-token", **data},
        )

    def login(self):
        return self.post(
            "/review/login",
            {"username": "reviewer@example.test", "password": "correct test password"},
        )

    def test_login_required_before_review_data_access(self):
        with patch(
            "modules.reports.routes.db.get_db_connection",
            side_effect=AssertionError("unauthenticated request must not query DB"),
        ):
            response = self.client.get("/reports/historical-detections")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/review/login", response.headers["Location"])

    def test_invalid_review_form_is_rejected_before_database_access(self):
        self.login()
        with patch(
            "modules.reports.routes.db.get_db_connection",
            side_effect=AssertionError("invalid review must not query DB"),
        ):
            response = self.post(
                "/reports/historical-detections/12/review",
                {"review_status": "APPROVED", "reason": ""},
            )
        self.assertEqual(response.status_code, 400)

    def test_authenticated_reviewer_can_view_historical_detection_evidence(self):
        self.assertEqual(self.login().status_code, 302)
        cursor = ReviewCursor(
            listing=[
                (
                    12, 1, "UEI-IMD-FL-2013-0131",
                    "uttarakhand_2013_evidence_sufficiency", "1.0",
                    {"status": "INSUFFICIENT_DATA", "evidence": {"Severity": None}},
                    "Evidence is insufficient.", None, "NEEDS_MORE_DATA",
                    "2026-11-09 12:00:00", None, None,
                )
            ]
        )
        with patch(
            "modules.reports.routes.db.get_db_connection",
            return_value=ReviewConnection(cursor),
        ):
            response = self.client.get("/reports/historical-detections")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"uttarakhand_2013_evidence_sufficiency", response.data)
        self.assertIn(b"Evidence is insufficient.", response.data)
        self.assertIn(b"INSUFFICIENT_DATA", response.data)
        self.assertIn(b"APPROVED", response.data)

    def test_login_and_review_submission_commit_transaction(self):
        self.assertEqual(self.login().status_code, 302)
        with self.client.session_transaction() as session_data:
            session_data["_csrf_token"] = "post-login-review-token"
        cursor = ReviewCursor(
            initial_review=("NEEDS_MORE_DATA", None, None, 7, 1)
        )
        connection = ReviewConnection(cursor)
        with patch(
            "modules.reports.routes.db.get_db_connection",
            return_value=connection,
        ):
            response = self.post(
                "/reports/historical-detections/12/review",
                {
                    "_csrf_token": "post-login-review-token",
                    "review_status": "APPROVED",
                    "reason": "Evidence reviewed.",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(connection.transaction_started)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        self.assertTrue(connection.closed)
        self.assertEqual(len(cursor.audit_rows), 1)

    def test_invalid_password_does_not_create_reviewer_session(self):
        response = self.post(
            "/review/login",
            {"username": "reviewer@example.test", "password": "wrong"},
        )

        self.assertEqual(response.status_code, 401)
        with self.client.session_transaction() as session_data:
            self.assertNotIn("historical_reviewer", session_data)

    def test_reviewer_login_and_logout_preserve_officer_session(self):
        officer_password_hash = generate_password_hash("officer test password")
        with patch.dict(
            os.environ,
            {
                "DISASTER_OFFICER_USERNAME": "officer@example.test",
                "DISASTER_OFFICER_PASSWORD_HASH": officer_password_hash,
            },
            clear=False,
        ):
            with self.client.session_transaction() as session_data:
                session_data["disaster_officer"] = "officer@example.test"

            login_response = self.login()
            self.assertEqual(login_response.status_code, 302)
            self.assertIn("/reports/historical-detections", login_response.headers["Location"])
            with self.client.session_transaction() as session_data:
                self.assertEqual(session_data.get("historical_reviewer"), "reviewer@example.test")
                self.assertEqual(session_data.get("disaster_officer"), "officer@example.test")
                self.assertEqual(session_data.get("_csrf_token"), "review-test-token")

            logout_response = self.post("/review/logout", {})
            self.assertEqual(logout_response.status_code, 302)
            self.assertIn("/review/login", logout_response.headers["Location"])
            with self.client.session_transaction() as session_data:
                self.assertNotIn("historical_reviewer", session_data)
                self.assertEqual(session_data.get("disaster_officer"), "officer@example.test")
                self.assertEqual(session_data.get("_csrf_token"), "review-test-token")

            from unittest.mock import MagicMock

            connection = MagicMock()
            connection.cursor.return_value.fetchall.side_effect = [[], []]
            with patch(
                "modules.emergency_requests.routes.db.get_db_connection",
                return_value=connection,
            ):
                officer_page = self.client.get("/requests/operational-generate")
            self.assertEqual(officer_page.status_code, 200)


if __name__ == "__main__":
    unittest.main()

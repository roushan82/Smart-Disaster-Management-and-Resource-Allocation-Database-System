"""Phase 5 - P4: Historical Detection Filters & Outcome Counts Tests.

Covers:
1. Valid outcome filter.
2. Valid review-status filter.
3. Both filters together.
4. Invalid filter handling (safely ignored, no SQL injection).
5. Parameterized SQL usage (assert %s placeholders and parameters passed).
6. Outcome summary counts grouped by outcome, independent of review status.
7. Empty results handling.
8. Existing reviewer actions and page rendering remain intact.
"""

import json
import os
import unittest
from unittest.mock import patch

from app import app
from werkzeug.security import generate_password_hash


class MockHistoricalDetectionsCursor:
    def __init__(self, detections=None, summary_counts=None):
        self.detections = detections if detections is not None else []
        self.summary_counts = summary_counts if summary_counts is not None else []
        self.executed_statements = []
        self.executed_parameters = []
        self.last_query = ""

    def execute(self, statement, parameters=None):
        norm = " ".join(statement.split())
        self.last_query = norm
        self.executed_statements.append(norm)
        self.executed_parameters.append(parameters)

    def fetchall(self):
        if "GROUP BY outcome" in self.last_query:
            return self.summary_counts
        if "FROM detection_results dr" in self.last_query:
            return self.detections
        return []

    def close(self):
        pass


class MockHistoricalDetectionsConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def cursor(self, **kwargs):
        return self.fake_cursor

    def close(self):
        pass


class HistoricalDetectionFiltersAndCountsTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.secret_key = "unit-test-only-session-secret-value-0001"

        self.env_patcher = patch.dict(
            os.environ,
            {
                "REVIEWER_USERNAME": "reviewer@example.test",
                "REVIEWER_PASSWORD_HASH": generate_password_hash("test-pass"),
                "FLASK_SECRET_KEY": "unit-test-secret-key-at-least-32-bytes-long",
            },
            clear=False,
        )
        self.env_patcher.start()

        self.notification_patcher = patch(
            "modules.notifications.service.get_unread_notification_count",
            return_value=0,
        )
        self.notification_patcher.start()

        self.client = app.test_client()

        # Log in as reviewer
        with self.client.session_transaction() as sess:
            sess["historical_reviewer"] = "reviewer@example.test"
            sess["_csrf_token"] = "test-csrf-token"

        # Standard sample dataset:
        # Record 1: CONFIRMED outcome (evaluated CONFIRMED, review PENDING_REVIEW)
        # Record 2: CONFIRMED outcome (evaluated POTENTIAL, review APPROVED)
        # Record 3: POTENTIAL outcome (evaluated POTENTIAL, review PENDING_REVIEW)
        # Record 4: INSUFFICIENT_DATA outcome (evaluated INSUFFICIENT_DATA, review PENDING_REVIEW)
        # Record 5: REJECTED outcome (evaluated POTENTIAL, review REJECTED)
        self.sample_detections = [
            (
                1, 101, "REC-001", "rule_1", "1.0",
                json.dumps({"status": "CONFIRMED"}),
                "Confirmed disaster evidence.", "CRITICAL", "PENDING_REVIEW",
                "2026-11-16 10:00:00", None, None,
            ),
            (
                2, 102, "REC-002", "rule_1", "1.0",
                json.dumps({"status": "POTENTIAL"}),
                "Approved disaster evidence.", "HIGH", "APPROVED",
                "2026-11-16 10:05:00", "2026-11-16 11:00:00", "reviewer@example.test",
            ),
            (
                3, 103, "REC-003", "rule_1", "1.0",
                json.dumps({"status": "POTENTIAL"}),
                "Potential disaster evidence.", "MEDIUM", "PENDING_REVIEW",
                "2026-11-16 10:10:00", None, None,
            ),
            (
                4, 104, "REC-004", "rule_1", "1.0",
                json.dumps({"status": "INSUFFICIENT_DATA"}),
                "Insufficient evidence.", None, "PENDING_REVIEW",
                "2026-11-16 10:15:00", None, None,
            ),
            (
                5, 105, "REC-005", "rule_1", "1.0",
                json.dumps({"status": "POTENTIAL"}),
                "Rejected after review.", "LOW", "REJECTED",
                "2026-11-16 10:20:00", "2026-11-16 11:30:00", "reviewer@example.test",
            ),
        ]

        # Summary counts grouped by outcome:
        # CONFIRMED: 2 (records 1 and 2)
        # POTENTIAL: 1 (record 3)
        # INSUFFICIENT_DATA: 1 (record 4)
        # REJECTED: 1 (record 5)
        # Notice: Grouping by review_status would give PENDING_REVIEW: 3, APPROVED: 1, REJECTED: 1.
        self.sample_summary_counts = [
            ("CONFIRMED", 2),
            ("POTENTIAL", 1),
            ("INSUFFICIENT_DATA", 1),
            ("REJECTED", 1),
        ]

    def tearDown(self):
        self.notification_patcher.stop()
        self.env_patcher.stop()

    def test_valid_outcome_filter(self):
        """1. Valid outcome filter applies filter and retrieves matching records."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports/historical-detections?outcome=CONFIRMED")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Should render REC-001 and REC-002 (both have CONFIRMED outcome)
        self.assertIn("REC-001", html)
        self.assertIn("REC-002", html)
        # Should not render REC-003, REC-004, REC-005
        self.assertNotIn("REC-003", html)
        self.assertNotIn("REC-004", html)
        self.assertNotIn("REC-005", html)

    def test_valid_review_status_filter(self):
        """2. Valid review-status filter applies filter and retrieves matching records."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports/historical-detections?review_status=APPROVED")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Only REC-002 is APPROVED
        self.assertIn("REC-002", html)
        self.assertNotIn("REC-001", html)
        self.assertNotIn("REC-003", html)
        self.assertNotIn("REC-004", html)
        self.assertNotIn("REC-005", html)

    def test_both_filters_together(self):
        """3. Both outcome and review_status filters work together independently."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get(
                "/reports/historical-detections?outcome=CONFIRMED&review_status=PENDING_REVIEW"
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Only REC-001 is both CONFIRMED outcome and PENDING_REVIEW status
        self.assertIn("REC-001", html)
        self.assertNotIn("REC-002", html)
        self.assertNotIn("REC-003", html)
        self.assertNotIn("REC-004", html)
        self.assertNotIn("REC-005", html)

    def test_invalid_filter_handling(self):
        """4. Invalid filter values are safely ignored and do not alter the query."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get(
                "/reports/historical-detections?outcome=INVALID_OUTCOME&review_status=HACK' OR 1=1--"
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # All records remain visible because invalid filters are ignored
        self.assertIn("REC-001", html)
        self.assertIn("REC-002", html)
        self.assertIn("REC-003", html)
        self.assertIn("REC-004", html)
        self.assertIn("REC-005", html)

        # The query should not contain invalid values
        listing_stmt = next(
            s for s in cursor.executed_statements
            if "JOIN source_observations so" in s
        )
        self.assertNotIn("INVALID_OUTCOME", listing_stmt)
        self.assertNotIn("HACK", listing_stmt)

    def test_parameterized_sql_usage(self):
        """5. Query parameters are strictly passed as parameterized SQL (%s), never interpolated."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            self.client.get("/reports/historical-detections?outcome=POTENTIAL&review_status=PENDING_REVIEW")

        # Find the statement and parameters for the detections listing
        listing_index = next(
            i for i, s in enumerate(cursor.executed_statements)
            if "JOIN source_observations so" in s
        )
        stmt = cursor.executed_statements[listing_index]
        params = cursor.executed_parameters[listing_index]

        # Verify SQL uses %s placeholders for filter criteria
        self.assertIn("dr.review_status = %s", stmt)
        self.assertIn("END) = %s", stmt)

        # Verify SQL does NOT interpolate user input values into WHERE criteria
        self.assertNotIn("END) = 'POTENTIAL'", stmt)
        self.assertNotIn("dr.review_status = 'PENDING_REVIEW'", stmt)

        # Verify params tuple contains the exact parameterized values
        self.assertEqual(params, ("PENDING_REVIEW", "POTENTIAL"))

    def test_outcome_counts_grouped_by_outcome_not_review_status(self):
        """6. Summary counts are grouped by outcome (CONFIRMED: 2, etc.), not review status."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports/historical-detections")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Summary cards labels
        self.assertIn("Total Detections", html)
        self.assertIn("Confirmed", html)
        self.assertIn("Potential", html)
        self.assertIn("Insufficient Data", html)
        self.assertIn("Rejected", html)

        # Assert outcome summary values match outcome counts:
        # Total = 5, Confirmed = 2, Potential = 1, Insufficient Data = 1, Rejected = 1
        self.assertIn('id="summary-total">5</span>', html)
        self.assertIn('id="summary-confirmed">2</span>', html)
        self.assertIn('id="summary-potential">1</span>', html)
        self.assertIn('id="summary-insufficient-data">1</span>', html)
        self.assertIn('id="summary-rejected">1</span>', html)

        # Confirm the summary query groups by outcome
        summary_stmt = next(
            s for s in cursor.executed_statements
            if "GROUP BY outcome" in s
        )
        self.assertIn("GROUP BY outcome", summary_stmt)

    def test_empty_results_handling(self):
        """7. Empty table or no matching results handled safely with 0 counts."""
        cursor = MockHistoricalDetectionsCursor(detections=[], summary_counts=[])
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports/historical-detections")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # All summary counts are 0
        self.assertIn('id="summary-total">0</span>', html)
        self.assertIn('id="summary-confirmed">0</span>', html)
        self.assertIn('id="summary-potential">0</span>', html)
        self.assertIn('id="summary-insufficient-data">0</span>', html)
        self.assertIn('id="summary-rejected">0</span>', html)

        # Clean empty message rendered
        self.assertIn("No historical detection results are available for review.", html)

    def test_existing_reviewer_actions_and_reset_filters_remain_intact(self):
        """8. Review forms, decision options, reason textarea, and reset filter option are intact."""
        cursor = MockHistoricalDetectionsCursor(
            detections=self.sample_detections,
            summary_counts=self.sample_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            response = self.client.get("/reports/historical-detections")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)

        # Reset filters button/link is present
        self.assertIn("Reset Filters", html)
        self.assertIn('href="/reports/historical-detections"', html)

        # Review action form is present for pending items (e.g., detection #1)
        self.assertIn('action="/reports/historical-detections/1/review"', html)
        self.assertIn('name="review_status"', html)
        self.assertIn('value="APPROVED"', html)
        self.assertIn('value="REJECTED"', html)
        self.assertIn('value="NEEDS_MORE_DATA"', html)
        self.assertIn('name="reason"', html)
        self.assertIn("Save review", html)

    def test_canonical_outcome_computation_and_regression_matrix(self):
        """9. Regression: compute_detection_outcome returns only canonical outcomes for all combinations."""
        from os_engine.historical_detection import compute_detection_outcome
        from modules.reports.routes import VALID_DETECTION_OUTCOMES

        test_cases = [
            # CONFIRMED + any review status remains CONFIRMED
            (("CONFIRMED", "REJECTED"), "CONFIRMED"),
            (("CONFIRMED", "APPROVED"), "CONFIRMED"),
            (("CONFIRMED", "PENDING_REVIEW"), "CONFIRMED"),
            (("CONFIRMED", "NEEDS_MORE_DATA"), "CONFIRMED"),
            # POTENTIAL combinations
            (("POTENTIAL", "APPROVED"), "CONFIRMED"),
            (("POTENTIAL", "REJECTED"), "REJECTED"),
            (("POTENTIAL", "PENDING_REVIEW"), "POTENTIAL"),
            (("POTENTIAL", "NEEDS_MORE_DATA"), "POTENTIAL"),
            # INSUFFICIENT_DATA combinations
            (("INSUFFICIENT_DATA", "REJECTED"), "REJECTED"),
            (("INSUFFICIENT_DATA", "APPROVED"), "INSUFFICIENT_DATA"),
            (("INSUFFICIENT_DATA", "PENDING_REVIEW"), "INSUFFICIENT_DATA"),
            (("INSUFFICIENT_DATA", "NEEDS_MORE_DATA"), "INSUFFICIENT_DATA"),
            # NULL / missing evaluated status
            ((None, "REJECTED"), "INSUFFICIENT_DATA"),
            ((None, "PENDING_REVIEW"), "INSUFFICIENT_DATA"),
            ((None, "APPROVED"), "INSUFFICIENT_DATA"),
            (("", "REJECTED"), "INSUFFICIENT_DATA"),
            # Unexpected evaluated status
            (("UNKNOWN", "PENDING_REVIEW"), "INSUFFICIENT_DATA"),
            (("UNKNOWN", "REJECTED"), "INSUFFICIENT_DATA"),
            (("UNKNOWN", "APPROVED"), "INSUFFICIENT_DATA"),
        ]

        for (eval_status, rev_status), expected in test_cases:
            with self.subTest(eval_status=eval_status, rev_status=rev_status):
                actual = compute_detection_outcome(eval_status, rev_status)
                self.assertEqual(actual, expected)
                self.assertIn(actual, VALID_DETECTION_OUTCOMES)

    def test_edge_case_detections_rendering_and_filtering(self):
        """10. Regression: edge cases (CONFIRMED+REJECTED, NULL+REJECTED, UNKNOWN+PENDING) filter and render correctly."""
        edge_case_detections = [
            (
                51, 201, "REC-EDGE-CONFIRMED-REJ", "rule_1", "1.0",
                json.dumps({"status": "CONFIRMED"}),
                "Confirmed evidence, rejected review.", "HIGH", "REJECTED",
                "2026-11-16 12:00:00", "2026-11-16 12:30:00", "reviewer@example.test",
            ),
            (
                52, 202, "REC-EDGE-NULL-REJ", "rule_1", "1.0",
                json.dumps({}),
                "Missing status evidence, rejected review.", None, "REJECTED",
                "2026-11-16 12:05:00", "2026-11-16 12:35:00", "reviewer@example.test",
            ),
            (
                53, 203, "REC-EDGE-UNKNOWN-PEND", "rule_1", "1.0",
                json.dumps({"status": "UNKNOWN_CUSTOM"}),
                "Unexpected status evidence, pending review.", "LOW", "PENDING_REVIEW",
                "2026-11-16 12:10:00", None, None,
            ),
        ]
        edge_summary_counts = [
            ("CONFIRMED", 1),
            ("INSUFFICIENT_DATA", 2),
            ("POTENTIAL", 0),
            ("REJECTED", 0),
        ]

        cursor = MockHistoricalDetectionsCursor(
            detections=edge_case_detections,
            summary_counts=edge_summary_counts,
        )
        connection = MockHistoricalDetectionsConnection(cursor)

        with patch("core.db.get_db_connection", return_value=connection):
            # 1. Filter by outcome=CONFIRMED
            resp_confirmed = self.client.get("/reports/historical-detections?outcome=CONFIRMED")
            self.assertEqual(resp_confirmed.status_code, 200)
            html_confirmed = resp_confirmed.get_data(as_text=True)
            self.assertIn("REC-EDGE-CONFIRMED-REJ", html_confirmed)
            self.assertNotIn("REC-EDGE-NULL-REJ", html_confirmed)
            self.assertNotIn("REC-EDGE-UNKNOWN-PEND", html_confirmed)

            # 2. Filter by outcome=INSUFFICIENT_DATA
            resp_insufficient = self.client.get("/reports/historical-detections?outcome=INSUFFICIENT_DATA")
            self.assertEqual(resp_insufficient.status_code, 200)
            html_insufficient = resp_insufficient.get_data(as_text=True)
            self.assertIn("REC-EDGE-NULL-REJ", html_insufficient)
            self.assertIn("REC-EDGE-UNKNOWN-PEND", html_insufficient)
            self.assertNotIn("REC-EDGE-CONFIRMED-REJ", html_insufficient)


if __name__ == "__main__":
    unittest.main()

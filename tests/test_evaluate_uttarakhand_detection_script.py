import unittest
from unittest.mock import patch

from scripts.evaluate_uttarakhand_detection import evaluate_and_persist


class EvaluationCursor:
    def __init__(self, observation):
        self.observation = observation
        self.result = None
        self.closed = False

    def execute(self, statement, parameters=None):
        normalized = " ".join(statement.split())
        if "FROM source_observations" in normalized:
            self.result = self.observation
        elif "FROM detection_results" in normalized:
            self.result = ("NEEDS_MORE_DATA", None)
        else:
            raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self.result

    def close(self):
        self.closed = True


class EvaluationConnection:
    def __init__(self, observation):
        self.cursor_instance = EvaluationCursor(observation)
        self.autocommit = True
        self.transaction_started = False
        self.committed = False
        self.rolled_back = False

    def start_transaction(self):
        self.transaction_started = True

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class EvaluateUttarakhandDetectionScriptTests(unittest.TestCase):
    def setUp(self):
        self.connection = EvaluationConnection(
            (
                7,
                "UEI-IMD-FL-2013-0131",
                "Chamoli, Pithoragarh, Rudra Prayag,Uttar Kashi Kashi",
                None,
                {
                    "Severity": None,
                    "Human fatality": "5000",
                    "Description of Casualties/injured": "died/missing",
                },
            )
        )

    @patch(
        "scripts.evaluate_uttarakhand_detection.persist_uttarakhand_2013_evaluation",
        return_value=23,
    )
    def test_commits_successful_persistence_and_returns_result(self, persist):
        result = evaluate_and_persist(self.connection)

        self.assertEqual(
            result,
            {
                "detection_id": 23,
                "status": "INSUFFICIENT_DATA",
                "review_status": "NEEDS_MORE_DATA",
                "candidate_priority": None,
            },
        )
        self.assertTrue(self.connection.transaction_started)
        self.assertTrue(self.connection.committed)
        self.assertFalse(self.connection.rolled_back)
        self.assertTrue(self.connection.cursor_instance.closed)
        self.assertEqual(persist.call_args.kwargs["source_id"], 7)
        self.assertEqual(persist.call_args.kwargs["observation_id"], 1)

    @patch(
        "scripts.evaluate_uttarakhand_detection.persist_uttarakhand_2013_evaluation",
        side_effect=RuntimeError("persistence failure"),
    )
    def test_rolls_back_when_persistence_fails(self, persist):
        with self.assertRaisesRegex(RuntimeError, "persistence failure"):
            evaluate_and_persist(self.connection)

        self.assertTrue(self.connection.rolled_back)
        self.assertFalse(self.connection.committed)
        self.assertTrue(self.connection.cursor_instance.closed)

    def test_rejects_wrong_observation_and_rolls_back(self):
        self.connection.cursor_instance.observation = (
            7, "OTHER-UEI", "Location", None, {}
        )

        with self.assertRaisesRegex(ValueError, "not the selected Uttarakhand event"):
            evaluate_and_persist(self.connection)

        self.assertTrue(self.connection.rolled_back)
        self.assertFalse(self.connection.committed)


if __name__ == "__main__":
    unittest.main()

import unittest
from copy import deepcopy
import json

from os_engine.historical_detection import (
    GENERAL_DETECTION_RULE_ID,
    GENERAL_DETECTION_RULE_VERSION,
    UTTARAKHAND_2013_RULE_ID,
    UTTARAKHAND_2013_RULE_VERSION,
    UTTARAKHAND_2013_UEI,
    compute_detection_outcome,
    evaluate_historical_observation,
    evaluate_uttarakhand_2013_observation,
    persist_historical_evaluation,
    persist_uttarakhand_2013_evaluation,
)


class DetectionPersistenceCursor:
    def __init__(self):
        self.detections = {}
        self.audit_rows = []
        self.lastrowid = 0
        self._result = None
        self._savepoint_state = None
        self.fail_audit_insert = False

    def execute(self, statement, parameters=None):
        statement = " ".join(statement.split())
        if statement.startswith("SAVEPOINT "):
            self._savepoint_state = (
                deepcopy(self.detections),
                deepcopy(self.audit_rows),
            )
        elif statement.startswith("ROLLBACK TO SAVEPOINT "):
            self.detections, self.audit_rows = deepcopy(self._savepoint_state)
        elif statement.startswith("RELEASE SAVEPOINT "):
            self._savepoint_state = None
        elif "SELECT detection_id, evaluated_values" in statement:
            self._result = self.detections.get(tuple(parameters))
        elif "INSERT INTO detection_results" in statement:
            observation_id, rule_id, rule_version, values, explanation, review_status = parameters
            detection_id = len(self.detections) + 1
            self.detections[(observation_id, rule_id, rule_version)] = (
                detection_id,
                values,
                explanation,
                None,
                review_status,
            )
            self.lastrowid = detection_id
        elif "INSERT INTO detection_audit" in statement:
            if self.fail_audit_insert:
                raise RuntimeError("Simulated audit insert failure")
            self.audit_rows.append(parameters)
            self.lastrowid = len(self.audit_rows)
        else:
            raise AssertionError(f"Unexpected SQL: {statement}")

    def fetchone(self):
        return self._result


class HistoricalDetectionTests(unittest.TestCase):
    def setUp(self):
        self.observation = {
            "source_record_id": UTTARAKHAND_2013_UEI,
            "raw_location": "Chamoli, Pithoragarh, Rudra Prayag,Uttar Kashi Kashi",
            "normalized_location": None,
            "raw_values": {
                "Severity": None,
                "Area Affected": None,
                "Latitude": None,
                "Longitude": None,
                "Human fatality": "5000",
                "Human injured": None,
                "Human Displaced": None,
                "Description of Casualties/injured": (
                    "About 5,000 people died/missing."
                ),
                "Main Cause": "heavy rainfall/flash floods",
            },
        }
        self.cursor = DetectionPersistenceCursor()

    def persist(self):
        return persist_uttarakhand_2013_evaluation(
            self.cursor,
            source_id=1,
            observation_id=1,
            observation=self.observation,
        )

    def test_uttarakhand_record_returns_insufficient_data_without_priority(self):
        result = evaluate_uttarakhand_2013_observation(self.observation)

        self.assertEqual(result.status, "INSUFFICIENT_DATA")
        self.assertIsNone(result.candidate_priority)
        self.assertEqual(
            result.missing_evidence,
            (
                "Severity",
                "Area Affected",
                "Latitude",
                "Longitude",
                "Human injured",
                "Human Displaced",
            ),
        )
        self.assertIn("measured rainfall", result.explanation)
        self.assertIn("died/missing", result.explanation)
        self.assertIn("not normalized", result.explanation)
        self.assertIn("resource demand", result.explanation)

    def test_evaluation_does_not_modify_observation(self):
        original_values = dict(self.observation["raw_values"])
        original_location = self.observation["raw_location"]

        evaluate_uttarakhand_2013_observation(self.observation)

        self.assertEqual(self.observation["raw_values"], original_values)
        self.assertEqual(self.observation["raw_location"], original_location)

    def test_rejects_an_observation_for_another_source_record(self):
        wrong_event = dict(
            self.observation,
            source_record_id="OTHER-UEI",
        )

        with self.assertRaises(ValueError):
            evaluate_uttarakhand_2013_observation(wrong_event)

    def test_persists_insufficient_data_and_audit(self):
        detection_id = self.persist()

        rule_key = (
            1,
            UTTARAKHAND_2013_RULE_ID,
            UTTARAKHAND_2013_RULE_VERSION,
        )
        self.assertIn(rule_key, self.cursor.detections)
        result = next(iter(self.cursor.detections.values()))
        self.assertEqual(detection_id, result[0])
        self.assertEqual(json.loads(result[1])["status"], "INSUFFICIENT_DATA")
        self.assertIsNone(result[3])
        self.assertEqual(result[4], "NEEDS_MORE_DATA")
        self.assertEqual(len(self.cursor.audit_rows), 1)
        self.assertEqual(self.cursor.audit_rows[0][3], "DETECTION_EVALUATION")
        self.assertEqual(self.cursor.audit_rows[0][2], detection_id)

    def test_repeated_evaluation_is_idempotent(self):
        first_id = self.persist()
        second_id = self.persist()

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(self.cursor.detections), 1)
        self.assertEqual(len(self.cursor.audit_rows), 1)

    def test_audit_failure_rolls_back_detection_insert(self):
        self.cursor.fail_audit_insert = True

        with self.assertRaisesRegex(RuntimeError, "Simulated audit insert failure"):
            self.persist()

        self.assertEqual(self.cursor.detections, {})
        self.assertEqual(self.cursor.audit_rows, [])


class DetectionOutcomeComputationTests(unittest.TestCase):
    def test_confirmed_only_when_potential_and_approved(self):
        self.assertEqual(compute_detection_outcome("POTENTIAL", "APPROVED"), "CONFIRMED")

    def test_potential_when_unreviewed_or_needs_more_data(self):
        self.assertEqual(compute_detection_outcome("POTENTIAL", "PENDING_REVIEW"), "POTENTIAL")
        self.assertEqual(compute_detection_outcome("POTENTIAL", "NEEDS_MORE_DATA"), "POTENTIAL")

    def test_insufficient_data_never_becomes_confirmed(self):
        self.assertEqual(compute_detection_outcome("INSUFFICIENT_DATA", "APPROVED"), "INSUFFICIENT_DATA")
        self.assertEqual(compute_detection_outcome("INSUFFICIENT_DATA", "NEEDS_MORE_DATA"), "INSUFFICIENT_DATA")

    def test_rejection_maps_to_rejected(self):
        self.assertEqual(compute_detection_outcome("POTENTIAL", "REJECTED"), "REJECTED")
        self.assertEqual(compute_detection_outcome("INSUFFICIENT_DATA", "REJECTED"), "REJECTED")


class GeneralHistoricalDetectionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.valid_candidate = {
            "source_record_id": "UEI-ASSAM-FL-2020-0042",
            "raw_location": "Guwahati, Kamrup, Assam",
            "normalized_location": "Guwahati",
            "event_start_date": "2020-07-15",
            "quality_status": "VERIFIED",
            "raw_values": {
                "Main Cause": "heavy rainfall/river breach",
                "Human fatality": "14",
                "Human injured": "45",
                "Extent of damage ": "Severe flooding in residential areas",
                "Severity": None,
                "Area Affected": None,
                "Latitude": None,
                "Longitude": None,
            },
        }

    def test_potential_disaster_candidate_with_sufficient_evidence(self):
        result = evaluate_historical_observation(self.valid_candidate)

        self.assertEqual(result.status, "POTENTIAL")
        self.assertIsNone(result.candidate_priority)
        self.assertIn("Evidence suggests a possible disaster event", result.explanation)
        self.assertTrue(any("Location: Guwahati" in s for s in result.supporting_evidence))
        self.assertTrue(any("Reported fatalities: 14" in s for s in result.supporting_evidence))
        self.assertIn("Severity", result.missing_evidence)
        self.assertIn("Latitude", result.missing_evidence)

    def test_confirmed_status_when_sufficient_evidence_and_already_approved(self):
        approved_candidate = dict(self.valid_candidate, review_status="APPROVED")
        result = evaluate_historical_observation(approved_candidate)

        self.assertEqual(result.status, "CONFIRMED")
        self.assertIsNone(result.candidate_priority)
        self.assertIn("Confirmed historical disaster observation", result.explanation)

    def test_insufficient_data_when_missing_disaster_type(self):
        obs = deepcopy(self.valid_candidate)
        del obs["raw_values"]["Main Cause"]

        result = evaluate_historical_observation(obs)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")
        self.assertIn("disaster_type", result.missing_evidence)
        self.assertIn("No identifiable disaster type", result.explanation)

    def test_insufficient_data_when_missing_location(self):
        obs = deepcopy(self.valid_candidate)
        obs["raw_location"] = None
        obs["normalized_location"] = None

        result = evaluate_historical_observation(obs)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")
        self.assertIn("location", result.missing_evidence)
        self.assertIn("No identifiable location", result.explanation)

    def test_insufficient_data_when_missing_event_date(self):
        obs = deepcopy(self.valid_candidate)
        obs["event_start_date"] = None

        result = evaluate_historical_observation(obs)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")
        self.assertIn("event_date", result.missing_evidence)
        self.assertIn("No event start date", result.explanation)

    def test_insufficient_data_when_no_impact_evidence(self):
        obs = deepcopy(self.valid_candidate)
        obs["raw_values"] = {
            "Main Cause": "heavy rainfall",
            "Human fatality": None,
            "Human injured": None,
            "Description of Casualties/injured": None,
            "Extent of damage ": None,
            "Rainfall": None,
        }

        result = evaluate_historical_observation(obs)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")
        self.assertIn("impact_evidence", result.missing_evidence)

    def test_insufficient_data_when_quality_status_is_invalid_or_incomplete(self):
        for quality in ("INVALID", "INCOMPLETE"):
            with self.subTest(quality=quality):
                obs = dict(self.valid_candidate, quality_status=quality)
                result = evaluate_historical_observation(obs)
                self.assertEqual(result.status, "INSUFFICIENT_DATA")
                self.assertIn("data_quality", result.missing_evidence)

    def test_insufficient_data_when_casualty_narrative_is_ambiguous(self):
        obs = deepcopy(self.valid_candidate)
        obs["raw_values"]["Human fatality"] = "100"
        obs["raw_values"]["Description of Casualties/injured"] = "100 died/missing (unconfirmed estimates)"

        result = evaluate_historical_observation(obs)
        self.assertEqual(result.status, "INSUFFICIENT_DATA")
        self.assertIn("ambiguous due to source narrative", result.explanation)

    def test_evaluation_never_invents_severity_or_coordinates_or_priority(self):
        result = evaluate_historical_observation(self.valid_candidate)
        self.assertIsNone(result.candidate_priority)
        self.assertIn("Severity", result.missing_evidence)
        self.assertIn("Latitude", result.missing_evidence)
        self.assertIn("Longitude", result.missing_evidence)
        self.assertIn("Area Affected", result.missing_evidence)

    def test_does_not_mutate_input_dict(self):
        original = deepcopy(self.valid_candidate)
        evaluate_historical_observation(self.valid_candidate)
        self.assertEqual(self.valid_candidate, original)


class GeneralHistoricalDetectionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.cursor = DetectionPersistenceCursor()
        self.valid_obs = {
            "source_record_id": "UEI-ODISHA-CY-2019-0015",
            "raw_location": "Puri, Odisha",
            "normalized_location": "Puri",
            "event_start_date": "2019-05-03",
            "quality_status": "VERIFIED",
            "raw_values": {
                "Main Cause": "Cyclone Fani",
                "Human fatality": "64",
                "Extent of damage ": "Severe structural damage across coastal belt",
            },
        }

    def test_persists_potential_with_pending_review_and_audit(self):
        detection_id = persist_historical_evaluation(
            self.cursor,
            source_id=2,
            observation_id=5,
            observation=self.valid_obs,
        )

        rule_key = (5, GENERAL_DETECTION_RULE_ID, GENERAL_DETECTION_RULE_VERSION)
        self.assertIn(rule_key, self.cursor.detections)
        row = self.cursor.detections[rule_key]
        self.assertEqual(row[0], detection_id)
        self.assertEqual(json.loads(row[1])["status"], "POTENTIAL")
        self.assertIsNone(row[3])
        self.assertEqual(row[4], "PENDING_REVIEW")
        self.assertEqual(len(self.cursor.audit_rows), 1)
        self.assertEqual(self.cursor.audit_rows[0][3], "DETECTION_EVALUATION")

    def test_persists_insufficient_data_with_needs_more_data(self):
        insufficient_obs = dict(self.valid_obs, raw_location=None, normalized_location=None)
        detection_id = persist_historical_evaluation(
            self.cursor,
            source_id=2,
            observation_id=6,
            observation=insufficient_obs,
        )

        rule_key = (6, GENERAL_DETECTION_RULE_ID, GENERAL_DETECTION_RULE_VERSION)
        self.assertIn(rule_key, self.cursor.detections)
        row = self.cursor.detections[rule_key]
        self.assertEqual(row[0], detection_id)
        self.assertEqual(json.loads(row[1])["status"], "INSUFFICIENT_DATA")
        self.assertEqual(row[4], "NEEDS_MORE_DATA")

    def test_repeated_persistence_is_idempotent(self):
        first_id = persist_historical_evaluation(
            self.cursor, source_id=2, observation_id=5, observation=self.valid_obs
        )
        second_id = persist_historical_evaluation(
            self.cursor, source_id=2, observation_id=5, observation=self.valid_obs
        )
        self.assertEqual(first_id, second_id)
        self.assertEqual(len(self.cursor.detections), 1)
        self.assertEqual(len(self.cursor.audit_rows), 1)

    def test_audit_failure_rolls_back_savepoint(self):
        self.cursor.fail_audit_insert = True
        with self.assertRaisesRegex(RuntimeError, "Simulated audit insert failure"):
            persist_historical_evaluation(
                self.cursor, source_id=2, observation_id=5, observation=self.valid_obs
            )
        self.assertEqual(self.cursor.detections, {})
        self.assertEqual(self.cursor.audit_rows, [])


if __name__ == "__main__":
    unittest.main()

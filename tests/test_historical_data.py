import json
import unittest
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import mysql.connector
from os_engine.historical_data import (
    SourceRecordConflictError,
    import_source_observation,
    register_dataset_source,
)


class HistoricalDataCursor:
    def __init__(self):
        self.sources = {}
        self.observations = {}
        self.audit_rows = []
        self.statements = []
        self.lastrowid = 0
        self._result = None
        self._next_source_id = 1
        self._next_observation_id = 1
        self._savepoint_state = None
        self.fail_audit_insert = False
        self.concurrent_duplicate_on_insert = False

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        if statement.startswith("SAVEPOINT "):
            self._savepoint_state = (
                deepcopy(self.observations),
                deepcopy(self.audit_rows),
            )
        elif statement.startswith("ROLLBACK TO SAVEPOINT "):
            self.observations, self.audit_rows = deepcopy(self._savepoint_state)
        elif statement.startswith("RELEASE SAVEPOINT "):
            self._savepoint_state = None
        elif "INSERT INTO dataset_sources" in statement:
            source_key = parameters[0]
            row = self.sources.get(source_key)
            if row is None:
                row = (self._next_source_id, *parameters)
                self.sources[source_key] = row
                self._next_source_id += 1
            self.lastrowid = row[0]
        elif "INSERT INTO source_observations" in statement:
            source_id, source_record_id = parameters[:2]
            key = (source_id, source_record_id)
            row = self.observations.get(key)
            if row is not None or self.concurrent_duplicate_on_insert:
                if row is None:
                    row = (self._next_observation_id, *parameters)
                    self.observations[key] = row
                    self._next_observation_id += 1
                    self.concurrent_duplicate_on_insert = False
                raise mysql.connector.IntegrityError(
                    msg="Duplicate source observation",
                    errno=1062,
                )
            row = (self._next_observation_id, *parameters)
            self.observations[key] = row
            self._next_observation_id += 1
            self.lastrowid = row[0]
        elif "SELECT observation_id, event_start_date" in statement:
            row = self.observations.get(tuple(parameters))
            self._result = (row[0], *row[3:]) if row is not None else None
        elif "INSERT INTO detection_audit" in statement:
            if self.fail_audit_insert:
                raise RuntimeError("Simulated audit insert failure")
            self.audit_rows.append(parameters)
            self.lastrowid = len(self.audit_rows)
        else:
            raise AssertionError(f"Unexpected SQL in historical importer: {statement}")

    def fetchone(self):
        return self._result


class HistoricalDataTests(unittest.TestCase):
    def setUp(self):
        self.cursor = HistoricalDataCursor()
        self.source_id = register_dataset_source(
            self.cursor,
            provider="IIT Delhi",
            source_url="https://zenodo.org/records/16994648",
            dataset_name="India_Flood_Inventory_v3.csv",
            dataset_version=None,
            license_attribution="CC BY-NC 4.0; DOI: 10.5281/zenodo.16994648",
            retrieved_at=datetime(2026, 11, 9, 12, 0, 0),
            checksum="sha256:example-retrieval-checksum",
        )

    def test_reimporting_same_source_record_is_idempotent(self):
        values = {"UEI": "UEI-IMD-FL-2013-0131", "Human injured": None}
        payload = {"UEI": "UEI-IMD-FL-2013-0131", "Human injured": None}
        first_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values=values,
            raw_payload=payload,
            event_start_date=date(2013, 6, 14),
            event_end_date=date(2013, 6, 18),
            raw_location="Chamoli; Pithoragarh; Rudra Prayag; Uttar Kashi Kashi",
            quality_status="INCOMPLETE",
        )
        second_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values=values,
            raw_payload=payload,
            event_start_date=date(2013, 6, 14),
            event_end_date=date(2013, 6, 18),
            raw_location="Chamoli; Pithoragarh; Rudra Prayag; Uttar Kashi Kashi",
            quality_status="INCOMPLETE",
        )

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(self.cursor.observations), 1)
        self.assertEqual(len(self.cursor.audit_rows), 1)

    def test_timezone_aware_and_naive_datetimes_normalize_to_same_utc(self):
        aware_observed = datetime(
            2013, 6, 14, 5, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        naive_utc_observed = datetime(2013, 6, 14, 0, 0)
        observation_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values={"UEI": "UEI-IMD-FL-2013-0131"},
            raw_payload={"UEI": "UEI-IMD-FL-2013-0131"},
            observed_at=aware_observed,
        )
        repeated_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values={"UEI": "UEI-IMD-FL-2013-0131"},
            raw_payload={"UEI": "UEI-IMD-FL-2013-0131"},
            observed_at=naive_utc_observed,
        )

        row = next(iter(self.cursor.observations.values()))
        self.assertEqual(row[5], naive_utc_observed)
        self.assertEqual(observation_id, repeated_id)
        self.assertEqual(len(self.cursor.audit_rows), 1)

    def test_source_registration_normalizes_aware_retrieval_datetime(self):
        retrieved_at = datetime(
            2026, 11, 9, 17, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        source_id = register_dataset_source(
            self.cursor,
            provider="IIT Delhi",
            source_url="https://zenodo.org/records/16994648",
            dataset_name="India_Flood_Inventory_v3.csv",
            dataset_version=None,
            license_attribution="CC BY-NC 4.0; DOI: 10.5281/zenodo.16994648",
            retrieved_at=retrieved_at,
            checksum="sha256:another-retrieval-checksum",
        )
        source = next(row for row in self.cursor.sources.values() if row[0] == source_id)
        self.assertEqual(source[7], datetime(2026, 11, 9, 12, 0))

    def test_source_record_ids_are_case_sensitive(self):
        values = {"UEI": "UEI-IMD-FL-2013-0131"}
        uppercase_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values=values,
            raw_payload=values,
        )
        lowercase_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="uei-imd-fl-2013-0131",
            raw_values=values,
            raw_payload=values,
        )

        self.assertNotEqual(uppercase_id, lowercase_id)
        self.assertEqual(len(self.cursor.observations), 2)
        self.assertEqual(len(self.cursor.audit_rows), 2)
        schema = Path(__file__).parents[1] / "database" / "schema.sql"
        schema_text = schema.read_text(encoding="utf-8")
        self.assertIn(
            "source_record_id VARCHAR(191) CHARACTER SET utf8mb4 "
            "COLLATE utf8mb4_0900_as_cs NOT NULL",
            schema_text,
        )

    def test_concurrent_identical_import_uses_locked_existing_row_without_audit(self):
        self.cursor.concurrent_duplicate_on_insert = True
        observation_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values={"UEI": "UEI-IMD-FL-2013-0131"},
            raw_payload={"UEI": "UEI-IMD-FL-2013-0131"},
        )

        self.assertEqual(observation_id, 1)
        self.assertEqual(len(self.cursor.observations), 1)
        self.assertEqual(self.cursor.audit_rows, [])
        self.assertTrue(any("FOR UPDATE" in sql for sql in self.cursor.statements))

    def test_audit_failure_rolls_back_observation_to_savepoint(self):
        self.cursor.fail_audit_insert = True
        with self.assertRaisesRegex(RuntimeError, "Simulated audit insert failure"):
            import_source_observation(
                self.cursor,
                source_id=self.source_id,
                source_record_id="UEI-IMD-FL-2013-0131",
                raw_values={"UEI": "UEI-IMD-FL-2013-0131"},
                raw_payload={"UEI": "UEI-IMD-FL-2013-0131"},
            )

        self.assertEqual(self.cursor.observations, {})
        self.assertEqual(self.cursor.audit_rows, [])
        self.assertIn(
            "ROLLBACK TO SAVEPOINT historical_observation_import",
            self.cursor.statements,
        )

    def test_missing_fields_remain_null_or_absent_and_never_become_zero(self):
        observation_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values={"Severity": None, "Area Affected": None},
            raw_payload={
                "UEI": "UEI-IMD-FL-2013-0131",
                "Severity": None,
                "Area Affected": None,
            },
            quality_status="INCOMPLETE",
        )

        row = next(iter(self.cursor.observations.values()))
        self.assertEqual(row[8], '{"Severity":null,"Area Affected":null}')
        self.assertEqual(
            row[9],
            '{"UEI":"UEI-IMD-FL-2013-0131","Severity":null,"Area Affected":null}',
        )
        self.assertNotIn("0", (row[8], row[9]))
        self.assertEqual(observation_id, row[0])

    def test_ambiguous_casualty_value_is_preserved_and_flagged(self):
        raw_values = {"Human fatality": "5000", "Human injured": None}
        observation_id = import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values=raw_values,
            raw_payload={
                "UEI": "UEI-IMD-FL-2013-0131",
                **raw_values,
            },
            quality_status="AMBIGUOUS",
        )

        row = next(iter(self.cursor.observations.values()))
        self.assertEqual(row[8], '{"Human fatality":"5000","Human injured":null}')
        self.assertEqual(row[10], "AMBIGUOUS")
        self.assertEqual(observation_id, row[0])

    def test_conflicting_reimport_is_reported_without_overwriting(self):
        import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values={"Severity": None},
            raw_payload={"UEI": "UEI-IMD-FL-2013-0131", "Severity": None},
            quality_status="INCOMPLETE",
        )
        with self.assertRaises(SourceRecordConflictError):
            import_source_observation(
                self.cursor,
                source_id=self.source_id,
                source_record_id="UEI-IMD-FL-2013-0131",
                raw_values={"Severity": "HIGH"},
                raw_payload={"UEI": "UEI-IMD-FL-2013-0131", "Severity": "HIGH"},
                quality_status="INCOMPLETE",
            )

        stored = next(iter(self.cursor.observations.values()))
        self.assertEqual(json.loads(stored[8]), {"Severity": None})

    def test_historical_import_writes_no_live_request_or_allocation_data(self):
        import_source_observation(
            self.cursor,
            source_id=self.source_id,
            source_record_id="UEI-IMD-FL-2013-0131",
            raw_values={"UEI": "UEI-IMD-FL-2013-0131"},
            raw_payload={"UEI": "UEI-IMD-FL-2013-0131"},
        )
        sql = "\n".join(self.cursor.statements).lower()
        self.assertNotIn("emergency_requests", sql)
        self.assertNotIn("allocations", sql)
        self.assertNotIn("resources", sql)

        schema = Path(__file__).parents[1] / "database" / "schema.sql"
        schema_text = schema.read_text(encoding="utf-8")
        historical_ddl = schema_text.split(
            "-- -------------------------------------------------------- historical data foundation"
        )[1].split("-- ---------------------------------------------------------------- 9. shelters")[0]
        self.assertIn("CREATE TABLE IF NOT EXISTS source_observations", historical_ddl)
        self.assertNotIn("REFERENCES emergency_requests", historical_ddl)
        self.assertIn("prevent_detection_audit_update", schema_text)
        self.assertIn("prevent_detection_audit_delete", schema_text)
        self.assertIn(
            "BEFORE UPDATE ON detection_audit\nFOR EACH ROW\nBEGIN\n"
            "    SIGNAL SQLSTATE '45000'",
            schema_text,
        )
        self.assertIn(
            "BEFORE DELETE ON detection_audit\nFOR EACH ROW\nBEGIN\n"
            "    SIGNAL SQLSTATE '45000'",
            schema_text,
        )

    def test_source_registration_is_idempotent(self):
        repeated_id = register_dataset_source(
            self.cursor,
            provider="IIT Delhi",
            source_url="https://zenodo.org/records/16994648",
            dataset_name="India_Flood_Inventory_v3.csv",
            dataset_version=None,
            license_attribution="CC BY-NC 4.0; DOI: 10.5281/zenodo.16994648",
            retrieved_at=datetime(2026, 11, 9, 12, 0, 0),
            checksum="sha256:example-retrieval-checksum",
        )
        self.assertEqual(self.source_id, repeated_id)
        self.assertEqual(len(self.cursor.sources), 1)


if __name__ == "__main__":
    unittest.main()

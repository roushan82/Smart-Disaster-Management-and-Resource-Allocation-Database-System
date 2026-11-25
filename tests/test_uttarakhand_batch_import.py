import csv
import hashlib
import io
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scripts.import_uttarakhand_2013 import (
    DATASET_API_URL,
    DATASET_DOI,
    DATASET_FILE_NAME,
    DATASET_LICENSE_ID,
    DATASET_RECORD_ID,
    EXPECTED_COLUMNS,
    EXPECTED_UEI,
    ImportPreparationError,
)
from scripts.import_uttarakhand_batch import (
    fetch_prepared_batch,
    import_prepared_batch,
    prepare_batch_import,
)
from os_engine.historical_data import SourceRecordConflictError


def make_row(uei, state, **overrides):
    row = {column: "" for column in EXPECTED_COLUMNS}
    row.update({
        "Unnamed: 0": "1",
        "UEI": uei,
        "Start Date": "14-06-2013 00:00",
        "End Date": "18-06-2013 00:00",
        "Duration(Days)": "5",
        "Main Cause": "flood",
        "Districts": "Chamoli",
        "State": state,
        "Event Source": "IMD",
        "District_LGD_Codes": "47",
        "State_Codes": "5",
    })
    row.update(overrides)
    return row


def make_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=EXPECTED_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8")


class BatchImportPreparationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            make_row(EXPECTED_UEI, "Uttarakhand"),
            make_row("UEI-UT-002", "Uttar Pradesh, Uttarakhand", Severity=""),
            make_row("UEI-OTHER", "Himachal Pradesh"),
        ]
        self.csv_bytes = make_csv(self.rows)
        self.record = {
            "id": DATASET_RECORD_ID,
            "doi": DATASET_DOI,
            "metadata": {"license": {"id": DATASET_LICENSE_ID}},
        }
        self.file_metadata = {
            "key": DATASET_FILE_NAME,
            "size": len(self.csv_bytes),
            "checksum": "md5:" + hashlib.md5(self.csv_bytes).hexdigest(),
            "links": {"self": "https://example.invalid/inventory.csv"},
        }
        self.retrieved_at = datetime(2026, 11, 15, tzinfo=timezone.utc)

    def prepare(self, rows=None):
        csv_bytes = make_csv(self.rows if rows is None else rows)
        file_metadata = dict(
            self.file_metadata,
            size=len(csv_bytes),
            checksum="md5:" + hashlib.md5(csv_bytes).hexdigest(),
        )
        return prepare_batch_import(
            self.record,
            file_metadata,
            csv_bytes,
            retrieved_at=self.retrieved_at,
        )

    def test_selects_single_and_multi_state_uttarakhand_rows(self):
        prepared = self.prepare()
        self.assertEqual(
            [item["source_record_id"] for item in prepared.observations],
            [EXPECTED_UEI, "UEI-UT-002"],
        )

    def test_batch_does_not_require_pinned_2013_event(self):
        rows = [make_row("UEI-UT-OTHER", "Uttarakhand")]
        with patch(
            "scripts.import_uttarakhand_2013.prepare_import",
            side_effect=AssertionError("single-event preparation must not be used"),
        ):
            prepared = self.prepare(rows)

        self.assertEqual(
            [item["source_record_id"] for item in prepared.observations],
            ["UEI-UT-OTHER"],
        )

    def test_rejects_duplicate_and_missing_ueis_in_selected_batch(self):
        duplicate_rows = [
            self.rows[0],
            make_row("UEI-UT-002", "Uttarakhand"),
            make_row("UEI-UT-002", "Uttarakhand"),
        ]
        with self.assertRaisesRegex(ImportPreparationError, "Duplicate UEI"):
            self.prepare(duplicate_rows)

        missing_rows = [
            self.rows[0],
            make_row("", "Uttarakhand"),
        ]
        with self.assertRaisesRegex(ImportPreparationError, "blank UEI"):
            self.prepare(missing_rows)

    def test_rejects_invalid_dataset_metadata_size_checksum_and_header(self):
        with self.assertRaisesRegex(ImportPreparationError, "Zenodo DOI"):
            prepare_batch_import(
                dict(self.record, doi="incorrect"),
                self.file_metadata,
                self.csv_bytes,
                retrieved_at=self.retrieved_at,
            )
        with self.assertRaisesRegex(ImportPreparationError, "file size"):
            prepare_batch_import(
                self.record,
                dict(self.file_metadata, size=len(self.csv_bytes) + 1),
                self.csv_bytes,
                retrieved_at=self.retrieved_at,
            )
        with self.assertRaisesRegex(ImportPreparationError, "checksum"):
            prepare_batch_import(
                self.record,
                dict(self.file_metadata, checksum="md5:incorrect"),
                self.csv_bytes,
                retrieved_at=self.retrieved_at,
            )

        bad_csv = b"UEI,State\n"
        bad_file_metadata = {
            **self.file_metadata,
            "size": len(bad_csv),
            "checksum": "md5:" + hashlib.md5(bad_csv).hexdigest(),
        }
        with self.assertRaisesRegex(ImportPreparationError, "CSV header"):
            prepare_batch_import(
                self.record,
                bad_file_metadata,
                bad_csv,
                retrieved_at=self.retrieved_at,
            )

    def test_incomplete_values_are_preserved_and_not_verified(self):
        prepared = self.prepare()
        observation = prepared.observations[1]
        self.assertIsNone(observation["raw_values"]["Severity"])
        self.assertEqual(observation["raw_payload"]["Severity"], "")
        self.assertEqual(observation["quality_status"], "UNASSESSED")
        self.assertIsNone(observation["normalized_location"])
        self.assertIsNone(observation["observed_at"])

    def test_fetch_uses_mocked_downloads_and_existing_source_validation(self):
        api_response = dict(
            self.record,
            files=[self.file_metadata],
        )
        with patch(
            "scripts.import_uttarakhand_batch._download",
            side_effect=[json.dumps(api_response).encode(), self.csv_bytes],
        ) as download:
            prepared = fetch_prepared_batch()

        self.assertEqual(download.call_args_list[0].args, (DATASET_API_URL,))
        self.assertEqual(len(prepared.observations), 2)
        self.assertEqual(prepared.source["dataset_name"], DATASET_FILE_NAME)


class BatchImportPersistenceTests(unittest.TestCase):
    class FakeCursor:
        def close(self):
            pass

    class FakeConnection:
        def __init__(self):
            self.fake_cursor = BatchImportPersistenceTests.FakeCursor()
            self.commits = 0
            self.rollbacks = 0
            self.closed = False
            self.autocommit = True

        def start_transaction(self):
            pass

        def cursor(self):
            return self.fake_cursor

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

        def close(self):
            self.closed = True

    def setUp(self):
        fixture = BatchImportPreparationTests()
        fixture.setUp()
        self.prepared = fixture.prepare()

    def test_uses_existing_helpers_for_idempotent_imports(self):
        connection = self.FakeConnection()
        observation_ids = {}

        def idempotent_import(cursor, *, source_id, source_record_id, **kwargs):
            return observation_ids.setdefault(source_record_id, len(observation_ids) + 10)

        with patch("core.db.get_db_connection", return_value=connection):
            with patch(
                "os_engine.historical_data.register_dataset_source",
                return_value=7,
            ) as register_source:
                with patch(
                    "os_engine.historical_data.import_source_observation",
                    side_effect=idempotent_import,
                ) as import_observation:
                    first = import_prepared_batch(self.prepared)
                    second = import_prepared_batch(self.prepared)

        self.assertEqual(first["observation_ids"], second["observation_ids"])
        self.assertEqual(register_source.call_count, 2)
        self.assertEqual(import_observation.call_count, 4)
        self.assertEqual(connection.commits, 2)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(connection.closed)

    def test_conflicting_record_rolls_back_whole_batch(self):
        connection = self.FakeConnection()
        with patch("core.db.get_db_connection", return_value=connection):
            with patch(
                "os_engine.historical_data.register_dataset_source",
                return_value=7,
            ):
                with patch(
                    "os_engine.historical_data.import_source_observation",
                    side_effect=SourceRecordConflictError("conflicting record"),
                ):
                    with self.assertRaises(SourceRecordConflictError):
                        import_prepared_batch(self.prepared)

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()

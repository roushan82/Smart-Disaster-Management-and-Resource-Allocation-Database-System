import csv
import io
import unittest
from datetime import datetime, timezone

from scripts.import_uttarakhand_2013 import (
    DATASET_DOI,
    DATASET_FILE_NAME,
    DATASET_LICENSE_ID,
    DATASET_RECORD_ID,
    EXPECTED_COLUMNS,
    EXPECTED_UEI,
    ImportPreparationError,
    prepare_import,
)


VERIFIED_EVENT_ROW = {
    "Unnamed: 0": "4404",
    "UEI": EXPECTED_UEI,
    "Start Date": "14-06-2013 00:00",
    "End Date": "18-06-2013 00:00",
    "Duration(Days)": "5",
    "Main Cause": "heavy to very rainfall/flash floods (saraswati,mandakini rivers,its tributaries triggered by very heavy rainfall,lake burst), cloudburst on jun16 &17 jun, massive landslides,avalanches reported at multiple places. ",
    "Location": "",
    "Districts": "Chamoli, Pithoragarh, Rudra Prayag,Uttar Kashi Kashi",
    "State": "Uttarakhand",
    "Latitude": "",
    "Longitude": "",
    "Severity": "",
    "Area Affected": "",
    "Human fatality": "5000",
    "Human injured": "",
    "Human Displaced": "",
    "Animal Fatality": "1000",
    "Description of Casualties/injured": "i) About 5,000 people- the locals, the pilgrims, the tourists, trakers died/missing. ii) More than thousand livestock perished. ",
    "Extent of damage ": "i) More than thousands of pilgrims of Char Dham on the Gangotri,Yamunotri routes, Hemkund, Roopkunda,tourists/trakers of Valley of flowers stranded. ii) Lakhs of people shifted to safer places. iii) Many roads including state highways, infrastructures, villages washed away/damaged. iv) Many Hydroelectric projects, houses, water schemes, bridges, commercial,residential buildings, hotels, rest houses, shops, temples, telephone towers, vehicles destroyed/ drifted in the floods of rivers. v) Damage to the cash crops especially apple crops reported.    ",
    "Event Source": "IMD",
    "Event Souce ID": "",
    "District_LGD_Codes": "47, 53, 54, None",
    "State_Codes": "5",
}


def make_dataset():
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=EXPECTED_COLUMNS)
    writer.writeheader()
    writer.writerow(VERIFIED_EVENT_ROW)
    return stream.getvalue().encode("utf-8")


class UttarakhandImportMappingTests(unittest.TestCase):
    def setUp(self):
        self.csv_bytes = make_dataset()
        self.record = {
            "id": DATASET_RECORD_ID,
            "doi": DATASET_DOI,
            "metadata": {
                "license": {"id": DATASET_LICENSE_ID},
                "title": "India Flood Inventory-Impacts",
            },
        }
        import hashlib

        self.file_metadata = {
            "key": DATASET_FILE_NAME,
            "size": len(self.csv_bytes),
            "checksum": "md5:" + hashlib.md5(self.csv_bytes).hexdigest(),
        }
        self.retrieved_at = datetime(2026, 11, 9, tzinfo=timezone.utc)

    def prepare(self):
        return prepare_import(
            self.record,
            self.file_metadata,
            self.csv_bytes,
            retrieved_at=self.retrieved_at,
        )

    def test_maps_only_verified_uei_and_preserves_original_record(self):
        prepared = self.prepare()
        observation = prepared.observation

        self.assertEqual(observation["source_record_id"], EXPECTED_UEI)
        self.assertEqual(observation["event_start_date"].isoformat(), "2013-06-14")
        self.assertEqual(observation["event_end_date"].isoformat(), "2013-06-18")
        self.assertEqual(observation["raw_location"], VERIFIED_EVENT_ROW["Districts"])
        self.assertIsNone(observation["normalized_location"])
        self.assertIsNone(observation["observed_at"])
        self.assertEqual(observation["raw_payload"], VERIFIED_EVENT_ROW)
        self.assertEqual(observation["raw_values"]["Duration(Days)"], "5")

    def test_blank_cells_become_null_in_values_and_stay_blank_in_raw_payload(self):
        observation = self.prepare().observation
        self.assertIsNone(observation["raw_values"]["Severity"])
        self.assertIsNone(observation["raw_values"]["Area Affected"])
        self.assertIsNone(observation["raw_values"]["Latitude"])
        self.assertEqual(observation["raw_payload"]["Severity"], "")
        self.assertEqual(observation["raw_payload"]["Area Affected"], "")
        self.assertNotEqual(observation["raw_values"]["Severity"], 0)

    def test_ambiguous_casualty_count_and_original_narrative_are_preserved(self):
        observation = self.prepare().observation
        self.assertEqual(observation["quality_status"], "AMBIGUOUS")
        self.assertEqual(observation["raw_values"]["Human fatality"], "5000")
        self.assertIn(
            "died/missing",
            observation["raw_values"]["Description of Casualties/injured"],
        )
        self.assertTrue(any("not treated as verified casualties" in note
                            for note in self.prepare().data_quality_notes))

    def test_source_metadata_uses_published_license_version_and_checksum(self):
        prepared = self.prepare()
        self.assertEqual(prepared.source["dataset_version"], "v4")
        self.assertIn(DATASET_DOI, prepared.source["license_attribution"])
        self.assertIn("CC BY-NC 4.0", prepared.source["license_attribution"])
        self.assertTrue(prepared.source["checksum"].startswith("sha256:"))
        self.assertTrue(prepared.source_file_md5.startswith("md5:"))
        self.assertEqual(prepared.source["retrieved_at"], self.retrieved_at)

    def test_rejects_modified_file_checksum_and_incorrect_headers(self):
        with self.assertRaises(ImportPreparationError):
            prepare_import(
                self.record,
                dict(self.file_metadata, checksum="md5:incorrect"),
                self.csv_bytes,
                retrieved_at=self.retrieved_at,
            )
        bad_csv = b"UEI,Start Date\n" + self.csv_bytes
        metadata = dict(
            self.file_metadata,
            size=len(bad_csv),
            checksum="md5:" + __import__("hashlib").md5(bad_csv).hexdigest(),
        )
        with self.assertRaises(ImportPreparationError):
            prepare_import(
                self.record,
                metadata,
                bad_csv,
                retrieved_at=self.retrieved_at,
            )

    def test_rejects_missing_or_duplicate_target_event(self):
        missing_row = dict(VERIFIED_EVENT_ROW, UEI="OTHER-UEI")
        for rows, expected_matches in (
            ([missing_row], 0),
            ([VERIFIED_EVENT_ROW, VERIFIED_EVENT_ROW], 2),
        ):
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, fieldnames=EXPECTED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
            contents = stream.getvalue().encode("utf-8")
            import hashlib

            metadata = dict(
                self.file_metadata,
                size=len(contents),
                checksum="md5:" + hashlib.md5(contents).hexdigest(),
            )
            with self.subTest(expected_matches=expected_matches):
                with self.assertRaises(ImportPreparationError):
                    prepare_import(
                        self.record,
                        metadata,
                        contents,
                        retrieved_at=self.retrieved_at,
                    )


if __name__ == "__main__":
    unittest.main()

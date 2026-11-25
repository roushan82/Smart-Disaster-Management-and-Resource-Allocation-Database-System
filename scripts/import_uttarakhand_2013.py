"""Prepare or import the verified IFI-Impacts Uttarakhand 2013 event row."""

import argparse
import csv
import hashlib
import io
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATASET_RECORD_ID = 16994648
DATASET_RECORD_URL = f"https://zenodo.org/records/{DATASET_RECORD_ID}"
DATASET_API_URL = f"https://zenodo.org/api/records/{DATASET_RECORD_ID}"
DATASET_FILE_NAME = "India_Flood_Inventory_v3.csv"
DATASET_VERSION = "v4"
DATASET_DOI = "10.5281/zenodo.16994648"
DATASET_LICENSE_ID = "cc-by-nc-4.0"
EXPECTED_UEI = "UEI-IMD-FL-2013-0131"
EXPECTED_COLUMNS = (
    "Unnamed: 0",
    "UEI",
    "Start Date",
    "End Date",
    "Duration(Days)",
    "Main Cause",
    "Location",
    "Districts",
    "State",
    "Latitude",
    "Longitude",
    "Severity",
    "Area Affected",
    "Human fatality",
    "Human injured",
    "Human Displaced",
    "Animal Fatality",
    "Description of Casualties/injured",
    "Extent of damage ",
    "Event Source",
    "Event Souce ID",
    "District_LGD_Codes",
    "State_Codes",
)


class ImportPreparationError(ValueError):
    """Raised when the source record does not match the verified dataset."""


@dataclass(frozen=True)
class PreparedImport:
    source: dict
    observation: dict
    data_quality_notes: tuple
    source_file_md5: str


def _download(url):
    request = Request(url, headers={"User-Agent": "SmartDisasterManagement/Phase2"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def _read_event_row(csv_bytes):
    text_stream = io.TextIOWrapper(
        io.BytesIO(csv_bytes),
        encoding="utf-8-sig",
        newline="",
    )
    reader = csv.DictReader(text_stream)
    if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
        raise ImportPreparationError(
            "The CSV header does not match the inspected IFI-Impacts file."
        )

    matches = [
        row for row in reader
        if row.get("UEI") == EXPECTED_UEI
    ]
    if len(matches) != 1:
        raise ImportPreparationError(
            f"Expected exactly one row for {EXPECTED_UEI}; found {len(matches)}."
        )
    return matches[0]


def prepare_import(record_metadata, file_metadata, csv_bytes, retrieved_at=None):
    """Validate metadata and map the selected source row without database access."""
    metadata = record_metadata.get("metadata", {})
    if record_metadata.get("id") != DATASET_RECORD_ID:
        raise ImportPreparationError("The Zenodo record ID did not match the selected dataset.")
    if record_metadata.get("doi") != DATASET_DOI:
        raise ImportPreparationError("The Zenodo DOI did not match the selected dataset.")
    if metadata.get("license", {}).get("id") != DATASET_LICENSE_ID:
        raise ImportPreparationError("The dataset license does not match the inspected record.")
    if file_metadata.get("key") != DATASET_FILE_NAME:
        raise ImportPreparationError("The selected file is not the inspected inventory CSV.")
    if len(csv_bytes) != file_metadata.get("size"):
        raise ImportPreparationError("Downloaded file size does not match Zenodo metadata.")

    source_file_md5 = hashlib.md5(csv_bytes).hexdigest()
    expected_md5 = str(file_metadata.get("checksum", "")).removeprefix("md5:")
    if not expected_md5 or source_file_md5 != expected_md5:
        raise ImportPreparationError("Downloaded file checksum does not match Zenodo metadata.")

    row = _read_event_row(csv_bytes)
    event_start = datetime.strptime(row["Start Date"], "%d-%m-%Y %H:%M").date()
    event_end = datetime.strptime(row["End Date"], "%d-%m-%Y %H:%M").date()
    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc)

    raw_values = {
        key: (value if value != "" else None)
        for key, value in row.items()
    }
    source_file_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    license_attribution = (
        f"India Flood Inventory-Impacts (IFI-Impacts), "
        f"Manabendra Saharia, IIT Delhi; DOI: {DATASET_DOI}; "
        f"license: CC BY-NC 4.0; file: {DATASET_FILE_NAME}; version: {DATASET_VERSION}"
    )
    source = {
        "provider": "IIT Delhi",
        "source_url": DATASET_RECORD_URL,
        "dataset_name": DATASET_FILE_NAME,
        "dataset_version": DATASET_VERSION,
        "license_attribution": license_attribution,
        "retrieved_at": retrieved_at,
        "checksum": f"sha256:{source_file_sha256}",
    }
    notes = (
        "Severity, area affected, latitude, longitude, human injured, and human displaced are blank in the source row.",
        "The source's 5000 Human fatality value is ambiguous because its narrative says died/missing; it is preserved as source text and is not treated as verified casualties.",
        "The original Districts label is retained verbatim; normalized_location is left unset.",
        "No rainfall observation, affected population, resource type, or resource quantity is inferred.",
    )
    observation = {
        "source_record_id": row["UEI"],
        "event_start_date": event_start,
        "event_end_date": event_end,
        "observed_at": None,
        "raw_location": row["Districts"],
        "normalized_location": None,
        "raw_values": raw_values,
        "raw_payload": row,
        "quality_status": "AMBIGUOUS",
    }
    return PreparedImport(
        source=source,
        observation=observation,
        data_quality_notes=notes,
        source_file_md5=f"md5:{source_file_md5}",
    )


def fetch_prepared_import():
    record = json.loads(_download(DATASET_API_URL))
    file_metadata = next(
        (
            item for item in record.get("files", [])
            if item.get("key") == DATASET_FILE_NAME
        ),
        None,
    )
    if file_metadata is None:
        raise ImportPreparationError(
            f"Zenodo record does not list {DATASET_FILE_NAME}."
        )
    file_url = file_metadata.get("links", {}).get("self")
    if not file_url:
        raise ImportPreparationError("Zenodo metadata has no download URL for the CSV.")
    return prepare_import(record, file_metadata, _download(file_url))


def _print_dry_run(prepared):
    observation = prepared.observation
    print("MODE=DRY_RUN; no database connection or writes performed")
    print(f"DATASET_RECORD_URL={DATASET_RECORD_URL}")
    print(f"DATASET_DOI={DATASET_DOI}")
    print(f"DATASET_VERSION={DATASET_VERSION}")
    print(f"DATASET_FILE={DATASET_FILE_NAME}")
    print(f"CHECKSUM_SHA256={prepared.source['checksum']}")
    print(f"CHECKSUM_ZENODO_MD5={prepared.source_file_md5}")
    print(f"SOURCE_RECORD_ID={observation['source_record_id']}")
    print(f"EVENT_START_DATE={observation['event_start_date'].isoformat()}")
    print(f"EVENT_END_DATE={observation['event_end_date'].isoformat()}")
    print(f"RAW_LOCATION={observation['raw_location']}")
    print(f"NORMALIZED_LOCATION={observation['normalized_location']}")
    print(f"QUALITY_STATUS={observation['quality_status']}")
    print("RAW_VALUES=" + json.dumps(observation["raw_values"], ensure_ascii=False))
    print("RAW_PAYLOAD=" + json.dumps(observation["raw_payload"], ensure_ascii=False))
    for note in prepared.data_quality_notes:
        print("DATA_QUALITY_NOTE=" + note)


def _import_prepared(prepared):
    from core import db
    from os_engine.historical_data import (
        import_source_observation,
        register_dataset_source,
    )

    connection = db.get_db_connection()
    cursor = None
    try:
        connection.autocommit = False
        connection.start_transaction()
        cursor = connection.cursor()
        source_id = register_dataset_source(cursor, **prepared.source)
        observation_id = import_source_observation(
            cursor,
            source_id=source_id,
            **prepared.observation,
        )
        connection.commit()
        print(
            f"IMPORT_COMMITTED source_id={source_id} "
            f"observation_id={observation_id}"
        )
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show the mapping without connecting to MySQL (default)",
    )
    mode.add_argument(
        "--import-to-mysql",
        action="store_true",
        help="explicitly write only the dataset source and historical observation",
    )
    args = parser.parse_args(argv)

    try:
        prepared = fetch_prepared_import()
        if args.import_to_mysql:
            _import_prepared(prepared)
        else:
            _print_dry_run(prepared)
    except (ImportPreparationError, URLError, TimeoutError, OSError) as error:
        print(f"PREPARATION_FAILED={type(error).__name__}: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        if error.__class__.__module__.startswith("mysql.connector"):
            print(
                "IMPORT_FAILED="
                f"{type(error).__name__}; "
                f"error_code={getattr(error, 'errno', None)}; "
                f"sqlstate={getattr(error, 'sqlstate', None)}",
                file=sys.stderr,
            )
        else:
            print(f"IMPORT_FAILED={type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

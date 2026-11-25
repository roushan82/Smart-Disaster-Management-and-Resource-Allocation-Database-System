"""Prepare or explicitly import all Uttarakhand records from IFI-Impacts."""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import URLError

from scripts.import_uttarakhand_2013 import (
    DATASET_API_URL,
    DATASET_DOI,
    DATASET_FILE_NAME,
    DATASET_LICENSE_ID,
    DATASET_RECORD_ID,
    DATASET_RECORD_URL,
    DATASET_VERSION,
    EXPECTED_COLUMNS,
    ImportPreparationError,
    _download,
)


@dataclass(frozen=True)
class PreparedBatchImport:
    source: dict
    observations: tuple
    source_file_md5: str


def _state_identifies_uttarakhand(value):
    if not value:
        return False
    parts = re.split(r"[,;|/&]|\band\b", value, flags=re.IGNORECASE)
    return any(part.strip().casefold() == "uttarakhand" for part in parts)


def _parse_event_date(value, field, source_record_id):
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value, "%d-%m-%Y %H:%M").date()
    except ValueError as error:
        raise ImportPreparationError(
            f"Invalid {field} for source record {source_record_id}: {value!r}."
        ) from error


def prepare_batch_import(record_metadata, file_metadata, csv_bytes, retrieved_at=None):
    """Validate the source file and prepare every row identifying Uttarakhand."""
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

    reader = csv.DictReader(
        io.TextIOWrapper(io.BytesIO(csv_bytes), encoding="utf-8-sig", newline="")
    )
    if tuple(reader.fieldnames or ()) != EXPECTED_COLUMNS:
        raise ImportPreparationError(
            "The CSV header does not match the inspected IFI-Impacts file."
        )

    rows = list(reader)
    selected_rows = [
        row for row in rows
        if _state_identifies_uttarakhand(row.get("State"))
    ]
    if not selected_rows:
        raise ImportPreparationError("No Uttarakhand records were found in the CSV.")

    seen_ueis = set()
    observations = []
    for row in selected_rows:
        source_record_id = row.get("UEI", "")
        if not isinstance(source_record_id, str) or not source_record_id.strip():
            raise ImportPreparationError(
                "A Uttarakhand record has a blank UEI."
            )
        if source_record_id in seen_ueis:
            raise ImportPreparationError(
                f"Duplicate UEI in the Uttarakhand batch: {source_record_id}."
            )
        seen_ueis.add(source_record_id)

        raw_values = {
            key: (value if value != "" else None)
            for key, value in row.items()
        }
        observations.append({
            "source_record_id": source_record_id,
            "event_start_date": _parse_event_date(
                row.get("Start Date", ""),
                "Start Date",
                source_record_id,
            ),
            "event_end_date": _parse_event_date(
                row.get("End Date", ""),
                "End Date",
                source_record_id,
            ),
            "observed_at": None,
            "raw_location": row.get("Districts") or None,
            "normalized_location": None,
            "raw_values": raw_values,
            "raw_payload": row,
            "quality_status": "UNASSESSED",
        })

    if retrieved_at is None:
        retrieved_at = datetime.now(timezone.utc)
    source_file_sha256 = hashlib.sha256(csv_bytes).hexdigest()
    source = {
        "provider": "IIT Delhi",
        "source_url": DATASET_RECORD_URL,
        "dataset_name": DATASET_FILE_NAME,
        "dataset_version": DATASET_VERSION,
        "license_attribution": (
            f"India Flood Inventory-Impacts (IFI-Impacts), "
            f"Manabendra Saharia, IIT Delhi; DOI: {DATASET_DOI}; "
            f"license: CC BY-NC 4.0; file: {DATASET_FILE_NAME}; "
            f"version: {DATASET_VERSION}"
        ),
        "retrieved_at": retrieved_at,
        "checksum": f"sha256:{source_file_sha256}",
    }

    return PreparedBatchImport(
        source=source,
        observations=tuple(observations),
        source_file_md5=f"md5:{source_file_md5}",
    )


def fetch_prepared_batch():
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
    return prepare_batch_import(record, file_metadata, _download(file_url))


def persist_prepared_batch(cursor, prepared):
    """Store the source and observations using the existing idempotent helpers."""
    from os_engine.historical_data import (
        import_source_observation,
        register_dataset_source,
    )

    source_id = register_dataset_source(cursor, **prepared.source)
    observation_ids = [
        import_source_observation(
            cursor,
            source_id=source_id,
            **observation,
        )
        for observation in prepared.observations
    ]
    return {"source_id": source_id, "observation_ids": observation_ids}


def import_prepared_batch(prepared):
    """Import a prepared batch atomically; the caller explicitly opts in."""
    from core import db

    connection = db.get_db_connection()
    cursor = None
    try:
        connection.autocommit = False
        connection.start_transaction()
        cursor = connection.cursor()
        result = persist_prepared_batch(cursor, prepared)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()
        connection.close()


def _print_dry_run(prepared):
    print("MODE=DRY_RUN; no database connection or writes performed")
    print(f"DATASET={prepared.source['dataset_name']}")
    print(f"DATASET_VERSION={prepared.source['dataset_version']}")
    print(f"CHECKSUM_SHA256={prepared.source['checksum']}")
    print(f"CHECKSUM_ZENODO_MD5={prepared.source_file_md5}")
    print(f"UTTARAKHAND_OBSERVATION_COUNT={len(prepared.observations)}")
    for observation in prepared.observations:
        print(
            f"SOURCE_RECORD_ID={observation['source_record_id']} "
            f"QUALITY_STATUS={observation['quality_status']}"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show the batch without connecting to MySQL (default)",
    )
    mode.add_argument(
        "--import-to-mysql",
        action="store_true",
        help="explicitly write the validated batch as historical observations",
    )
    args = parser.parse_args(argv)

    try:
        prepared = fetch_prepared_batch()
        if args.import_to_mysql:
            result = import_prepared_batch(prepared)
            print(
                f"IMPORT_COMMITTED source_id={result['source_id']} "
                f"observation_count={len(result['observation_ids'])}"
            )
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

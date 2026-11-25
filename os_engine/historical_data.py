"""Persistence helpers for isolated, source-traceable historical observations."""

import hashlib
import json
from collections.abc import Mapping
from datetime import date, datetime, timezone

import mysql.connector
from mysql.connector import errorcode


class SourceRecordConflictError(ValueError):
    """Raised when an existing source record ID is re-imported with new data."""


QUALITY_STATUSES = frozenset({
    "UNASSESSED",
    "INCOMPLETE",
    "AMBIGUOUS",
    "INVALID",
    "VERIFIED",
})


def _required_text(name, value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value


def _json_text(name, value, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping or None.")
    if any(not isinstance(key, str) for key in value):
        raise TypeError(f"{name} keys must be strings.")
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def _decoded_json(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _utc_mysql_datetime(name, value):
    """Return a naive UTC datetime for a MySQL DATETIME column.

    Naive Python datetimes are explicitly interpreted as UTC. Aware datetimes
    are converted to UTC before removing tzinfo because MySQL DATETIME stores
    no timezone metadata.
    """
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.")
    if value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def register_dataset_source(
    cursor,
    *,
    provider,
    source_url,
    dataset_name,
    dataset_version,
    license_attribution,
    retrieved_at,
    checksum,
):
    """Insert a source once; repeated registrations return the existing ID.

    The caller owns the database transaction. A checksum and retrieval time
    must come from the actual source retrieval, not a generated placeholder.
    """
    provider = _required_text("provider", provider)
    source_url = _required_text("source_url", source_url)
    dataset_name = _required_text("dataset_name", dataset_name)
    license_attribution = _required_text("license_attribution", license_attribution)
    checksum = _required_text("checksum", checksum)
    if dataset_version is not None and not isinstance(dataset_version, str):
        raise TypeError("dataset_version must be a string or None.")
    retrieved_at = _utc_mysql_datetime("retrieved_at", retrieved_at)

    identity = json.dumps(
        [
            provider,
            source_url,
            dataset_name,
            dataset_version,
            license_attribution,
            checksum,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    source_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()

    cursor.execute(
        """
        INSERT INTO dataset_sources
            (source_key, provider, source_url, dataset_name, dataset_version,
             license_attribution, retrieved_at, checksum)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE source_id = LAST_INSERT_ID(source_id)
        """,
        (
            source_key, provider, source_url, dataset_name, dataset_version,
            license_attribution, retrieved_at, checksum,
        ),
    )
    if not cursor.lastrowid:
        raise RuntimeError("The database did not return a dataset source ID.")
    return cursor.lastrowid


def append_detection_audit(
    cursor,
    *,
    action,
    actor,
    source_id=None,
    observation_id=None,
    detection_id=None,
    details=None,
):
    """Append one audit event using the caller's transaction."""
    action = _required_text("action", action)
    actor = _required_text("actor", actor)
    details_json = _json_text("details", details, nullable=True)
    cursor.execute(
        """
        INSERT INTO detection_audit
            (source_id, observation_id, detection_id, action, actor, details)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (source_id, observation_id, detection_id, action, actor, details_json),
    )
    return cursor.lastrowid


def import_source_observation(
    cursor,
    *,
    source_id,
    source_record_id,
    raw_values,
    raw_payload,
    quality_status="UNASSESSED",
    event_start_date=None,
    event_end_date=None,
    observed_at=None,
    raw_location=None,
    normalized_location=None,
):
    """Idempotently import one record without touching live request data.

    Naive datetimes are interpreted as UTC; aware datetimes are normalized to
    UTC before being stored in MySQL DATETIME columns. The caller owns the
    transaction and must keep autocommit disabled. A savepoint makes the
    observation and its initial audit event atomic without committing the
    caller's surrounding transaction. Identical re-imports do not add another
    audit row; conflicting values raise SourceRecordConflictError.
    """
    source_record_id = _required_text("source_record_id", source_record_id)
    raw_values_json = _json_text("raw_values", raw_values)
    raw_payload_json = _json_text("raw_payload", raw_payload)
    if quality_status not in QUALITY_STATUSES:
        raise ValueError("quality_status is not a supported observation status.")
    for field, value in (
        ("event_start_date", event_start_date),
        ("event_end_date", event_end_date),
    ):
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise TypeError(f"{field} must be a date or None.")
    if observed_at is not None:
        observed_at = _utc_mysql_datetime("observed_at", observed_at)
    for field, value in (
        ("raw_location", raw_location),
        ("normalized_location", normalized_location),
    ):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{field} must be a string or None.")

    expected = (
        event_start_date,
        event_end_date,
        observed_at,
        raw_location,
        normalized_location,
        json.loads(raw_values_json),
        json.loads(raw_payload_json),
        quality_status,
    )

    def read_existing(*, lock=False):
        query = """
            SELECT observation_id, event_start_date, event_end_date, observed_at,
                   raw_location, normalized_location, raw_values, raw_payload,
                   quality_status
            FROM source_observations
            WHERE source_id = %s AND source_record_id = %s
        """
        if lock:
            query += " FOR UPDATE"
        cursor.execute(
            query,
            (source_id, source_record_id),
        )
        return cursor.fetchone()

    def verify_existing(row):
        actual = (
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            _decoded_json(row[6]),
            _decoded_json(row[7]),
            row[8],
        )
        if actual != expected:
            raise SourceRecordConflictError(
                "This source record ID already exists with different values; "
                "the existing observation was not overwritten."
            )
        return row[0]

    cursor.execute("SAVEPOINT historical_observation_import")
    try:
        existing = read_existing(lock=True)
        if existing is not None:
            observation_id = verify_existing(existing)
            cursor.execute("RELEASE SAVEPOINT historical_observation_import")
            return observation_id

        try:
            cursor.execute(
                """
                INSERT INTO source_observations
                    (source_id, source_record_id, event_start_date, event_end_date,
                     observed_at, raw_location, normalized_location, raw_values,
                     raw_payload, quality_status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    source_id, source_record_id, event_start_date, event_end_date,
                    observed_at, raw_location, normalized_location, raw_values_json,
                    raw_payload_json, quality_status,
                ),
            )
        except mysql.connector.IntegrityError as error:
            if error.errno != errorcode.ER_DUP_ENTRY:
                raise
            existing = read_existing()
            if existing is None:
                raise
            observation_id = verify_existing(existing)
            cursor.execute("RELEASE SAVEPOINT historical_observation_import")
            return observation_id

        observation_id = cursor.lastrowid
        if not observation_id:
            raise RuntimeError("The database did not return an observation ID.")

        append_detection_audit(
            cursor,
            action="OBSERVATION_IMPORT",
            actor="SYSTEM_IMPORT",
            source_id=source_id,
            observation_id=observation_id,
            details={"source_record_id": source_record_id},
        )
        cursor.execute("RELEASE SAVEPOINT historical_observation_import")
        return observation_id
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT historical_observation_import")
        cursor.execute("RELEASE SAVEPOINT historical_observation_import")
        raise

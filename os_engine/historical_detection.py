"""Pure evaluation of evidence sufficiency for historical disaster records."""

from collections.abc import Mapping
from dataclasses import dataclass
import json

import mysql.connector
from mysql.connector import errorcode

from os_engine.historical_data import append_detection_audit


UTTARAKHAND_2013_UEI = "UEI-IMD-FL-2013-0131"
MISSING_EVIDENCE_FIELDS = (
    "Severity",
    "Area Affected",
    "Latitude",
    "Longitude",
    "Human injured",
    "Human Displaced",
)
UTTARAKHAND_2013_RULE_ID = "uttarakhand_2013_evidence_sufficiency"
UTTARAKHAND_2013_RULE_VERSION = "1.0"
GENERAL_DETECTION_RULE_ID = "historical_evidence_sufficiency"
GENERAL_DETECTION_RULE_VERSION = "1.0"
DETECTION_OUTCOMES = ("CONFIRMED", "POTENTIAL", "INSUFFICIENT_DATA")


@dataclass(frozen=True)
class HistoricalEvaluation:
    status: str
    candidate_priority: object
    explanation: str
    missing_evidence: tuple
    supporting_evidence: tuple = ()


def compute_detection_outcome(evaluated_status, review_status):
    """Determine the effective disaster detection outcome according to approved rules:
    - CONFIRMED: Observation evaluated as CONFIRMED, or POTENTIAL approved through human review.
    - POTENTIAL: Evidence suggests a possible disaster, but confirmation is incomplete.
    - INSUFFICIENT_DATA: Essential information is missing/unreliable, or evaluation status is missing/unexpected.
    - REJECTED: POTENTIAL or INSUFFICIENT_DATA observation rejected through human review.
    """
    if evaluated_status == "CONFIRMED":
        return "CONFIRMED"
    if evaluated_status == "POTENTIAL":
        if review_status == "APPROVED":
            return "CONFIRMED"
        if review_status == "REJECTED":
            return "REJECTED"
        return "POTENTIAL"
    if evaluated_status == "INSUFFICIENT_DATA":
        if review_status == "REJECTED":
            return "REJECTED"
        return "INSUFFICIENT_DATA"
    return "INSUFFICIENT_DATA"


def evaluate_historical_observation(observation):
    """Evaluate disaster evidence from any historical observation without guessing.

    Outcomes:
    - POTENTIAL: Evidence suggests a possible disaster event (identifiable disaster type,
      location, temporal anchor, and reported impact), but confirmation is incomplete pending human review.
    - INSUFFICIENT_DATA: Essential information is missing (missing disaster type, location,
      or date), reported impact is entirely absent/unreliable, or data quality is invalid/incomplete.
    - CONFIRMED: Only when sufficient evidence exists AND the observation has already been
      approved through human review (via review_status == 'APPROVED').

    Never invents severity, casualties, affected area, coordinates, or priority thresholds.
    """
    if not isinstance(observation, Mapping):
        raise TypeError("observation must be a mapping.")

    # If it is specifically the Uttarakhand 2013 record, maintain exact historical assessment
    if observation.get("source_record_id") == UTTARAKHAND_2013_UEI:
        return evaluate_uttarakhand_2013_observation(observation)

    raw_values = observation.get("raw_values")
    if not isinstance(raw_values, Mapping):
        raise TypeError("observation.raw_values must be a mapping.")

    missing = []
    supporting = []
    reasons = []

    # 1. Quality status check
    quality_status = observation.get("quality_status")
    if quality_status in ("INVALID", "INCOMPLETE"):
        missing.append("data_quality")
        reasons.append(f"Source observation data quality status is marked {quality_status}.")

    # 2. Disaster type / cause identification
    disaster_type = None
    for key in ("Main Cause", "type", "disaster_type", "cause", "Hazard", "Disaster Type"):
        val = raw_values.get(key)
        if isinstance(val, str) and val.strip():
            disaster_type = val.strip()
            break

    if disaster_type:
        supporting.append(f"Disaster type/cause: {disaster_type}")
    else:
        missing.append("disaster_type")
        reasons.append("No identifiable disaster type or cause is present in the record.")

    # 3. Location identification
    location = None
    if isinstance(observation.get("normalized_location"), str) and observation.get("normalized_location").strip():
        location = observation.get("normalized_location").strip()
    elif isinstance(observation.get("raw_location"), str) and observation.get("raw_location").strip():
        location = observation.get("raw_location").strip()
    else:
        for key in ("Location", "Districts", "State", "District", "city"):
            val = raw_values.get(key)
            if isinstance(val, str) and val.strip():
                location = val.strip()
                break

    if location:
        supporting.append(f"Location: {location}")
        if observation.get("normalized_location") is None:
            reasons.append("Location is retained from source text and is not officially normalized.")
    else:
        missing.append("location")
        reasons.append("No identifiable location or district was found in the record.")

    # 4. Temporal identification (Event dates)
    event_date = None
    if observation.get("event_start_date"):
        event_date = str(observation.get("event_start_date"))
    elif observation.get("observed_at"):
        event_date = str(observation.get("observed_at"))
    else:
        for key in ("Start Date", "Date", "event_start_date", "Year"):
            val = raw_values.get(key)
            if val is not None and str(val).strip():
                event_date = str(val).strip()
                break

    if event_date:
        supporting.append(f"Date/Time: {event_date}")
    else:
        missing.append("event_date")
        reasons.append("No event start date or observation timestamp is recorded.")

    # 5. Impact evidence (Casualties, damage, displacement, physical measurements)
    has_impact_evidence = False
    casualty_narrative = raw_values.get("Description of Casualties/injured")
    fatalities = raw_values.get("Human fatality")
    injured = raw_values.get("Human injured")
    displaced = raw_values.get("Human Displaced")
    damage_extent = raw_values.get("Extent of damage ") or raw_values.get("Extent of damage")
    rainfall = raw_values.get("Rainfall")

    # Check for missing standard fields without inventing them
    for field in ("Severity", "Area Affected", "Latitude", "Longitude"):
        if raw_values.get(field) is None or raw_values.get(field) == "":
            missing.append(field)

    # Assess whether reported impact is credible or ambiguous
    ambiguous_impact = False
    if fatalities is not None and str(fatalities).strip():
        supporting.append(f"Reported fatalities: {fatalities}")
        has_impact_evidence = True
        if isinstance(casualty_narrative, str) and any(
            phrase in casualty_narrative.lower()
            for phrase in ("died/missing", "unconfirmed", "disputed", "approx", "estimated")
        ):
            ambiguous_impact = True
            reasons.append(
                f"Reported fatality figure '{fatalities}' is ambiguous due to source narrative: '{casualty_narrative.strip()}'."
            )
    elif isinstance(casualty_narrative, str) and casualty_narrative.strip():
        supporting.append(f"Casualty narrative: {casualty_narrative.strip()}")
        has_impact_evidence = True
    else:
        missing.append("Human fatality")

    if injured is not None and str(injured).strip():
        supporting.append(f"Reported injuries: {injured}")
        has_impact_evidence = True
    else:
        missing.append("Human injured")

    if displaced is not None and str(displaced).strip():
        supporting.append(f"Reported displaced: {displaced}")
        has_impact_evidence = True
    else:
        missing.append("Human Displaced")

    if damage_extent is not None and str(damage_extent).strip():
        supporting.append(f"Damage extent: {damage_extent.strip()}")
        has_impact_evidence = True

    if rainfall is not None and str(rainfall).strip():
        supporting.append(f"Measured rainfall: {rainfall}")
        has_impact_evidence = True
    else:
        reasons.append("The record has no measured rainfall field.")

    if not has_impact_evidence:
        missing.append("impact_evidence")
        reasons.append("No verifiable casualty figures, damage descriptions, or physical measurements exist.")

    reasons.append(
        "No validated decision rule or threshold supports assigning a priority or estimating resource demand."
    )

    # 6. Evaluation determination
    essential_missing = any(
        field in missing
        for field in ("disaster_type", "location", "event_date", "impact_evidence", "data_quality")
    )

    if essential_missing or ambiguous_impact:
        status = "INSUFFICIENT_DATA"
        reasons.insert(0, "Evidence is insufficient to evaluate disaster occurrence reliably.")
    else:
        review_status = observation.get("review_status")
        if review_status == "APPROVED":
            status = "CONFIRMED"
            reasons.insert(0, "Confirmed historical disaster observation based on validated evidence and approved human review.")
        else:
            status = "POTENTIAL"
            reasons.insert(0, "Evidence suggests a possible disaster event; confirmation is incomplete pending human review.")

    return HistoricalEvaluation(
        status=status,
        candidate_priority=None,
        explanation=" ".join(reasons),
        missing_evidence=tuple(missing),
        supporting_evidence=tuple(supporting),
    )


class DetectionResultConflictError(ValueError):
    """Raised when an existing rule/version result has different evaluation data."""


class DetectionReviewError(ValueError):
    """Raised when a detection result cannot accept the submitted review."""


class DetectionReviewNotFoundError(DetectionReviewError):
    """Raised when a detection result does not exist."""


class DuplicateDetectionReviewError(DetectionReviewError):
    """Raised when a detection result has already received a human review."""


def evaluate_uttarakhand_2013_observation(observation):
    """Assess evidence sufficiency without assigning priority or side effects.

    The selected source record contains no validated decision thresholds.
    Narrative descriptions and ambiguous casualty counts are not converted
    into severity or resource-demand estimates.
    """
    if not isinstance(observation, Mapping):
        raise TypeError("observation must be a mapping.")
    if observation.get("source_record_id") != UTTARAKHAND_2013_UEI:
        raise ValueError(
            f"observation must have source_record_id {UTTARAKHAND_2013_UEI}."
        )

    raw_values = observation.get("raw_values")
    if not isinstance(raw_values, Mapping):
        raise TypeError("observation.raw_values must be a mapping.")

    missing = tuple(
        field for field in MISSING_EVIDENCE_FIELDS
        if raw_values.get(field) is None or raw_values.get(field) == ""
    )
    reasons = []
    if missing:
        reasons.append(
            "The source record has no values for: " + ", ".join(missing) + "."
        )
    if "Rainfall" not in raw_values:
        reasons.append("The record has no measured rainfall field.")

    casualty_narrative = raw_values.get("Description of Casualties/injured")
    if (
        raw_values.get("Human fatality") == "5000"
        and isinstance(casualty_narrative, str)
        and "died/missing" in casualty_narrative.lower()
    ):
        reasons.append(
            "The source's Human fatality value '5000' is ambiguous because "
            "its narrative says 'died/missing'; it is not treated as verified."
        )

    if observation.get("normalized_location") is None:
        reasons.append(
            "The district label is retained as source text and is not normalized."
        )
    reasons.append(
        "No validated decision rule or threshold supports assigning a priority "
        "or estimating resource demand."
    )

    return HistoricalEvaluation(
        status="INSUFFICIENT_DATA",
        candidate_priority=None,
        explanation=" ".join(reasons),
        missing_evidence=missing,
    )


def persist_uttarakhand_2013_evaluation(
    cursor,
    *,
    source_id,
    observation_id,
    observation,
):
    """Evaluate and persist one historical result and audit row atomically.

    The caller owns the transaction and must commit or roll it back. The
    savepoint keeps the detection result and its audit entry atomic without
    committing unrelated work in the caller's transaction.
    """
    evaluation = evaluate_uttarakhand_2013_observation(observation)
    if not isinstance(observation_id, int) or observation_id <= 0:
        raise ValueError("observation_id must be a positive integer.")
    if not isinstance(source_id, int) or source_id <= 0:
        raise ValueError("source_id must be a positive integer.")

    evaluated_values = {
        "status": evaluation.status,
        "evidence": dict(observation["raw_values"]),
        "missing_evidence": list(evaluation.missing_evidence),
    }
    evaluated_json = json.dumps(
        evaluated_values,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    expected_values = json.loads(evaluated_json)
    review_status = "NEEDS_MORE_DATA"

    def read_existing(*, lock=False):
        query = """
            SELECT detection_id, evaluated_values, explanation,
                   candidate_priority, review_status
            FROM detection_results
            WHERE observation_id = %s AND rule_id = %s AND rule_version = %s
        """
        if lock:
            query += " FOR UPDATE"
        cursor.execute(
            query,
            (
                observation_id,
                UTTARAKHAND_2013_RULE_ID,
                UTTARAKHAND_2013_RULE_VERSION,
            ),
        )
        return cursor.fetchone()

    def verify_existing(row):
        existing_values = row[1]
        if isinstance(existing_values, (str, bytes)):
            existing_values = json.loads(existing_values)
        if (
            existing_values != expected_values
            or row[2] != evaluation.explanation
            or row[3] is not None
            or row[4] != review_status
        ):
            raise DetectionResultConflictError(
                "This observation already has a different result for this "
                "detection rule version."
            )
        return row[0]

    cursor.execute("SAVEPOINT historical_detection_persist")
    try:
        existing = read_existing(lock=True)
        if existing is not None:
            detection_id = verify_existing(existing)
            cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
            return detection_id

        try:
            cursor.execute(
                """
                INSERT INTO detection_results
                    (observation_id, rule_id, rule_version, evaluated_values,
                     explanation, candidate_priority, review_status)
                VALUES (%s, %s, %s, %s, %s, NULL, %s)
                """,
                (
                    observation_id,
                    UTTARAKHAND_2013_RULE_ID,
                    UTTARAKHAND_2013_RULE_VERSION,
                    evaluated_json,
                    evaluation.explanation,
                    review_status,
                ),
            )
        except mysql.connector.IntegrityError as error:
            if error.errno != errorcode.ER_DUP_ENTRY:
                raise
            existing = read_existing(lock=True)
            if existing is None:
                raise
            detection_id = verify_existing(existing)
            cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
            return detection_id

        detection_id = cursor.lastrowid
        if not detection_id:
            raise RuntimeError("The database did not return a detection ID.")

        append_detection_audit(
            cursor,
            action="DETECTION_EVALUATION",
            actor="SYSTEM_EVALUATOR",
            source_id=source_id,
            observation_id=observation_id,
            detection_id=detection_id,
            details={
                "rule_id": UTTARAKHAND_2013_RULE_ID,
                "rule_version": UTTARAKHAND_2013_RULE_VERSION,
                "status": evaluation.status,
            },
        )
        cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
        return detection_id
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT historical_detection_persist")
        cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
        raise


def persist_historical_evaluation(
    cursor,
    *,
    source_id,
    observation_id,
    observation,
    rule_id=GENERAL_DETECTION_RULE_ID,
    rule_version=GENERAL_DETECTION_RULE_VERSION,
):
    """Evaluate and persist one historical result and audit row atomically.

    The caller owns the transaction and must commit or roll it back. The
    savepoint keeps the detection result and its audit entry atomic without
    committing unrelated work in the caller's transaction.
    """
    evaluation = evaluate_historical_observation(observation)
    if not isinstance(observation_id, int) or observation_id <= 0:
        raise ValueError("observation_id must be a positive integer.")
    if not isinstance(source_id, int) or source_id <= 0:
        raise ValueError("source_id must be a positive integer.")

    evaluated_values = {
        "status": evaluation.status,
        "evidence": dict(observation.get("raw_values", {})),
        "missing_evidence": list(evaluation.missing_evidence),
        "supporting_evidence": list(evaluation.supporting_evidence),
    }
    evaluated_json = json.dumps(
        evaluated_values,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    expected_values = json.loads(evaluated_json)

    if evaluation.status == "POTENTIAL":
        review_status = "PENDING_REVIEW"
    elif evaluation.status == "CONFIRMED":
        review_status = "APPROVED"
    else:
        review_status = "NEEDS_MORE_DATA"

    def read_existing(*, lock=False):
        query = """
            SELECT detection_id, evaluated_values, explanation,
                   candidate_priority, review_status
            FROM detection_results
            WHERE observation_id = %s AND rule_id = %s AND rule_version = %s
        """
        if lock:
            query += " FOR UPDATE"
        cursor.execute(query, (observation_id, rule_id, rule_version))
        return cursor.fetchone()

    def verify_existing(row):
        existing_values = row[1]
        if isinstance(existing_values, (str, bytes)):
            existing_values = json.loads(existing_values)
        if (
            existing_values != expected_values
            or row[2] != evaluation.explanation
            or row[3] is not None
            or row[4] != review_status
        ):
            raise DetectionResultConflictError(
                "This observation already has a different result for this "
                "detection rule version."
            )
        return row[0]

    cursor.execute("SAVEPOINT historical_detection_persist")
    try:
        existing = read_existing(lock=True)
        if existing is not None:
            detection_id = verify_existing(existing)
            cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
            return detection_id

        try:
            cursor.execute(
                """
                INSERT INTO detection_results
                    (observation_id, rule_id, rule_version, evaluated_values,
                     explanation, candidate_priority, review_status)
                VALUES (%s, %s, %s, %s, %s, NULL, %s)
                """,
                (
                    observation_id,
                    rule_id,
                    rule_version,
                    evaluated_json,
                    evaluation.explanation,
                    review_status,
                ),
            )
        except mysql.connector.IntegrityError as error:
            if error.errno != errorcode.ER_DUP_ENTRY:
                raise
            existing = read_existing(lock=True)
            if existing is None:
                raise
            detection_id = verify_existing(existing)
            cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
            return detection_id

        detection_id = cursor.lastrowid
        if not detection_id:
            raise RuntimeError("The database did not return a detection ID.")

        append_detection_audit(
            cursor,
            action="DETECTION_EVALUATION",
            actor="SYSTEM_EVALUATOR",
            source_id=source_id,
            observation_id=observation_id,
            detection_id=detection_id,
            details={
                "rule_id": rule_id,
                "rule_version": rule_version,
                "status": evaluation.status,
            },
        )
        cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
        return detection_id
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT historical_detection_persist")
        cursor.execute("RELEASE SAVEPOINT historical_detection_persist")
        raise


def review_detection_result(
    cursor,
    detection_id,
    *,
    reviewer,
    review_status,
    reason,
):
    """Apply one human decision and audit it within the caller's transaction."""
    if type(detection_id) is not int or detection_id <= 0:
        raise ValueError("detection_id must be a positive integer.")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 150:
        raise ValueError("reviewer must contain 1 to 150 characters.")
    reviewer = reviewer.strip()
    if review_status not in ("APPROVED", "REJECTED", "NEEDS_MORE_DATA"):
        raise ValueError("review_status is not a supported review decision.")
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        raise ValueError("reason must contain 1 to 2000 characters.")
    reason = reason.strip()

    cursor.execute("SAVEPOINT historical_detection_review")
    try:
        cursor.execute(
            """
            SELECT dr.review_status, dr.reviewed_at, dr.reviewer,
                   so.source_id, dr.observation_id
            FROM detection_results dr
            JOIN source_observations so ON so.observation_id = dr.observation_id
            WHERE dr.detection_id = %s
            FOR UPDATE
            """,
            (detection_id,),
        )
        current = cursor.fetchone()
        if current is None:
            raise DetectionReviewNotFoundError("Detection result was not found.")
        if (
            current[0] not in ("PENDING_REVIEW", "NEEDS_MORE_DATA")
            or current[1] is not None
            or current[2] is not None
        ):
            raise DuplicateDetectionReviewError(
                "This detection result has already been reviewed."
            )

        cursor.execute(
            """
            UPDATE detection_results
            SET review_status = %s, reviewer = %s, reviewed_at = CURRENT_TIMESTAMP
            WHERE detection_id = %s
              AND reviewed_at IS NULL
              AND reviewer IS NULL
              AND review_status IN ('PENDING_REVIEW', 'NEEDS_MORE_DATA')
            """,
            (review_status, reviewer, detection_id),
        )
        if cursor.rowcount != 1:
            raise DuplicateDetectionReviewError(
                "This detection result has already been reviewed."
            )

        append_detection_audit(
            cursor,
            action="DETECTION_REVIEW",
            actor=reviewer,
            source_id=current[3],
            observation_id=current[4],
            detection_id=detection_id,
            details={"review_status": review_status, "reason": reason},
        )
        cursor.execute("RELEASE SAVEPOINT historical_detection_review")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT historical_detection_review")
        cursor.execute("RELEASE SAVEPOINT historical_detection_review")
        raise

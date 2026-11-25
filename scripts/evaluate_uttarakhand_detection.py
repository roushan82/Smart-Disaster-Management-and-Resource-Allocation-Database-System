"""Persist the historical evidence evaluation for observation 1."""

import json
import sys
from collections.abc import Mapping

from os_engine.historical_detection import (
    UTTARAKHAND_2013_UEI,
    evaluate_uttarakhand_2013_observation,
    persist_uttarakhand_2013_evaluation,
)


OBSERVATION_ID = 1


def _decode_raw_values(value):
    if isinstance(value, (str, bytes)):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise TypeError("The stored observation raw_values is not a JSON object.")
    return value


def evaluate_and_persist(connection):
    """Evaluate the selected observation and commit only its result and audit."""
    observation_id = OBSERVATION_ID
    cursor = None
    try:
        connection.autocommit = False
        connection.start_transaction()
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT source_id, source_record_id, raw_location,
                   normalized_location, raw_values
            FROM source_observations
            WHERE observation_id = %s
            FOR UPDATE
            """,
            (observation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(
                f"Historical observation {observation_id} does not exist."
            )

        source_id, source_record_id, raw_location, normalized_location, raw_values = row
        if source_record_id != UTTARAKHAND_2013_UEI:
            raise ValueError(
                f"Observation {observation_id} is not the selected Uttarakhand event."
            )
        observation = {
            "source_record_id": source_record_id,
            "raw_location": raw_location,
            "normalized_location": normalized_location,
            "raw_values": _decode_raw_values(raw_values),
        }
        evaluation = evaluate_uttarakhand_2013_observation(observation)
        detection_id = persist_uttarakhand_2013_evaluation(
            cursor,
            source_id=source_id,
            observation_id=observation_id,
            observation=observation,
        )

        cursor.execute(
            """
            SELECT review_status, candidate_priority
            FROM detection_results
            WHERE detection_id = %s
            """,
            (detection_id,),
        )
        persisted_result = cursor.fetchone()
        if persisted_result is None:
            raise RuntimeError("Persisted detection result could not be read back.")

        review_status, candidate_priority = persisted_result
        if evaluation.status != "INSUFFICIENT_DATA" or candidate_priority is not None:
            raise RuntimeError("Persisted detection result has an unexpected status.")
        connection.commit()
        return {
            "detection_id": detection_id,
            "status": evaluation.status,
            "review_status": review_status,
            "candidate_priority": candidate_priority,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        if cursor is not None:
            cursor.close()


def main():
    from core.db import get_db_connection

    connection = None
    try:
        connection = get_db_connection()
        result = evaluate_and_persist(connection)
        print(f"detection_id={result['detection_id']}")
        print(f"status={result['status']}")
        print(f"review_status={result['review_status']}")
        print(f"candidate_priority={result['candidate_priority']}")
        return 0
    except Exception as error:
        print(f"Evaluation failed: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())

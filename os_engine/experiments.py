from collections import deque
from datetime import datetime, timedelta, timezone

from os_engine.policies import AGING_THRESHOLD_SECONDS

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
SIMULATION_PROCESSING_TIME_SECONDS = 600
DEFAULT_RR_QUANTUM_SECONDS = 120
SYNTHETIC_MIN_DURATION_SECONDS = 120
SYNTHETIC_MAX_DURATION_SECONDS = 1200
SIMULATION_ALGORITHMS = ("FCFS", "PRIORITY", "RR", "SJF")


def calculate_synthetic_duration(request_id):
    """Deterministically assign a synthetic processing duration between 120 and 1200 seconds.

    Derived strictly from stable request_id attribute (independent of real resource stocks,
    quantities, or disaster priority).
    Formula: 120 + ((request_id * 7) % 10) * 120
    Produces distinct durations in [120, 240, 360, 480, 600, 720, 840, 960, 1080, 1200].
    """
    if not isinstance(request_id, int) or request_id < 0:
        return float(SIMULATION_PROCESSING_TIME_SECONDS)
    step = (request_id * 7) % 10
    return float(SYNTHETIC_MIN_DURATION_SECONDS + step * 120)


def _coerce_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        cleaned = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                try:
                    parsed = datetime.strptime(cleaned, fmt)
                    break
                except ValueError:
                    continue
            else:
                return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_request_rows(cursor):
    cursor.execute("""
        SELECT request_id, request_time, priority
        FROM emergency_requests
        WHERE request_time IS NOT NULL
        ORDER BY request_time ASC, request_id ASC
    """)
    requests = []
    for row in cursor.fetchall():
        request_time = _coerce_datetime(row[1])
        if request_time is None:
            continue
        requests.append({
            "request_id": row[0],
            "request_time": request_time,
            "priority": row[2],
        })
    return requests


def _priority_rank(request, reference_time):
    request_time = request["request_time"]
    waiting_seconds = 0
    if request_time is not None:
        waiting_seconds = max(0, (reference_time - request_time).total_seconds())
    return (
        0 if waiting_seconds >= AGING_THRESHOLD_SECONDS else PRIORITY_ORDER.get(request["priority"], 99),
        request_time,
        request["request_id"],
    )


def _simulate_round_robin(rows, reference_time, time_quantum=DEFAULT_RR_QUANTUM_SECONDS):
    """Simulate preemptive Round Robin execution with a fixed time quantum.

    Tracks first start time, cumulative waiting time, completion time,
    and turnaround time for each request.
    """
    if not rows:
        return {
            "algorithm": "RR",
            "reference_time": reference_time,
            "request_count": 0,
            "average_wait_seconds": 0.0,
            "average_turnaround_seconds": 0.0,
            "average_response_seconds": 0.0,
            "throughput_per_second": 0.0,
            "throughput_per_hour": 0.0,
            "total_simulation_seconds": 0.0,
            "time_quantum_seconds": time_quantum,
            "is_simulated": True,
            "results": [],
        }

    # Order initial pending queue by arrival time, tie-broken by request_id
    pending = sorted(rows, key=lambda r: (r["request_time"], r["request_id"]))
    req_lookup = {r["request_id"]: r for r in pending}
    remaining = {r["request_id"]: float(r["duration_seconds"]) for r in pending}
    first_starts = {}
    completions = {}

    current_time = pending[0]["request_time"]
    ready_queue = deque()
    pending_idx = 0
    n = len(pending)

    # Enqueue requests that have arrived by current_time
    while pending_idx < n and pending[pending_idx]["request_time"] <= current_time:
        ready_queue.append(pending[pending_idx]["request_id"])
        pending_idx += 1

    while ready_queue or pending_idx < n:
        if not ready_queue:
            current_time = pending[pending_idx]["request_time"]
            while pending_idx < n and pending[pending_idx]["request_time"] <= current_time:
                ready_queue.append(pending[pending_idx]["request_id"])
                pending_idx += 1

        req_id = ready_queue.popleft()
        if req_id not in first_starts:
            first_starts[req_id] = current_time

        slice_sec = min(float(time_quantum), remaining[req_id])
        slice_end = current_time + timedelta(seconds=slice_sec)
        remaining[req_id] -= slice_sec

        # Any newly arrived requests during this slice join the ready queue before preempted task
        while pending_idx < n and pending[pending_idx]["request_time"] <= slice_end:
            ready_queue.append(pending[pending_idx]["request_id"])
            pending_idx += 1

        current_time = slice_end

        if remaining[req_id] <= 1e-9:
            completions[req_id] = current_time
        else:
            ready_queue.append(req_id)

    results = []
    total_wait = 0.0
    total_turnaround = 0.0
    total_response = 0.0
    earliest_arrival = None
    latest_completion = None

    for r in sorted(rows, key=lambda item: (completions[item["request_id"]], item["request_id"])):
        req_id = r["request_id"]
        arrival = r["request_time"]
        start_time = first_starts[req_id]
        completion_time = completions[req_id]
        duration = float(r["duration_seconds"])

        turnaround_time = (completion_time - arrival).total_seconds()
        waiting_time = turnaround_time - duration
        response_time = (start_time - arrival).total_seconds()

        total_wait += waiting_time
        total_turnaround += turnaround_time
        total_response += response_time

        if earliest_arrival is None or arrival < earliest_arrival:
            earliest_arrival = arrival
        if latest_completion is None or completion_time > latest_completion:
            latest_completion = completion_time

        results.append({
            "request_id": req_id,
            "priority": r["priority"],
            "arrival": arrival,
            "start_time": start_time,
            "completion_time": completion_time,
            "waiting_seconds": waiting_time,
            "turnaround_seconds": turnaround_time,
            "response_time_seconds": response_time,
            "duration_seconds": duration,
        })

    request_count = len(results)
    sim_seconds = 0.0
    if earliest_arrival is not None and latest_completion is not None:
        sim_seconds = max(0.0, (latest_completion - earliest_arrival).total_seconds())

    throughput_sec = (request_count / sim_seconds) if sim_seconds > 0 else 0.0

    return {
        "algorithm": "RR",
        "reference_time": reference_time,
        "request_count": request_count,
        "average_wait_seconds": total_wait / request_count if request_count else 0.0,
        "average_turnaround_seconds": total_turnaround / request_count if request_count else 0.0,
        "average_response_seconds": total_response / request_count if request_count else 0.0,
        "throughput_per_second": throughput_sec,
        "throughput_per_hour": throughput_sec * 3600.0,
        "total_simulation_seconds": sim_seconds,
        "time_quantum_seconds": time_quantum,
        "is_simulated": True,
        "results": results,
    }


def _simulate_non_preemptive(rows, algorithm, reference_time):
    """Simulate non-preemptive queue scheduling (FCFS, PRIORITY, SJF) using an event-driven ready queue.

    At each scheduling step:
    - Only requests that have ALREADY ARRIVED by current_time (arrival <= current_time)
      are available in the ready queue.
    - If the CPU is idle (ready queue is empty), current_time advances to the arrival
      time of the next pending request.
    - FCFS selects earliest arrival: (request_time, request_id).
    - PRIORITY selects highest priority tier (with 900s aging): (priority_group, request_time, request_id).
    - SJF selects shortest duration among AVAILABLE requests: (duration_seconds, request_time, request_id).
    """
    pending = sorted(rows, key=lambda r: (r["request_time"], r["request_id"]))
    n = len(pending)
    pending_idx = 0
    current_time = pending[0]["request_time"]
    ready_queue = []

    # Enqueue requests that have arrived by current_time
    while pending_idx < n and pending[pending_idx]["request_time"] <= current_time:
        ready_queue.append(pending[pending_idx])
        pending_idx += 1

    results = []
    total_wait = 0.0
    total_turnaround = 0.0
    total_response = 0.0
    earliest_arrival = None
    latest_completion = None

    while ready_queue or pending_idx < n:
        if not ready_queue:
            current_time = pending[pending_idx]["request_time"]
            while pending_idx < n and pending[pending_idx]["request_time"] <= current_time:
                ready_queue.append(pending[pending_idx])
                pending_idx += 1

        # Select next request from available ready queue
        if algorithm == "SJF":
            next_req = min(
                ready_queue,
                key=lambda r: (r["duration_seconds"], r["request_time"], r["request_id"]),
            )
        elif algorithm == "PRIORITY":
            next_req = min(
                ready_queue,
                key=lambda r: _priority_rank(r, reference_time),
            )
        else:  # FCFS
            next_req = min(
                ready_queue,
                key=lambda r: (r["request_time"], r["request_id"]),
            )

        ready_queue.remove(next_req)

        arrival = next_req["request_time"]
        duration = next_req["duration_seconds"]

        if earliest_arrival is None or arrival < earliest_arrival:
            earliest_arrival = arrival

        start_time = current_time
        completion_time = start_time + timedelta(seconds=duration)
        waiting_time = (start_time - arrival).total_seconds()
        turnaround_time = (completion_time - arrival).total_seconds()
        response_time = (start_time - arrival).total_seconds()

        total_wait += waiting_time
        total_turnaround += turnaround_time
        total_response += response_time

        if latest_completion is None or completion_time > latest_completion:
            latest_completion = completion_time

        results.append({
            "request_id": next_req["request_id"],
            "priority": next_req["priority"],
            "arrival": arrival,
            "start_time": start_time,
            "completion_time": completion_time,
            "waiting_seconds": waiting_time,
            "turnaround_seconds": turnaround_time,
            "response_time_seconds": response_time,
            "duration_seconds": duration,
        })

        current_time = completion_time

        # Enqueue any requests that arrived while next_req was executing
        while pending_idx < n and pending[pending_idx]["request_time"] <= current_time:
            ready_queue.append(pending[pending_idx])
            pending_idx += 1

    request_count = len(results)
    sim_seconds = 0.0
    if earliest_arrival is not None and latest_completion is not None:
        sim_seconds = max(0.0, (latest_completion - earliest_arrival).total_seconds())

    throughput_sec = (request_count / sim_seconds) if sim_seconds > 0 else 0.0

    return {
        "algorithm": algorithm,
        "reference_time": reference_time,
        "request_count": request_count,
        "average_wait_seconds": total_wait / request_count if request_count else 0.0,
        "average_turnaround_seconds": total_turnaround / request_count if request_count else 0.0,
        "average_response_seconds": total_response / request_count if request_count else 0.0,
        "throughput_per_second": throughput_sec,
        "throughput_per_hour": throughput_sec * 3600.0,
        "total_simulation_seconds": sim_seconds,
        "is_simulated": True,
        "results": results,
    }


def _simulate_requests(
    rows,
    algorithm,
    reference_time,
    default_processing_time=SIMULATION_PROCESSING_TIME_SECONDS,
    time_quantum=DEFAULT_RR_QUANTUM_SECONDS,
):
    """Simulate a given scheduling policy on an identical request workload.

    Supported algorithms: FCFS, PRIORITY, RR, SJF.
    """
    reference_time = _coerce_datetime(reference_time)
    normalized_rows = []
    for request in rows:
        request_time = _coerce_datetime(request.get("request_time"))
        if request_time is None:
            continue
        normalized_request = dict(request)
        normalized_request["request_time"] = request_time
        duration = request.get("duration_seconds")
        if duration is None or duration <= 0:
            duration = default_processing_time
        normalized_request["duration_seconds"] = float(duration)
        normalized_rows.append(normalized_request)
    rows = normalized_rows

    if not rows:
        return {
            "algorithm": algorithm,
            "reference_time": reference_time,
            "request_count": 0,
            "average_wait_seconds": 0.0,
            "average_turnaround_seconds": 0.0,
            "average_response_seconds": 0.0,
            "throughput_per_second": 0.0,
            "throughput_per_hour": 0.0,
            "total_simulation_seconds": 0.0,
            "is_simulated": True,
            "results": [],
        }

    if algorithm == "RR":
        return _simulate_round_robin(rows, reference_time, time_quantum=time_quantum)

    return _simulate_non_preemptive(rows, algorithm, reference_time)


def run_scheduling_experiment(
    cursor=None,
    algorithms=None,
    processing_time_seconds=SIMULATION_PROCESSING_TIME_SECONDS,
    time_quantum=DEFAULT_RR_QUANTUM_SECONDS,
    rows=None,
):
    """Run a simulation-only comparison using the same request records.

    The processing-time assumption is a model used only for experiment comparison.
    It does not change production emergency request data or resource quantities.
    When algorithms is None, defaults to ("FCFS", "PRIORITY") for backward compatibility.
    Accepts pre-loaded request rows or loads them via cursor if rows is None.
    """
    reference_time = datetime.now(timezone.utc)
    if rows is None:
        rows = _load_request_rows(cursor) if cursor is not None else []
    else:
        # Shallow copy items so modification doesn't alter caller's input list
        rows = [dict(r) for r in rows]

    if not rows:
        return {
            "label": "Simulation unavailable",
            "reference_time": reference_time,
            "processing_time_seconds": processing_time_seconds,
            "notes": [
                "This illustrative simulation is not actual production performance.",
                "No request history with valid request timestamps is available.",
            ],
            "algorithms": [],
            "comparison": [],
        }

    target_algorithms = algorithms if algorithms is not None else ("FCFS", "PRIORITY")
    results = []
    for algo in target_algorithms:
        if (
            processing_time_seconds == SIMULATION_PROCESSING_TIME_SECONDS
            and time_quantum == DEFAULT_RR_QUANTUM_SECONDS
        ):
            results.append(_simulate_requests(rows, algo, reference_time))
        else:
            results.append(
                _simulate_requests(
                    rows,
                    algo,
                    reference_time,
                    processing_time_seconds,
                    time_quantum,
                )
            )
    comparison = [
        {
            "algorithm": result["algorithm"],
            "request_count": result["request_count"],
            "average_wait_seconds": result["average_wait_seconds"],
            "average_turnaround_seconds": result["average_turnaround_seconds"],
            "average_response_seconds": result.get("average_response_seconds", 0.0),
            "throughput_per_second": result.get("throughput_per_second", 0.0),
            "throughput_per_hour": result.get("throughput_per_hour", 0.0),
            "total_simulation_seconds": result.get("total_simulation_seconds", 0.0),
            "is_simulated": True,
        }
        for result in results
    ]

    priority_note = None
    fcfs_res = next((r for r in results if r["algorithm"] == "FCFS"), None)
    pri_res = next((r for r in results if r["algorithm"] == "PRIORITY"), None)
    if fcfs_res and pri_res and rows:
        fcfs_order = [r["request_id"] for r in fcfs_res["results"]]
        pri_order = [r["request_id"] for r in pri_res["results"]]
        if fcfs_order == pri_order:
            all_aged = all(
                (reference_time - r["request_time"]).total_seconds() >= AGING_THRESHOLD_SECONDS
                for r in rows
            )
            distinct_priorities = len(set(r.get("priority") for r in rows))
            if all_aged:
                priority_note = (
                    "FCFS and Priority Scheduling produced identical queue sequences because all requests "
                    "in this workload have waited >= 900 seconds from their creation timestamps to the simulation "
                    "reference timestamp. Under the 900-second aging threshold, all aged requests are promoted "
                    "to the highest priority tier (group 0) to prevent starvation, tie-breaking on original arrival order. "
                    "This demonstrates proper starvation prevention under long queue wait times."
                )
            elif distinct_priorities <= 1:
                priority_note = (
                    "FCFS and Priority Scheduling produced identical results because all requests in this workload "
                    "share the same priority level."
                )
            else:
                priority_note = (
                    "FCFS and Priority Scheduling produced identical results because requests naturally arrived "
                    "in descending priority order."
                )

    return {
        "label": "Illustrative simulation only — results are not actual production performance.",
        "reference_time": reference_time,
        "processing_time_seconds": processing_time_seconds,
        "time_quantum_seconds": time_quantum,
        "priority_comparison_note": priority_note,
        "notes": [
            "This experiment uses the same request records for each algorithm.",
            f"Processing time is an artificial fixed assumption of {processing_time_seconds} seconds per request unless varied synthetic durations are assigned.",
            "Requests with NULL or invalid request timestamps are excluded.",
            "No production emergency request status or resource quantity is modified.",
            "SJF orders requests by synthetic estimated duration with deterministic tie-breaking (duration, request_time, request_id).",
            f"Round Robin operates preemptively using a simulated time quantum of {time_quantum} seconds.",
            "All simulated metrics (waiting time, turnaround time, response time, and throughput) are clearly distinguished from live production allocation events.",
        ],
        "algorithms": results,
        "comparison": comparison,
    }


def run_fair_algorithm_comparison(
    cursor=None,
    algorithms=("FCFS", "PRIORITY", "RR", "SJF"),
    processing_time_seconds=None,
    time_quantum=DEFAULT_RR_QUANTUM_SECONDS,
    durations=None,
    rows=None,
):
    """Run fair comparison across all 4 algorithms on identical request workloads.

    Ensures the workload is queried from the database exactly once and that
    deterministic synthetic durations are assigned once and preserved across
    FCFS, Priority, RR, and SJF.
    """
    if rows is None:
        rows = _load_request_rows(cursor) if cursor is not None else []
    else:
        rows = [dict(r) for r in rows]

    # Assign deterministic synthetic duration to each request once
    for r in rows:
        req_id = r.get("request_id")
        if durations and req_id in durations:
            r["duration_seconds"] = float(durations[req_id])
        elif "duration_seconds" not in r:
            if processing_time_seconds is not None:
                r["duration_seconds"] = float(processing_time_seconds)
            else:
                r["duration_seconds"] = calculate_synthetic_duration(req_id)

    res = run_scheduling_experiment(
        cursor=None,
        algorithms=algorithms,
        processing_time_seconds=processing_time_seconds or SIMULATION_PROCESSING_TIME_SECONDS,
        time_quantum=time_quantum,
        rows=rows,
    )
    if processing_time_seconds is None and not durations:
        res["notes"].append(
            "Synthetic processing durations are deterministically assigned per request in the range 120-1200 seconds "
            "based strictly on request ID (independent of real resource quantities or priority)."
        )
    return res


import argparse
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from core import db
from os_engine.allocator import allocate_request
from os_engine.worker import WORKER_LOCK_NAME

KNOWN_FAILURES = (
    "Insufficient quantity",
    "No available resource found",
    "is not PENDING",
)


# ------------------------------------------------------------------ fixtures
def create_fixture(resource_total, quantities):
    """Naya isolated resource type + 1 resource + len(quantities) PENDING requests."""
    suffix = uuid.uuid4().hex[:10]
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO locations (city, state) VALUES (%s, %s)",
            (f"ConcTest-{suffix}", "ConcTest"),
        )
        location_id = cur.lastrowid
        cur.execute(
            "INSERT INTO resource_types (type_name, unit) VALUES (%s, %s)",
            (f"conc-{suffix}", "unit"),
        )
        type_id = cur.lastrowid
        cur.execute(
            "INSERT INTO disasters (name, type, severity, location_id) "
            "VALUES (%s, 'Flood', 'HIGH', %s)",
            (f"ConcTest disaster {suffix}", location_id),
        )
        disaster_id = cur.lastrowid
        cur.execute(
            "INSERT INTO resources (type_id, location_id, total_quantity, "
            "available_quantity, status) VALUES (%s, %s, %s, %s, 'AVAILABLE')",
            (type_id, location_id, resource_total, resource_total),
        )
        resource_id = cur.lastrowid
        request_ids = []
        for quantity in quantities:
            cur.execute(
                "INSERT INTO emergency_requests (disaster_id, resource_type_id, "
                "requested_quantity, priority) VALUES (%s, %s, %s, 'HIGH')",
                (disaster_id, type_id, quantity),
            )
            request_ids.append(cur.lastrowid)
        conn.commit()
        return {
            "location_id": location_id,
            "type_id": type_id,
            "disaster_id": disaster_id,
            "resource_id": resource_id,
            "request_ids": request_ids,
            "total": resource_total,
        }
    finally:
        cur.close()
        conn.close()


def drop_fixture(fx):
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        ids = fx["request_ids"]
        marks = ", ".join(["%s"] * len(ids))
        cur.execute(f"DELETE FROM notifications WHERE related_request_id IN ({marks})", ids)
        cur.execute(f"DELETE FROM allocations WHERE request_id IN ({marks})", ids)
        cur.execute(f"DELETE FROM emergency_requests WHERE request_id IN ({marks})", ids)
        cur.execute("DELETE FROM resources WHERE resource_id = %s", (fx["resource_id"],))
        cur.execute("DELETE FROM disasters WHERE disaster_id = %s", (fx["disaster_id"],))
        cur.execute("DELETE FROM resource_types WHERE type_id = %s", (fx["type_id"],))
        cur.execute("DELETE FROM locations WHERE location_id = %s", (fx["location_id"],))
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ------------------------------------------------------------------ run + verify
def hammer(request_ids):
    """Har request_id ke liye ek thread; sab Barrier par ruk kar ek saath chhoote hain."""
    barrier = threading.Barrier(len(request_ids))

    def worker(request_id):
        barrier.wait(timeout=30)
        return allocate_request(request_id)

    with ThreadPoolExecutor(max_workers=len(request_ids)) as pool:
        futures = [pool.submit(worker, rid) for rid in request_ids]
        return [f.result(timeout=120) for f in futures]


def snapshot(fx):
    conn = db.get_db_connection()
    cur = conn.cursor()
    try:
        ids = fx["request_ids"]
        marks = ", ".join(["%s"] * len(ids))
        cur.execute(
            "SELECT available_quantity, total_quantity, status FROM resources "
            "WHERE resource_id = %s", (fx["resource_id"],))
        available, total, res_status = cur.fetchone()
        cur.execute(
            f"SELECT request_id, allocated_quantity FROM allocations "
            f"WHERE request_id IN ({marks})", ids)
        allocations = cur.fetchall()
        cur.execute(
            f"SELECT COUNT(*) FROM emergency_requests "
            f"WHERE request_id IN ({marks}) AND status = 'ALLOCATED'", ids)
        allocated_requests = cur.fetchone()[0]
        cur.execute(
            f"SELECT COUNT(*) FROM notifications "
            f"WHERE related_request_id IN ({marks})", ids)
        notification_count = cur.fetchone()[0]
        return {
            "available": available, "total": total, "res_status": res_status,
            "allocations": allocations, "allocated_requests": allocated_requests,
            "notifications": notification_count,
        }
    finally:
        cur.close()
        conn.close()


def check_scenario(name, resource_total, quantities, same_request, expect_successes,
                   expect_available):
    fx = create_fixture(resource_total, quantities)
    try:
        if same_request:
            targets = [fx["request_ids"][0]] * len(quantities)
        else:
            targets = list(fx["request_ids"])
        results = hammer(targets)
        s = snapshot(fx)
    finally:
        drop_fixture(fx)

    successes = sum(1 for ok, _ in results if ok)
    alloc_total = sum(q for _, q in s["allocations"])
    per_request = {}
    for rid, _ in s["allocations"]:
        per_request[rid] = per_request.get(rid, 0) + 1

    checks = [
        ("stock kabhi negative nahi", s["available"] >= 0),
        ("stock total se zyada nahi", s["available"] <= s["total"]),
        ("total - available == sum(allocations)",
         s["total"] - s["available"] == alloc_total),
        ("ek request ki 1 se zyada allocation row nahi",
         all(c == 1 for c in per_request.values())),
        ("successful calls == allocation rows",
         successes == len(s["allocations"])),
        ("successful calls == ALLOCATED requests",
         successes == s["allocated_requests"]),
        ("notifications = 2 per allocated request",
         s["notifications"] == 2 * successes),
        (f"expected successes == {expect_successes}", successes == expect_successes),
        (f"expected final stock == {expect_available}", s["available"] == expect_available),
        ("stock 0 hote hi status UNAVAILABLE",
         (s["available"] == 0) == (s["res_status"] == "UNAVAILABLE")),
    ]

    unexpected = [
        msg for ok, msg in results
        if not ok and not any(known in msg for known in KNOWN_FAILURES)
    ]
    detail = (f"successes={successes}, allocations={len(s['allocations'])}, "
              f"stock {s['total']}->{s['available']}")
    return checks, unexpected, detail


# ------------------------------------------------------------------ main
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--threads", type=int, default=10)
    parser.add_argument("--rounds", type=int, default=3,
                        help="har scenario kitni baar repeat ho (race kabhi-kabhi hi pakadti hai)")
    parser.add_argument("--force", action="store_true",
                        help="worker chal raha ho tab bhi chalao (results gadbad ho sakte hain)")
    args = parser.parse_args()
    n = args.threads
    if n < 2:
        sys.exit("--threads kam se kam 2 hona chahiye.")

    conn = db.get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT VERSION(), @@transaction_isolation")
    version, isolation = cur.fetchone()
    cur.execute("SELECT IS_USED_LOCK(%s)", (WORKER_LOCK_NAME,))
    worker_running = cur.fetchone()[0] is not None
    cur.close()
    conn.close()

    print(f"MySQL {version} | isolation: {isolation} | threads: {n} | rounds: {args.rounds}")
    if worker_running and not args.force:
        sys.exit("Automatic worker chal raha hai; wo test requests utha lega. "
                 "Pehle scheduler_worker.py band karo (ya --force).")

    # (naam, stock, quantities, same_request, expected successes, expected final stock)
    scenarios = [
        (f"A. ek hi request par {n} threads (stock kaafi hai)",
         100, [6] * n, True, 1, 94),
        (f"B. {n} alag requests, stock sirf 1 ke liye (5 stock, har request 5)",
         5, [5] * n, False, 1, 0),
        (f"C. {n} alag requests, har ek 3, stock 10 (sirf 3 fit hongi)",
         10, [3] * n, False, 3, 1),
    ]

    failed = False
    warnings = []
    for name, stock, quantities, same, exp_ok, exp_stock in scenarios:
        print(f"\n== {name}")
        for round_no in range(1, args.rounds + 1):
            checks, unexpected, detail = check_scenario(
                name, stock, quantities, same, exp_ok, exp_stock)
            bad = [label for label, ok in checks if not ok]
            status = "PASS" if not bad else "FAIL"
            print(f"  round {round_no}: {status}  ({detail})")
            for label in bad:
                print(f"      x {label}")
            if bad:
                failed = True
            warnings.extend(unexpected)

    if warnings:
        print(f"\nWARN: {len(warnings)} call(s) unexpected error ke saath fail hui "
              "(data safe tha, rollback hua) - jaise deadlock/lock timeout:")
        for msg in sorted(set(warnings)):
            print(f"   - {msg} (x{warnings.count(msg)})")

    print("\nRESULT:", "FAIL - integrity toot rahi hai" if failed else "PASS - koi integrity violation nahi")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
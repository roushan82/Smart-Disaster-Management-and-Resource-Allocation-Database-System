import unittest
from unittest.mock import patch

from os_engine.worker import WORKER_LOCK_NAME, is_worker_running, run_automatic_worker


class WorkerCursor:
    def __init__(self, connection_id=51, generation_values=(1,), pending_values=(1,),
                 lock_acquired=True, runnable_values=(1,)):
        self.connection_id = connection_id
        self.generation_values = iter(generation_values)
        self.pending_values = iter(pending_values)
        self.runnable_values = iter(runnable_values)
        self.lock_acquired = lock_acquired
        self.row = None
        self.lock_names = []

    def execute(self, statement, parameters=None):
        if "GET_LOCK" in statement:
            self.lock_names.append(parameters[0])
            self.row = (1 if self.lock_acquired else 0,)
        elif "IS_USED_LOCK" in statement:
            self.row = (self.connection_id, self.connection_id)
        elif "SELECT generation" in statement:
            self.row = (next(self.generation_values),)
        elif "SELECT COUNT(*)" in statement:
            self.row = (next(self.pending_values),)
        elif "SELECT EXISTS" in statement:
            self.row = (next(self.runnable_values),)
        elif "RELEASE_LOCK" in statement:
            self.lock_names.append(parameters[0])
            self.row = (1,)

    def fetchone(self):
        return self.row

    def close(self):
        pass


class WorkerConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return self.fake_cursor

    def is_connected(self):
        return not self.closed

    def close(self):
        self.closed = True


class StopAfterWait:
    def __init__(self, max_waits=1):
        self.max_waits = max_waits
        self.waits = 0

    def is_set(self):
        return self.waits >= self.max_waits

    def wait(self, _seconds):
        self.waits += 1


class AutomaticWorkerTests(unittest.TestCase):
    def test_worker_runs_after_pending_signal_and_releases_singleton_lock(self):
        lock_cursor = WorkerCursor(connection_id=51)
        lock_connection = WorkerConnection(lock_cursor)
        poll_cursor = WorkerCursor(generation_values=(4,), pending_values=(1,))
        poll_connection = WorkerConnection(poll_cursor)
        stop_event = StopAfterWait()

        with patch(
            "core.db.get_db_connection",
            side_effect=[lock_connection, poll_connection],
        ):
            with patch(
                "os_engine.worker.schedule_automatically",
                return_value={"run_id": 1, "algorithm": "FCFS", "message": "run complete"},
            ) as scheduler:
                started = run_automatic_worker(
                    stop_event=stop_event,
                    poll_interval=0,
                    retry_interval=30,
                )

        self.assertTrue(started)
        scheduler.assert_called_once_with()
        self.assertEqual(lock_cursor.lock_names, [WORKER_LOCK_NAME, WORKER_LOCK_NAME])
        self.assertTrue(lock_connection.closed)

    def test_duplicate_worker_exits_without_polling_or_scheduling(self):
        lock_cursor = WorkerCursor(lock_acquired=False)
        lock_connection = WorkerConnection(lock_cursor)
        with patch(
            "core.db.get_db_connection",
            return_value=lock_connection,
        ):
            with patch("os_engine.worker.schedule_automatically") as scheduler:
                started = run_automatic_worker(stop_event=StopAfterWait())

        self.assertFalse(started)
        scheduler.assert_not_called()
        self.assertTrue(lock_connection.closed)

    def test_worker_rechecks_new_generation_after_cycle_finishes(self):
        lock_connection = WorkerConnection(WorkerCursor(connection_id=73))
        poll_connections = [
            WorkerConnection(WorkerCursor(generation_values=(generation,), pending_values=(1,)))
            for generation in (10, 11)
        ]
        stop_event = StopAfterWait(max_waits=2)

        with patch(
            "core.db.get_db_connection",
            side_effect=[lock_connection, *poll_connections],
        ):
            with patch(
                "os_engine.worker.schedule_automatically",
                return_value={"run_id": 2, "algorithm": "PRIORITY", "message": "run complete"},
            ) as scheduler:
                run_automatic_worker(
                    stop_event=stop_event,
                    poll_interval=0,
                    retry_interval=30,
                )

        self.assertEqual(scheduler.call_count, 2)


    def _run_worker(self, poll_cursors, retry_interval=0, max_waits=None):
        """Run the worker over several polls; return how many times it scheduled."""
        lock_connection = WorkerConnection(WorkerCursor(connection_id=90))
        poll_connections = [WorkerConnection(c) for c in poll_cursors]
        stop_event = StopAfterWait(max_waits=max_waits or len(poll_connections))
        with patch(
            "core.db.get_db_connection",
            side_effect=[lock_connection, *poll_connections],
        ):
            with patch(
                "os_engine.worker.schedule_automatically",
                return_value={"run_id": 5, "algorithm": "FCFS", "message": "run complete"},
            ) as scheduler:
                run_automatic_worker(
                    stop_event=stop_event, poll_interval=0, retry_interval=retry_interval,
                )
        return scheduler.call_count

    def test_retry_is_skipped_when_no_pending_request_fits_the_stock(self):
        # Poll 1: new generation -> one run. Polls 2-3: same generation, pending
        # requests still stuck (nothing runnable) -> no more runs, no log spam.
        polls = [
            WorkerCursor(generation_values=(7,), pending_values=(2,), runnable_values=(0,)),
            WorkerCursor(generation_values=(7,), pending_values=(2,), runnable_values=(0,)),
            WorkerCursor(generation_values=(7,), pending_values=(2,), runnable_values=(0,)),
        ]
        self.assertEqual(self._run_worker(polls), 1)

    def test_retry_runs_again_once_stock_makes_a_pending_request_runnable(self):
        # Same generation, but after a replenish a pending request now fits.
        polls = [
            WorkerCursor(generation_values=(7,), pending_values=(2,), runnable_values=(0,)),
            WorkerCursor(generation_values=(7,), pending_values=(2,), runnable_values=(0,)),
            WorkerCursor(generation_values=(7,), pending_values=(2,), runnable_values=(1,)),
        ]
        self.assertEqual(self._run_worker(polls), 2)

    def test_new_request_signal_always_runs_even_if_nothing_is_runnable(self):
        polls = [
            WorkerCursor(generation_values=(7,), pending_values=(1,), runnable_values=(0,)),
            WorkerCursor(generation_values=(8,), pending_values=(2,), runnable_values=(0,)),
        ]
        self.assertEqual(self._run_worker(polls), 2)

    def test_no_pending_requests_means_no_run_and_no_runnable_query(self):
        cursor = WorkerCursor(generation_values=(3,), pending_values=(0,), runnable_values=())
        self.assertEqual(self._run_worker([cursor]), 0)


class WorkerStatusTests(unittest.TestCase):
    def test_is_worker_running_reflects_the_singleton_lock_holder(self):
        class Cursor:
            def __init__(self, holder):
                self.holder = holder
            def execute(self, statement, parameters=None):
                assert "IS_USED_LOCK" in statement and parameters == (WORKER_LOCK_NAME,)
            def fetchone(self):
                return (self.holder,)

        self.assertTrue(is_worker_running(Cursor(77)))
        self.assertFalse(is_worker_running(Cursor(None)))


if __name__ == "__main__":
    unittest.main()
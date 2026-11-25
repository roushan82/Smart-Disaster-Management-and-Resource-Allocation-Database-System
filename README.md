# Smart Disaster Management System

Flask + MySQL. Every feature has its own module; the operating-system concepts live separately in `os_engine/`.

## Structure

| Folder / file | Purpose |
|---|---|
| `app.py` | Creates the app and registers all modules (blueprints) |
| `core/` | DB connection, CSRF security, shared validation helpers |
| `modules/dashboard` | Home page: counts and worker status |
| `modules/disasters` | Add / resolve disasters |
| `modules/resources` | Resource stock and replenishment |
| `modules/emergency_requests` | Create requests, manual allocate, FCFS / Priority / Round Robin buttons, scheduling log |
| `modules/teams` | Rescue teams |
| `modules/personnel` | Personnel |
| `modules/assignments` | Request ↔ team assignment |
| `modules/reports` | Reports and allocation history |
| `modules/notifications` | Notification inbox and unread count |
| `os_engine/policies.py` | FCFS, Priority, Round Robin, **aging (15 min)**, automatic algorithm selection |
| `os_engine/allocator.py` | Allocation as a critical section (transaction + `FOR UPDATE` + rollback) |
| `os_engine/scheduler.py` | One scheduling run (`GET_LOCK` mutual exclusion) and its run log |
| `os_engine/worker.py` | Background automatic worker (singleton lock) |
| `database/schema.sql` | Full schema for a fresh install · `sample_data.sql` holds demo data |
| `templates/<module>/` | HTML pages for each module · `templates/shared/` holds the common nav and CSRF field |

## Run

```
pip install -r requirements.txt
mysql -u root -p < database/schema.sql          # optional: database/sample_data.sql
set FLASK_SECRET_KEY=...  DB_USER=...  DB_PASSWORD=...  DB_NAME=disaster_management   (see .env.example)
flask --app app run                              # web UI
python scheduler_worker.py                       # automatic worker (separate terminal)
python -m unittest discover -s tests -t .        # tests
```

`FLASK_SECRET_KEY` must be a random string of at least 32 characters, otherwise every page returns a 500 error.
The `set` syntax above is for Windows Command Prompt. In PowerShell use `$env:FLASK_SECRET_KEY="..."` instead.

### Historical detection review

The review page uses a single reviewer account configured with `REVIEWER_USERNAME`
and `REVIEWER_PASSWORD_HASH`. Generate a password hash without echoing the
password, then store the username and hash in the root `.env` (which is ignored
by Git):

```
python -c "from getpass import getpass; from werkzeug.security import generate_password_hash; print(generate_password_hash(getpass()))"
```

Set `REVIEWER_PASSWORD_HASH` to the generated hash. Do not store the plaintext
password. The review routes require this configured account and a valid signed
Flask session; POST actions also require the application's CSRF token. Configure
HTTPS and a strong `FLASK_SECRET_KEY` before exposing the service beyond a
trusted development environment.

## Where the OS / DBMS concepts are

* **FCFS / Priority / Aging / automatic selection** → `os_engine/policies.py`. If any pending request is CRITICAL the policy is PRIORITY, otherwise FCFS. A request that has waited more than 900 s moves to the front of the queue.
* **Round Robin** → Manual scheduling rotates pending requests across RR runs using a durable request-ID anchor in `scheduling_runs`. Each request still receives one full allocation attempt; RR does not split allocations or use CPU-style time slices. Automatic selection remains FCFS/Priority.
* **Critical section / no double allocation** → `os_engine/allocator.py` (`SELECT … FOR UPDATE`, commit / rollback).
* **Deadlock handling** → `allocator.py` always locks the request row first and the resource row second, so lock cycles cannot form. If MySQL still reports a deadlock (error 1213), the whole allocation is retried on a fresh connection, up to 3 attempts.
* **Mutual exclusion** → `GET_LOCK` in `scheduler.py` (one scheduling run at a time) and in `worker.py` (only one worker at a time).
* **Is the worker running?** → The Dashboard and Requests pages show the real status using `IS_USED_LOCK`.
* **Worker retry** → A new request always triggers one scheduling run. The periodic retry (every 30 s) only runs when at least one pending request fits the stock available right now, so stuck requests do not fill the scheduling log with failed rows. Once stock is replenished, the waiting request is picked up automatically.
* **Integrity** → CHECK constraints, foreign keys and validation triggers (disaster type, team type, personnel role and contact) in `schema.sql`. The unique `event_key` prevents duplicate notifications.
* **Schema** → `database/schema.sql` matches a dump of the real `disaster_management` database (tables, constraints, triggers).

## Testing

* `tests/` contains unit tests that mock the database, so they do not need a running MySQL server.
* `tests/real_mysql_concurrency_check.py` is a separate script (not part of `unittest discover`) that runs concurrent allocations against a real MySQL database and checks that stock never goes negative and a request is never allocated twice. Stop the automatic worker before running it:
  `python -m tests.real_mysql_concurrency_check --threads 10 --rounds 5`
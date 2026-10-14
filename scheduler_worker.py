import logging

from os_engine.worker import run_automatic_worker

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not run_automatic_worker():
        raise SystemExit("Automatic scheduler worker is already running.")

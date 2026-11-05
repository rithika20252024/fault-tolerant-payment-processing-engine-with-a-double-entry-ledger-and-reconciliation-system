import logging
import os
import random
import signal
import time
from uuid import uuid4

from .db import Database
from .engine import Engine
from .providers import ReviewRequired
from .runtime import validate_cloud_environment

log = logging.getLogger("worker")


class Worker:
    def __init__(self, engine, lease_seconds=30, max_attempts=8):
        self.engine = engine
        self.db = engine.db
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    def claim(self):
        with self.db.tx() as c:
            c.execute("""UPDATE jobs SET status='dead',last_error='retry_budget_exhausted'
              WHERE attempts >= %s AND (status='ready' OR (status='running' AND lease_until<now()))""", (self.max_attempts,))
            return c.execute("""WITH candidate AS (
              SELECT id FROM jobs WHERE attempts<%s AND
              ((status='ready' AND available_at<=now()) OR (status='running' AND lease_until<now()))
              ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1)
              UPDATE jobs j SET status='running',attempts=attempts+1,lease_token=%s,
              lease_until=now()+%s*interval '1 second' FROM candidate c WHERE j.id=c.id RETURNING j.*""",
              (self.max_attempts, uuid4().hex, self.lease_seconds)).fetchone()

    def finish(self, job):
        with self.db.tx() as c:
            c.execute("UPDATE jobs SET status='done',lease_until=NULL WHERE id=%s AND lease_token=%s AND status='running'",
                      (job["id"], job["lease_token"]))

    def fail(self, job, exc):
        dead = isinstance(exc, ReviewRequired) or job["attempts"] >= self.max_attempts
        delay = min(60, 2 ** job["attempts"]) + random.random()
        with self.db.tx() as c:
            c.execute("""UPDATE jobs SET status=%s,lease_until=NULL,available_at=now()+%s*interval '1 second',last_error=%s
              WHERE id=%s AND lease_token=%s AND status='running'""",
              ("dead" if dead else "ready", delay, str(exc) if isinstance(exc, ReviewRequired) else type(exc).__name__, job["id"], job["lease_token"]))
        log.warning("job_failed id=%s kind=%s error_type=%s dead=%s", job["id"], job["kind"], type(exc).__name__, dead)

    def once(self):
        job = self.claim()
        if not job:
            return False
        try:
            self.engine.process(job)
            self.finish(job)
        except Exception as exc:
            self.fail(job, exc)
        return True


def main():
    validate_cloud_environment()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("stripe").setLevel(logging.CRITICAL)
    db = Database()
    worker = Worker(Engine(db, os.getenv("PROVIDER", "simulator")))
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while running:
            try:
                if not worker.once():
                    time.sleep(0.1)
            except Exception:
                log.exception("worker_loop_failed")
                time.sleep(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()

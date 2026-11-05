from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg
import pytest

from payments.engine import Conflict
from payments.worker import Worker
from conftest import drain


def fault(db, key, mode, remaining=1):
    with db.tx() as c:
        c.execute("INSERT INTO sim_faults(operation_key,mode,remaining) VALUES(%s,%s,%s)", (key, mode, remaining))


def make_ready(db):
    with db.tx() as c:
        c.execute("UPDATE jobs SET available_at=now() WHERE status='ready'")


def test_100_concurrent_identical_payments_create_one_operation(engine, db):
    with ThreadPoolExecutor(max_workers=20) as pool:
        rows = list(pool.map(lambda _: engine.create_payment("same", 1500, "usd"), range(100)))
    assert len({r["id"] for r in rows}) == 1
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"] == 1
    drain(engine)
    assert engine.invariants()["ok"]
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM journal").fetchone()["n"] == 1


def test_idempotency_parameter_mismatch(engine):
    engine.create_payment("key", 100, "usd")
    with pytest.raises(Conflict):
        engine.create_payment("key", 200, "usd")


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, 100000001])
def test_invalid_money_rejected(engine, amount):
    with pytest.raises(ValueError):
        engine.create_payment("bad", amount, "usd")


def test_concurrent_refunds_cannot_exceed_payment(engine, paid, db):
    def attempt(i):
        try:
            return engine.create_refund(paid["id"], f"refund-{i}", 1000)
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=20) as pool:
        accepted = [r for r in pool.map(attempt, range(30)) if r]
    assert len(accepted) == 10
    drain(engine)
    p = engine.payment(paid["id"])
    assert p["reserved_refunds"] == 10000
    assert all(r["status"] == "succeeded" for r in p["refunds"])
    assert engine.invariants()["ok"]


def test_refund_idempotency_across_concurrent_calls(engine, paid):
    with ThreadPoolExecutor(max_workers=20) as pool:
        rows = list(pool.map(lambda _: engine.create_refund(paid["id"], "one-refund", 1000), range(100)))
    assert len({r["id"] for r in rows}) == 1
    assert engine.payment(paid["id"])["reserved_refunds"] == 1000


def test_timeout_after_provider_commit_does_not_duplicate(engine, db):
    p = engine.create_payment("lost-response", 1000, "usd")
    fault(db, p["id"], "timeout_after_commit")
    worker = Worker(engine)
    assert worker.once()
    assert engine.payment(p["id"])["status"] == "pending"
    make_ready(db)
    assert worker.once()
    assert engine.payment(p["id"])["status"] == "succeeded"
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM sim_objects").fetchone()["n"] == 1
        assert c.execute("SELECT count(*) AS n FROM journal").fetchone()["n"] == 1


def test_crash_after_ledger_commit_before_job_ack(engine, db):
    p = engine.create_payment("crash", 1000, "usd")
    worker = Worker(engine)
    job = worker.claim()
    engine.process(job)  # process dies here without acknowledging
    with db.tx() as c:
        c.execute("UPDATE jobs SET lease_until=now()-interval '1 second'")
    assert worker.once()
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM journal").fetchone()["n"] == 1
        assert c.execute("SELECT status FROM jobs").fetchone()["status"] == "done"
    assert engine.invariants()["ok"]


def test_crash_before_local_commit_rolls_back(engine, db, monkeypatch):
    p = engine.create_payment("rollback", 1000, "usd")
    original = engine.post
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash_before_transaction_commit")
    monkeypatch.setattr(engine, "post", fail)
    Worker(engine).once()
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM journal").fetchone()["n"] == 0
    assert engine.payment(p["id"])["status"] == "pending"
    monkeypatch.setattr(engine, "post", original)
    make_ready(db)
    drain(engine)
    assert engine.payment(p["id"])["status"] == "succeeded"
    assert engine.invariants()["ok"]


def test_failed_refund_releases_reservation(engine, paid, db):
    ref = engine.create_refund(paid["id"], "failed-refund", 9000)
    fault(db, ref["id"], "decline")
    drain(engine)
    assert engine.payment(paid["id"])["reserved_refunds"] == 0
    engine.create_refund(paid["id"], "replacement", 10000)
    drain(engine)
    assert engine.invariants()["ok"]


def test_duplicate_and_out_of_order_events_fetch_canonical_state(engine, paid, db):
    event = {"id": "evt_1", "type": "payment_intent.processing", "data": {"object": {
        "id": paid["provider_id"], "status": "processing", "metadata": {"engine_payment_id": paid["id"]}}}}
    assert engine.inbox(event, "simulator")
    assert not engine.inbox(event, "simulator")
    event["id"] = "evt_2"
    assert engine.inbox(event, "simulator")
    drain(engine)
    assert engine.payment(paid["id"])["status"] == "succeeded"
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM journal").fetchone()["n"] == 1


def test_reconciliation_repairs_missing_webhook(engine, db):
    p = engine.create_payment("missing-event", 1000, "usd")
    fault(db, p["id"], "pending")
    drain(engine)
    with db.tx() as c:
        c.execute("UPDATE sim_objects SET status='succeeded' WHERE payment_id=%s", (p["id"],))
    assert engine.payment(p["id"])["status"] == "processing"
    run = engine.reconcile()
    assert run["repaired"] == 1
    assert engine.payment(p["id"])["status"] == "succeeded"
    assert engine.invariants()["ok"]


def test_reconciliation_paginates(engine):
    for i in range(5):
        engine.create_payment(str(i), 1000, "usd")
    drain(engine)
    after = ""
    scanned = 0
    while True:
        run = engine.reconcile(limit=2, after=after)
        scanned += run["scanned"]
        after = run["next_cursor"]
        if not after:
            break
    assert scanned == 5


def test_journal_rejects_mutation(engine, paid, db):
    for query in ["UPDATE journal SET amount=1", "DELETE FROM journal", "TRUNCATE journal"]:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.tx() as c:
                c.execute(query)


def test_expired_ambiguous_stripe_retry_requires_review(engine, db):
    p = engine.create_payment("old", 1000, "usd")
    with db.tx() as c:
        c.execute("UPDATE payments SET created_at=now()-interval '25 hours' WHERE id=%s", (p["id"],))
    Worker(engine).once()
    with db.tx() as c:
        job = c.execute("SELECT * FROM jobs").fetchone()
        assert job["status"] == "dead"
        assert "idempotency_window" in job["last_error"]
        assert c.execute("SELECT count(*) AS n FROM sim_objects").fetchone()["n"] == 0


def test_stale_worker_cannot_ack_new_lease(engine, db):
    engine.create_payment("leases", 1000, "usd")
    worker = Worker(engine)
    old = worker.claim()
    with db.tx() as c:
        c.execute("UPDATE jobs SET lease_until=now()-interval '1 second'")
    new = worker.claim()
    worker.finish(old)
    with db.tx() as c:
        assert c.execute("SELECT status FROM jobs").fetchone()["status"] == "running"
    engine.process(new)
    worker.finish(new)
    assert engine.invariants()["ok"]


def test_retry_budget_enters_dead_letter_state(engine, db):
    p = engine.create_payment("exhaust", 1000, "usd")
    fault(db, p["id"], "unavailable", remaining=10)
    worker = Worker(engine, max_attempts=3)
    for _ in range(3):
        make_ready(db)
        assert worker.once()
    with db.tx() as c:
        assert c.execute("SELECT status FROM jobs").fetchone()["status"] == "dead"

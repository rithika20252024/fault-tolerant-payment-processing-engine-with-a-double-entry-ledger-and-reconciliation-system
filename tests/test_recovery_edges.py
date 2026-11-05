from concurrent.futures import ThreadPoolExecutor

import pytest

from payments.engine import Conflict
from payments.providers import ReviewRequired
from payments.worker import Worker
from conftest import drain
from test_correctness import fault, make_ready


def test_many_workers_claim_different_jobs(engine, db):
    for i in range(100):
        engine.create_payment(f"many-{i}", 1000, "usd")
    def work(_):
        worker = Worker(engine)
        while worker.once():
            pass
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(work, range(8)))
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM jobs WHERE status='done'").fetchone()["n"] == 100
        assert c.execute("SELECT max(attempts) AS n FROM jobs").fetchone()["n"] == 1
        assert c.execute("SELECT count(*) AS n FROM journal").fetchone()["n"] == 100
    assert engine.invariants()["ok"]


def test_refund_timeout_after_provider_commit(engine, paid, db):
    ref = engine.create_refund(paid["id"], "lost-refund", 3000)
    fault(db, ref["id"], "timeout_after_commit")
    Worker(engine).once()
    assert engine.payment(paid["id"])["reserved_refunds"] == 3000
    make_ready(db)
    drain(engine)
    assert engine.payment(paid["id"])["refunds"][0]["status"] == "succeeded"
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM sim_objects WHERE kind='refund'").fetchone()["n"] == 1
        assert c.execute("SELECT count(*) AS n FROM journal WHERE operation_id=%s", (ref["id"],)).fetchone()["n"] == 1


def test_pending_refund_reconciles_after_missing_event(engine, paid, db):
    ref = engine.create_refund(paid["id"], "pending-refund", 3000)
    fault(db, ref["id"], "pending")
    drain(engine)
    assert engine.payment(paid["id"])["refunds"][0]["status"] == "pending"
    with db.tx() as c:
        c.execute("UPDATE sim_objects SET status='succeeded' WHERE operation_key=%s", (ref["id"],))
    assert engine.reconcile()["repaired"] == 1
    assert engine.payment(paid["id"])["refunds"][0]["status"] == "succeeded"
    assert engine.invariants()["ok"]


def test_external_refund_is_flagged(engine, paid, db):
    with db.tx() as c:
        c.execute("""INSERT INTO sim_objects(id,operation_key,kind,payment_id,amount,currency,status,parent_id)
            VALUES('sim_external','unknown','refund',%s,100,'usd','succeeded',%s)""", (paid["id"],paid["provider_id"]))
    run = engine.reconcile()
    assert run["findings"] == [{"payment_id":paid["id"],"issue":"unmanaged_provider_refund:sim_external"}]
    assert engine.payment(paid["id"])["reserved_refunds"] == 0


def test_dead_ambiguous_refund_keeps_reservation(engine, paid, db):
    ref = engine.create_refund(paid["id"], "ambiguous", 9000)
    fault(db, ref["id"], "unavailable", remaining=10)
    Worker(engine,max_attempts=1).once()
    assert engine.payment(paid["id"])["reserved_refunds"] == 9000
    with pytest.raises(Conflict):
        engine.create_refund(paid["id"], "another", 2000)


def test_stale_refund_does_not_release_successful_reservation(engine, paid):
    ref = engine.create_refund(paid["id"], "successful", 5000)
    drain(engine)
    current = engine.payment(paid["id"])["refunds"][0]
    engine.apply_refund(ref["id"], {"id":current["provider_id"],"operation_key":ref["id"],"amount":5000,
        "currency":"usd","status":"failed","parent_id":paid["provider_id"]})
    assert engine.payment(paid["id"])["reserved_refunds"] == 5000
    assert engine.invariants()["ok"]


def test_currency_and_parent_refund_mismatch_rejected(engine, paid):
    ref = engine.create_refund(paid["id"], "wrong-parent", 1000)
    with pytest.raises(ReviewRequired):
        engine.apply_refund(ref["id"], {"id":"sim_wrong","operation_key":ref["id"],"amount":1000,
            "currency":"eur","status":"succeeded","parent_id":paid["provider_id"]})


def test_migration_is_repeatable(db):
    from payments.migrate import migrate
    migrate(db)
    with db.tx() as c:
        assert c.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"] == 1

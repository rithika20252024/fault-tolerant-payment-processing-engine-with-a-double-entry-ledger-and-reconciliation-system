import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from payments.db import Database
from payments.engine import Engine
from payments.migrate import migrate
from payments.worker import Worker


@pytest.fixture
def db():
    url = os.environ.get("DATABASE_URL", "postgresql://payments:local-development-only@localhost:55432/payments")
    schema = "test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    database = Database(url, schema)
    migrate(database)
    yield database
    database.close()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def engine(db):
    return Engine(db)


def drain(engine):
    worker = Worker(engine)
    for _ in range(1000):
        if not worker.once():
            return
    raise AssertionError("Queue did not drain")


@pytest.fixture
def paid(engine):
    p = engine.create_payment("original", 10000, "usd")
    drain(engine)
    return engine.payment(p["id"])

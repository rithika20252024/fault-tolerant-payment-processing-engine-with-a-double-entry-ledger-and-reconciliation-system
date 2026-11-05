from pathlib import Path
from .db import Database


def migrate(db):
    with db.tx() as c:
        c.execute("SELECT pg_advisory_xact_lock(784318)")
        c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version integer PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now())")
        if not c.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
            c.execute(Path(__file__).with_name("schema.sql").read_text())
            c.execute("INSERT INTO schema_migrations(version) VALUES(1)")


if __name__ == "__main__":
    db = Database()
    migrate(db)
    db.close()
    print("Schema version 1 installed")

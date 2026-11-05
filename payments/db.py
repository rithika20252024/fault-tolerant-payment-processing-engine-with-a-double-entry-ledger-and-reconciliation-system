import os
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


class Database:
    def __init__(self, url=None, schema=None):
        self.pool = ConnectionPool(
            url or os.environ.get("DATABASE_URL", "postgresql://payments:local-development-only@localhost:55432/payments"),
            min_size=1, max_size=16, timeout=10, open=True,
            kwargs={"row_factory": dict_row, "options": f"-c search_path={schema or 'public'}"},
        )
        self.pool.wait(timeout=15)

    @contextmanager
    def tx(self):
        with self.pool.connection() as connection:
            with connection.transaction():
                yield connection

    def close(self):
        self.pool.close()

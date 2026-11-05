"""Start one Railway role after an idempotent, locked migration."""
import os
import sys

from .db import Database
from .migrate import migrate
from .runtime import validate_cloud_environment


def main():
    validate_cloud_environment()
    role = os.getenv("SERVICE_ROLE", "api")
    if role not in {"api", "worker"}:
        raise ValueError("SERVICE_ROLE must be api or worker")
    port = int(os.getenv("PORT", "8000"))
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    db = Database()
    try:
        migrate(db)
    finally:
        db.close()
    if role == "worker":
        os.execv(sys.executable, [sys.executable, "-m", "payments.worker"])
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "payments.api:app",
        "--host", "0.0.0.0", "--port", str(port)])


if __name__ == "__main__":
    main()

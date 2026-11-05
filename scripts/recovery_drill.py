"""Project-scoped real worker kill and backlog recovery. Simulator only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx
import psycopg


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True, stdout=subprocess.DEVNULL)


def main():
    token = os.getenv("API_TOKEN", "local-demo-token-change-before-sharing")
    url = os.getenv("DATABASE_URL", "postgresql://payments:local-development-only@localhost:55432/payments")
    client = httpx.Client(base_url="http://localhost:18080", headers={"Authorization":"Bearer "+token},timeout=30)
    if client.get("/health").json()["provider"] != "simulator":
        raise SystemExit("Simulator required")
    run = uuid4().hex
    count = 100
    started_at = datetime.now(timezone.utc).isoformat()
    compose("stop", "worker")
    try:
        def create(i):
            response = client.post("/payments",json={"amount":1000,"currency":"usd"},
                                   headers={"Idempotency-Key":f"recovery:{run}:{i}"})
            response.raise_for_status()
            return response.json()["id"]
        with ThreadPoolExecutor(max_workers=10) as pool:
            ids = list(pool.map(create,range(count)))
        with psycopg.connect(url) as c:
            reference = c.execute("SELECT reference FROM jobs WHERE status='ready' AND reference=ANY(%s) ORDER BY created_at LIMIT 1", (ids,)).fetchone()[0]
            c.execute("INSERT INTO sim_faults(operation_key,mode,delay_ms) VALUES(%s,'timeout_after_commit',2000)", (reference,))
        compose("start", "worker")
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            with psycopg.connect(url) as c:
                active = c.execute("SELECT count(*) FROM jobs WHERE reference=ANY(%s) AND status='running'", (ids,)).fetchone()[0]
            if active:
                break
            time.sleep(.02)
        else:
            raise RuntimeError("No worker claimed a recovery job")
        compose("kill", "-s", "SIGKILL", "worker")
        restart_time = time.monotonic()
        compose("start", "worker")
        deadline = time.monotonic()+90
        while time.monotonic()<deadline:
            with psycopg.connect(url) as c:
                completed = c.execute("SELECT count(*) FROM payments WHERE id=ANY(%s) AND status='succeeded'", (ids,)).fetchone()[0]
            if completed==count:
                break
            time.sleep(.2)
        else:
            raise RuntimeError("Recovery did not complete within 90 seconds")
        duration = time.monotonic()-restart_time
        with psycopg.connect(url) as c:
            postings = c.execute("SELECT count(*) FROM journal WHERE payment_id=ANY(%s)", (ids,)).fetchone()[0]
            provider_objects = c.execute("SELECT count(*) FROM sim_objects WHERE payment_id=ANY(%s)", (ids,)).fetchone()[0]
            attempts = c.execute("SELECT max(attempts) FROM jobs WHERE reference=ANY(%s)", (ids,)).fetchone()[0]
        response = client.get("/invariants")
        response.raise_for_status()
        assert response.json()["ok"] and postings==count and provider_objects==count and attempts>=2
        report = {"started_at":started_at,"provider":"simulator","scenario":"SIGKILL while job leased, restart, backlog drain",
            "backlog":count,"completed":completed,"recovery_seconds":duration,"lease_seconds":30,
            "journal_postings":postings,"provider_objects":provider_objects,"max_attempts":attempts,"invariants":response.json()}
        Path("reports/recovery.json").write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps(report,indent=2))
    finally:
        compose("start", "worker")
        client.close()


if __name__ == "__main__":
    main()

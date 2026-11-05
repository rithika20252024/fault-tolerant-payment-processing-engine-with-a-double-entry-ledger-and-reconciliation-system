"""Closed-loop HTTP benchmark. Simulator only; never load-tests Stripe."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import time
from uuid import uuid4

import httpx


def percentile(values, quantile):
    values = sorted(values)
    if not values:
        return None
    position = (len(values) - 1) * quantile
    low = int(position)
    high = min(low + 1, len(values)-1)
    return values[low] + (values[high] - values[low]) * (position-low)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:18080")
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--workers-label", type=int, required=True)
    parser.add_argument("--output", default="reports/benchmark.json")
    args = parser.parse_args()
    if args.count < 1 or not 1 <= args.concurrency <= 200:
        parser.error("count must be positive; concurrency must be 1..200")
    headers = {"Authorization": "Bearer " + os.getenv("API_TOKEN", "local-demo-token-change-before-sharing")}
    with httpx.Client(base_url=args.url, headers=headers, timeout=30, limits=httpx.Limits(max_connections=250)) as client:
        health = client.get("/health").json()
        if health["provider"] != "simulator":
            raise SystemExit("Refusing to load-test a non-simulator provider")
        run = uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.perf_counter()

        def operation(i):
            begin = time.perf_counter()
            result = {"index": i, "transport_errors": []}

            def request(method, path, **kwargs):
                # Retrying creation is safe only because the original operation
                # keeps its idempotency key. Retrying GET is intrinsically safe.
                for attempt in range(3):
                    try:
                        return client.request(method, path, **kwargs)
                    except httpx.TransportError as exc:
                        result["transport_errors"].append(type(exc).__name__)
                        if attempt == 2:
                            raise
                        time.sleep(.01 * 2**attempt)
            try:
                response = request("POST", "/payments", json={"amount":1000,"currency":"usd"},
                    headers={"Idempotency-Key": f"bench:{run}:{i}"})
                response.raise_for_status()
                p = response.json()
                result["id"] = p["id"]
                result["acceptance_ms"] = (time.perf_counter()-begin)*1000
                deadline = begin + 60
                while time.perf_counter() < deadline:
                    response = request("GET", "/payments/"+p["id"])
                    response.raise_for_status()
                    p = response.json()
                    if p["status"] == "succeeded":
                        created = datetime.fromisoformat(p["created_at"])
                        completed = datetime.fromisoformat(p["completed_at"])
                        result.update(ok=True, completion_ms=(completed-created).total_seconds()*1000,
                            client_observed_ms=(time.perf_counter()-begin)*1000,
                            created_at=p["created_at"], completed_at=p["completed_at"])
                        return result
                    time.sleep(0.025)
                result.update(ok=False, error="completion_timeout")
            except Exception as exc:
                result.update(ok=False, error=type(exc).__name__)
            return result

        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            samples = list(executor.map(operation, range(args.count)))
        duration = time.perf_counter()-started
        audit = client.get("/invariants")
        audit.raise_for_status()
        audit = audit.json()
        stats = client.get("/stats").json()
    successes = [s for s in samples if s["ok"]]
    latencies = [s["completion_ms"] for s in successes]
    report = {"run_id":run,"started_at":started_at,"provider":"simulator",
        "method":"closed-loop HTTP create + polling; throughput includes polling and queueing",
        "environment":{"os":platform.platform(),"architecture":platform.machine(),"cpu_count":os.cpu_count(),
            "python":platform.python_version(),"api_url":args.url,"worker_count_label":args.workers_label},
        "configuration":{"count":args.count,"concurrency":args.concurrency,"poll_interval_ms":25},
        "duration_seconds":duration,"completed":len(successes),"failed":args.count-len(successes),
        "transport_errors":sum(len(s["transport_errors"]) for s in samples),
        "completed_per_second":len(successes)/duration,"completion_p95_ms":percentile(latencies,.95),
        "completion_p99_ms":percentile(latencies,.99),"acceptance_p95_ms":percentile([s["acceptance_ms"] for s in successes],.95),
        "invariants":audit,"database_stats_after_run":stats,"samples":samples}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("completed","failed","duration_seconds","completed_per_second","completion_p95_ms","completion_p99_ms")},indent=2))
    if report["failed"] or not audit["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

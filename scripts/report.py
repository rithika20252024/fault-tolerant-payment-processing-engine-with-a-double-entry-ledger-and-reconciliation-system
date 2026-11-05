"""Render measured results; never invent data for unexecuted checks."""
import json
from pathlib import Path
import re
import statistics


def main():
    root = Path("reports")
    summary_path = root / "scaling-summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else []
    environment = json.loads((root/"environment.json").read_text()) if (root/"environment.json").exists() else {}
    test_file = root/"test-results.txt"
    match = re.search(r"(\d+) passed", test_file.read_text()) if test_file.exists() else None
    test_status = f"{match.group(1)} tests passed" if match else "Final container test results have not been recorded"
    lines = ["# Local experimental results", "", "These are personal-project simulator measurements, not real payment-provider throughput or production operating history.", "",
        "## Correctness", "", test_status + " against local PostgreSQL, including 100 concurrent identical requests, concurrent refund limits, multi-worker claims, provider-response loss, crash boundaries, stale leases, refund recovery, reconciliation, immutable journal enforcement, and Stripe SDK/signature checks. See `test-results.txt` for the final recorded test run.", "",
        "## Environment", "", "```json", json.dumps(environment,indent=2), "```", "",
        "## Closed-loop scaling experiment", "", "Each measured run creates 3,000 payments with concurrency 40 over HTTP, polling at 25 ms. Three alternating runs compare one API instance with one versus four workers. Dataset grows across runs; the database is not reset. The simulator shares PostgreSQL with the engine. Dashboard polling and normal host activity may add background noise.", "",
        "| Workers | Repeat | Completed | Failed | Transport errors | Duration (s) | Completed/s | p95 completion (ms) | p99 completion (ms) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in summary:
        lines.append(f'| {r["workers"]} | {r["repeat"]} | {r["completed"]} | {r["failed"]} | {r["transport_errors"]} | {r["duration_seconds"]:.2f} | {r["completed_per_second"]:.2f} | {r["completion_p95_ms"]:.2f} | {r["completion_p99_ms"]:.2f} |')
    for workers in (1,4):
        rows = [r for r in summary if r["workers"]==workers]
        if rows:
            lines += ["",f'{workers} worker(s): median throughput **{statistics.median(r["completed_per_second"] for r in rows):.2f} completed simulated payments/s** across {len(rows)} runs.']
    lines += ["", "These runs do not establish a maximum sustainable throughput. Adding workers did not produce proportional throughput gains. The host-side client, API, PostgreSQL, shared simulator, and worker queue all remain possible limits. Resource snapshots are individual observations, not profiling proof of a bottleneck. A defensible next experiment would isolate load-generator and API capacity, then inspect database query timing and lock waits.", "",
        "### Preserved initial failure", "", "Before client retries were added, one 3,000-operation run had a `RemoteProtocolError` during polling. All 3,000 server-side payments succeeded and the invariant audit passed, but the client observed 2,999 completions. The original raw report is preserved as `initial-transport-failure.json`. The benchmark now retries transport failures up to three times using the same creation idempotency key and records every transport error. These retries are part of the reported workload, not silently discarded failures.", ""]
    k6 = root/"k6-summary.json"
    if k6.exists():
        data=json.loads(k6.read_text())
        metrics=data.get("metrics",{})
        def metric(name):
            value=metrics.get(name,{})
            return value.get("values",value)
        lines += ["## Sustained arrival-rate test", "", "k6 simulator workload: 100 offered payment operations/s for 60 seconds, using one API and one worker. Each operation polls until success. Consult `k6-output.txt` and `k6-summary.json` for checks, dropped iterations, error rates, and threshold results.", "", "```json", json.dumps({name:metric(name) for name in ["payments_completed","payment_completion_ms","payment_completion_failures","http_req_failed","dropped_iterations"]},indent=2), "```", ""]
    else:
        lines += ["## Sustained arrival-rate test", "", "Not executed yet. The k6 script is provided; no achieved threshold is claimed.", ""]
    recovery=root/"recovery.json"
    if recovery.exists():
        data=json.loads(recovery.read_text())
        lines += ["## Real worker termination", "", "```json",json.dumps(data,indent=2),"```", "", "The drill sends SIGKILL while a job is leased, restarts the worker, and drains a 100-payment backlog. Recovery includes the 30-second lease expiry; shortening that lease trades faster recovery for more concurrent duplicate attempts. This is a process failure on one local host, not a database or regional outage.", ""]
    else:
        lines += ["## Real worker termination", "", "Not executed yet. Unit/integration tests cover crash boundaries, but no live process-kill result is claimed.", ""]
    restore_file = root/"restore-output.txt"
    restore_ok = restore_file.exists() and '"ok": true' in restore_file.read_text()
    restore_status = "Executed successfully; the restored database passed the semantic invariant audit." if restore_ok else "A successful restore audit has not been recorded."
    cloud_file = root/"railway-deployment.json"
    cloud = json.loads(cloud_file.read_text()) if cloud_file.exists() else None
    stripe_status = "Pending an account-level run. Offline SDK tests alone do not establish a successful sandbox transaction."
    if cloud and cloud.get("stripe_sandbox",{}).get("refund_status")=="succeeded":
        stripe_status = "An actual Stripe sandbox payment and partial refund succeeded against the deployed Railway API. The ledger audit passed. See `railway-deployment.json` for timestamps, service deployment IDs, and test operation IDs."
        if cloud.get("webhook_delivery_verified"):
            stripe_status += " Real Stripe Dashboard webhook delivery was also verified without manual synchronization: the event updated the payment automatically, a signed duplicate was suppressed, and exactly one payment journal posting remained. See `stripe-webhook-verification.json`."
        else:
            stripe_status += " Webhook delivery remains unverified until the dashboard endpoint signing secret is configured."
    lines += ["## Backup restoration", "", restore_status + " See `restore-output.txt` for the drill output. The script restores into a separate database and runs semantic ledger invariants. The active database is preserved. This checks snapshot restoration, not point-in-time recovery or discovery of provider operations missing from an old snapshot.", "",
        "## Stripe account verification", "", stripe_status + " Follow `docs/stripe-development.md` for setup.", "",
        "## Scope of the evidence", "", "The runs are short local experiments. They do not establish multi-day reliability, real users, high availability, production payment volume, or employment experience. The GitHub Actions workflow is supplied but has not been run in a connected repository.", ""]
    (root/"RESULTS.md").write_text("\n".join(lines))
    print("Wrote reports/RESULTS.md")


if __name__=="__main__":
    main()

# Local experimental results

These are personal-project simulator measurements, not real payment-provider throughput or production operating history.

## Correctness

35 tests passed against local PostgreSQL, including 100 concurrent identical requests, concurrent refund limits, multi-worker claims, provider-response loss, crash boundaries, stale leases, refund recovery, reconciliation, immutable journal enforcement, and Stripe SDK/signature checks. See `test-results.txt` for the final recorded test run.

## Environment

```json
{
  "started_at": "2026-11-04T15:41:48.611841+00:00",
  "host": "macOS-26.5.2-arm64-arm-64bit-Mach-O",
  "docker": {
    "ServerVersion": "29.8.0",
    "NCPU": 10,
    "MemTotal": 8319504384,
    "Architecture": "aarch64",
    "OperatingSystem": "Docker Desktop"
  },
  "source_sha256": {
    "payments/__init__.py": "1cf5635b5520f94b371c6aae0e27ca503d76c9ce6d8d3367471690cef9ac33f9",
    "payments/api.py": "5edfc017b6cbbab887a1ec5e0744db87b7d2efb534bf8314d952d0ef20a7b071",
    "payments/db.py": "e19ffbf5cbfcd34e056b43671003bdd1e992e187de37c6f814ad29c203dc3c4a",
    "payments/engine.py": "7567dda236831b62e85e905e023a2c403caf0176b91b3b479f345303d63d69cb",
    "payments/migrate.py": "7c45d98622368488b061f7e93d1cd62c5e30d924ae050a94fda6905e95b4770e",
    "payments/providers.py": "544b9a070256a7ba52543bb712a22c336956222d0d53212f1174cfbd1e201496",
    "payments/schema.sql": "47c7fb9b172184c65b2ba8b07fa4fa6582c4e1f0b362d8214a1390fc3ed37abf",
    "payments/worker.py": "3be6f097f6020a4925f45c72dce74cb4530fbb99b729ea53e0e40e8f757fe26c"
  },
  "runtime_versions": {
    "api": "Python 3.13.18",
    "database": "postgres (PostgreSQL) 17.11",
    "k6": "2.3.0"
  }
}
```

## Closed-loop scaling experiment

Each measured run creates 3,000 payments with concurrency 40 over HTTP, polling at 25 ms. Three alternating runs compare one API instance with one versus four workers. Dataset grows across runs; the database is not reset. The simulator shares PostgreSQL with the engine. Dashboard polling and normal host activity may add background noise.

| Workers | Repeat | Completed | Failed | Transport errors | Duration (s) | Completed/s | p95 completion (ms) | p99 completion (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1 | 3000 | 0 | 0 | 20.26 | 148.11 | 135.01 | 219.32 |
| 4 | 1 | 3000 | 0 | 0 | 19.89 | 150.82 | 99.02 | 172.08 |
| 1 | 2 | 3000 | 0 | 0 | 15.75 | 190.44 | 110.47 | 145.30 |
| 4 | 2 | 3000 | 0 | 0 | 16.99 | 176.59 | 96.81 | 104.41 |
| 1 | 3 | 3000 | 0 | 0 | 18.23 | 164.52 | 132.08 | 180.94 |
| 4 | 3 | 3000 | 0 | 0 | 15.73 | 190.69 | 95.90 | 101.69 |

1 worker(s): median throughput **164.52 completed simulated payments/s** across 3 runs.

4 worker(s): median throughput **176.59 completed simulated payments/s** across 3 runs.

These runs do not establish a maximum sustainable throughput. Adding workers did not produce proportional throughput gains. The host-side client, API, PostgreSQL, shared simulator, and worker queue all remain possible limits. Resource snapshots are individual observations, not profiling proof of a bottleneck. A defensible next experiment would isolate load-generator and API capacity, then inspect database query timing and lock waits.

### Preserved initial failure

Before client retries were added, one 3,000-operation run had a `RemoteProtocolError` during polling. All 3,000 server-side payments succeeded and the invariant audit passed, but the client observed 2,999 completions. The original raw report is preserved as `initial-transport-failure.json`. The benchmark now retries transport failures up to three times using the same creation idempotency key and records every transport error. These retries are part of the reported workload, not silently discarded failures.

## Sustained arrival-rate test

k6 simulator workload: 100 offered payment operations/s for 60 seconds, using one API and one worker. Each operation polls until success. Consult `k6-output.txt` and `k6-summary.json` for checks, dropped iterations, error rates, and threshold results.

```json
{
  "payments_completed": {
    "count": 6001,
    "rate": 99.44345099729863
  },
  "payment_completion_ms": {
    "avg": 53.59523412764539,
    "min": 2,
    "med": 54,
    "max": 116,
    "p(90)": 95,
    "p(95)": 99,
    "thresholds": {
      "p(95)<2000": false
    }
  },
  "payment_completion_failures": {
    "passes": 0,
    "fails": 6001,
    "thresholds": {
      "rate<0.01": false
    },
    "value": 0
  },
  "http_req_failed": {
    "passes": 0,
    "fails": 18004,
    "thresholds": {
      "rate<0.01": false
    },
    "value": 0
  },
  "dropped_iterations": {
    "count": 0,
    "rate": 0,
    "thresholds": {
      "count==0": false
    }
  }
}
```

## Real worker termination

```json
{
  "started_at": "2026-11-04T15:45:45.806831+00:00",
  "provider": "simulator",
  "scenario": "SIGKILL while job leased, restart, backlog drain",
  "backlog": 100,
  "completed": 100,
  "recovery_seconds": 29.96349504200043,
  "lease_seconds": 30,
  "journal_postings": 100,
  "provider_objects": 100,
  "max_attempts": 2,
  "invariants": {
    "ok": true,
    "failures": {
      "unbalanced": [],
      "missing_payments": [],
      "refund_reservations": [],
      "missing_refunds": [],
      "invalid_postings": []
    }
  }
}
```

The drill sends SIGKILL while a job is leased, restarts the worker, and drains a 100-payment backlog. Recovery includes the 30-second lease expiry; shortening that lease trades faster recovery for more concurrent duplicate attempts. This is a process failure on one local host, not a database or regional outage.

## Backup restoration

Executed successfully; the restored database passed the semantic invariant audit. See `restore-output.txt` for the drill output. The script restores into a separate database and runs semantic ledger invariants. The active database is preserved. This checks snapshot restoration, not point-in-time recovery or discovery of provider operations missing from an old snapshot.

## Stripe account verification

An actual Stripe sandbox payment and partial refund succeeded against the deployed Railway API. The ledger audit passed. See `railway-deployment.json` for timestamps, service deployment IDs, and test operation IDs. Real Stripe Dashboard webhook delivery was also verified without manual synchronization: the event updated the payment automatically, a signed duplicate was suppressed, and exactly one payment journal posting remained. See `stripe-webhook-verification.json`. Follow `docs/stripe-development.md` for setup.

## Scope of the evidence

The runs are short local experiments. They do not establish multi-day reliability, real users, high availability, production payment volume, or employment experience. The GitHub Actions correctness workflow is enabled in the connected repository; its initial remote run passed.

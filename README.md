# Payment Reliability Lab

A personal engineering project for studying payment correctness under concurrency, partial failure, and load. Python/FastAPI, PostgreSQL, durable workers, a controllable provider simulator, and a Stripe sandbox adapter. This project does not move real money.

**Live application:** [Payment Reliability Lab on Railway](https://api-production-0213.up.railway.app/) · [Live API reference](https://api-production-0213.up.railway.app/docs)

The live dashboard uses Stripe sandbox mode and requires the owner's API token for protected operations.

## Start locally

Prerequisite: Docker Desktop with Compose. From this directory:

```sh
cp -n .env.example .env
docker compose up -d --build
```

Open <http://localhost:18080> and enter the `API_TOKEN` from `.env`. The API reference is <http://localhost:18080/docs>. The default development token is `local-demo-token-change-before-sharing`. Both exposed ports bind only to localhost.

```sh
docker compose run --rm api python -m pytest -q
docker compose logs --tail 100 api worker
docker compose up -d --scale worker=4
docker compose down                 # Keeps the database volume
```

Docker is sufficient for the application and tests. To run host-side benchmark and recovery scripts, install Python 3.13+ and create a local virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
```

Host-side scripts use the default local token unless you export `API_TOKEN`. They use localhost port 55432 for PostgreSQL. For a custom environment, export the corresponding values yourself; scripts do not automatically load `.env`.

## Try a payment and refund

```sh
export API_TOKEN=local-demo-token-change-before-sharing
curl -s http://localhost:18080/payments \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: example-payment-1' \
  -d '{"amount":1500,"currency":"usd"}'
```

The API responds `202` after committing the payment and its job. The worker subsequently creates the provider operation and posts the ledger transaction. Poll `GET /payments/{id}` for completion. Reusing the key and same parameters returns the same payment; changed parameters return `409`.

```sh
curl -s http://localhost:18080/payments/PAYMENT_ID/refunds \
  -H "Authorization: Bearer $API_TOKEN" \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: example-refund-1' \
  -d '{"amount":400}'
curl -s http://localhost:18080/invariants -H "Authorization: Bearer $API_TOKEN"
```

Amounts are integer minor units. Supported currencies are USD, INR, EUR, and GBP. This is a single-owner lab, with no tenant or merchant isolation. Provider fees, settlement, disputes, chargebacks, FX, and payouts are outside its accounting model.

## What is implemented

- Payment creation with durable request idempotency and an atomic transactional outbox.
- Refund reservation under a payment-row lock, preventing concurrent over-refunding.
- A constrained journal representation containing both sides of each posting, plus a conventional debit/credit view.
- Database triggers rejecting journal updates, deletion, and truncation.
- Leased jobs claimed using `FOR UPDATE SKIP LOCKED`, bounded exponential retry, failed-job inspection, and explicit replay.
- Fenced job acknowledgments: a worker with an expired lease cannot acknowledge a newer worker's lease.
- Signed, time-limited webhook ingestion; durable inbox and duplicate-event suppression.
- Canonical provider-state retrieval to handle stale or reordered notifications.
- Reconciliation with keyset pagination, recorded findings, and repair of supported missing transitions.
- Simulator faults for unavailable provider, accepted request with lost response, pending operation, and decline.
- Sandbox-only Stripe PaymentIntents and refunds, with an executable sandbox smoke script.
- An operator dashboard, Prometheus-format metrics, load-test scripts, real worker-kill experiment, and backup/restore scripts.

## Evidence and learning

- [Architecture and tradeoffs](docs/architecture.md)
- [Stripe development and sandbox setup](docs/stripe-development.md)
- [Operations and recovery](docs/runbook.md)
- [Railway deployment](docs/railway.md)
- [Evidence and claim boundaries](docs/evidence.md)
- [Measured local results](reports/RESULTS.md)

### Reproduce performance measurements

```sh
docker compose up -d --scale worker=1
.venv/bin/python scripts/benchmark.py --workers-label 1 --count 300 --output reports/one-worker.json
docker compose up -d --scale worker=4
.venv/bin/python scripts/benchmark.py --workers-label 4 --count 300 --output reports/four-workers.json
```

`--workers-label` records your declared topology; the script does not provision workers. The Python benchmark is closed-loop: it creates a payment, polls until success, and measures complete business operations. Raw per-payment samples are retained. Results apply to this simulator, host, dataset, concurrency, and run length.

An open-loop k6 test is in `benchmarks/payments.js` (the recorded experiment used Grafana k6 2.3.0):

```sh
API_TOKEN="$API_TOKEN" RATE=100 DURATION=60s k6 run --summary-export reports/k6-summary.json benchmarks/payments.js
```

The k6 thresholds define experiment targets; see the measured results report for executed runs. Increase `RATE` and `DURATION` deliberately. The Python benchmark records bounded transport retries and preserves each operation’s idempotency key. Both benchmark tools refuse to run against a Stripe-configured API. Do not load-test Stripe's sandbox.

### Reproduce failure and restore experiments

```sh
.venv/bin/python scripts/recovery_drill.py
bash scripts/backup.sh
bash scripts/restore_drill.sh
```

The recovery drill stops and kills **this Compose project's workers**, enqueues 100 simulated payments, restarts workers, waits for lease recovery, and checks for duplicate provider operations and ledger postings. Run it when no other experiment is using these workers. The restore drill creates a new database and does not overwrite the active one.

## Project status

Local correctness and deployment results are recorded in `reports/RESULTS.md`. Railway account-level verification is recorded in `reports/railway-deployment.json`. Stripe account tests are distinguished from offline SDK checks. The [GitHub Actions correctness workflow](https://github.com/rithika20252024/fault-tolerant-payment-processing-engine-with-a-double-entry-ledger-and-reconciliation-system/actions/workflows/test.yml) runs on pushes and pull requests; its initial remote run passed. Long-duration operating history is not claimed by this repository.

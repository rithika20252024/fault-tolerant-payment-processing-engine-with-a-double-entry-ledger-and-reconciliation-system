# Operations runbook

## Inspect the instance

Use the dashboard or authenticated `GET /stats`, `/jobs`, `/ledger`, `/invariants`, and `/metrics`. `/health` checks database connectivity but does not assert provider reachability, worker liveness, or absence of dead jobs.

Suggested alert conditions for a future monitoring integration:

- Any failed ledger invariant: investigate immediately; stop introducing new writes during diagnosis.
- Dead jobs greater than zero: inspect the exception category and provider outcome.
- Oldest ready/running job older than 60 seconds under normal low load: verify worker availability and provider health.
- Increasing queue age and completion latency: reduce offered load and inspect database/provider constraints.

The repository exposes metrics; it does not configure an external monitoring service or notifications.

```sh
docker compose ps
docker compose logs --tail 100 worker api
docker compose exec -T db psql -U payments -d payments -c \
  "SELECT status,count(*) FROM jobs GROUP BY status;"
```

## Failed jobs

`GET /jobs` shows the newest 100 unfinished jobs. `POST /jobs/{job_id}/replay` requeues only dead jobs and resets their retry budget. Inspect the provider before replaying ambiguous operations. The same operation ID remains the provider idempotency key. Operations older than the safe retry window still require review after replay.

Exception messages from providers are not stored because they may contain sensitive response data. The job stores the exception category; review-required errors use fixed internal codes. Investigate provider IDs in your own sandbox dashboard.

## Missing events and reconciliation

Call `POST /reconcile?limit=100`. Continue using `after=next_cursor` until `next_cursor` is null. The dashboard's reconciliation button follows this pagination automatically. New payments inserted during a scan may fall before its cursor and are covered by the next complete run.

Known transitions can be repaired. Unmanaged refunds and identity mismatches require investigation. Reconciliation is not a substitute for backups and does not recreate all local records from an external account.

## Fault injection

Faults are available only in simulator mode with `ENABLE_FAULTS=true`. Stop workers before creating and configuring a payment to avoid a race:

```sh
docker compose stop worker
# Create a payment through the API and copy its internal pay_… ID.
curl -s http://localhost:18080/simulator/faults \
  -H "Authorization: Bearer $API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"operation_key":"PAYMENT_ID","mode":"timeout_after_commit","remaining":1}'
docker compose start worker
```

Configure the environment before recreating API/worker containers if needed. Supported modes are `unavailable`, `timeout_after_commit`, `pending`, and `decline`. The fault counter is durable, so an injected once-only failure is not repeated after a restart.

The recovery drill directly configures an internal simulator fault in the local database and kills only this project's worker containers. It is intended for an isolated local experiment, not simultaneous shared use.

## Backup and restore

```sh
bash scripts/backup.sh
bash scripts/restore_drill.sh
```

The dump is created with restrictive file permissions in ignored `work/`. It can contain provider event data; keep it local. The restore drill creates a separately named database, restores the dump, and prints journal counts and per-currency balance checks. A full recovery verification should also run the engine's semantic invariant audit against that restored database.

For a real provider integration, after restoring an old snapshot, reconcile known records and investigate operations that occurred after the backup. This project has no automated point-in-time recovery, provider-wide discovery, or recovery of lost local idempotency keys. Simulator state lives in the same database server, so it cannot independently reconstruct state lost from a backup.

## Stop and clean up

`docker compose down` stops this project's services and retains data. Do not delete the volume unless you intentionally want to discard all local records. Restore drill databases remain available for inspection; remove a specific drill database manually after review.

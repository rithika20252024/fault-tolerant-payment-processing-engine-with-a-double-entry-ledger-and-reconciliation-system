# Railway deployment

Use three services in a dedicated Railway project:

- `Postgres`: Railway PostgreSQL, reached using private networking.
- `api`: Dockerfile source, `SERVICE_ROLE=api`, public HTTPS domain.
- `worker`: same source, `SERVICE_ROLE=worker`, no public domain.

The deployment process copies `railway.api.json` or `railway.worker.json` to `railway.json` in a clean upload directory. This lets the API have an HTTP health check without applying that check to the background worker. Only runtime source and locked dependencies are uploaded; local `.env`, dumps, benchmarks, and reports are excluded.

Both roles execute `python -m payments.start`. It runs the versioned migration under a PostgreSQL advisory lock before starting the selected process. The API binds to Railway's `PORT` on `0.0.0.0`. Concurrent starts cannot apply the migration twice.

Required private service variables:

```text
DATABASE_URL=${{Postgres.DATABASE_URL}}
SERVICE_ROLE=api                       # worker for the worker service
PROVIDER=stripe
API_TOKEN=<generated private token, same on both services>
STRIPE_SECRET_KEY=<sandbox sk_test_ key>
STRIPE_WEBHOOK_SECRET=<dashboard endpoint whsec_ secret, once created>
ENABLE_FAULTS=false
```

The deployment helper transfers secrets via CLI stdin, rather than placing their values in command arguments or source uploads. Railway startup rejects the public development API token, missing database configuration, or enabled fault injection. HTTP responses prohibit caching.

After the API is healthy, create a Railway-generated HTTPS domain for **api only**. The Stripe Dashboard endpoint URL is:

```text
https://<actual-api-domain>.up.railway.app/webhooks/stripe
```

Select sandbox events and Snapshot payloads as described in `stripe-development.md`. Copy that endpoint's signing secret into `STRIPE_WEBHOOK_SECRET` on the API service, then deploy the variable change. Until the signing secret is configured, the webhook returns HTTP 503; it never accepts unsigned events.

The deployed dashboard requires the generated API token. It is stored in a local owner-readable credentials file during deployment and is not published in the source archive or this guide. The local development token does not work on Railway.

Railway services consume the account's configured credits/resources. No live Stripe keys are supported. Use `scripts/stripe_smoke.py` against the deployed API to verify a small sandbox payment and refund; never run the load benchmarks against Stripe or the public deployment.

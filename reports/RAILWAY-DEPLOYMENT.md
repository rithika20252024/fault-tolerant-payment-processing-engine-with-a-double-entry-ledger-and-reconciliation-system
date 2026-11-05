# Railway deployment verification

Public application: https://api-production-0213.up.railway.app

Stripe Dashboard endpoint URL:

```text
https://api-production-0213.up.railway.app/webhooks/stripe
```

The PostgreSQL service, API, and private worker are deployed in the dedicated `payment-reliability-lab` project. Both application services reached successful deployment status. The API passed public health checks, rejected unauthenticated access and the local development token, accepted the generated private token, and returned non-cacheable authenticated responses.

An actual Stripe sandbox payment and partial refund succeeded through the public API and worker. The ledger invariants passed. Exact timestamps, deployment IDs, and operation identifiers are in `railway-deployment.json`.

The current test suite passes **42 tests**. Account testing exposed two compatibility differences that offline dictionary mocks did not cover: dashboard-managed payment methods on the current Stripe API, and Stripe SDK response objects without dictionary `.get`. The adapter now omits the legacy request field and normalizes actual SDK objects. A real-object regression test was added.

Initial failed diagnostic jobs are preserved separately in `railway-diagnostic-attempts.json`. An idempotency error on an old request must be reviewed rather than retried under a new operation key. Those diagnostic attempts are not counted as successful payments or silently deleted.

## Webhook configuration (completed)

1. In the Stripe sandbox Dashboard, create the destination using the exact endpoint URL above.
2. Select Snapshot events listed in `docs/stripe-development.md`.
3. Reveal that destination's `whsec_` signing secret.
4. In Railway, open **api → Variables** and set `STRIPE_WEBHOOK_SECRET` to that value.
5. Apply/deploy the variable change.

The signing secret is configured on Railway's API service. A separate account-level test confirmed that an actual Stripe Dashboard event updated its payment automatically, without `/sync` or reconciliation. A signed replay of the same event was identified as a duplicate, exactly one payment posting remained, and all ledger invariants passed. The event identifier and timestamp are recorded in `stripe-webhook-verification.json`. Unsigned requests are rejected with HTTP 400.

The generated dashboard bearer token is stored locally in `.env.railway` with owner-only permissions. It is excluded from the source archive and version control. The local development token does not grant access to the public instance.

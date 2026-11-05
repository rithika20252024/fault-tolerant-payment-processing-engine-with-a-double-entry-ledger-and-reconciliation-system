# Stripe sandbox development

The adapter uses Stripe's official Python SDK, pinned in `requirements.lock.txt`. It implements PaymentIntent creation, retrieval, refunds, refund pagination, metadata correlation, stable idempotency keys, and signature verification using `stripe.Webhook.construct_event`.

PaymentIntents are used here to expose the payment state machine for study. For a conventional customer checkout, Stripe recommends its Checkout Sessions integration for most use cases. This project does not implement a customer-facing checkout or browser authentication flow.

## Configure credentials locally

1. Use your Stripe sandbox/test-mode dashboard to obtain a test secret key.
2. Edit the project `.env` file. It is excluded from version control and Docker build context.

```dotenv
PROVIDER=stripe
STRIPE_SECRET_KEY=sk_test_your_sandbox_key
STRIPE_WEBHOOK_SECRET=
```

Only `sk_test_` keys are accepted. Never paste keys into chat, commit them, or use live keys. Set a custom `API_TOKEN` before sharing any instance.

Alternatively, run `python3 scripts/configure_stripe.py` in your own terminal. It prompts without echoing credentials, saves `.env` with owner-only permissions, and does not print either key.

## Start and forward webhooks

Install Stripe CLI using [Stripe's installation instructions](https://docs.stripe.com/stripe-cli). Authenticate the CLI with your sandbox account:

```sh
stripe login
stripe listen --events payment_intent.succeeded,payment_intent.processing,payment_intent.payment_failed,payment_intent.canceled,refund.created,refund.updated,refund.failed,charge.refunded \
  --forward-to localhost:18080/webhooks/stripe
```

The CLI prints a `whsec_…` signing secret. Put it in `STRIPE_WEBHOOK_SECRET` in `.env`. The CLI listener's secret is different from a registered dashboard endpoint's secret. Then recreate the services so they read the changed environment:

```sh
docker compose up -d --build --force-recreate api worker
```

The worker creates an **unconfirmed** PaymentIntent; it does not collect raw card details. The smoke script confirms using Stripe's `pm_card_visa` test PaymentMethod. Production card collection and additional authentication are outside this lab.

Payment methods are managed by the Stripe sandbox Dashboard. The adapter deliberately omits the legacy `payment_method_types` field. The smoke confirmation supplies a return URL for compatibility with dashboard-enabled redirect payment methods.

## Execute the account-level smoke test

The API and worker read Compose `.env`; the smoke script runs inside that configured container:

```sh
docker compose exec -T -e BASE_URL=http://localhost:8000 api python scripts/stripe_smoke.py
```

The script creates a 1000-minor-unit USD test payment, repeats the creation key, confirms its PaymentIntent, synchronizes state, requests a 400-minor-unit refund, waits for success, and audits the ledger. It prints internal/provider identifiers and statuses without printing secrets.

For an end-to-end Dashboard webhook test, `scripts/stripe_webhook_smoke.py` confirms a sandbox payment and waits for the webhook to update the engine, without calling `/sync` or reconciliation. It then verifies that a matching signed duplicate is suppressed and that exactly one payment journal posting exists. Run it with `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `API_TOKEN`, and `BASE_URL` configured locally. Recorded Railway results are in `reports/stripe-webhook-verification.json`.

Explicit synchronization makes this smoke test useful even without a listener. It **does not prove webhook delivery**. To verify the webhook path separately:

1. Create a new engine payment with the API and wait until it has a provider ID.
2. Confirm that PaymentIntent using the Stripe CLI with `pm_card_visa`.
3. Do not call `/sync` or reconciliation.
4. Observe the payment become `succeeded` and the listener show a successful webhook response.
5. Resend the same event and verify that only one journal transaction exists for the payment.

Record the real event IDs, test time, and result in a local report, excluding payloads and keys. The CLI forwarding target is unauthenticated by bearer token; its authentication is the Stripe signature.

## Scenarios to test in your account

| Scenario | Expected behavior |
|---|---|
| Successful card | One success posting |
| Declined card | No success posting; state remains available for another payment method |
| Additional authentication | `requires_action`; no premature success posting |
| Repeated creation key | Same local payment and provider operation |
| Partial refund | One reversal posting for the refund amount |
| Duplicate webhook | Durable inbox deduplication; no extra posting |
| Missing webhook | Explicit sync or reconciliation repairs local state |
| External dashboard refund | Reconciliation reports unmanaged refund for review |
| Bad/expired signature | HTTP 400; nothing enqueued |

Consult the test PaymentMethods in [Stripe's testing guide](https://docs.stripe.com/testing). The smoke script currently exercises success and partial refund; the other account scenarios require separate execution. SDK-mocked tests cover parameter mapping, and locally signed events cover the real SDK verification code. Neither substitutes for account execution.

## Important development boundaries

- Do not run k6 or the Python load benchmark against Stripe. Stripe explicitly discourages sandbox load testing; both project benchmark scripts guard against it.
- Store one local operation ID in provider metadata and use that ID as the idempotency key. Never generate a new key when retrying the same operation.
- Webhook order is not trusted. Workers retrieve current provider state.
- A timeout does not prove payment failure. Preserve the operation and resolve the provider outcome.
- Refunds can remain pending or fail. Reservations are released only on confirmed failure/cancellation.
- Refund fees, disputes, settlement, capture, subscriptions, Connect accounts, and live payments are not supported.

## Official sources consulted

- [PaymentIntents](https://docs.stripe.com/payments/payment-intents)
- [Idempotent requests](https://docs.stripe.com/api/idempotent_requests)
- [Webhooks](https://docs.stripe.com/webhooks)
- [Create a refund](https://docs.stripe.com/api/refunds/create)
- [Testing and load-testing guidance](https://docs.stripe.com/testing)

"""Small real Stripe sandbox exercise; no card data or secrets printed."""
import json
import os
import time
from uuid import uuid4

import httpx
import stripe


def main():
    secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret.startswith("sk_test_"):
        raise SystemExit("Set STRIPE_SECRET_KEY to a sandbox key")
    provider = stripe.StripeClient(secret, max_network_retries=2, http_client=stripe.RequestsClient(timeout=10))
    token = os.getenv("API_TOKEN", "local-demo-token-change-before-sharing")
    url = os.getenv("BASE_URL", "http://localhost:8000")
    headers = {"Authorization":"Bearer "+token}
    with httpx.Client(base_url=url,headers=headers,timeout=30) as client:
        if client.get("/health").json().get("provider") != "stripe":
            raise SystemExit("Run the API and worker with PROVIDER=stripe first")
        key = "stripe-smoke:"+uuid4().hex
        response = client.post("/payments",json={"amount":1000,"currency":"usd"},headers={"Idempotency-Key":key})
        response.raise_for_status()
        payment = response.json()
        same = client.post("/payments",json={"amount":1000,"currency":"usd"},headers={"Idempotency-Key":key})
        same.raise_for_status()
        assert same.json()["id"] == payment["id"]
        deadline = time.monotonic()+60
        while time.monotonic()<deadline:
            payment = client.get("/payments/"+payment["id"]).json()
            if payment["provider_id"]:
                break
            time.sleep(.25)
        else:
            raise SystemExit("Worker did not create PaymentIntent; inspect /jobs")
        # Stripe-provided test PaymentMethod; no raw card handling.
        intent = provider.v1.payment_intents.confirm(payment["provider_id"],
            {"payment_method":"pm_card_visa", "return_url":url+"/"}, options={"idempotency_key":key+":confirm"})
        assert intent["livemode"] is False
        assert intent["status"] == "succeeded"
        response = client.post(f'/payments/{payment["id"]}/sync')
        response.raise_for_status()
        payment = client.get("/payments/"+payment["id"]).json()
        assert payment["status"] == "succeeded"
        response = client.post(f'/payments/{payment["id"]}/refunds',json={"amount":400},
            headers={"Idempotency-Key":key+":refund"})
        response.raise_for_status()
        refund_id = response.json()["id"]
        deadline = time.monotonic()+60
        while time.monotonic()<deadline:
            response = client.post(f'/payments/{payment["id"]}/sync')
            response.raise_for_status()
            payment = client.get("/payments/"+payment["id"]).json()
            refund = next(r for r in payment["refunds"] if r["id"]==refund_id)
            if refund["status"]=="succeeded":
                break
            if refund["status"] in {"failed","canceled"}:
                raise SystemExit("Sandbox refund failed")
            time.sleep(.5)
        else:
            raise SystemExit("Refund still pending; inspect Stripe sandbox and /jobs")
        audit = client.get("/invariants")
        audit.raise_for_status()
        assert audit.json()["ok"]
        print(json.dumps({"provider":"stripe_sandbox","payment_id":payment["id"],"provider_id":payment["provider_id"],
            "payment_status":payment["status"],"refund_status":refund["status"],"invariants_ok":True,
            "note":"This run uses explicit sync; separately verify CLI-delivered webhooks."},indent=2))


if __name__ == "__main__":
    main()

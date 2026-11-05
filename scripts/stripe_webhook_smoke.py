"""Verify real Dashboard webhook delivery, then signed duplicate suppression.

No /sync or /reconcile calls are made by this test.
"""
import hashlib
import hmac
import json
import os
import time
from uuid import uuid4

import httpx
import stripe


def main():
    key=os.environ.get("STRIPE_SECRET_KEY", "")
    signing=os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    token=os.environ.get("API_TOKEN", "")
    base=os.environ.get("BASE_URL", "http://localhost:18080").rstrip("/")
    if not key.startswith("sk_test_") or not signing.startswith("whsec_") or not token:
        raise SystemExit("Configure the sandbox API key, webhook signing secret, and application API token")
    provider=stripe.StripeClient(key,max_network_retries=2,http_client=stripe.RequestsClient(timeout=10))
    started=int(time.time())-5
    operation="webhook-smoke:"+uuid4().hex
    with httpx.Client(base_url=base,headers={"Authorization":"Bearer "+token},timeout=20) as client:
        assert client.get("/health").json()["provider"]=="stripe"
        response=client.post("/payments",json={"amount":1000,"currency":"usd"},
            headers={"Idempotency-Key":operation})
        response.raise_for_status()
        payment_id=response.json()["id"]
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            response=client.get("/payments/"+payment_id)
            response.raise_for_status()
            payment=response.json()
            if payment["provider_id"]:
                break
            time.sleep(.5)
        else:
            raise SystemExit("Worker did not create the sandbox PaymentIntent")
        intent=provider.v1.payment_intents.confirm(payment["provider_id"],
            {"payment_method":"pm_card_visa","return_url":base+"/"},
            options={"idempotency_key":operation+":confirm"})
        assert intent["status"]=="succeeded" and intent["livemode"] is False
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            response=client.get("/payments/"+payment_id)
            response.raise_for_status()
            payment=response.json()
            if payment["status"]=="succeeded":
                break
            time.sleep(.5)
        else:
            raise SystemExit("Stripe payment succeeded but the app did not receive/process its webhook")
        events=provider.v1.events.list({"type":"payment_intent.succeeded","created":{"gte":started},"limit":100})
        event_id=None
        for event in events.auto_paging_iter():
            event=event.to_dict()
            if event["data"]["object"]["id"]==payment["provider_id"]:
                event_id=event["id"]
                break
        if not event_id:
            raise SystemExit("Could not locate the matching Stripe success event")
        # A minimal signed duplicate avoids copying provider event payload data.
        duplicate={"id":event_id,"object":"event","type":"payment_intent.succeeded","livemode":False,
            "data":{"object":{"id":payment["provider_id"],"metadata":{"engine_payment_id":payment_id}}}}
        raw=json.dumps(duplicate).encode()
        stamp=str(int(time.time()))
        signature=hmac.new(signing.encode(),stamp.encode()+b"."+raw,hashlib.sha256).hexdigest()
        response=client.post("/webhooks/stripe",content=raw,
            headers={"stripe-signature":f"t={stamp},v1={signature}"})
        response.raise_for_status()
        assert response.json()["duplicate"] is True, "Matching real Stripe event was not already in the durable inbox"
        ledger=client.get("/ledger?limit=100")
        ledger.raise_for_status()
        postings=[row for row in ledger.json() if row["operation_id"]==payment_id]
        assert len(postings)==1
        audit=client.get("/invariants")
        audit.raise_for_status()
        assert audit.json()["ok"]
        print(json.dumps({"provider":"stripe_sandbox","payment_id":payment_id,"provider_id":payment["provider_id"],
            "event_id":event_id,"payment_status":"succeeded","webhook_delivery_verified":True,
            "duplicate_suppressed":True,"payment_journal_postings":len(postings),"invariants_ok":True,
            "manual_sync_used":False},indent=2))


if __name__=="__main__":
    main()

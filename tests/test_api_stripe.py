import hashlib
import hmac
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import stripe
from fastapi.testclient import TestClient

from payments.api import create_app
from payments.engine import Engine
from payments.providers import StripeSandbox


def stripe_signature(raw, secret, timestamp=None):
    timestamp = timestamp or int(time.time())
    sig = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"


def test_api_auth_validation_and_dashboard(engine, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "test-token")
    headers = {"Authorization": "Bearer test-token", "Idempotency-Key": "api"}
    with TestClient(create_app(engine)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/stats").status_code == 401
        assert client.post("/payments", json={"amount": True}, headers=headers).status_code == 422
        assert client.post("/payments", json={"amount": 1000}, headers=headers).status_code == 202
        assert client.post("/payments", json={"amount": 2000}, headers=headers).status_code == 409
        assert client.get("/payments/recent/list", headers=headers).status_code == 200
        assert client.get("/invariants", headers=headers).json()["ok"]
        assert client.get("/metrics", headers=headers).status_code == 200
        assert client.post("/simulator/faults", headers=headers, json={"operation_key":"x","mode":"decline"}).status_code == 403


def test_stripe_adapter_parameter_mapping_and_idempotency():
    client = Mock()
    client.v1.payment_intents.create.return_value = {"id":"pi_test", "amount":1000,"currency":"usd",
        "status":"requires_payment_method","metadata":{"engine_payment_id":"pay_1"}}
    adapter = StripeSandbox("sk_test_fake", client=client)
    p = {"id":"pay_1","amount":1000,"currency":"usd","provider_id":"pi_test"}
    result = adapter.create_payment(p)
    assert result["payment_id"] == "pay_1"
    args, kwargs = client.v1.payment_intents.create.call_args
    assert kwargs["options"]["idempotency_key"] == "pay_1"
    assert args[0]["metadata"] == {"engine_payment_id":"pay_1"}
    assert "payment_method_types" not in args[0]
    client.v1.refunds.create.return_value = {"id":"re_test", "amount":500,"currency":"usd",
        "status":"pending","payment_intent":"pi_test","metadata":{"engine_refund_id":"ref_1"}}
    result = adapter.create_refund({"id":"ref_1","amount":500}, p)
    assert result["parent_id"] == "pi_test"
    assert client.v1.refunds.create.call_args.kwargs["options"]["idempotency_key"] == "ref_1"


def test_live_stripe_keys_rejected():
    with pytest.raises(ValueError):
        StripeSandbox("sk_live_forbidden")


def test_adapter_normalizes_real_stripe_sdk_objects():
    payment = stripe.PaymentIntent.construct_from({"id":"pi_sdk", "amount":1000,"currency":"usd",
        "status":"requires_payment_method", "metadata":{"engine_payment_id":"pay_sdk"}},"sk_test_offline")
    assert StripeSandbox.payment(payment)["payment_id"] == "pay_sdk"
    refund = stripe.Refund.construct_from({"id":"re_sdk", "amount":400,"currency":"usd","status":"succeeded",
        "payment_intent":"pi_sdk", "metadata":{"engine_refund_id":"ref_sdk"}},"sk_test_offline")
    assert StripeSandbox.refund(refund)["operation_key"] == "ref_sdk"


def test_real_stripe_sdk_constructs_without_network():
    assert StripeSandbox("sk_test_offline").client is not None


def test_stripe_webhook_sdk_verification_and_duplicate_detection(db, monkeypatch):
    secret = "whsec_offline_test"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)
    adapter = Mock()
    engine = Engine(db, "stripe", provider_override=adapter)
    event = {"id":"evt_test","object":"event","type":"payment_intent.succeeded", "livemode":False,
             "data":{"object":{"id":"pi_test","object":"payment_intent","metadata":{}}}}
    raw = json.dumps(event).encode()
    with TestClient(create_app(engine)) as client:
        assert client.post("/webhooks/stripe", content=raw, headers={"stripe-signature":"bad"}).status_code == 400
        headers = {"stripe-signature":stripe_signature(raw, secret)}
        assert client.post("/webhooks/stripe", content=raw, headers=headers).json()["duplicate"] is False
        assert client.post("/webhooks/stripe", content=raw, headers=headers).json()["duplicate"] is True
        old = {"stripe-signature":stripe_signature(raw, secret, int(time.time())-600)}
        assert client.post("/webhooks/stripe", content=raw, headers=old).status_code == 400
        event["livemode"] = True
        raw = json.dumps(event).encode()
        assert client.post("/webhooks/stripe", content=raw,
            headers={"stripe-signature":stripe_signature(raw, secret)}).status_code == 400


def test_provider_amount_mismatch_goes_to_review(engine, db):
    from payments.providers import ReviewRequired
    p = engine.create_payment("identity", 1000, "usd")
    with pytest.raises(ReviewRequired):
        engine.apply_payment(p["id"], {"id":"sim_fake","payment_id":p["id"],"amount":1,"currency":"usd","status":"succeeded"})


def test_simulator_signed_webhook(engine):
    raw = json.dumps({"id":"evt_sim","type":"ignored","data":{"object":{}}}).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(b"local-simulator-secret", stamp.encode()+b"."+raw, hashlib.sha256).hexdigest()
    with TestClient(create_app(engine)) as client:
        assert client.post("/webhooks/simulator", content=raw).status_code == 400
        assert client.post("/webhooks/simulator", content=raw, headers={
            "x-simulator-timestamp":stamp,"x-simulator-signature":sig}).status_code == 200

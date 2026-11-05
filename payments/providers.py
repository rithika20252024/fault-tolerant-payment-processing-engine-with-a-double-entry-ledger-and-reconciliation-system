import os
import time
from uuid import uuid4

import stripe


class Retryable(Exception):
    pass


class ReviewRequired(Exception):
    pass


class Simulator:
    name = "simulator"

    def __init__(self, db):
        self.db = db

    def _create(self, key, kind, payment, amount, parent=None):
        fault = None
        with self.db.tx() as c:
            f = c.execute("SELECT * FROM sim_faults WHERE operation_key=%s FOR UPDATE", (key,)).fetchone()
            if f and f["remaining"] > 0:
                fault = f
                c.execute("UPDATE sim_faults SET remaining=remaining-1 WHERE operation_key=%s", (key,))
        if fault:
            time.sleep(min(fault["delay_ms"], 5000) / 1000)
            if fault["mode"] == "unavailable":
                raise Retryable("simulated_provider_unavailable")
        status = "processing" if fault and fault["mode"] == "pending" else "succeeded"
        if fault and fault["mode"] == "decline":
            status = "requires_payment_method" if kind == "payment" else "failed"
        with self.db.tx() as c:
            c.execute("""INSERT INTO sim_objects(id,operation_key,kind,payment_id,amount,currency,status,parent_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(operation_key) DO NOTHING""",
                (f"sim_{uuid4().hex}", key, kind, payment["id"], amount, payment["currency"], status, parent))
            obj = c.execute("SELECT * FROM sim_objects WHERE operation_key=%s", (key,)).fetchone()
        if fault and fault["mode"] == "timeout_after_commit":
            raise Retryable("simulated_response_lost_after_provider_commit")
        return obj

    def create_payment(self, payment):
        return self._create(payment["id"], "payment", payment, payment["amount"])

    def create_refund(self, refund, payment):
        obj = self._create(refund["id"], "refund", payment, refund["amount"], payment["provider_id"])
        return obj

    def retrieve(self, provider_id):
        with self.db.tx() as c:
            obj = c.execute("SELECT * FROM sim_objects WHERE id=%s", (provider_id,)).fetchone()
        if not obj:
            raise ReviewRequired("provider_object_missing")
        return obj

    def refunds(self, provider_id):
        with self.db.tx() as c:
            return c.execute("SELECT * FROM sim_objects WHERE parent_id=%s ORDER BY created_at", (provider_id,)).fetchall()


class StripeSandbox:
    name = "stripe"

    def __init__(self, key=None, client=None):
        key = key or os.environ.get("STRIPE_SECRET_KEY", "")
        if not key.startswith("sk_test_"):
            raise ValueError("Only Stripe sk_test_ sandbox keys are accepted")
        self.client = client or stripe.StripeClient(key, max_network_retries=2,
            http_client=stripe.RequestsClient(timeout=10))

    @staticmethod
    def payment(obj):
        if isinstance(obj, stripe.StripeObject):
            obj = obj.to_dict()
        return {"id": obj["id"], "payment_id": obj.get("metadata", {}).get("engine_payment_id"),
                "amount": obj["amount"], "currency": obj["currency"], "status": obj["status"]}

    @staticmethod
    def refund(obj):
        if isinstance(obj, stripe.StripeObject):
            obj = obj.to_dict()
        return {"id": obj["id"], "operation_key": obj.get("metadata", {}).get("engine_refund_id"),
                "amount": obj["amount"], "currency": obj["currency"], "status": obj["status"],
                "parent_id": obj["payment_intent"]}

    def create_payment(self, payment):
        obj = self.client.v1.payment_intents.create({
            "amount": payment["amount"], "currency": payment["currency"],
            "metadata": {"engine_payment_id": payment["id"]},
        }, options={"idempotency_key": payment["id"]})
        return self.payment(obj)

    def create_refund(self, refund, payment):
        obj = self.client.v1.refunds.create({"payment_intent": payment["provider_id"],
            "amount": refund["amount"], "metadata": {"engine_refund_id": refund["id"]}},
            options={"idempotency_key": refund["id"]})
        return self.refund(obj)

    def retrieve(self, provider_id):
        return self.payment(self.client.v1.payment_intents.retrieve(provider_id))

    def refunds(self, provider_id):
        result = self.client.v1.refunds.list({"payment_intent": provider_id, "limit": 100})
        return [self.refund(obj) for obj in result.auto_paging_iter()]


def provider_for(db, name):
    if name == "simulator":
        return Simulator(db)
    if name == "stripe":
        return StripeSandbox()
    raise ValueError("Unknown provider")

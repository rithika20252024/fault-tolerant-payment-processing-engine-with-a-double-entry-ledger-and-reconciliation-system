import json
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from .providers import ReviewRequired, provider_for


class Conflict(Exception):
    pass


class NotFound(Exception):
    pass


def identifier(prefix):
    return f"{prefix}_{uuid4().hex}"


def enqueue(c, kind, ref, job_id=None):
    c.execute("INSERT INTO jobs(id,kind,reference) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
              (job_id or identifier("job"), kind, ref))


class Engine:
    def __init__(self, db, provider="simulator", provider_override=None):
        self.db = db
        self.provider_name = provider
        self.provider_override = provider_override

    def provider(self, name):
        return self.provider_override or provider_for(self.db, name)

    def create_payment(self, key, amount, currency):
        if not key or len(key) > 200 or type(amount) is not int or not 0 < amount <= 100000000 or currency not in {"usd", "inr", "eur", "gbp"}:
            raise ValueError("Invalid key, amount, or currency")
        with self.db.tx() as c:
            new = c.execute("""INSERT INTO payments(id,idempotency_key,amount,currency,provider)
              VALUES(%s,%s,%s,%s,%s) ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",
              (identifier("pay"), key, amount, currency, self.provider_name)).fetchone()
            row = new or c.execute("SELECT * FROM payments WHERE idempotency_key=%s", (key,)).fetchone()
            if (row["amount"], row["currency"], row["provider"]) != (amount, currency, self.provider_name):
                raise Conflict("Idempotency key already used with different parameters")
            if new:
                enqueue(c, "payment", row["id"], "create:" + row["id"])
            return row

    def payment(self, payment_id):
        with self.db.tx() as c:
            row = c.execute("SELECT * FROM payments WHERE id=%s", (payment_id,)).fetchone()
            if not row:
                raise NotFound("Payment not found")
            row["refunds"] = c.execute("SELECT * FROM refunds WHERE payment_id=%s ORDER BY created_at", (payment_id,)).fetchall()
            return row

    def create_refund(self, payment_id, key, amount):
        if not key or len(key) > 200 or type(amount) is not int or amount <= 0:
            raise ValueError("Invalid refund key or amount")
        with self.db.tx() as c:
            # Serialize identical refund keys even when callers name different payments.
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("refund:" + key,))
            existing = c.execute("SELECT * FROM refunds WHERE idempotency_key=%s", (key,)).fetchone()
            if existing:
                if (existing["payment_id"], existing["amount"]) != (payment_id, amount):
                    raise Conflict("Refund idempotency parameters differ")
                return existing
            payment = c.execute("SELECT * FROM payments WHERE id=%s FOR UPDATE", (payment_id,)).fetchone()
            if not payment:
                raise NotFound("Payment not found")
            if payment["status"] != "succeeded" or payment["reserved_refunds"] + amount > payment["amount"]:
                raise Conflict("Payment is not succeeded or refund exceeds available amount")
            c.execute("UPDATE payments SET reserved_refunds=reserved_refunds+%s WHERE id=%s", (amount, payment_id))
            row = c.execute("""INSERT INTO refunds(id,payment_id,idempotency_key,amount)
              VALUES(%s,%s,%s,%s) RETURNING *""", (identifier("ref"), payment_id, key, amount)).fetchone()
            enqueue(c, "refund", row["id"], "create:" + row["id"])
            return row

    def inbox(self, event, provider):
        if not isinstance(event, dict) or not isinstance(event.get("id"), str) or len(event["id"]) > 255 or not isinstance(event.get("data", {}).get("object"), dict):
            raise ValueError("Malformed event")
        event_key = provider + ":" + event["id"]
        with self.db.tx() as c:
            row = c.execute("INSERT INTO inbox(id,provider,payload) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id",
                            (event_key, provider, Jsonb(event))).fetchone()
            if row:
                enqueue(c, "event", event_key, "event:" + event_key)
        return bool(row)

    @staticmethod
    def post(c, operation_id, payment, amount, refund=False):
        debit, credit = ("merchant_payable", "provider_clearing") if refund else ("provider_clearing", "merchant_payable")
        c.execute("""INSERT INTO journal(operation_id,payment_id,currency,amount,debit_account,credit_account)
          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(operation_id) DO NOTHING""",
          (operation_id, payment["id"], payment["currency"], amount, debit, credit))

    def apply_payment(self, payment_id, obj):
        with self.db.tx() as c:
            p = c.execute("SELECT * FROM payments WHERE id=%s FOR UPDATE", (payment_id,)).fetchone()
            if not p:
                raise NotFound("Payment not found")
            if obj.get("payment_id") != p["id"] or (obj["amount"], obj["currency"]) != (p["amount"], p["currency"]) or (p["provider_id"] and p["provider_id"] != obj["id"]):
                raise ReviewRequired("provider_payment_identity_or_amount_mismatch")
            status = obj["status"]
            if status == "requires_confirmation":
                status = "requires_payment_method"
            if status == "requires_capture":
                raise ReviewRequired("manual_capture_not_supported")
            if status not in {"requires_payment_method", "requires_action", "processing", "succeeded", "canceled"}:
                raise ReviewRequired("unknown_payment_state")
            if p["status"] in {"succeeded", "canceled"} and status != p["status"]:
                # Another sync may have fetched an older snapshot before this row was locked.
                return False
            changed = p["status"] != status or not p["provider_id"]
            c.execute("""UPDATE payments SET provider_id=%s,status=%s,
              completed_at=CASE WHEN %s='succeeded' THEN coalesce(completed_at,clock_timestamp()) ELSE completed_at END
              WHERE id=%s""", (obj["id"], status, status, payment_id))
            if status == "succeeded":
                self.post(c, p["id"], p, p["amount"])
            return changed

    def apply_refund(self, refund_id, obj):
        with self.db.tx() as c:
            # All paths acquire payment before refund to prevent inverted-lock deadlocks.
            ref = c.execute("SELECT * FROM refunds WHERE id=%s", (refund_id,)).fetchone()
            if not ref:
                raise ReviewRequired("unknown_refund")
            p = c.execute("SELECT * FROM payments WHERE id=%s FOR UPDATE", (ref["payment_id"],)).fetchone()
            ref = c.execute("SELECT * FROM refunds WHERE id=%s FOR UPDATE", (refund_id,)).fetchone()
            if (obj.get("operation_key"), obj["amount"], obj["currency"], obj["parent_id"]) != (ref["id"], ref["amount"], p["currency"], p["provider_id"]):
                raise ReviewRequired("provider_refund_identity_or_amount_mismatch")
            if ref["provider_id"] and ref["provider_id"] != obj["id"]:
                raise ReviewRequired("provider_refund_id_changed")
            status = obj["status"]
            if status == "requires_action":
                status = "pending"
            if status == "processing":
                status = "pending"
            if status not in {"pending", "succeeded", "failed", "canceled"}:
                raise ReviewRequired("unknown_refund_state")
            if ref["status"] in {"succeeded", "failed", "canceled"}:
                return False
            if status in {"failed", "canceled"}:
                c.execute("UPDATE payments SET reserved_refunds=reserved_refunds-%s WHERE id=%s", (ref["amount"], p["id"]))
            if status == "succeeded":
                self.post(c, ref["id"], p, ref["amount"], refund=True)
            c.execute("UPDATE refunds SET status=%s,provider_id=%s WHERE id=%s", (status, obj["id"], refund_id))
            return status != ref["status"] or not ref["provider_id"]

    @staticmethod
    def check_retry_age(row):
        if row["created_at"] < datetime.now(timezone.utc) - timedelta(hours=23):
            raise ReviewRequired("ambiguous_operation_older_than_safe_idempotency_window")

    def process(self, job):
        if job["kind"] == "payment":
            p = self.payment(job["reference"])
            provider = self.provider(p["provider"])
            if p["provider_id"]:
                obj = provider.retrieve(p["provider_id"])
            else:
                self.check_retry_age(p)
                obj = provider.create_payment(p)
            self.apply_payment(p["id"], obj)
        elif job["kind"] == "refund":
            with self.db.tx() as c:
                ref = c.execute("SELECT * FROM refunds WHERE id=%s", (job["reference"],)).fetchone()
            p = self.payment(ref["payment_id"])
            if ref["status"] in {"succeeded", "failed", "canceled"}:
                return
            provider = self.provider(p["provider"])
            if ref["provider_id"]:
                objects = provider.refunds(p["provider_id"])
                obj = next((o for o in objects if o["id"] == ref["provider_id"]), None)
                if obj is None:
                    raise ReviewRequired("provider_refund_missing")
            else:
                self.check_retry_age(ref)
                obj = provider.create_refund(ref, p)
            self.apply_refund(ref["id"], obj)
        elif job["kind"] == "event":
            with self.db.tx() as c:
                event = c.execute("SELECT * FROM inbox WHERE id=%s", (job["reference"],)).fetchone()
                body = event["payload"]
                obj = body["data"]["object"]
                event_type = body.get("type", "")
                if event_type.startswith("payment_intent."):
                    p = c.execute("SELECT id FROM payments WHERE provider=%s AND (provider_id=%s OR id=%s)",
                        (event["provider"], obj.get("id"), obj.get("metadata", {}).get("engine_payment_id"))).fetchone()
                elif event_type.startswith("refund.") or event_type == "charge.refunded":
                    p = c.execute("SELECT id FROM payments WHERE provider=%s AND provider_id=%s",
                        (event["provider"], obj.get("payment_intent"))).fetchone()
                else:
                    return
            if p:
                self.sync(p["id"])

    def sync(self, payment_id):
        p = self.payment(payment_id)
        if not p["provider_id"]:
            return False, ["provider_id_not_yet_known"]
        provider = self.provider(p["provider"])
        changed = self.apply_payment(p["id"], provider.retrieve(p["provider_id"]))
        findings = []
        for obj in provider.refunds(p["provider_id"]):
            known = next((r for r in p["refunds"] if r["id"] == obj.get("operation_key")), None)
            if known:
                changed = self.apply_refund(known["id"], obj) or changed
            else:
                findings.append("unmanaged_provider_refund:" + obj["id"])
        return changed, findings

    def reconcile(self, limit=1000, after=""):
        run_id = identifier("rec")
        with self.db.tx() as c:
            c.execute("INSERT INTO reconciliation_runs(id) VALUES(%s)", (run_id,))
            rows = c.execute("SELECT id FROM payments WHERE id>%s ORDER BY id LIMIT %s", (after, limit + 1)).fetchall()
        has_more = len(rows) > limit
        rows = rows[:limit]
        repaired = 0
        findings = []
        for row in rows:
            try:
                changed, issues = self.sync(row["id"])
                repaired += int(changed)
                findings.extend({"payment_id": row["id"], "issue": issue} for issue in issues)
            except Exception as exc:
                findings.append({"payment_id": row["id"], "issue": type(exc).__name__})
        with self.db.tx() as c:
            result = c.execute("""UPDATE reconciliation_runs SET finished_at=now(),scanned=%s,repaired=%s,findings=%s
              WHERE id=%s RETURNING *""", (len(rows), repaired, Jsonb(findings), run_id)).fetchone()
        result["next_cursor"] = rows[-1]["id"] if has_more else None
        return result

    def invariants(self):
        with self.db.tx() as c:
            # One consistent database snapshot for this multi-query audit.
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            unbalanced = c.execute("SELECT operation_id FROM ledger_entries GROUP BY operation_id,currency HAVING sum(debit)<>sum(credit)").fetchall()
            missing = c.execute("SELECT p.id FROM payments p LEFT JOIN journal j ON j.operation_id=p.id WHERE p.status='succeeded' AND j.operation_id IS NULL").fetchall()
            bad_refunds = c.execute("""SELECT p.id FROM payments p LEFT JOIN refunds r ON r.payment_id=p.id
              GROUP BY p.id HAVING coalesce(sum(r.amount) FILTER (WHERE r.status IN ('pending','succeeded')),0)<>p.reserved_refunds""").fetchall()
            missing_refunds = c.execute("SELECT r.id FROM refunds r LEFT JOIN journal j ON j.operation_id=r.id WHERE r.status='succeeded' AND j.operation_id IS NULL").fetchall()
            invalid = c.execute("""SELECT j.operation_id FROM journal j JOIN payments p ON p.id=j.payment_id
              LEFT JOIN refunds r ON r.id=j.operation_id
              WHERE j.currency<>p.currency OR NOT (
                (j.operation_id=p.id AND p.status='succeeded' AND j.amount=p.amount
                 AND j.debit_account='provider_clearing' AND j.credit_account='merchant_payable') OR
                (r.id IS NOT NULL AND r.payment_id=p.id AND r.status='succeeded' AND j.amount=r.amount
                 AND j.debit_account='merchant_payable' AND j.credit_account='provider_clearing'))""").fetchall()
            failures = {"unbalanced": unbalanced, "missing_payments": missing, "refund_reservations": bad_refunds,
                        "missing_refunds": missing_refunds, "invalid_postings": invalid}
            return {"ok": not any(failures.values()), "failures": failures}

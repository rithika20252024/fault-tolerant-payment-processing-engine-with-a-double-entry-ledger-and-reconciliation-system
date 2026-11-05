import hmac
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

import stripe
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, ConfigDict

from .db import Database
from .engine import Engine, Conflict, NotFound
from .runtime import validate_cloud_environment


class Money(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Annotated[int, Field(strict=True, gt=0, le=100000000)]


class PaymentInput(Money):
    currency: Literal["usd", "inr", "eur", "gbp"] = "usd"


class FaultInput(BaseModel):
    operation_key: str = Field(min_length=1, max_length=100)
    mode: Literal["unavailable", "timeout_after_commit", "pending", "decline"]
    remaining: int = Field(default=1, ge=1, le=10)
    delay_ms: int = Field(default=0, ge=0, le=5000)


def create_app(engine=None):
    @asynccontextmanager
    async def lifespan(app):
        validate_cloud_environment()
        if engine is None:
            app.state.engine = Engine(Database(), os.getenv("PROVIDER", "simulator"))
        else:
            app.state.engine = engine
        if app.state.engine.provider_name == "stripe":
            app.state.engine.provider("stripe")  # Fail early for missing/live keys.
        yield
        if engine is None:
            app.state.engine.db.close()

    app = FastAPI(title="Payment Reliability Lab", version="1.0.0", lifespan=lifespan)

    @app.middleware("http")
    async def response_headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def auth(authorization: str = Header(default="")):
        token = os.environ.get("API_TOKEN", "local-demo-token-change-before-sharing")
        if not token or not hmac.compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "Bearer token required")

    protected = [Depends(auth)]

    def service():
        return app.state.engine

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(NotFound)
    async def not_found(_, exc):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health")
    def health():
        with service().db.tx() as c:
            c.execute("SELECT 1")
        return {"status": "ok", "provider": service().provider_name}

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(Path(__file__).parent.parent / "static" / "index.html")

    @app.get("/dashboard.js", include_in_schema=False)
    def dashboard_js():
        return FileResponse(Path(__file__).parent.parent / "static" / "dashboard.js", media_type="application/javascript")

    @app.post("/payments", status_code=202, dependencies=protected)
    def create_payment(body: PaymentInput, idempotency_key: str = Header(min_length=1, max_length=200)):
        return service().create_payment(idempotency_key, body.amount, body.currency)

    @app.get("/payments", dependencies=protected)
    def payments(limit: int = 30, after: str = ""):
        if not 1 <= limit <= 100:
            raise HTTPException(422, "limit must be 1..100")
        with service().db.tx() as c:
            return c.execute("SELECT * FROM payments WHERE id>%s ORDER BY id LIMIT %s", (after, limit)).fetchall()

    @app.get("/payments/{payment_id}", dependencies=protected)
    def payment(payment_id: str):
        return service().payment(payment_id)

    @app.get("/payments/recent/list", dependencies=protected)
    def recent():
        with service().db.tx() as c:
            return c.execute("SELECT * FROM payments ORDER BY created_at DESC LIMIT 20").fetchall()

    @app.post("/payments/{payment_id}/refunds", status_code=202, dependencies=protected)
    def refund(payment_id: str, body: Money, idempotency_key: str = Header(min_length=1, max_length=200)):
        return service().create_refund(payment_id, idempotency_key, body.amount)

    @app.post("/payments/{payment_id}/sync", dependencies=protected)
    def sync(payment_id: str):
        changed, findings = service().sync(payment_id)
        return {"changed": changed, "findings": findings}

    @app.get("/ledger", dependencies=protected)
    def ledger(limit: int = 50):
        if not 1 <= limit <= 100:
            raise HTTPException(422, "limit must be 1..100")
        with service().db.tx() as c:
            return c.execute("SELECT * FROM journal ORDER BY posted_at DESC LIMIT %s", (limit,)).fetchall()

    @app.get("/invariants", dependencies=protected)
    def invariants():
        return service().invariants()

    @app.get("/jobs", dependencies=protected)
    def jobs():
        with service().db.tx() as c:
            return c.execute("SELECT * FROM jobs WHERE status<>'done' ORDER BY created_at DESC LIMIT 100").fetchall()

    @app.post("/jobs/{job_id}/replay", dependencies=protected)
    def replay(job_id: str):
        with service().db.tx() as c:
            row = c.execute("""UPDATE jobs SET status='ready',attempts=0,available_at=now(),lease_until=NULL,lease_token=NULL
              WHERE id=%s AND status='dead' RETURNING *""", (job_id,)).fetchone()
            if not row:
                raise HTTPException(409, "Only dead jobs can be replayed")
            return row

    @app.post("/reconcile", dependencies=protected)
    def reconcile(limit: int = 100, after: str = ""):
        if not 1 <= limit <= 1000:
            raise HTTPException(422, "limit must be 1..1000")
        return service().reconcile(limit=limit, after=after)

    @app.get("/stats", dependencies=protected)
    def stats():
        with service().db.tx() as c:
            payments = c.execute("SELECT status,count(*) AS count FROM payments GROUP BY status").fetchall()
            jobs = c.execute("SELECT status,count(*) AS count FROM jobs GROUP BY status").fetchall()
            latency = c.execute("""SELECT percentile_cont(0.95) WITHIN GROUP
              (ORDER BY extract(epoch FROM completed_at-created_at)*1000) AS p95_ms,
              percentile_cont(0.99) WITHIN GROUP
              (ORDER BY extract(epoch FROM completed_at-created_at)*1000) AS p99_ms
              FROM payments WHERE completed_at IS NOT NULL""").fetchone()
            backlog = c.execute("""SELECT coalesce(max(extract(epoch FROM now()-created_at)),0) AS oldest_seconds
              FROM jobs WHERE status IN ('ready','running')""").fetchone()
            journal = c.execute("SELECT count(*) AS count FROM journal").fetchone()
        return {"provider": service().provider_name, "payments": {r["status"]:r["count"] for r in payments},
                "jobs": {r["status"]:r["count"] for r in jobs}, "latency": latency,
                "oldest_job_seconds": float(backlog["oldest_seconds"]), "journal_transactions": journal["count"]}

    @app.get("/metrics", dependencies=protected, response_class=PlainTextResponse)
    def metrics():
        data = stats()
        lines = []
        for group in ("payments", "jobs"):
            lines += [f'# TYPE lab_{group} gauge']
            for state, count in data[group].items():
                lines.append(f'lab_{group}{{status="{state}"}} {count}')
        lines.append(f'lab_oldest_job_seconds {data["oldest_job_seconds"]}')
        lines.append(f'lab_journal_transactions {data["journal_transactions"]}')
        for q in ("p95_ms", "p99_ms"):
            if data["latency"][q] is not None:
                lines.append(f'lab_payment_completion_{q} {data["latency"][q]}')
        return "\n".join(lines) + "\n"

    @app.post("/webhooks/{provider}")
    async def webhook(provider: Literal["stripe", "simulator"], request: Request):
        if provider != service().provider_name:
            raise HTTPException(400, "Provider not enabled")
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > 262144:
                raise HTTPException(413, "Webhook too large")
        raw = bytes(chunks)
        try:
            if provider == "stripe":
                secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
                if not secret:
                    raise HTTPException(503, "Webhook signing secret is not configured")
                event = stripe.Webhook.construct_event(raw, request.headers.get("stripe-signature", ""), secret, tolerance=300)
                event = event.to_dict()
                if event.get("livemode") is not False:
                    raise ValueError("Only sandbox events are accepted")
            else:
                stamp = request.headers.get("x-simulator-timestamp", "")
                if abs(time.time() - int(stamp)) > 300:
                    raise ValueError("Expired signature")
                secret = os.getenv("SIMULATOR_WEBHOOK_SECRET", "local-simulator-secret")
                expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(expected, request.headers.get("x-simulator-signature", "")):
                    raise ValueError("Invalid signature")
                event = json.loads(raw)
            inserted = service().inbox(event, provider)
        except (ValueError, KeyError, TypeError, AttributeError, stripe.SignatureVerificationError):
            raise HTTPException(400, "Invalid webhook")
        return {"accepted": True, "duplicate": not inserted}

    @app.post("/simulator/faults", dependencies=protected)
    def fault(body: FaultInput):
        if service().provider_name != "simulator" or os.getenv("ENABLE_FAULTS") != "true":
            raise HTTPException(403, "Fault injection is disabled")
        with service().db.tx() as c:
            c.execute("""INSERT INTO sim_faults(operation_key,mode,remaining,delay_ms) VALUES(%s,%s,%s,%s)
              ON CONFLICT(operation_key) DO UPDATE SET mode=excluded.mode,remaining=excluded.remaining,delay_ms=excluded.delay_ms""",
              (body.operation_key, body.mode, body.remaining, body.delay_ms))
        return {"configured": True}

    return app


app = create_app()

CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY, installed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE payments (
    id text PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    amount bigint NOT NULL CHECK (amount > 0 AND amount <= 100000000),
    currency text NOT NULL CHECK (currency IN ('usd','inr','eur','gbp')),
    provider text NOT NULL CHECK (provider IN ('simulator','stripe')),
    provider_id text UNIQUE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN
      ('pending','requires_payment_method','requires_action','processing','succeeded','canceled')),
    reserved_refunds bigint NOT NULL DEFAULT 0 CHECK (reserved_refunds >= 0 AND reserved_refunds <= amount),
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE TABLE refunds (
    id text PRIMARY KEY,
    payment_id text NOT NULL REFERENCES payments(id),
    idempotency_key text NOT NULL UNIQUE,
    amount bigint NOT NULL CHECK (amount > 0),
    provider_id text UNIQUE,
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','succeeded','failed','canceled')),
    created_at timestamptz NOT NULL DEFAULT now()
);
-- Each immutable journal row IS a complete two-legged posting. It cannot
-- contain an unbalanced amount or a missing counter-entry.
CREATE TABLE journal (
    operation_id text PRIMARY KEY,
    payment_id text NOT NULL REFERENCES payments(id),
    currency text NOT NULL CHECK (currency IN ('usd','inr','eur','gbp')),
    amount bigint NOT NULL CHECK (amount > 0),
    debit_account text NOT NULL,
    credit_account text NOT NULL,
    posted_at timestamptz NOT NULL DEFAULT now(),
    CHECK (debit_account <> credit_account)
);
CREATE VIEW ledger_entries AS
 SELECT operation_id,payment_id,currency,debit_account AS account,amount AS debit,0::bigint AS credit FROM journal
 UNION ALL
 SELECT operation_id,payment_id,currency,credit_account,0::bigint,amount FROM journal;
CREATE FUNCTION immutable_journal() RETURNS trigger LANGUAGE plpgsql AS $$
 BEGIN RAISE EXCEPTION 'Journal is immutable: post a compensating transaction'; END;
$$;
CREATE TRIGGER journal_immutable BEFORE UPDATE OR DELETE ON journal FOR EACH ROW EXECUTE FUNCTION immutable_journal();
CREATE TRIGGER journal_no_truncate BEFORE TRUNCATE ON journal FOR EACH STATEMENT EXECUTE FUNCTION immutable_journal();
CREATE TABLE jobs (
    id text PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('payment','refund','event')),
    reference text NOT NULL,
    status text NOT NULL DEFAULT 'ready' CHECK (status IN ('ready','running','done','dead')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_until timestamptz,
    lease_token text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_ready ON jobs(available_at,created_at) WHERE status='ready';
CREATE INDEX jobs_expired ON jobs(lease_until) WHERE status='running';
CREATE INDEX refunds_payment ON refunds(payment_id);
CREATE TABLE inbox (
    id text PRIMARY KEY, provider text NOT NULL,
    payload jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE reconciliation_runs (
    id text PRIMARY KEY, started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz, scanned integer NOT NULL DEFAULT 0, repaired integer NOT NULL DEFAULT 0,
    findings jsonb NOT NULL DEFAULT '[]'
);
-- Simulator state is durable and transactionally separate from engine state.
CREATE TABLE sim_objects (
    id text PRIMARY KEY, operation_key text NOT NULL UNIQUE,
    kind text NOT NULL, payment_id text NOT NULL, amount bigint NOT NULL,
    currency text NOT NULL, status text NOT NULL,
    parent_id text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sim_faults (
    operation_key text PRIMARY KEY, mode text NOT NULL,
    remaining integer NOT NULL DEFAULT 1, delay_ms integer NOT NULL DEFAULT 0
);

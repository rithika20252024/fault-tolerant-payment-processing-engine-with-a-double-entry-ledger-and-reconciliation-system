# Evidence and defensible résumé claims

This project can demonstrate implementation depth, failure analysis, performance measurement, and technical judgment. It cannot turn two months of employment into more production experience. A local or publicly deployed demo is still a personal project unless it has actual operational use.

| Original criticism | Project evidence | Remaining limit |
|---|---|---|
| Small academic projects | Complete API, database, worker, deployment, operations scripts | No external users are claimed |
| Weak systems design | Transaction boundaries, concurrency controls, recovery behavior, recorded tradeoffs | Not a claim of designing a production payment processor |
| No scaling evidence | Raw synthetic results across worker counts, with latency and correctness | Single-host results; not geographic/distributed scaling |
| No high-throughput payment evidence | Payment-specific load and failure experiments | Synthetic throughput is not real provider payment volume |
| Buzzwords without numbers | Reproducible benchmark and recovery reports | Only use measured numbers under disclosed conditions |
| Short production history | Maintain this project, record releases and actual operation | Elapsed operating history must actually accumulate |

## Before presenting the project

Explain these without reading code:

1. Why can a timeout happen after a payment succeeded?
2. Which transaction makes payment acceptance and job publication atomic?
3. What prevents concurrent refunds from exceeding the payment?
4. Why does a lease not guarantee exactly-once processing?
5. How can Stripe's idempotency retention affect old ambiguous retries?
6. What does the ledger schema guarantee, and what does it not model?
7. What would make you introduce a broker or split the database?
8. Which bottleneck did the actual measurements reveal, and what further evidence would validate that diagnosis?

## Suggested résumé wording

Use the verified numbers in `reports/RESULTS.md`, name the simulator, state the workload, and keep the project under personal projects. Example structure:

> Built a PostgreSQL-backed payment reliability lab with idempotent processing, concurrent refund protection, and an immutable two-leg ledger; measured [X] completed simulated payments/sec under [conditions] and verified recovery of [N] queued payments after worker termination without duplicate postings.

Only add “integrated and tested Stripe sandbox payments and refunds” after an actual account-level run. Do not write “production-grade,” “processed millions of dollars,” “exactly-once distributed payments,” or “operated high-throughput payments” based on this lab.

AI-assisted implementation is a starting point for learning. Your strongest evidence will be personally reproducing the experiments, finding a new failure case, making a change you can explain, and measuring its effect. Keep a dated engineering log for those changes.

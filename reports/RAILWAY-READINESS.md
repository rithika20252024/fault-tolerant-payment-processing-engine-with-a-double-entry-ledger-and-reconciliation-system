# Railway readiness

The complete local test suite passed: **42 tests**, including six additional checks for Railway configuration, required database access, a private dashboard token, and disabled fault injection, plus a regression check using actual Stripe SDK response objects.

Deployment source adds separate API and worker configurations, migration-before-start, dynamic `PORT` handling, public-response cache controls, and rejection of the shared local development token in Railway.

The runtime upload is staged separately and contains only the Dockerfile, locked dependencies, application modules, static dashboard, and the role-specific Railway configuration. It excludes `.env`, credentials, database dumps, and benchmark reports.

The dedicated Railway project and PostgreSQL service were created. After explicit user approval, the Stripe test key and generated API token were stored as private Railway variables. The API and worker were deployed. Final account-level verification is recorded separately in `railway-deployment.json` when completed.

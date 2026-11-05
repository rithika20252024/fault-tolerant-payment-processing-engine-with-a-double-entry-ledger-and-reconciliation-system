#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source_dump="${1:-work/payments.dump}"
test -s "$source_dump"
# Create a NEW database; never restore over the running project database.
restore_db="restore_drill_$(date +%s)"
docker compose exec -T db createdb -U payments "$restore_db"
docker compose exec -T db pg_restore -U payments -d "$restore_db" --no-owner --exit-on-error < "$source_dump"
docker compose exec -T db psql -U payments -d "$restore_db" -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) AS journal_transactions FROM journal; SELECT currency,sum(debit)-sum(credit) AS imbalance FROM ledger_entries GROUP BY currency;"
docker compose exec -T -e "DATABASE_URL=postgresql://payments:local-development-only@db:5432/$restore_db" api \
  python -c 'from payments.db import Database; from payments.engine import Engine; import json; db=Database(); result=Engine(db).invariants(); print(json.dumps(result)); db.close(); assert result["ok"]'
echo "Restored into separate database $restore_db; source database untouched."
echo "Inspect this database, then remove it explicitly when you no longer need it."

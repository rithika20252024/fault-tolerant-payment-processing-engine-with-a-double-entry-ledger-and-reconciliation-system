.PHONY: up down test benchmark audit logs backup restore
up:
	docker compose up -d --build
down:
	docker compose down
test:
	docker compose run --rm api python -m pytest -q
benchmark:
	python scripts/benchmark.py --workers-label 1
audit:
	docker compose exec -T api python -c 'from payments.db import Database; from payments.engine import Engine; import json; result=Engine(Database()).invariants(); print(json.dumps(result)); assert result["ok"]'
logs:
	docker compose logs --tail 100 api worker
backup:
	bash scripts/backup.sh
restore:
	bash scripts/restore_drill.sh

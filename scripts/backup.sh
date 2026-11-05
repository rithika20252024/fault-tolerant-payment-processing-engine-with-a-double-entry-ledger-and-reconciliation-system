#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p work
destination="${1:-work/payments.dump}"
umask 077
docker compose exec -T db pg_dump -U payments -d payments -Fc > "$destination"
echo "Backup written to $destination"

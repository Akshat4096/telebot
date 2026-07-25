#!/usr/bin/env bash
# Container entrypoint: make sure the schema exists and the catalogue is
# seeded (only on first run — safe to re-run, add_product refuses
# duplicates), then hand off to the bot process.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p "$(dirname "${KIRANA_DB_PATH:-./data/kirana.db}")"

python3 -c "from db.db import init_db; init_db()"

PRODUCT_COUNT=$(python3 -c "
from db.db import read_conn
with read_conn() as conn:
    print(conn.execute('SELECT COUNT(*) FROM products').fetchone()[0])
")

if [ "$PRODUCT_COUNT" -eq 0 ]; then
  echo "No products found — seeding catalogue..."
  python3 -m data.seed
fi

echo "Starting Supermarket Ops Agent (Telegram polling)..."
exec python3 -m bot.telegram_bot

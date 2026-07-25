#!/usr/bin/env bash
# Local run helper. Requires: Python 3.10+, Node.js (for the Claude Code CLI
# the Agent SDK drives), and TELEGRAM_BOT_TOKEN + ANTHROPIC_API_KEY in the
# environment (or a .env file — copy .env.example and fill it in, then
# `set -a && source .env && set +a` before running this).
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v claude >/dev/null 2>&1; then
  echo "Installing @anthropic-ai/claude-code CLI (required by claude-agent-sdk)..."
  npm install -g @anthropic-ai/claude-code
fi

python3 -m pip install -r requirements.txt --break-system-packages -q

if [ ! -f "${KIRANA_DB_PATH:-data/kirana.db}" ]; then
  echo "Seeding catalogue..."
  python3 -m data.seed
fi

echo "Running tests..."
python3 -m pytest tests/ -q

echo "Starting bot..."
python3 -m bot.telegram_bot

# Supermarket Ops Agent — runs the Telegram bot as a long-lived worker
# process (polling, no HTTP port needed). Needs both Python (the bot,
# tools, DB, PDF/PPTX generation) and Node.js (the Claude Code CLI that
# claude-agent-sdk drives under the hood).
FROM python:3.11-slim

# Node.js, for `npm install -g @anthropic-ai/claude-code`
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get purge -y curl gnupg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# SQLite DB lives here — mount a Railway Volume at /data so it survives
# redeploys and restarts (see README's Railway section). Falls back to a
# plain container path if no volume is attached, which is fine for a
# review window but will reset on redeploy.
ENV KIRANA_DB_PATH=/data/kirana.db

RUN chmod +x scripts/start.sh
CMD ["./scripts/start.sh"]

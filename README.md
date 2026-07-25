# How to Start 
 - Clone repo 
 - install following modules : 
    -claude-agent-sdk>=0.2.124
    - python-telegram-bot>=22.8
    - python-dotenv>=1.0
    - reportlab>=4.0
    - python-pptx>=0.6.23
    - matplotlib>=3.8
    - Pillow>=10.0
    - pytest>=8.0

- Run python -m bot.telegram_bot  


# Supermarket Ops Agent

Runs a small Indian kirana store end-to-end from a Telegram chat: receiving stock, cutting GST-correct bills, khata (credit), daily close, PDF invoices, PPTX analysis decks — all through an agent reasoning over tools, not a command router.

**Telegram bot:** `@telerwlbot` — fill in after deploying (see *Running it* below); not deployed yet.

## Harness: Claude Agent SDK (Python)

Chosen because the assignment's control loop — observe → reason → act (tool call) → feed result back → continue, chaining calls within one turn — is exactly what the SDK's `ClaudeSDKClient` already does; I don't hand-roll a loop. Its **in-process MCP tools** (`@tool` + `create_sdk_mcp_server`) let business logic run as plain, unit-testable Python with direct DB access — no IPC, no separate tool server to deploy. Its **Agent Skills** (`SKILL.md`, progressive disclosure) are a first-class fit for "author skills and tools" — domain playbooks live in `skills/*/SKILL.md`, hard rules live in `tools/*.py`. Its **subagents** (`AgentDefinition`) give a clean way to scope a narrower tool set to document generation. Vercel AI SDK would've meant building all three of these myself.

## Architecture

```
bot/telegram_bot.py   Telegram polling loop, one ClaudeSDKClient session per chat, update_id dedupe
agent/options.py       System prompt (rebuilt from DB prefs every session) + tool/skill/subagent wiring
tools/server.py        Thin @tool adapters — the ONLY file that knows about the SDK wire format
tools/{inventory,billing,khata,daily_close,preferences}.py   Plain Python business logic, unit-tested
tools/{gst,invoice_pdf,analysis_deck}.py                     GST math, PDF/PPTX rendering
skills/*/SKILL.md      Domain playbooks: when to ask, how to phrase, which tools to chain
db/schema.sql, db/db.py SQLite, WAL mode, BEGIN IMMEDIATE transactions
```

The model only ever sees `mcp__kirana__*` tools plus `Skill`/`Task` — built-in Bash/Read/Write/WebSearch are disabled (`tools=["Skill"]` in `ClaudeAgentOptions`), so there's no path to the filesystem that skips the tool layer.

## The hard parts

**Grounding.** Every price/GST/stock fact comes from a tool call against SQLite; the system prompt explicitly forbids inventing one. `resolve_product` fuzzy-matches names but only returns an exact DB row.

**Oversell guard.** Enforced in `tools/billing.py`, not the prompt: `add_bill_item` checks stock as a fast UX signal, but the authoritative check re-runs inside `finalize_bill`'s own transaction, immediately before decrementing — because stock can move between drafting and finalizing.

**GST correctness.** `tools/gst.py` treats MRP as GST-inclusive, splits CGST/SGST evenly (odd paisa to SGST), rounds each line with `Decimal`/`ROUND_HALF_UP`, and rounds the bill grand total to the nearest rupee with an explicit "round off" line — verified in `tests/test_gst.py` to reconcile to the paisa.

**Multi-turn bills.** `start_bill` → repeated `add_bill_item`/`remove_bill_item`/`update_bill_item_qty` → `finalize_bill`. Stock is untouched until finalize; a bill is just draft rows until then.

**Idempotency.** `finalize_bill(bill_id, idempotency_key)`: a `UNIQUE` constraint on `bills.idempotency_key` plus a check at the top of the transaction means a repeated call with the same key returns the original result instead of re-decrementing stock. The Telegram layer *also* dedupes on `update_id` in a `processed_updates` table before a message ever reaches the agent — two independent layers, either one alone would stop a double-bill.

**Concurrency.** SQLite WAL + `BEGIN IMMEDIATE` on every mutation serializes writers: the second of two concurrent `finalize_bill` calls blocks until the first commits, then sees fresh stock and correctly refuses if it would now oversell. `tests/test_concurrency.py` runs two bills racing for the last units of stock with a `threading.Barrier` and asserts exactly one wins.

**Guardrails.** Below-cost sales refused unless explicitly overridden (`allow_below_cost`); no product/stock delete tool exists at all — only ledgered `+/-` corrections; khata payments refuse an unknown customer or an overpayment past the balance without explicit confirmation.

**Real artifacts.** `tools/invoice_pdf.py` (reportlab) and `tools/analysis_deck.py` (python-pptx + matplotlib, charts rendered to PNG and embedded) — bundled DejaVu Sans font so ₹ renders correctly regardless of host fonts.

**Memory across sessions.** `owner_preferences` table, read fresh into the system prompt at the start of *every* session (`agent/options.py::build_system_prompt`) — not conversation history. `/new` drops the cached `ClaudeSDKClient` (fresh agent session, no chat history) but the next session's system prompt still contains everything saved via `set_preference`.

## Running it

Needs Python 3.10+, Node.js (for the Claude Code CLI the SDK drives: `npm install -g @anthropic-ai/claude-code`), an `ANTHROPIC_API_KEY`, and a `TELEGRAM_BOT_TOKEN` from @BotFather.

```bash
cp .env.example .env   # fill in the two tokens
set -a && source .env && set +a
./scripts/run.sh        # installs deps, seeds the catalogue, runs tests, starts polling
```

`KIRANA_DB_PATH` must point at a real local disk path, not a network/FUSE-mounted drive — SQLite's WAL locking doesn't work reliably there (hit this directly during development: `disk I/O error` on a mounted folder, fine on local disk).

## Deploying (Railway)

The repo includes a `Dockerfile`, `scripts/start.sh`, and `railway.json` for a one-service deploy as a background worker (polling Telegram, no HTTP port needed):

1. Push this repo to GitHub (or use the Railway CLI to deploy the local folder directly with `railway up`, no GitHub required).
2. On railway.app, **New Project → Deploy from GitHub repo** (or run `railway init` + `railway up` from this folder).
3. Railway should auto-detect the `Dockerfile` via `railway.json`. If it instead tries Nixpacks, override the builder to "Dockerfile" in Settings → Build.
4. In **Variables**, set `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY`. Leave `KIRANA_DB_PATH` alone (the Dockerfile sets it to `/data/kirana.db`).
5. In **Settings → Volumes**, attach a volume mounted at `/data` — without this, the SQLite DB (stock, khata, bills, preferences) resets on every redeploy.
6. This is a worker, not a web service — it never listens on a port. If Railway's health check complains about no open port, disable the health check for this service in Settings → Deploy (worker/background services don't need one).
7. First boot runs `scripts/start.sh`, which seeds the catalogue automatically if the DB is empty, then starts polling. Check the Deploy Logs for "Starting Supermarket Ops Agent" to confirm it's live, then message your bot on Telegram to confirm.

Not yet verified against a live Railway deploy end-to-end — budget a few minutes for the first deploy in case Railway's current UI or Nixpacks detection differs from what's described above.

## Tests

`pytest tests/` — 23 tests covering GST math/rounding, multi-turn bill edits, the oversell/below-cost/khata guardrails, finalize idempotency, the two-threads-racing-for-stock concurrency scenario, and the diagnostic harness below.

## Diagnostic-only: testing the tools with a free API (no Anthropic key needed)

`scripts/groq_agent.py` is **not part of the submission** — the submitted harness is the Claude Agent SDK above. It exists so the same real business-logic tools (`tools/tool_specs.py` is the single shared source of truth both this script and `tools/server.py` build their tool definitions from) can be exercised by an actual LLM without needing a paid Anthropic key, since Groq's API is free. It hand-rolls the tool-calling loop that `ClaudeSDKClient` normally provides for free — that's exactly the gap the real submission's harness choice avoids.

```bash
pip install -r requirements.txt
export GROQ_API_KEY=gsk_...
python -m scripts.groq_agent --scenario   # runs the assignment's example messages, prints every tool call
python -m scripts.groq_agent              # interactive terminal chat instead
```

`tests/test_groq_agent_loop.py` verifies the loop's plumbing offline (tool_call → real function → threaded back with matching `tool_call_id` → final answer), using a scripted fake client — no network needed for that part. Actually calling Groq needs outbound access to `api.groq.com`, which some locked-down networks/CI runners block — run it wherever you have normal internet access.

## Seed catalogue

`python -m data.seed` loads real SKUs with GST slabs matching current retail practice: Aashirvaad Atta 5kg (5%), Tata Salt (0%), Amul Butter 100g (12%), Fortune Oil 1L (5%), Maggi 70g (12%), Parle-G (18%), Surf Excel (18%), plus loose sugar/rice/dal (0%).

## Stretch not attempted

Branded invoice templates beyond the current letterhead, scheduled auto-sent decks, FEFO/expiry tracking, voice notes, multi-language, barcode lookup, khata reminders — out of scope for this pass; the tool/skill seams (`add_product`, `documents` skill, `khata` skill) are where each would plug in.

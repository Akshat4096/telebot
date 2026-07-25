"""
DIAGNOSTIC HARNESS — NOT part of the submission.

The submitted harness is the Claude Agent SDK (bot/telegram_bot.py +
agent/options.py). This script exists only because Groq's API is free and
reachable from wherever you're running this, so it lets you exercise the
same real business-logic tools (tools/tool_specs.py -> tools/inventory.py,
billing.py, khata.py, ...) end-to-end with an actual LLM making the tool-call
decisions, without needing an Anthropic key.

It implements a minimal manual tool-calling loop against Groq's
OpenAI-compatible chat completions API: send messages + tool schemas, execute
whatever tools the model asks for by calling the real functions, feed results
back as role="tool" messages, repeat until the model answers in plain text.
This loop is hand-rolled (Groq has no equivalent of the Agent SDK's control
loop) — that absence is exactly why the real submission uses the Agent SDK
instead of reinventing this.

Each GroqAgent is bound to one chat_id (same idea as one ClaudeSDKClient per
Telegram chat in the real bot) — start_bill is server-bound to that chat_id
(see tools/tool_specs.py::build_tool_specs) so the model is never asked to
supply a chat identifier it has no way of knowing.

Usage:
    export GROQ_API_KEY=gsk_...
    python -m scripts.groq_agent            # interactive terminal chat
    python -m scripts.groq_agent --scenario  # runs the assignment's example
                                              # scenarios non-interactively
                                              # and prints a transcript
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # picks up GROQ_API_KEY / GROQ_MODEL from .env if present

from groq import Groq, BadRequestError, RateLimitError

from db.db import init_db
from tools.tool_specs import TOOL_SPECS_BY_NAME, build_tool_specs
from tools.errors import ToolError, AmbiguousProductError
from tools.preferences import get_all_preferences

# llama-3.3-70b-versatile has a low free-tier daily token cap (100k/day at
# time of writing) and this loop resends the full growing conversation +
# all tool schemas on every turn, so a ~10-turn scenario can exhaust it.
# A smaller model (higher free-tier TPD, but less reliable at multi-step
# tool orchestration in our testing) avoids this — override with GROQ_MODEL.
MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

DEFAULT_CHAT_ID = "groq-diagnostic-chat"


class RateLimited(Exception):
    """A hard daily/per-minute rate limit was hit — retrying immediately
    won't help, unlike a one-off tool_use_failed glitch. Callers should stop
    the run rather than keep spending turns."""


SYSTEM_PROMPT = """You are the Supermarket Ops Agent for a small Indian kirana store,
talking with the shop owner in plain, terse, real-shopkeeper English. There is no
menu or app; the chat is the product.

Rules:
- Never invent a product, price, GST rate, HSN code or stock quantity — always call a
  tool. If something isn't in the catalogue, say so.
- If a request is ambiguous (which brand/size, which of two similar customer names),
  ask a short clarifying question instead of guessing, unless a stored preference
  (get_preference) already answers it.
- A bill is a draft until finalize_bill is called; nothing is sold/deducted before that.
  Batch multiple add_bill_item / edit calls in one turn for a multi-item message.
- finalize_bill needs a stable idempotency_key; reuse the same key on a retry of the
  same finalize, never mint a new one.
- Tool refusals (oversell, below-cost, unknown khata, overpayment) are deliberate
  guardrails — relay the real numbers back and ask how to proceed, don't retry with
  different numbers to force success.
- Save standing preferences with set_preference when the owner states one.
- Always show the GST breakup (taxable value, CGST, SGST, round-off, total) on bills.
Keep replies short and concrete.
"""


def _tools_payload(specs) -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": spec.name, "description": spec.description, "parameters": spec.schema,
        }}
        for spec in specs
    ]


def _execute_tool(name: str, args: dict, specs_by_name: dict | None = None) -> dict:
    specs_by_name = specs_by_name if specs_by_name is not None else TOOL_SPECS_BY_NAME
    spec = specs_by_name.get(name)
    if spec is None:
        return {"error": f"no such tool: {name}", "error_type": "UnknownTool"}
    try:
        return spec.fn(**args)
    except AmbiguousProductError as e:
        return {"error": str(e), "error_type": "ambiguous_product", "candidates": e.candidates}
    except ToolError as e:
        return {"error": str(e), "error_type": type(e).__name__}


def _system_message() -> dict:
    prefs = get_all_preferences()
    prompt = SYSTEM_PROMPT
    if prefs:
        prompt += "\nStanding owner preferences (apply by default):\n" + "\n".join(
            f"- {k}: {v}" for k, v in prefs.items()
        )
    return {"role": "system", "content": prompt}


class GroqAgent:
    """Owns the message history for one 'chat' (one chat_id) and runs the
    tool-call loop. Mirrors, at a much smaller scale, what ClaudeSDKClient
    does automatically for the real submission's SDK MCP tools."""

    def __init__(self, client: Groq | None = None, chat_id: str = DEFAULT_CHAT_ID):
        self.client = client or Groq(api_key=os.environ["GROQ_API_KEY"])
        self.chat_id = chat_id
        self.specs = build_tool_specs(chat_id)
        self.specs_by_name = {s.name: s for s in self.specs}
        self.messages: list[dict] = [_system_message()]
        self.trace: list[dict] = []  # every tool call + result, for test reporting

    def reset(self):
        """Equivalent of /new: fresh conversation, preferences reloaded from DB.
        Same chat_id — this is the owner's store resetting the chat, not a
        different chat."""
        self.messages = [_system_message()]
        self.trace = []

    def _call_model(self):
        """
        Groq's smaller/generalist models occasionally emit a malformed
        pseudo-XML function-call tag (e.g. `<function=start_bill,{...}>`)
        instead of a clean structured tool_call, which the API rejects with
        a 400 'tool_use_failed' BadRequestError. This is a real reliability
        gap of hand-rolling a tool loop against a general-purpose free model
        — ironic but instructive, since it's exactly the class of problem
        the Claude Agent SDK's built-in control loop (used by the actual
        submission) doesn't have to deal with. temperature=0 plus a couple
        of retries clears it most of the time; if it doesn't, we surface a
        clear error instead of a stack trace so the rest of a scenario run
        can still proceed.
        """
        last_error = None
        for attempt in range(3):
            try:
                return self.client.chat.completions.create(
                    model=MODEL, messages=self.messages, tools=_tools_payload(self.specs),
                    tool_choice="auto", temperature=0,
                )
            except BadRequestError as e:
                last_error = e
                body = getattr(e, "body", None) or {}
                code = (body.get("error") or {}).get("code") if isinstance(body, dict) else None
                if code != "tool_use_failed":
                    raise
                continue
        raise last_error

    def send(self, user_text: str, max_rounds: int = 8) -> str:
        self.messages.append({"role": "user", "content": user_text})

        for _ in range(max_rounds):
            try:
                response = self._call_model()
            except BadRequestError as e:
                note = ("(model failed to format a valid tool call after retries — "
                        f"try a different GROQ_MODEL; raw error: {e})")
                self.messages.append({"role": "assistant", "content": note})
                return note
            except RateLimitError as e:
                # Don't retry a hard rate limit — surface it and let the
                # caller (run_scenario/run_interactive) decide to stop.
                raise RateLimited(str(e)) from e
            msg = response.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None)

            if not tool_calls:
                self.messages.append({"role": "assistant", "content": msg.content or ""})
                return msg.content or ""

            self.messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [tc.model_dump() for tc in tool_calls],
            })
            for tc in tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = _execute_tool(tc.function.name, args, self.specs_by_name)
                self.trace.append({"tool": tc.function.name, "args": args, "result": result})
                self.messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

        return "(hit max tool-call rounds without a final answer)"


# "/new" is a sentinel run_scenario() intercepts to call agent.reset() instead
# of sending it to the model — same as the Telegram bot's /new command.
SCENARIO = [
    "50 packets of Maggi came in, cost 12, mrp 14",
    "new item: Amul Butter 100g, GST 12%, cost 48, MRP 62",
    "make a bill: 2kg sugar, 1 Aashirvaad atta 5kg, 4 Maggi, 1 Amul butter, UPI",
    "drop the butter, make it 6 Maggi",
    "actually make it 999 Aashirvaad atta",  # should trigger the oversell guard
    "ok forget that, finalize the bill",
    "send me that bill as a PDF",
    "put ₹500 on Ramesh's credit",
    "Ramesh paid ₹300",
    "Ramesh's balance?",
    "today's sales?",
    "make this week's sales analysis deck",
    "always assume UPI unless I say cash",
    "/new",
    "what's my default payment mode?",  # should still answer UPI after reset
]


def _ensure_scenario_db():
    """
    Use a dedicated, fresh throwaway DB for the scenario run instead of
    whatever KIRANA_DB_PATH might already point at — the assignment's
    scenario needs a clean, seeded catalogue to be meaningful (e.g. the
    oversell test needs Aashirvaad Atta to actually exist with real stock),
    and re-running it should be reproducible rather than accumulating state
    from prior runs.
    """
    if not os.environ.get("GROQ_SCENARIO_KEEP_DB"):
        scenario_db = Path("data") / "kirana_groq_scenario.db"
        for suffix in ("", "-wal", "-shm", "-journal"):
            p = Path(str(scenario_db) + suffix)
            if p.exists():
                p.unlink()
        os.environ["KIRANA_DB_PATH"] = str(scenario_db)
    init_db()
    from data.seed import run as seed_run
    seed_run()


def run_scenario():
    _ensure_scenario_db()
    agent = GroqAgent()
    for turn in SCENARIO:
        if turn == "/new":
            agent.reset()
            print("\n>>> OWNER: /new")
            print("<<< (fresh conversation started; preferences still apply)")
            continue
        print(f"\n>>> OWNER: {turn}")
        trace_before = len(agent.trace)
        try:
            reply = agent.send(turn)
        except RateLimited as e:
            print(f"\n!!! Stopped: Groq rate limit hit ({e}).")
            print("    Everything above this line ran for real against the model.")
            print("    Try again later, or set GROQ_MODEL to a smaller model with a higher free-tier cap.")
            break
        for call in agent.trace[trace_before:]:
            print(f"    [tool] {call['tool']}({call['args']}) -> {call['result']}")
        print(f"<<< AGENT: {reply}")
    print("\n--- preferences after this chat (durable, outside conversation history) ---")
    print(get_all_preferences())
    print("\nCheck data/generated/ for the invoice PDF and analysis PPTX if those steps succeeded.")


def run_interactive():
    init_db()
    agent = GroqAgent()
    print("Supermarket Ops Agent (Groq diagnostic mode). Ctrl-C to quit, type /new to reset.")
    while True:
        try:
            text = input("owner> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/new":
            agent.reset()
            print("(fresh conversation; preferences still apply)")
            continue
        try:
            print(agent.send(text))
        except RateLimited as e:
            print(f"Rate limit hit: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", action="store_true", help="run the scripted assignment scenario")
    args = parser.parse_args()
    if not os.environ.get("GROQ_API_KEY"):
        print("Set GROQ_API_KEY first.", file=sys.stderr)
        sys.exit(1)
    if args.scenario:
        run_scenario()
    else:
        run_interactive()

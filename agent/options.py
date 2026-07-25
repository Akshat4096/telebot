"""
Assembles the ClaudeAgentOptions for the store-ops agent: the system prompt
(rebuilt from durable owner preferences on every session — this is the
"memory outside the context window" the assignment asks for), the in-process
kirana MCP tool server, the Agent Skills (SKILL.md files under skills/), and
a document-generation subagent.

Tool exposure is deliberately locked down: `tools=["Skill"]` disables every
built-in tool (Bash, Read, Write, WebSearch, ...) so the only things the
model can do are (a) read the domain skill files and (b) call the kirana
MCP tools. There is no path for the model to touch the filesystem or a
shell directly — every mutation goes through the tool layer, where the
guardrails live.
"""
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition

from tools.server import build_kirana_mcp_server
from tools.preferences import get_all_preferences

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Every kirana tool, fully qualified the way the Claude Agent SDK exposes
# in-process MCP tools to the model: mcp__<server_name>__<tool_name>.
_KIRANA_TOOL_NAMES = [
    "add_product", "receive_stock", "get_stock", "low_stock_report", "list_products", "adjust_stock",
    "start_bill", "add_bill_item", "remove_bill_item", "update_bill_item_qty", "set_payment_mode",
    "view_bill_draft", "finalize_bill", "void_bill",
    "credit_sale", "record_payment", "get_balance", "list_debtors",
    "sales_summary", "set_preference", "get_preference",
    "generate_invoice_pdf", "generate_analysis_deck",
]
KIRANA_TOOLS = [f"mcp__kirana__{name}" for name in _KIRANA_TOOL_NAMES]

# The document subagent only ever reads state and renders files — it never
# gets billing/khata/inventory *mutation* tools, so delegating to it can
# never be how a sale or stock change accidentally happens.
_DOCUMENT_TOOL_NAMES = [
    "generate_invoice_pdf", "generate_analysis_deck", "sales_summary",
    "low_stock_report", "view_bill_draft", "get_preference",
]
DOCUMENT_TOOLS = [f"mcp__kirana__{name}" for name in _DOCUMENT_TOOL_NAMES]


BASE_SYSTEM_PROMPT = """You are the Supermarket Ops Agent — you run a small Indian kirana
store end-to-end from this Telegram chat, on behalf of the shop owner. There is no
menu, app or admin panel; the chat is the product. The owner will type in plain,
terse, real-shopkeeper English ("2kg sugar, 1 atta, UPI") — interpret it, don't
demand structured input.

Hard rules (enforced in the tools, but you must respect them rather than work
around them):
- Never invent a product, price, GST rate, HSN code or stock quantity. Always
  call a tool to look it up. If it's genuinely not in the catalogue, say so.
- If a request is ambiguous (which brand/size of a product, which of two
  similarly-named customers, an unclear quantity or unit), ask a short
  clarifying question instead of guessing — unless a stored preference
  already answers it (check get_preference first).
- A bill is a draft until finalize_bill is called; nothing is sold or
  deducted from stock before that. Multiple items and edits in one message
  are normal — batch the tool calls, don't make the owner repeat themselves.
- finalize_bill needs a stable idempotency_key; reuse the same key if you
  retry the same finalize instead of minting a new one.
- Tool refusals (oversell, below-cost, unknown khata, overpayment, etc.) are
  deliberate guardrails, not bugs — relay the real numbers back to the owner
  and ask how they'd like to proceed; don't silently retry with different
  numbers to make it succeed.
- When the owner tells you a standing preference ("always assume UPI unless
  I say cash", "default atta is Aashirvaad 5kg", the shop's name/GSTIN),
  save it with set_preference so it applies in future chats too, and
  confirm you've remembered it.
- For PDF invoices and analysis decks, delegate to the document-writer
  subagent rather than trying to reason about chart layout yourself.

Keep replies short and concrete — a shopkeeper on a phone, not a report.
Always show GST breakup (taxable value, CGST, SGST, round-off, total) on
bills, not just a final number.
"""


def build_system_prompt() -> str:
    """Rebuilt from the owner_preferences table at the start of every session
    (including after /new) — this is what makes preferences durable memory
    rather than something that lives only in conversation history."""
    prefs = get_all_preferences()
    if not prefs:
        return BASE_SYSTEM_PROMPT + "\nNo standing owner preferences saved yet."
    lines = "\n".join(f"- {k}: {v}" for k, v in prefs.items())
    return BASE_SYSTEM_PROMPT + f"\nStanding owner preferences (apply these by default):\n{lines}\n"


def build_options(chat_id: str) -> ClaudeAgentOptions:
    # start_bill is bound to this chat_id server-side (tools/tool_specs.py::
    # build_tool_specs) — the model is never asked to supply a chat
    # identifier it has no way of knowing.
    kirana_server = build_kirana_mcp_server(chat_id)

    document_writer = AgentDefinition(
        description=(
            "Generates finished documents: GST invoice PDFs for a finalized bill, and "
            "PPTX sales/stock analysis decks for a date range. Delegate here for any "
            "'send me a PDF' / 'make a deck' / 'presentation' request instead of handling "
            "document layout in the main conversation."
        ),
        prompt=(
            "You produce one document per invocation and report back the file path plus a "
            "one-line summary of what's in it. Use sales_summary and low_stock_report to "
            "gather the numbers for a deck; use view_bill_draft to confirm a bill is "
            "finalized before generating its invoice. Never fabricate figures — every "
            "number must come from a tool call."
        ),
        tools=DOCUMENT_TOOLS,
        model="claude-sonnet-4-5",
    )

    return ClaudeAgentOptions(
        system_prompt=build_system_prompt(),
        mcp_servers={"kirana": kirana_server},
        tools=["Skill"],                      # disable Bash/Read/Write/WebSearch/... entirely
        allowed_tools=[*KIRANA_TOOLS, "Skill", "Task"],
        permission_mode="bypassPermissions",  # headless bot process; no human to click "allow"
        cwd=str(PROJECT_ROOT),
        setting_sources=["project"],           # discover skills/*/SKILL.md under the project
        skills="all",
        agents={"document-writer": document_writer},
        model="claude-sonnet-4-5",
        max_turns=40,
        # NOTE: deliberately not setting `user=chat_id` here — it's a CLI-level
        # option for tagging the underlying process and isn't supported on
        # Windows ("The 'user' parameter is not supported on the current
        # platform"), and it wasn't doing any actual work for us: chat_id
        # binding already happens via build_kirana_mcp_server(chat_id) above.
    )

"""
Wraps the shared tool specs (tools/tool_specs.py) as an in-process Claude
Agent SDK MCP server. This is the *only* file that knows about the SDK's
tool wire format — every tools/*.py business-logic module is plain Python
you could unit test (and we do, see tests/) without an agent anywhere in
the loop, and tool_specs.py is the single source of truth for the tool
surface shared with any other tool-calling front end (see
scripts/groq_agent.py for a diagnostic example using a different provider).

Design principle: tools are thin adapters over the business-logic functions;
they don't re-implement any rule. The oversell guard, GST math, idempotency
and khata rules all live in the functions being wrapped, not here and not in
the system prompt — so no amount of prompt drift can bypass them.
"""
from __future__ import annotations

import functools
import json
from typing import Callable

from claude_agent_sdk import tool, create_sdk_mcp_server

from tools.tool_specs import build_tool_specs
from tools.errors import ToolError, AmbiguousProductError


def _wrap(fn: Callable) -> Callable:
    """Turn a sync tool-layer function into the async (args_dict) -> result_dict
    handler shape the SDK expects, translating ToolError subclasses into
    structured, model-readable refusals instead of raw exceptions."""

    @functools.wraps(fn)
    async def handler(args: dict) -> dict:
        try:
            result = fn(**args)
            return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}
        except AmbiguousProductError as e:
            payload = {"error": str(e), "error_type": "ambiguous_product", "candidates": e.candidates}
            return {"content": [{"type": "text", "text": json.dumps(payload)}], "is_error": True}
        except ToolError as e:
            payload = {"error": str(e), "error_type": type(e).__name__}
            return {"content": [{"type": "text", "text": json.dumps(payload)}], "is_error": True}

    return handler


def _make_tools(chat_id: str) -> list:
    return [tool(spec.name, spec.description, spec.schema)(_wrap(spec.fn))
            for spec in build_tool_specs(chat_id)]


def build_kirana_mcp_server(chat_id: str):
    """One MCP server per Telegram chat: start_bill is bound to this exact
    chat_id server-side (see tools/tool_specs.py::build_tool_specs) so the
    model is never asked to supply a chat identifier it has no way of
    knowing — it only ever sees the bill_id that start_bill hands back."""
    return create_sdk_mcp_server(name="kirana", version="1.0.0", tools=_make_tools(chat_id))

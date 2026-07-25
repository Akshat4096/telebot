"""
Single source of truth for the kirana tool surface: name, description, JSON
Schema, and the underlying Python function. Both the Claude Agent SDK
wrapper (tools/server.py, the real submission) and any other tool-calling
front end (e.g. scripts/groq_agent.py, a diagnostic harness for testing with
a different provider) build their tool definitions from this one list —
so the two can never drift apart, and the guardrails/GST math/idempotency
logic in tools/{inventory,billing,khata,...}.py is exercised identically
either way.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Callable

from tools import inventory, billing, khata, daily_close, preferences, documents


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict          # JSON Schema {"type": "object", "properties": {...}, "required": [...]}
    fn: Callable


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


STR = {"type": "string"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}


TOOL_SPECS: list[ToolSpec] = [
    # ---------------- Inventory ----------------
    ToolSpec("add_product", "Add a brand-new SKU to the catalogue. Refuses if it already exists.",
             _schema({
                 "name": STR | {"description": "Full product name, e.g. 'Amul Butter 100g'"},
                 "unit": {"type": "string", "enum": ["kg", "g", "litre", "ml", "packet", "dozen", "piece"]},
                 "gst_rate": NUM | {"description": "GST percent applied to MRP, e.g. 0, 5, 12, 18"},
                 "mrp": NUM | {"description": "GST-inclusive selling price per unit"},
                 "cost_price": NUM | {"description": "What the shop pays per unit"},
                 "hsn_code": STR,
                 "brand": STR, "category": STR,
                 "is_loose": BOOL | {"description": "True for loose goods sold by weight, e.g. sugar/rice/dal"},
                 "reorder_level": NUM, "opening_quantity": NUM,
             }, ["name", "unit", "gst_rate", "mrp", "cost_price", "hsn_code"]),
             inventory.add_product),

    ToolSpec("receive_stock", "Record stock coming in from a supplier. Increases quantity; "
                               "optionally updates cost price / MRP if the supplier's price changed.",
             _schema({
                 "product_query": STR | {"description": "Product name as the owner typed it"},
                 "quantity": NUM, "cost_price": NUM, "mrp": NUM,
             }, ["product_query", "quantity"]),
             inventory.receive_stock),

    ToolSpec("get_stock", "Look up current stock, price and reorder level for one product.",
             _schema({"product_query": STR}, ["product_query"]), inventory.get_stock),

    ToolSpec("low_stock_report", "List every product at or below its reorder level.",
             _schema({}, []), inventory.low_stock_report),

    ToolSpec("list_products", "List active catalogue products, optionally filtered by category.",
             _schema({"category": STR}, []), inventory.list_products),

    ToolSpec("adjust_stock", "Manual stock correction (breakage, recount). Never deletes a SKU; "
                              "logs a +/- ledger entry and refuses to take stock negative.",
             _schema({"product_query": STR, "delta": NUM, "reason_note": STR},
                      ["product_query", "delta", "reason_note"]),
             inventory.adjust_stock),

    # ---------------- Billing ----------------
    # NOTE: "chat_id" here is a placeholder schema for TOOL_SPECS's fallback/
    # unbound use (see build_tool_specs below for the real, chat-bound
    # version used by the actual submission and the Groq harness). The model
    # is never told a real chat_id anywhere in the conversation, so leaving
    # this as an LLM-supplied parameter is a footgun — it will invent one.
    ToolSpec("start_bill", "Start (or resume) a draft bill for this chat. A chat has at most one open draft.",
             _schema({"chat_id": STR}, ["chat_id"]), billing.start_bill),

    ToolSpec("add_bill_item", "Add a line item to a draft bill. Refuses if requested quantity exceeds "
                               "available stock, or if the price is below cost without an explicit override.",
             _schema({
                 "bill_id": NUM, "product_query": STR, "qty": NUM,
                 "unit_price_override": NUM, "allow_below_cost": BOOL,
             }, ["bill_id", "product_query", "qty"]),
             billing.add_bill_item),

    ToolSpec("remove_bill_item", "Remove a product entirely from a draft bill.",
             _schema({"bill_id": NUM, "product_query": STR}, ["bill_id", "product_query"]),
             billing.remove_bill_item),

    ToolSpec("update_bill_item_qty", "Change the quantity of a product already on a draft bill.",
             _schema({"bill_id": NUM, "product_query": STR, "new_qty": NUM},
                      ["bill_id", "product_query", "new_qty"]),
             billing.update_bill_item_qty),

    ToolSpec("set_payment_mode", "Set how a draft bill will be paid. Use payment_mode='credit' with "
                                  "customer_query to put it on that customer's khata.",
             _schema({
                 "bill_id": NUM, "payment_mode": {"type": "string", "enum": ["cash", "upi", "card", "credit"]},
                 "payment_ref": STR, "customer_query": STR,
             }, ["bill_id", "payment_mode"]),
             billing.set_payment_mode),

    ToolSpec("view_bill_draft", "Show the current state of a draft or finalized bill: lines, GST breakup, total.",
             _schema({"bill_id": NUM}, ["bill_id"]), billing.view_bill_draft),

    ToolSpec("finalize_bill", "Finalize a draft bill: decrements stock, computes final GST, charges khata "
                               "if on credit. MUST be called with a stable idempotency_key — calling it again "
                               "with the same key returns the original result and does not double-decrement "
                               "stock or double-charge. Generate the key once per finalize *intent* (e.g. "
                               "'bill-<bill_id>-final') and reuse it if you have to retry.",
             _schema({"bill_id": NUM, "idempotency_key": STR}, ["bill_id", "idempotency_key"]),
             billing.finalize_bill),

    ToolSpec("void_bill", "Void a draft bill that won't be completed (e.g. customer walked away). "
                          "Cannot void an already-finalized bill.",
             _schema({"bill_id": NUM, "reason": STR}, ["bill_id", "reason"]), billing.void_bill),

    # ---------------- Khata ----------------
    ToolSpec("credit_sale", "Put an amount on a customer's khata (credit ledger) directly, without a "
                             "full itemized bill. Creates the customer if new.",
             _schema({"customer_name": STR, "amount": NUM, "note": STR, "idempotency_key": STR},
                      ["customer_name", "amount"]),
             khata.credit_sale),

    ToolSpec("record_payment", "Record a customer paying down their khata balance. Refuses if the amount "
                                "exceeds what they owe, unless allow_overpayment is explicitly set.",
             _schema({"customer_name": STR, "amount": NUM, "allow_overpayment": BOOL, "idempotency_key": STR},
                      ["customer_name", "amount"]),
             khata.record_payment),

    ToolSpec("get_balance", "Get a customer's current khata balance and recent ledger entries.",
             _schema({"customer_name": STR}, ["customer_name"]), khata.get_balance),

    ToolSpec("list_debtors", "List all customers with an outstanding khata balance, highest first.",
             _schema({"min_balance": NUM}, []), khata.list_debtors),

    # ---------------- Analytics ----------------
    ToolSpec("sales_summary", "Sales summary for a date range: totals, GST collected, split by payment "
                               "mode, top-selling items. Use today's date for both fields for a daily close.",
             _schema({"date_from": STR, "date_to": STR}, ["date_from", "date_to"]), daily_close.sales_summary),

    # ---------------- Preferences (durable memory) ----------------
    ToolSpec("set_preference", "Remember a standing owner preference (e.g. default payment mode, "
                                "preferred brand, shop name/GSTIN) so it applies in future chats too.",
             _schema({"key": STR, "value": STR}, ["key", "value"]), preferences.set_preference),

    ToolSpec("get_preference", "Look up one stored owner preference by key.",
             _schema({"key": STR}, ["key"]), preferences.get_preference),

    # ---------------- Documents ----------------
    ToolSpec("generate_invoice_pdf", "Generate a GST-correct PDF invoice for a finalized bill. Returns "
                                      "the file path.",
             _schema({"bill_id": NUM}, ["bill_id"]), documents.generate_invoice_pdf),

    ToolSpec("generate_analysis_deck", "Generate a PPTX sales/stock analysis deck with charts for a date "
                                        "range. Returns the file path.",
             _schema({"date_from": STR, "date_to": STR, "title": STR},
                      ["date_from", "date_to"]), documents.generate_analysis_deck),
]

TOOL_SPECS_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


def build_tool_specs(chat_id: str) -> list[ToolSpec]:
    """
    The chat-bound tool surface actually used at runtime (by both
    tools/server.py for the real submission and scripts/groq_agent.py for
    the diagnostic harness): identical to TOOL_SPECS except start_bill has
    chat_id baked in server-side via functools.partial and dropped from the
    schema entirely, so the model is never asked to supply a value it has
    no way of knowing. Every other tool is unchanged — once start_bill
    returns a bill_id, that's an opaque handle the model tracks in
    conversation like any other tool result, which is a normal thing for an
    LLM to do (no session plumbing involved).
    """
    bound = []
    for spec in TOOL_SPECS:
        if spec.name == "start_bill":
            bound.append(ToolSpec(
                name="start_bill",
                description=spec.description,
                schema=_schema({}, []),
                fn=functools.partial(billing.start_bill, chat_id),
            ))
        else:
            bound.append(spec)
    return bound

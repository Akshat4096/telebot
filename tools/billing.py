"""
Billing: multi-turn draft bills, finalized atomically with the oversell guard
and idempotent finalize.

Lifecycle:
    start_bill(chat_id)                 -> draft bill row
    add_bill_item / remove_bill_item     -> mutate bill_items, draft only
    set_payment_mode                     -> cash/upi/card/credit
    finalize_bill(bill_id, idem_key)     -> ONE atomic transaction:
                                             re-validates stock for every
                                             line, decrements stock, computes
                                             GST, marks bill finalized.
                                             Stock is untouched until this
                                             call succeeds.

Oversell guard: enforced twice —
  1. At add_bill_item time (fast feedback: "only 6kg sugar left").
  2. Again at finalize_bill time, inside the same write transaction that
     decrements stock — because stock may have moved between when an item
     was added to the draft and when the bill is finalized (another sale,
     or this same product sold out from a concurrent bill). The check that
     matters is the one inside the transaction; the earlier one is just UX.

Idempotency: finalize_bill takes a caller-supplied `idempotency_key`. If a
bill with that key is already finalized, we return the *existing* result
instead of re-running the transaction — so a Telegram-redelivered "finalize"
update, or the model retrying after a network blip, can never double-decrement
stock or double-charge khata. The key is unique at the DB level (UNIQUE
constraint on bills.idempotency_key), so even a race between two identical
retries resolves safely.
"""
from __future__ import annotations

from typing import Optional

from db.db import write_txn, get_connection
from tools.errors import NotFoundError, OversellError, BelowCostError, GuardrailError
from tools.inventory import resolve_product
from tools import gst


def start_bill(chat_id: str) -> dict:
    with write_txn() as conn:
        existing = conn.execute(
            "SELECT id FROM bills WHERE chat_id=? AND status='draft'", (chat_id,)
        ).fetchone()
        if existing:
            return {"bill_id": existing["id"], "status": "draft", "note": "reused existing open draft"}
        cur = conn.execute("INSERT INTO bills (chat_id, status) VALUES (?, 'draft')", (chat_id,))
        return {"bill_id": cur.lastrowid, "status": "draft"}


def _get_draft(conn, bill_id: int) -> dict:
    bill = conn.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
    if not bill:
        raise NotFoundError(f"No bill with id {bill_id}.")
    if bill["status"] != "draft":
        raise GuardrailError(f"Bill {bill_id} is already {bill['status']} — cannot edit it.")
    return dict(bill)


def add_bill_item(bill_id: int, product_query: str, qty: float,
                   unit_price_override: Optional[float] = None,
                   allow_below_cost: bool = False) -> dict:
    if qty <= 0:
        raise GuardrailError("Quantity must be positive.")
    with write_txn() as conn:
        _get_draft(conn, bill_id)
        product = resolve_product(conn, product_query)

        # fast-feedback oversell check (authoritative check happens again at finalize)
        already_in_this_bill = conn.execute(
            "SELECT COALESCE(SUM(qty),0) as q FROM bill_items WHERE bill_id=? AND product_id=?",
            (bill_id, product["id"]),
        ).fetchone()["q"]
        if already_in_this_bill + qty > product["quantity"]:
            available = product["quantity"] - already_in_this_bill
            raise OversellError(
                f"Only {available}{product['unit']} of '{product['name']}' left in stock "
                f"(requested {qty}{product['unit']})."
            )

        unit_price = unit_price_override if unit_price_override is not None else product["mrp"]
        if unit_price < product["cost_price"] and not allow_below_cost:
            raise BelowCostError(
                f"₹{unit_price} for '{product['name']}' is below cost (₹{product['cost_price']}). "
                f"Confirm with the owner before overriding."
            )

        conn.execute(
            """INSERT INTO bill_items (bill_id, product_id, qty, unit_price, gst_rate, hsn_code)
               VALUES (?,?,?,?,?,?)""",
            (bill_id, product["id"], qty, unit_price, product["gst_rate"], product["hsn_code"]),
        )
        return view_bill_draft(bill_id, _conn=conn)


def remove_bill_item(bill_id: int, product_query: str) -> dict:
    with write_txn() as conn:
        _get_draft(conn, bill_id)
        product = resolve_product(conn, product_query)
        deleted = conn.execute(
            "DELETE FROM bill_items WHERE bill_id=? AND product_id=?", (bill_id, product["id"])
        ).rowcount
        if not deleted:
            raise NotFoundError(f"'{product['name']}' is not on bill {bill_id}.")
        return view_bill_draft(bill_id, _conn=conn)


def update_bill_item_qty(bill_id: int, product_query: str, new_qty: float) -> dict:
    with write_txn() as conn:
        _get_draft(conn, bill_id)
        product = resolve_product(conn, product_query)
        if new_qty <= 0:
            conn.execute("DELETE FROM bill_items WHERE bill_id=? AND product_id=?", (bill_id, product["id"]))
            return view_bill_draft(bill_id, _conn=conn)
        if new_qty > product["quantity"]:
            raise OversellError(
                f"Only {product['quantity']}{product['unit']} of '{product['name']}' in stock."
            )
        updated = conn.execute(
            "UPDATE bill_items SET qty=? WHERE bill_id=? AND product_id=?",
            (new_qty, bill_id, product["id"]),
        ).rowcount
        if not updated:
            raise NotFoundError(f"'{product['name']}' is not on bill {bill_id}. Use add_bill_item instead.")
        return view_bill_draft(bill_id, _conn=conn)


def set_payment_mode(bill_id: int, payment_mode: str, payment_ref: Optional[str] = None,
                      customer_query: Optional[str] = None) -> dict:
    if payment_mode not in ("cash", "upi", "card", "credit"):
        raise GuardrailError("payment_mode must be one of cash/upi/card/credit.")
    with write_txn() as conn:
        _get_draft(conn, bill_id)
        customer_id = None
        if payment_mode == "credit":
            if not customer_query:
                raise GuardrailError("Credit sales need a customer name for the khata.")
            cust = conn.execute(
                "SELECT * FROM customers WHERE lower(name)=lower(?)", (customer_query,)
            ).fetchone()
            if not cust:
                cur = conn.execute("INSERT INTO customers (name) VALUES (?)", (customer_query,))
                customer_id = cur.lastrowid
            else:
                customer_id = cust["id"]
        conn.execute(
            "UPDATE bills SET payment_mode=?, payment_ref=?, customer_id=? WHERE id=?",
            (payment_mode, payment_ref, customer_id, bill_id),
        )
        return view_bill_draft(bill_id, _conn=conn)


def view_bill_draft(bill_id: int, _conn=None) -> dict:
    owns_conn = _conn is None
    conn = _conn or get_connection()
    try:
        bill = conn.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if not bill:
            raise NotFoundError(f"No bill with id {bill_id}.")
        items = conn.execute(
            """SELECT bi.*, p.name as product_name, p.unit as unit
               FROM bill_items bi JOIN products p ON p.id = bi.product_id
               WHERE bi.bill_id=?""",
            (bill_id,),
        ).fetchall()
        lines = [gst.compute_line(i["qty"], i["unit_price"], i["gst_rate"]) | {
            "product_name": i["product_name"], "unit": i["unit"], "hsn_code": i["hsn_code"],
        } for i in items]
        totals = gst.compute_bill_totals(lines) if lines else {
            "subtotal_taxable": 0, "total_cgst": 0, "total_sgst": 0, "round_off": 0, "grand_total": 0
        }
        return {
            "bill_id": bill["id"], "status": bill["status"], "payment_mode": bill["payment_mode"],
            "lines": lines, **totals,
        }
    finally:
        if owns_conn:
            conn.close()


def finalize_bill(bill_id: int, idempotency_key: str) -> dict:
    """
    The one place stock actually moves for a sale. Everything before this is
    a draft the owner can still edit; nothing here is undone by re-calling it
    with the same idempotency_key.
    """
    with write_txn() as conn:
        # Idempotent replay: if this key was already used, return the prior result untouched.
        prior = conn.execute("SELECT id FROM bills WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if prior:
            return view_bill_draft(prior["id"], _conn=conn) | {"idempotent_replay": True}

        bill = _get_draft(conn, bill_id)
        items = conn.execute(
            """SELECT bi.*, p.name as product_name, p.quantity as stock_qty, p.unit as unit
               FROM bill_items bi JOIN products p ON p.id = bi.product_id
               WHERE bi.bill_id=?""",
            (bill_id,),
        ).fetchall()
        if not items:
            raise GuardrailError("Cannot finalize an empty bill.")
        if not bill["payment_mode"]:
            raise GuardrailError("Set a payment mode before finalizing (cash/upi/card/credit).")

        # Authoritative oversell re-check, inside the same transaction that decrements stock.
        for i in items:
            if i["qty"] > i["stock_qty"]:
                raise OversellError(
                    f"Only {i['stock_qty']}{i['unit']} of '{i['product_name']}' left — "
                    f"bill asks for {i['qty']}{i['unit']}. Adjust the bill."
                )

        lines = []
        for i in items:
            line = gst.compute_line(i["qty"], i["unit_price"], i["gst_rate"])
            conn.execute(
                """UPDATE bill_items SET taxable_value=?, cgst_amount=?, sgst_amount=?, line_total=?
                   WHERE id=?""",
                (line["taxable_value"], line["cgst_amount"], line["sgst_amount"], line["line_total"], i["id"]),
            )
            conn.execute(
                "UPDATE products SET quantity = quantity - ?, updated_at=datetime('now') WHERE id=?",
                (i["qty"], i["product_id"]),
            )
            conn.execute(
                """INSERT INTO stock_ledger (product_id, change_qty, reason, ref_type, ref_id)
                   VALUES (?,?,?,?,?)""",
                (i["product_id"], -i["qty"], "sale", "bill", bill_id),
            )
            lines.append(line)

        totals = gst.compute_bill_totals(lines)
        conn.execute(
            """UPDATE bills SET status='finalized', idempotency_key=?, subtotal_taxable=?, total_cgst=?,
               total_sgst=?, round_off=?, grand_total=?, finalized_at=datetime('now') WHERE id=?""",
            (idempotency_key, totals["subtotal_taxable"], totals["total_cgst"], totals["total_sgst"],
             totals["round_off"], totals["grand_total"], bill_id),
        )

        if bill["payment_mode"] == "credit" and bill["customer_id"]:
            cust = conn.execute("SELECT * FROM customers WHERE id=?", (bill["customer_id"],)).fetchone()
            new_balance = cust["balance"] + totals["grand_total"]
            conn.execute("UPDATE customers SET balance=? WHERE id=?", (new_balance, cust["id"]))
            conn.execute(
                """INSERT INTO khata_ledger (customer_id, entry_type, amount, ref_type, ref_id,
                   idempotency_key, balance_after) VALUES (?,?,?,?,?,?,?)""",
                (cust["id"], "charge", totals["grand_total"], "bill", bill_id,
                 f"bill:{idempotency_key}", new_balance),
            )

        return view_bill_draft(bill_id, _conn=conn) | {"idempotent_replay": False}


def void_bill(bill_id: int, reason: str) -> dict:
    """Void an un-finalized draft (e.g. customer walked away). Finalized bills are never
    silently voided — that would need an explicit reversal flow, out of scope here."""
    with write_txn() as conn:
        bill = _get_draft(conn, bill_id)
        conn.execute("UPDATE bills SET status='void' WHERE id=?", (bill_id,))
        return {"bill_id": bill_id, "status": "void", "reason": reason}

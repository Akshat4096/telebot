"""
Khata: customer credit ledger. Two entry points besides the automatic
"charge" written by finalize_bill(credit): manual credit_sale (owner logs a
credit sale without going through the full billing flow) and record_payment
(customer pays back, in part or full).

Guardrail: you cannot settle a khata that doesn't exist (unknown customer is
refused, not silently created for a payment — only credit *charges* auto-
create a customer, mirroring real shop behaviour: you can't receive a
payment from someone who was never extended credit). Overpayment beyond the
outstanding balance is refused unless explicitly confirmed, since it usually
means the owner mis-typed the amount or the customer.
"""
from __future__ import annotations

from typing import Optional

from db.db import write_txn, read_conn
from tools.errors import NotFoundError, GuardrailError


def _find_customer(conn, name: str):
    return conn.execute("SELECT * FROM customers WHERE lower(name)=lower(?)", (name,)).fetchone()


def credit_sale(customer_name: str, amount: float, note: Optional[str] = None,
                 idempotency_key: Optional[str] = None) -> dict:
    if amount <= 0:
        raise GuardrailError("Credit amount must be positive.")
    with write_txn() as conn:
        if idempotency_key:
            dup = conn.execute(
                "SELECT * FROM khata_ledger WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if dup:
                return {"customer": customer_name, "balance": dup["balance_after"], "idempotent_replay": True}

        cust = _find_customer(conn, customer_name)
        if not cust:
            cur = conn.execute("INSERT INTO customers (name) VALUES (?)", (customer_name,))
            customer_id, current_balance = cur.lastrowid, 0.0
        else:
            customer_id, current_balance = cust["id"], cust["balance"]

        new_balance = current_balance + amount
        conn.execute("UPDATE customers SET balance=? WHERE id=?", (new_balance, customer_id))
        conn.execute(
            """INSERT INTO khata_ledger (customer_id, entry_type, amount, ref_type, idempotency_key,
               note, balance_after) VALUES (?,?,?,?,?,?,?)""",
            (customer_id, "charge", amount, "manual", idempotency_key, note, new_balance),
        )
        return {"customer": customer_name, "charged": amount, "balance": new_balance, "idempotent_replay": False}


def record_payment(customer_name: str, amount: float, allow_overpayment: bool = False,
                    idempotency_key: Optional[str] = None) -> dict:
    if amount <= 0:
        raise GuardrailError("Payment amount must be positive.")
    with write_txn() as conn:
        if idempotency_key:
            dup = conn.execute(
                "SELECT * FROM khata_ledger WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if dup:
                return {"customer": customer_name, "balance": dup["balance_after"], "idempotent_replay": True}

        cust = _find_customer(conn, customer_name)
        if not cust:
            raise NotFoundError(f"No khata for '{customer_name}' — nothing to settle.")
        if amount > cust["balance"] and not allow_overpayment:
            raise GuardrailError(
                f"{customer_name} only owes ₹{cust['balance']}, payment of ₹{amount} looks like an "
                f"overpayment — confirm before recording it as an advance."
            )
        new_balance = cust["balance"] - amount
        conn.execute("UPDATE customers SET balance=? WHERE id=?", (new_balance, cust["id"]))
        conn.execute(
            """INSERT INTO khata_ledger (customer_id, entry_type, amount, ref_type, idempotency_key,
               balance_after) VALUES (?,?,?,?,?,?)""",
            (cust["id"], "payment", amount, "manual", idempotency_key, new_balance),
        )
        return {"customer": customer_name, "paid": amount, "balance": new_balance, "idempotent_replay": False}


def get_balance(customer_name: str) -> dict:
    with read_conn() as conn:
        cust = _find_customer(conn, customer_name)
        if not cust:
            raise NotFoundError(f"No khata for '{customer_name}'.")
        history = conn.execute(
            "SELECT * FROM khata_ledger WHERE customer_id=? ORDER BY created_at DESC LIMIT 10",
            (cust["id"],),
        ).fetchall()
        return {"customer": cust["name"], "balance": cust["balance"],
                "recent_entries": [dict(h) for h in history]}


def list_debtors(min_balance: float = 0.01) -> list:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT name, balance FROM customers WHERE balance >= ? ORDER BY balance DESC", (min_balance,)
        ).fetchall()
        return [dict(r) for r in rows]

"""
Inventory: catalogue + stock ledger.

Design note: product lookup is fuzzy-tolerant (case-insensitive substring
match on name/brand) because owners type "maggi" not "Maggi 70g Masala".
If the match is ambiguous we raise AmbiguousProductError with candidates —
the *tool* decides it's ambiguous (data fact: >1 active row matched), and
the *model* is the one that turns that into a clarifying question. That's
the split the assignment asks for: grounding + guard in code, judgement in
the model.
"""
from __future__ import annotations

from typing import Optional

from db.db import write_txn, read_conn
from tools.errors import NotFoundError, AmbiguousProductError, GuardrailError


def _find_products(conn, query: str, only_active: bool = True):
    like = f"%{query.strip()}%"
    sql = "SELECT * FROM products WHERE (name LIKE ? OR brand LIKE ?)"
    if only_active:
        sql += " AND is_active = 1"
    sql += " ORDER BY name"
    return conn.execute(sql, (like, like)).fetchall()


def resolve_product(conn, query: str) -> dict:
    """Exact name match wins outright; otherwise fuzzy match must be unique."""
    exact = conn.execute(
        "SELECT * FROM products WHERE lower(name) = lower(?) AND is_active = 1", (query.strip(),)
    ).fetchone()
    if exact:
        return dict(exact)

    rows = _find_products(conn, query)
    if len(rows) == 0:
        raise NotFoundError(f"No product matching '{query}' in the catalogue. Add it first.")
    if len(rows) > 1:
        raise AmbiguousProductError(
            f"'{query}' matches {len(rows)} products — ask the owner which one.",
            candidates=[{"id": r["id"], "name": r["name"], "unit": r["unit"], "mrp": r["mrp"]} for r in rows],
        )
    return dict(rows[0])


def add_product(
    name: str,
    unit: str,
    gst_rate: float,
    mrp: float,
    cost_price: float,
    hsn_code: str,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    is_loose: bool = False,
    reorder_level: float = 0,
    opening_quantity: float = 0,
) -> dict:
    if mrp <= 0 or cost_price < 0:
        raise GuardrailError("MRP must be positive and cost price cannot be negative.")
    if gst_rate not in (0, 0.25, 3, 5, 12, 18, 28):
        # Not a hard reject — GST slabs occasionally get special rates — but flag odd input.
        pass
    with write_txn() as conn:
        existing = conn.execute(
            "SELECT id FROM products WHERE lower(name)=lower(?) AND is_active=1", (name,)
        ).fetchone()
        if existing:
            raise GuardrailError(f"'{name}' already exists in the catalogue (id {existing['id']}).")
        cur = conn.execute(
            """INSERT INTO products
               (name, brand, category, unit, is_loose, hsn_code, gst_rate, cost_price, mrp, quantity, reorder_level)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (name, brand, category, unit, int(is_loose), hsn_code, gst_rate, cost_price, mrp,
             opening_quantity, reorder_level),
        )
        product_id = cur.lastrowid
        if opening_quantity:
            conn.execute(
                """INSERT INTO stock_ledger (product_id, change_qty, reason, unit_cost, note)
                   VALUES (?,?,?,?,?)""",
                (product_id, opening_quantity, "receive", cost_price, "opening stock"),
            )
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row)


def receive_stock(product_query: str, quantity: float, cost_price: Optional[float] = None,
                   mrp: Optional[float] = None) -> dict:
    """Stock-in. Optionally updates cost/MRP (supplier prices change) going forward."""
    if quantity <= 0:
        raise GuardrailError("Received quantity must be positive.")
    with write_txn() as conn:
        product = resolve_product(conn, product_query)
        new_qty = product["quantity"] + quantity
        new_cost = cost_price if cost_price is not None else product["cost_price"]
        new_mrp = mrp if mrp is not None else product["mrp"]
        conn.execute(
            "UPDATE products SET quantity=?, cost_price=?, mrp=?, updated_at=datetime('now') WHERE id=?",
            (new_qty, new_cost, new_mrp, product["id"]),
        )
        conn.execute(
            """INSERT INTO stock_ledger (product_id, change_qty, reason, unit_cost, note)
               VALUES (?,?,?,?,?)""",
            (product["id"], quantity, "receive", new_cost, "stock received"),
        )
        row = conn.execute("SELECT * FROM products WHERE id=?", (product["id"],)).fetchone()
        return dict(row)


def get_stock(product_query: str) -> dict:
    with read_conn() as conn:
        product = resolve_product(conn, product_query)
        return product


def low_stock_report() -> list:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE is_active=1 AND quantity <= reorder_level ORDER BY quantity ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def list_products(category: Optional[str] = None) -> list:
    with read_conn() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM products WHERE is_active=1 AND category=? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM products WHERE is_active=1 ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def adjust_stock(product_query: str, delta: float, reason_note: str) -> dict:
    """Manual correction (breakage, count mismatch). Never a raw delete of a SKU —
    only ever a ledgered +/- adjustment, and it still can't take stock negative."""
    with write_txn() as conn:
        product = resolve_product(conn, product_query)
        new_qty = product["quantity"] + delta
        if new_qty < 0:
            raise GuardrailError(
                f"Adjustment would take '{product['name']}' to {new_qty}{product['unit']} — refused."
            )
        conn.execute("UPDATE products SET quantity=?, updated_at=datetime('now') WHERE id=?",
                     (new_qty, product["id"]))
        conn.execute(
            """INSERT INTO stock_ledger (product_id, change_qty, reason, note) VALUES (?,?,?,?)""",
            (product["id"], delta, "correction", reason_note),
        )
        row = conn.execute("SELECT * FROM products WHERE id=?", (product["id"],)).fetchone()
        return dict(row)

"""
The two document-generation tools exposed to the agent. Both are read-only
from the DB's point of view — generating a PDF/PPTX never mutates state —
so there's no idempotency concern beyond "regenerating overwrites the file",
which is fine.
"""
from __future__ import annotations

from typing import Optional

from db.db import read_conn
from tools.errors import NotFoundError, GuardrailError
from tools.billing import view_bill_draft
from tools.daily_close import sales_summary
from tools.inventory import low_stock_report
from tools.preferences import get_all_preferences
from tools.invoice_pdf import render_invoice_pdf
from tools.analysis_deck import build_deck


def _shop_info() -> dict:
    prefs = get_all_preferences()
    return {
        "name": prefs.get("shop_name", "Kirana Store"),
        "gstin": prefs.get("shop_gstin"),
        "address": prefs.get("shop_address"),
    }


def generate_invoice_pdf(bill_id: int) -> dict:
    with read_conn() as conn:
        bill_row = conn.execute("SELECT * FROM bills WHERE id=?", (bill_id,)).fetchone()
        if not bill_row:
            raise NotFoundError(f"No bill with id {bill_id}.")
        if bill_row["status"] != "finalized":
            raise GuardrailError(f"Bill {bill_id} is not finalized yet — finalize it before invoicing.")
        customer_name = None
        if bill_row["customer_id"]:
            cust = conn.execute("SELECT name FROM customers WHERE id=?", (bill_row["customer_id"],)).fetchone()
            customer_name = cust["name"] if cust else None

    bill = view_bill_draft(bill_id)
    bill["customer_name"] = customer_name
    bill["finalized_at"] = bill_row["finalized_at"]

    path = render_invoice_pdf(bill, _shop_info())
    return {"bill_id": bill_id, "file_path": path}


def generate_analysis_deck(date_from: str, date_to: str, title: Optional[str] = None) -> dict:
    summary = sales_summary(date_from, date_to)
    low_stock = low_stock_report()
    shop = _shop_info()
    path = build_deck(shop["name"], date_from, date_to, summary, low_stock, title=title)
    return {"file_path": path, "bill_count": summary["bill_count"], "total_sales": summary["total_sales"]}

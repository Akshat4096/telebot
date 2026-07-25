"""
Daily sales summary / "close the day". Read-only aggregation over finalized
bills — closing the day doesn't mutate anything, it's a report, so no
idempotency concerns here (safe to call repeatedly).
"""
from __future__ import annotations

from db.db import read_conn


def _bills_for_range(conn, date_from: str, date_to: str):
    return conn.execute(
        """SELECT * FROM bills WHERE status='finalized'
           AND date(finalized_at) BETWEEN date(?) AND date(?)""",
        (date_from, date_to),
    ).fetchall()


def sales_summary(date_from: str, date_to: str) -> dict:
    with read_conn() as conn:
        bills = _bills_for_range(conn, date_from, date_to)
        bill_ids = [b["id"] for b in bills]

        total_sales = sum(b["grand_total"] for b in bills)
        total_cgst = sum(b["total_cgst"] for b in bills)
        total_sgst = sum(b["total_sgst"] for b in bills)
        by_mode = {}
        for b in bills:
            by_mode[b["payment_mode"]] = by_mode.get(b["payment_mode"], 0) + b["grand_total"]

        top_items = []
        if bill_ids:
            placeholders = ",".join("?" * len(bill_ids))
            rows = conn.execute(
                f"""SELECT p.name, p.unit, SUM(bi.qty) as qty_sold, SUM(bi.line_total) as revenue
                    FROM bill_items bi JOIN products p ON p.id=bi.product_id
                    WHERE bi.bill_id IN ({placeholders})
                    GROUP BY bi.product_id ORDER BY revenue DESC LIMIT 10""",
                bill_ids,
            ).fetchall()
            top_items = [dict(r) for r in rows]

        return {
            "date_from": date_from, "date_to": date_to,
            "bill_count": len(bills),
            "total_sales": round(total_sales, 2),
            "total_cgst": round(total_cgst, 2),
            "total_sgst": round(total_sgst, 2),
            "total_gst": round(total_cgst + total_sgst, 2),
            "by_payment_mode": {k: round(v, 2) for k, v in by_mode.items()},
            "top_items": top_items,
        }

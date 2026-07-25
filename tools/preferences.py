"""
Owner preferences — the durable memory the assignment calls out explicitly:
"Memory lives outside the context window, not just in the conversation."

These are plain key/value rows in owner_preferences, read fresh into the
system prompt every time a session starts (see agent/options.py::build_system_prompt).
A `/new` chat throws away conversation history but the next session's system
prompt is rebuilt from this table, so "always assume UPI unless I say cash"
survives it.

Known keys (others are accepted freeform so the model can store whatever the
owner says without a schema migration):
  default_payment_mode   cash | upi | card
  default_atta_brand     e.g. "Aashirvaad Atta 5kg"
  shop_name, shop_gstin, shop_address   used on invoice letterhead
"""
from __future__ import annotations

from db.db import write_txn, read_conn


def set_preference(key: str, value: str) -> dict:
    with write_txn() as conn:
        conn.execute(
            """INSERT INTO owner_preferences (key, value, updated_at) VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')""",
            (key, value),
        )
        return {"key": key, "value": value}


def get_preference(key: str) -> dict:
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM owner_preferences WHERE key=?", (key,)).fetchone()
        return {"key": key, "value": row["value"] if row else None}


def get_all_preferences() -> dict:
    with read_conn() as conn:
        rows = conn.execute("SELECT key, value FROM owner_preferences").fetchall()
        return {r["key"]: r["value"] for r in rows}

-- Supermarket Ops Agent — SQLite schema
-- WAL mode + BEGIN IMMEDIATE transactions (see db/db.py) give us the concurrency
-- guarantees a single-store kirana bot needs: writers serialize, readers don't block.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Catalogue
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,                 -- "Aashirvaad Atta 5kg"
    brand           TEXT,                           -- "Aashirvaad"
    category        TEXT,                           -- "atta" / "salt" / "dairy" ...
    unit            TEXT NOT NULL CHECK (unit IN ('kg','g','litre','ml','packet','dozen','piece')),
    is_loose        INTEGER NOT NULL DEFAULT 0,     -- 1 = sold loose by weight/volume (sugar, rice, dal)
    hsn_code        TEXT NOT NULL,                  -- GST HSN/SAC code
    gst_rate        REAL NOT NULL,                  -- 0, 5, 12, 18 ... percent, applied to MRP (inclusive)
    cost_price      REAL NOT NULL,                  -- per unit, what the shop paid
    mrp             REAL NOT NULL,                  -- per unit, GST-inclusive selling price
    quantity        REAL NOT NULL DEFAULT 0,        -- current stock, in `unit`
    reorder_level   REAL NOT NULL DEFAULT 0,        -- warn when quantity <= this
    is_active       INTEGER NOT NULL DEFAULT 1,     -- soft "retire", never hard-delete a SKU
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_products_name_brand
    ON products (name, COALESCE(brand, ''));

-- Every stock change (receive, sale decrement, correction) is logged here.
-- This is the audit trail — quantity on `products` is a derived cache that
-- every mutation keeps in sync inside the same transaction.
CREATE TABLE IF NOT EXISTS stock_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    change_qty      REAL NOT NULL,                  -- positive = stock in, negative = stock out
    reason          TEXT NOT NULL CHECK (reason IN ('receive','sale','sale_reversal','correction')),
    ref_type        TEXT,                           -- 'bill', 'manual', etc.
    ref_id          INTEGER,                         -- e.g. bill id
    unit_cost       REAL,                            -- cost price at time of receipt (for receive rows)
    note            TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Khata — customer credit ledger (declared before bills so bills.customer_id FK resolves)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    phone           TEXT,
    balance         REAL NOT NULL DEFAULT 0,   -- positive = customer owes the shop
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS khata_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL REFERENCES customers(id),
    entry_type      TEXT NOT NULL CHECK (entry_type IN ('charge','payment','adjustment')),
    amount          REAL NOT NULL,             -- charge: +balance, payment: -balance
    ref_type        TEXT,                      -- 'bill', 'manual'
    ref_id          INTEGER,
    idempotency_key TEXT UNIQUE,
    note            TEXT,
    balance_after   REAL NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Billing — multi-turn drafts, only decremented on finalize
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id             TEXT NOT NULL,               -- Telegram chat this bill belongs to
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','finalized','void')),
    customer_id         INTEGER REFERENCES customers(id),
    payment_mode        TEXT CHECK (payment_mode IN ('cash','upi','card','credit')),
    payment_ref         TEXT,                        -- UPI ref / card auth code, freeform
    idempotency_key     TEXT UNIQUE,                 -- set on finalize; repeat calls are no-ops
    subtotal_taxable    REAL,
    total_cgst          REAL,
    total_sgst          REAL,
    round_off           REAL,
    grand_total         REAL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    finalized_at        TEXT
);

CREATE TABLE IF NOT EXISTS bill_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id         INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    product_id      INTEGER NOT NULL REFERENCES products(id),
    qty             REAL NOT NULL,
    unit_price      REAL NOT NULL,          -- MRP per unit at time of billing (GST-inclusive)
    gst_rate        REAL NOT NULL,
    hsn_code        TEXT NOT NULL,
    taxable_value   REAL,                   -- computed on finalize
    cgst_amount     REAL,
    sgst_amount     REAL,
    line_total      REAL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Owner preferences — durable memory, outside the conversation/context window.
-- Loaded fresh into the system prompt at the start of every session, so a
-- `/new` chat still knows them.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS owner_preferences (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Telegram update idempotency — Telegram redelivers updates on timeout/retry;
-- we must not let the agent re-process (and thus re-call finalize_bill for)
-- an update we've already handled.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processed_updates (
    update_id       INTEGER PRIMARY KEY,
    chat_id         TEXT NOT NULL,
    processed_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_stock_ledger_product ON stock_ledger(product_id);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);
CREATE INDEX IF NOT EXISTS idx_bills_chat_status ON bills(chat_id, status);
CREATE INDEX IF NOT EXISTS idx_khata_customer ON khata_ledger(customer_id);

---
name: khata
description: Customer credit ledger (khata) — putting an amount on someone's credit, recording a payment, and checking balances. Use whenever the owner mentions a customer's name with money owed, credit, or "paid".
---

# Khata skill

Tools: `credit_sale`, `record_payment`, `get_balance`, `list_debtors`.

- "put ₹500 on Ramesh's credit" → `credit_sale("Ramesh", 500)`. Creates the
  customer if new — a first khata entry for someone is normal.
- "Ramesh paid ₹300" → `record_payment("Ramesh", 300)`.
- "Ramesh's balance?" → `get_balance("Ramesh")`.
- "who owes money?" / "khata list" → `list_debtors`.

## Guardrails
- `record_payment` refuses a payment to a customer who has no khata at all —
  that's a real signal something's off (wrong name, or they were never
  extended credit); ask the owner to confirm the name rather than silently
  creating a new customer for a *payment*.
- `record_payment` refuses an amount larger than the outstanding balance
  unless the owner explicitly confirms it as an advance
  (`allow_overpayment=True`) — don't set that flag on your own judgement,
  surface the mismatch first ("Ramesh only owes ₹200, you said ₹10,000 — did
  you mean a different amount, or is this an advance?").
- Name matching is case-insensitive exact match on the customer table — if
  "Ramesh" and "ramesh kumar" could both be meant, ask which one instead of
  guessing or creating a duplicate customer.

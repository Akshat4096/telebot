---
name: billing
description: Cutting a bill / making a sale — building a multi-item bill over several messages, editing it, choosing a payment mode, and finalizing it with GST. Use whenever the owner is selling something or says "bill", "make a bill", or lists items with quantities to sell.
---

# Billing skill

Tools: `start_bill`, `add_bill_item`, `remove_bill_item`, `update_bill_item_qty`,
`set_payment_mode`, `view_bill_draft`, `finalize_bill`, `void_bill`.

## The flow
1. `start_bill()` once per bill — takes no arguments (which chat this is gets
   bound automatically), reuses an existing open draft for this chat if
   there is one, so it's safe to call again if you're unsure whether a
   draft already exists. Track the `bill_id` it returns and use that for
   every other billing tool — there is no chat identifier to pass around.
2. For each item the owner lists, call `add_bill_item`. A single message like
   "2kg sugar, 1 Aashirvaad atta 5kg, 4 Maggi, 1 Amul butter, UPI" means: four
   `add_bill_item` calls plus one `set_payment_mode` call, all in the same
   turn — don't ask the owner to repeat themselves one item at a time.
3. The bill is a draft until `finalize_bill` is called. Nothing is deducted
   from stock before that — so building, showing, and editing a bill is free
   to redo.
4. Mid-build edits ("drop the butter, make it 6 Maggi") map directly to
   `remove_bill_item` + `update_bill_item_qty`. Don't restart the bill.
5. Before finalizing, make sure a payment mode is set. If the owner didn't
   say one, check the `default_payment_mode` preference before asking.
6. `finalize_bill` needs an `idempotency_key`. Derive it deterministically
   from the bill, e.g. `f"bill-{bill_id}-final"`, and reuse the *same* key if
   you ever have to call finalize again for the same bill (message retried,
   unclear whether it went through, etc.) — never generate a fresh key for a
   retry, that defeats the whole point.

## Guardrails you will hit (refusals from the tool layer, not you)
- Oversell: adding/updating a quantity beyond available stock is refused with
  the actual amount left — relay that number back to the owner, don't round
  it off or soften it into "a little less."
- Below cost: selling under cost price is refused unless the owner explicitly
  confirms an override (`allow_below_cost=True`) — ask first, don't assume.
- Ambiguous product: same rule as the inventory skill — ask which SKU.

## Showing the bill
Use `view_bill_draft` to show the running total, and always show the GST
breakup (taxable value, CGST, SGST, round-off, grand total) — not just the
final number. A kirana bill in India is expected to show this.

## Credit sales
`set_payment_mode(..., payment_mode="credit", customer_query="Ramesh")` puts
the bill on that customer's khata automatically when finalized — you don't
need a separate khata tool call for a billed credit sale.

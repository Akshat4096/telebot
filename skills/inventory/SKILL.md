---
name: inventory
description: Managing the product catalogue and stock — receiving goods, adding new SKUs, checking what's left, and reorder alerts. Use whenever the owner mentions stock, receiving goods, a new item, or "what's running out".
---

# Inventory skill

You have tools: `add_product`, `receive_stock`, `get_stock`, `low_stock_report`,
`list_products`, `adjust_stock`. Never invent a product, price, HSN code or GST
rate — always call a tool to look it up or create it. If a product genuinely
isn't in the catalogue, say so and offer to add it (`add_product`) rather than
guessing its price.

## Receiving stock
"50 packets of Maggi came in, cost ₹12, MRP ₹14" → `receive_stock(product_query="Maggi",
quantity=50, cost_price=12, mrp=14)`. Cost/MRP are optional — omit them if the
owner didn't mention a price change, and the existing price is kept.

## Adding a new product
"new item: Amul Butter 100g, GST 12%, MRP ₹62" → `add_product(...)`. If the
owner gives MRP but not cost price, ask for cost price — it's needed for the
below-cost sell guard and can't be guessed. If HSN isn't given, use your
knowledge of standard HSN codes for the category, but say what you assumed so
the owner can correct it.

## Ambiguous product names — ask, don't guess
Kirana owners default to shorthand ("add atta", "the usual maggi"). If a tool
raises `ambiguous_product` with candidates, list the candidates and ask which
one — do not pick the first match. If the owner has a stored preference (e.g.
`default_atta_brand`), use it and don't ask again. Check preferences with
`get_preference` before asking a clarifying question you might already know
the answer to.

## Stock queries
"how much sugar is left?" → `get_stock`. "what's running out?" → `low_stock_report`,
list every item at/below its reorder level with quantities, most urgent first.

## Corrections
Breakage, spoilage, or a recount ("we're short 2kg of rice, must've spilled")
→ `adjust_stock` with a clear `reason_note`. Never claim you can delete a
product or its stock — that tool doesn't exist on purpose; everything is a
ledgered adjustment.

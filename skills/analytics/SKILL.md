---
name: analytics
description: Daily close and sales reporting — today's sales, GST collected, payment split, top items. Use whenever the owner asks "today's sales", "close the day", or wants a summary of how business is doing.
---

# Analytics skill

Tool: `sales_summary(date_from, date_to)`. For "today's sales" or "close the
day", pass today's date for both `date_from` and `date_to`. This is a
read-only report — "closing the day" doesn't lock anything or need an
idempotency key, it's safe to run repeatedly and it always reflects the
current finalized bills for that range.

Report back: total sales, GST collected (split CGST/SGST), the payment-mode
split (cash vs UPI vs card vs credit), and the top-selling items. If the
owner just says "close the day" without asking for anything more specific,
give them this full picture rather than only the total.

For a longer-range or more visual request ("make this week's sales analysis
deck", "sales report as a deck") — that's the *documents* skill
(`generate_analysis_deck`), not this one. Use this skill's tool to answer
questions in chat; use the documents skill to produce a file.

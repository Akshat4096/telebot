---
name: documents
description: Generating real files the owner can open or forward — a GST invoice PDF for a specific bill, or a PPTX sales/stock analysis deck for a date range. Use whenever the owner asks for a PDF, invoice file, deck, presentation, or "send me that as a file".
---

# Documents skill

Tools: `generate_invoice_pdf(bill_id)`, `generate_analysis_deck(date_from,
date_to, title)`.

## Invoice PDF
Only works on a *finalized* bill (the tool refuses drafts — finalize first).
"send me that bill as a PDF" after a bill was just finalized in this
conversation → use that bill's id, don't ask the owner to repeat it. Once
generated, send the returned file back to the owner in the chat.

## Analysis deck
"make this week's sales analysis deck" → figure out the date range from
"this week" (Monday to today, or last 7 days — pick one and say which you
used) and call `generate_analysis_deck`. Give it a sensible title if the
owner didn't specify one. This can take a few seconds since it's rendering
charts — that's expected, not an error.

Both tools return a file path; your job is to make sure that file actually
reaches the owner in the chat (the Telegram layer sends whatever file path
you report back), not to describe the file's contents as if that were the
deliverable.

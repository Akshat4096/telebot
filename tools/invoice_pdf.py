"""
GST-correct PDF invoice, built with reportlab (real vector PDF, not a
screenshot). Pulls the shop letterhead (name/GSTIN/address) from owner
preferences so it's branded per-shop without a code change.

Font note: reportlab's built-in Helvetica has no glyph for ₹ (U+20B9), so we
bundle DejaVu Sans (assets/fonts/) and register it — this keeps the invoice
portable across OSes instead of depending on whatever fonts happen to be
installed on the host.
"""
from __future__ import annotations

from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "generated"
FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"

pdfmetrics.registerFont(TTFont("Kirana", str(FONTS_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("Kirana-Bold", str(FONTS_DIR / "DejaVuSans-Bold.ttf")))


def render_invoice_pdf(bill: dict, shop: dict) -> str:
    """
    bill: output of billing.view_bill_draft() for a finalized bill, plus
          bill_id, created_at/finalized_at, payment_mode, customer name if any.
    shop: dict with name, gstin, address (from owner_preferences, with
          sane fallbacks so the invoice still renders if unset).
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"invoice_{bill['bill_id']}.pdf"

    doc = SimpleDocTemplate(str(path), pagesize=A4,
                             topMargin=15 * mm, bottomMargin=15 * mm,
                             leftMargin=15 * mm, rightMargin=15 * mm)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("ShopTitle", parent=styles["Title"], alignment=TA_CENTER,
                                  fontSize=16, fontName="Kirana-Bold")
    small_center = ParagraphStyle("SmallCenter", parent=styles["Normal"], alignment=TA_CENTER,
                                   fontSize=9, fontName="Kirana")
    sub_style = ParagraphStyle("Sub", parent=styles["Heading2"], alignment=TA_CENTER, fontName="Kirana-Bold")
    footer_style = ParagraphStyle("Footer", parent=styles["Normal"], fontSize=7,
                                   textColor=colors.grey, fontName="Kirana")
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"], fontSize=9, fontName="Kirana")

    elements = []
    elements.append(Paragraph(shop.get("name") or "Kirana Store", title_style))
    addr_bits = [shop.get("address"), f"GSTIN: {shop['gstin']}" if shop.get("gstin") else None]
    addr_line = " | ".join([b for b in addr_bits if b])
    if addr_line:
        elements.append(Paragraph(addr_line, small_center))
    elements.append(Spacer(1, 4 * mm))
    elements.append(Paragraph("TAX INVOICE", sub_style))
    elements.append(Spacer(1, 3 * mm))

    meta_table = Table([
        [f"Invoice #: INV-{bill['bill_id']:06d}", f"Date: {bill.get('finalized_at', '')}"],
        [f"Payment mode: {(bill.get('payment_mode') or '-').upper()}",
         f"Customer: {bill.get('customer_name') or 'Walk-in'}"],
    ], colWidths=[90 * mm, 90 * mm])
    meta_table.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("FONTNAME", (0, 0), (-1, -1), "Kirana")]))
    elements.append(meta_table)
    elements.append(Spacer(1, 4 * mm))

    header = ["#", "Item", "HSN", "Qty", "Rate (incl. GST)", "Taxable", "CGST", "SGST", "Total"]
    rows = [header]
    for idx, line in enumerate(bill["lines"], start=1):
        rows.append([
            str(idx), line["product_name"], line["hsn_code"],
            f"{line['qty']} {line['unit']}",
            f"₹{line['unit_price']:.2f}",
            f"₹{line['taxable_value']:.2f}",
            f"₹{line['cgst_amount']:.2f} ({line['gst_rate']/2:.1f}%)",
            f"₹{line['sgst_amount']:.2f} ({line['gst_rate']/2:.1f}%)",
            f"₹{line['line_total']:.2f}",
        ])
    item_table = Table(rows, colWidths=[8*mm, 34*mm, 14*mm, 18*mm, 24*mm, 20*mm, 22*mm, 22*mm, 18*mm], repeatRows=1)
    item_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Kirana"),
        ("FONTNAME", (0, 0), (-1, 0), "Kirana-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3436")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 4 * mm))

    totals_rows = [
        ["Taxable value", f"₹{bill['subtotal_taxable']:.2f}"],
        ["Total CGST", f"₹{bill['total_cgst']:.2f}"],
        ["Total SGST", f"₹{bill['total_sgst']:.2f}"],
        ["Round off", f"₹{bill['round_off']:.2f}"],
        ["Grand total", f"₹{bill['grand_total']:.2f}"],
    ]
    totals_table = Table(totals_rows, colWidths=[140 * mm, 32 * mm])
    totals_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Kirana"),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, -1), (-1, -1), "Kirana-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
    ]))
    elements.append(totals_table)
    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph(
        "This is a system-generated invoice. CGST/SGST computed per HSN slab on GST-inclusive MRP.",
        footer_style))

    doc.build(elements)
    return str(path)

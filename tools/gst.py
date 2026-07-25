"""
GST math, isolated so it can be unit-tested independently of the DB.

Convention: MRP is GST-inclusive (how Indian retail prices are marked), and
the sale is intra-state, so tax splits evenly CGST/SGST. For a line:

    taxable_value = mrp_qty / (1 + rate/100)
    total_tax     = mrp_qty - taxable_value
    cgst = sgst   = total_tax / 2

Each line is rounded to paise (2dp, ROUND_HALF_UP). The bill grand total is
then rounded to the nearest whole rupee (standard Indian retail practice)
and the difference is shown as an explicit "Round off" line — so the
invoice always reconciles: sum(line_totals) + round_off == grand_total,
to the paisa.
"""
from decimal import Decimal, ROUND_HALF_UP


TWO_PLACES = Decimal("0.01")
WHOLE = Decimal("1")


def _d(x) -> Decimal:
    return Decimal(str(x))


def round2(x: Decimal) -> Decimal:
    return x.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def compute_line(qty, unit_price, gst_rate) -> dict:
    qty_d, price_d, rate_d = _d(qty), _d(unit_price), _d(gst_rate)
    line_total = round2(qty_d * price_d)
    divisor = Decimal("1") + rate_d / Decimal("100")
    taxable = round2(line_total / divisor)
    total_tax = line_total - taxable
    # split tax evenly; if it doesn't divide evenly (odd paisa), sgst gets the extra paisa
    cgst = round2(total_tax / 2)
    sgst = round2(total_tax - cgst)
    return {
        "qty": float(qty_d),
        "unit_price": float(price_d),
        "gst_rate": float(rate_d),
        "line_total": float(line_total),
        "taxable_value": float(taxable),
        "cgst_amount": float(cgst),
        "sgst_amount": float(sgst),
    }


def compute_bill_totals(lines: list) -> dict:
    subtotal_taxable = sum(_d(l["taxable_value"]) for l in lines)
    total_cgst = sum(_d(l["cgst_amount"]) for l in lines)
    total_sgst = sum(_d(l["sgst_amount"]) for l in lines)
    exact_total = sum(_d(l["line_total"]) for l in lines)  # == taxable+cgst+sgst by construction

    rounded_total = exact_total.quantize(WHOLE, rounding=ROUND_HALF_UP)
    round_off = rounded_total - exact_total

    return {
        "subtotal_taxable": float(round2(subtotal_taxable)),
        "total_cgst": float(round2(total_cgst)),
        "total_sgst": float(round2(total_sgst)),
        "round_off": float(round2(round_off)),
        "grand_total": float(round2(rounded_total)),
    }

"""GST math correctness — the tool layer's most fact-checkable piece."""
from tools import gst


def test_zero_rate_loose_goods():
    line = gst.compute_line(qty=2, unit_price=45, gst_rate=0)
    assert line["line_total"] == 90.0
    assert line["taxable_value"] == 90.0
    assert line["cgst_amount"] == 0.0
    assert line["sgst_amount"] == 0.0


def test_five_percent_inclusive_split():
    # 245 MRP inclusive of 5% GST -> taxable = 245 / 1.05 = 233.3333... -> 233.33
    line = gst.compute_line(qty=1, unit_price=245, gst_rate=5)
    assert line["line_total"] == 245.0
    assert line["taxable_value"] == 233.33
    assert line["cgst_amount"] == 5.84  # half of 11.67, rounded
    assert line["sgst_amount"] == 5.83  # gets the odd paisa
    # reconciles to the paisa
    assert round(line["taxable_value"] + line["cgst_amount"] + line["sgst_amount"], 2) == line["line_total"]


def test_odd_paisa_split_never_loses_a_paisa():
    for rate in (5, 12, 18):
        for price in (9.99, 10, 33.33, 62, 130, 245):
            line = gst.compute_line(qty=3, unit_price=price, gst_rate=rate)
            assert round(line["taxable_value"] + line["cgst_amount"] + line["sgst_amount"], 2) == line["line_total"]


def test_bill_totals_reconcile_and_round_off_to_whole_rupee():
    lines = [
        gst.compute_line(2, 45, 0),      # 90.00
        gst.compute_line(1, 245, 5),     # 245.00
        gst.compute_line(6, 14, 12),     # 84.00
    ]
    totals = gst.compute_bill_totals(lines)
    exact = sum(l["line_total"] for l in lines)  # 419.00 exactly here
    assert totals["grand_total"] == round(exact) or abs(totals["grand_total"] - exact) < 1
    reconciled = totals["subtotal_taxable"] + totals["total_cgst"] + totals["total_sgst"] + totals["round_off"]
    assert round(reconciled, 2) == totals["grand_total"]

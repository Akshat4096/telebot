"""Multi-turn bills, oversell guard, below-cost guard, and finalize idempotency."""
import pytest

from tools import inventory, billing
from tools.errors import OversellError, BelowCostError, GuardrailError, NotFoundError


def _seed_one_product():
    return inventory.add_product(
        name="Tata Salt 1kg", brand="Tata", unit="packet", gst_rate=0,
        mrp=22, cost_price=18, hsn_code="2501", opening_quantity=10, reorder_level=2,
    )


def test_multi_turn_bill_build_and_edit():
    _seed_one_product()
    inventory.add_product(name="Maggi 70g", brand="Nestle", unit="packet", gst_rate=12,
                           mrp=14, cost_price=10, hsn_code="1902", opening_quantity=50, reorder_level=5)
    inventory.add_product(name="Amul Butter 100g", brand="Amul", unit="packet", gst_rate=12,
                           mrp=62, cost_price=48, hsn_code="0405", opening_quantity=20, reorder_level=5)

    b = billing.start_bill("chatA")
    bill_id = b["bill_id"]
    billing.add_bill_item(bill_id, "maggi", 4)
    billing.add_bill_item(bill_id, "amul butter", 1)
    draft = billing.view_bill_draft(bill_id)
    assert len(draft["lines"]) == 2

    # edit mid-build: drop butter, bump maggi to 6
    billing.remove_bill_item(bill_id, "amul butter")
    billing.update_bill_item_qty(bill_id, "maggi", 6)
    draft = billing.view_bill_draft(bill_id)
    assert len(draft["lines"]) == 1
    assert draft["lines"][0]["qty"] == 6

    # stock untouched until finalize
    assert inventory.get_stock("maggi")["quantity"] == 50


def test_oversell_guard_refuses_and_stock_stays_intact():
    _seed_one_product()
    b = billing.start_bill("chatB")
    with pytest.raises(OversellError):
        billing.add_bill_item(b["bill_id"], "tata salt", 999)
    assert inventory.get_stock("tata salt")["quantity"] == 10


def test_below_cost_requires_explicit_override():
    _seed_one_product()
    b = billing.start_bill("chatC")
    with pytest.raises(BelowCostError):
        billing.add_bill_item(b["bill_id"], "tata salt", 1, unit_price_override=5)
    # explicit override succeeds
    billing.add_bill_item(b["bill_id"], "tata salt", 1, unit_price_override=5, allow_below_cost=True)
    draft = billing.view_bill_draft(b["bill_id"])
    assert draft["lines"][0]["unit_price"] == 5


def test_finalize_decrements_stock_exactly_once():
    _seed_one_product()
    b = billing.start_bill("chatD")
    billing.add_bill_item(b["bill_id"], "tata salt", 3)
    billing.set_payment_mode(b["bill_id"], "cash")
    result = billing.finalize_bill(b["bill_id"], idempotency_key="k1")
    assert result["status"] == "finalized"
    assert inventory.get_stock("tata salt")["quantity"] == 7


def test_finalize_is_idempotent_on_retry():
    _seed_one_product()
    b = billing.start_bill("chatE")
    billing.add_bill_item(b["bill_id"], "tata salt", 3)
    billing.set_payment_mode(b["bill_id"], "cash")
    key = "bill-%s-final" % b["bill_id"]

    first = billing.finalize_bill(b["bill_id"], idempotency_key=key)
    second = billing.finalize_bill(b["bill_id"], idempotency_key=key)

    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True
    assert first["grand_total"] == second["grand_total"]
    # stock decremented once, not twice
    assert inventory.get_stock("tata salt")["quantity"] == 7


def test_cannot_finalize_empty_bill():
    b = billing.start_bill("chatF")
    billing.set_payment_mode(b["bill_id"], "cash")
    with pytest.raises(GuardrailError):
        billing.finalize_bill(b["bill_id"], idempotency_key="empty")


def test_cannot_edit_a_finalized_bill():
    _seed_one_product()
    b = billing.start_bill("chatG")
    billing.add_bill_item(b["bill_id"], "tata salt", 1)
    billing.set_payment_mode(b["bill_id"], "cash")
    billing.finalize_bill(b["bill_id"], idempotency_key="k2")
    with pytest.raises(GuardrailError):
        billing.add_bill_item(b["bill_id"], "tata salt", 1)


def test_ambiguous_product_name_is_flagged_not_guessed():
    inventory.add_product(name="Parle-G Original", brand="Parle", unit="packet", gst_rate=18,
                           mrp=10, cost_price=8, hsn_code="1905", opening_quantity=10)
    inventory.add_product(name="Parle-G Gold", brand="Parle", unit="packet", gst_rate=18,
                           mrp=15, cost_price=12, hsn_code="1905", opening_quantity=10)
    from tools.errors import AmbiguousProductError
    with pytest.raises(AmbiguousProductError) as exc:
        inventory.get_stock("parle")
    assert len(exc.value.candidates) == 2

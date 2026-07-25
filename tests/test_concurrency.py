"""
Two 'in-flight at once' sales racing for the last units of stock. This is
the scenario the assignment calls out explicitly: two bills (or a bill and a
stock-in) must not corrupt stock. Each thread opens its own SQLite
connection (see db/db.py) and finalize_bill's BEGIN IMMEDIATE transaction is
what actually serializes the two — exactly one of the two concurrent
finalizes must win when demand exceeds supply, and stock must land on a
sane, non-negative number afterward.
"""
import threading

from tools import inventory, billing
from tools.errors import OversellError


def test_concurrent_finalize_cannot_oversell():
    inventory.add_product(name="Fortune Sunflower Oil 1L", brand="Fortune", unit="packet",
                           gst_rate=5, mrp=145, cost_price=118, hsn_code="1512", opening_quantity=10)

    bill_a = billing.start_bill("chatX")["bill_id"]
    bill_b = billing.start_bill("chatY")["bill_id"]
    billing.add_bill_item(bill_a, "fortune", 6)
    billing.add_bill_item(bill_b, "fortune", 6)
    billing.set_payment_mode(bill_a, "cash")
    billing.set_payment_mode(bill_b, "cash")

    results = {}
    barrier = threading.Barrier(2)

    def finalize(bill_id, key):
        barrier.wait()  # maximize the chance both threads hit BEGIN IMMEDIATE together
        try:
            billing.finalize_bill(bill_id, idempotency_key=key)
            results[bill_id] = "ok"
        except OversellError:
            results[bill_id] = "oversell_refused"

    t1 = threading.Thread(target=finalize, args=(bill_a, "final-a"))
    t2 = threading.Thread(target=finalize, args=(bill_b, "final-b"))
    t1.start(); t2.start()
    t1.join(); t2.join()

    outcomes = list(results.values())
    assert outcomes.count("ok") == 1, f"expected exactly one winner, got {results}"
    assert outcomes.count("oversell_refused") == 1, f"expected exactly one refusal, got {results}"

    final_stock = inventory.get_stock("fortune")["quantity"]
    assert final_stock == 4  # 10 - 6, the loser never touched stock
    assert final_stock >= 0


def test_concurrent_stock_receive_and_sale_do_not_lose_updates():
    inventory.add_product(name="Loose Sugar", unit="kg", gst_rate=0, mrp=45, cost_price=40,
                           hsn_code="1701", opening_quantity=20, is_loose=True)
    bill_id = billing.start_bill("chatZ")["bill_id"]
    billing.add_bill_item(bill_id, "sugar", 5)
    billing.set_payment_mode(bill_id, "cash")

    barrier = threading.Barrier(2)

    def receive():
        barrier.wait()
        inventory.receive_stock("sugar", 10)

    def sell():
        barrier.wait()
        billing.finalize_bill(bill_id, idempotency_key="sugar-sale-1")

    t1 = threading.Thread(target=receive)
    t2 = threading.Thread(target=sell)
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Whatever order they interleaved in, both updates must land: 20 + 10 - 5 = 25.
    assert inventory.get_stock("sugar")["quantity"] == 25

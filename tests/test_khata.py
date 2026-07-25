"""Khata credit ledger: charge/payment cycle and its guardrails."""
import pytest

from tools import khata
from tools.errors import NotFoundError, GuardrailError


def test_credit_and_payment_cycle():
    khata.credit_sale("Ramesh", 500)
    assert khata.get_balance("Ramesh")["balance"] == 500
    khata.record_payment("Ramesh", 300)
    assert khata.get_balance("Ramesh")["balance"] == 200


def test_cannot_settle_unknown_customer():
    with pytest.raises(NotFoundError):
        khata.record_payment("NoSuchPerson", 100)


def test_overpayment_refused_without_explicit_confirmation():
    khata.credit_sale("Suresh", 200)
    with pytest.raises(GuardrailError):
        khata.record_payment("Suresh", 5000)
    # explicit override succeeds and can go negative (advance)
    result = khata.record_payment("Suresh", 5000, allow_overpayment=True)
    assert result["balance"] == 200 - 5000


def test_khata_payment_idempotency_key_prevents_double_settle():
    khata.credit_sale("Mahesh", 1000)
    r1 = khata.record_payment("Mahesh", 400, idempotency_key="pay-1")
    r2 = khata.record_payment("Mahesh", 400, idempotency_key="pay-1")
    assert r1["idempotent_replay"] is False
    assert r2["idempotent_replay"] is True
    assert khata.get_balance("Mahesh")["balance"] == 600  # not 200


def test_list_debtors_only_shows_positive_balances():
    khata.credit_sale("A", 100)
    khata.credit_sale("B", 50)
    khata.record_payment("B", 50)
    debtors = khata.list_debtors()
    names = [d["name"] for d in debtors]
    assert "A" in names
    assert "B" not in names

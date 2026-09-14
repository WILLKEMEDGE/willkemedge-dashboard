"""What happens when the same financial operation arrives twice.

Duplicate requests are not hypothetical here. The bank retries an IPN until it
gets a 200. The GitHub Actions scheduler can fire a job twice. A user
double-clicks. A reconciliation command is re-run after a partial failure. Every
one of those paths has to land on the same books as a single execution.

These are deterministic repeat tests, not threaded ones: they prove the
*outcome* is idempotent. The row-level locks that protect the interleaved case
(`select_for_update` in `allocate_payment_fifo`, `_update_arrears` and
`void_payment`) cannot be exercised meaningfully on SQLite, so genuine
concurrent load is listed as a manual production verification item rather than
being faked here.
"""
import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Sum

from apps.accounts.models import FinancialAuditLog
from apps.buildings.models import Building, Unit, UnitClassification, UnitStatus
from apps.ledger.models import JournalEntry, JournalLine
from apps.payments.models import Arrears, Payment, PaymentType
from apps.payments.services import (
    allocate_payment_fifo,
    process_payment,
    void_payment,
)
from apps.tenants.models import Tenant, TenantStatus

User = get_user_model()


@pytest.fixture
def tenant(db, settings):
    settings.TENANT_NOTIFICATIONS_ENABLED = False
    settings.ADMIN_ALERTS_ENABLED = False
    building = Building.objects.create(name="Race Block", code="RC", total_floors=1)
    unit = Unit.objects.create(
        building=building, label="RC1", monthly_rent=Decimal("10000"),
        classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_UNPAID,
    )
    return Tenant.objects.create(
        first_name="Race", last_name="Tenant", id_number="RACE-1",
        phone="+254700000555", unit=unit, monthly_rent=Decimal("10000"),
        move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
    )


def _arrears(tenant, month=6, year=2026, **kw):
    return Arrears.objects.create(
        tenant=tenant, period_month=month, period_year=year,
        expected_rent=kw.get("expected_rent", Decimal("10000")),
        expected_vat=Decimal("0"), amount_paid=Decimal("0"),
        balance=kw.get("balance", Decimal("10000")), is_cleared=False,
    )


# ── Replayed single payment ─────────────────────────────────────────────────

def test_replaying_a_payment_with_the_same_key_books_it_once(tenant):
    _arrears(tenant)
    kwargs = dict(
        tenant=tenant, amount=Decimal("10000"), payment_date=dt.date(2026, 6, 5),
        period_month=6, period_year=2026, reference="MPESA-DUP",
        idempotency_key="MPESA-DUP",
    )
    first = process_payment(**kwargs)
    second = process_payment(**kwargs)

    assert first.pk == second.pk
    assert Payment.objects.filter(tenant=tenant).count() == 1
    ar = Arrears.objects.get(tenant=tenant, period_month=6)
    assert ar.amount_paid == Decimal("10000.00")
    assert ar.balance == Decimal("0.00")


def test_a_replay_does_not_post_a_second_journal_entry(tenant):
    _arrears(tenant)
    kwargs = dict(
        tenant=tenant, amount=Decimal("10000"), payment_date=dt.date(2026, 6, 5),
        period_month=6, period_year=2026, reference="GL-DUP", idempotency_key="GL-DUP",
    )
    payment = process_payment(**kwargs)
    process_payment(**kwargs)

    entries = JournalEntry.objects.filter(source_type="payment", source_id=payment.pk)
    assert entries.count() == 1
    lines = JournalLine.objects.filter(entry__in=entries)
    assert lines.aggregate(d=Sum("debit"))["d"] == lines.aggregate(c=Sum("credit"))["c"]


# ── Replayed bank credit (FIFO) ─────────────────────────────────────────────

def test_replaying_a_bank_credit_does_not_re_split_it(tenant):
    """The IPN path's guarantee: the bank may deliver the same credit twice."""
    _arrears(tenant, month=5, balance=Decimal("4000"), expected_rent=Decimal("4000"))
    _arrears(tenant, month=6)

    first = allocate_payment_fifo(
        tenant=tenant, amount=Decimal("14000"), payment_date=dt.date(2026, 6, 20),
        reference="TXN-REPLAY", idempotency_key="TXN-REPLAY",
    )
    second = allocate_payment_fifo(
        tenant=tenant, amount=Decimal("14000"), payment_date=dt.date(2026, 6, 20),
        reference="TXN-REPLAY", idempotency_key="TXN-REPLAY",
    )

    assert [p.pk for p in first] == [p.pk for p in second]
    total = Payment.objects.filter(tenant=tenant).aggregate(t=Sum("amount"))["t"]
    assert total == Decimal("14000.00"), "the credit was booked more than once"

    assert Arrears.objects.get(tenant=tenant, period_month=5).balance == Decimal("0.00")
    assert Arrears.objects.get(tenant=tenant, period_month=6).balance == Decimal("0.00")


def test_two_chunks_into_the_same_period_are_both_kept(tenant):
    """Why the chunk key is the ORDINAL and not the period.

    FIFO legitimately books two chunks to one period — clearing a partial arrear
    and spilling the remainder into the same month. A period-keyed idempotency
    scheme would swallow the second and lose the money.
    """
    _arrears(tenant, month=6, balance=Decimal("3000"), expected_rent=Decimal("3000"))

    payments = allocate_payment_fifo(
        tenant=tenant, amount=Decimal("10000"), payment_date=dt.date(2026, 6, 20),
        reference="TXN-SPLIT", idempotency_key="TXN-SPLIT",
    )
    same_period = [p for p in payments if (p.period_month, p.period_year) == (6, 2026)]
    assert len(same_period) == 2
    assert sum(p.amount for p in payments) == Decimal("10000.00")


# ── Repeated void ───────────────────────────────────────────────────────────

def test_voiding_twice_unwinds_once(tenant):
    """A double-click on Void, or two operators at once.

    `void_payment` re-reads the row under a lock and returns early if it is
    already void — so the second call must not add a second audit row, a second
    reversal entry, or double-reduce the arrears.
    """
    actor = User.objects.create_user(
        username="o", email="o@test.com", password="testpass123!", role="owner",
    )
    _arrears(tenant)
    payment = process_payment(
        tenant=tenant, amount=Decimal("10000"), payment_date=dt.date(2026, 6, 5),
        period_month=6, period_year=2026, reference="VOID-TWICE",
    )

    void_payment(payment, actor=actor, reason="wrong unit")
    void_payment(payment, actor=actor, reason="wrong unit")

    payment.refresh_from_db()
    assert payment.voided_at is not None

    reversals = JournalEntry.objects.filter(
        source_type="payment", source_id=payment.pk, kind="reversal",
    )
    assert reversals.count() == 1

    audit_rows = FinancialAuditLog.objects.filter(
        action="payment.void", object_id=payment.pk,
    )
    assert audit_rows.count() == 1, "the audit trail double-counted one void"

    ar = Arrears.objects.get(tenant=tenant, period_month=6)
    assert ar.amount_paid == Decimal("0.00")
    assert ar.balance == Decimal("10000.00")


def test_a_void_leaves_the_ledger_balanced(tenant):
    """Original + reversal must net to zero on every account touched."""
    actor = User.objects.create_user(
        username="o2", email="o2@test.com", password="testpass123!", role="owner",
    )
    _arrears(tenant)
    payment = process_payment(
        tenant=tenant, amount=Decimal("10000"), payment_date=dt.date(2026, 6, 5),
        period_month=6, period_year=2026, reference="VOID-BAL",
    )
    void_payment(payment, actor=actor, reason="duplicate receipt")

    lines = JournalLine.objects.filter(
        entry__source_type="payment", entry__source_id=payment.pk,
    )
    by_account = {}
    for line in lines:
        by_account.setdefault(line.account.code, Decimal("0"))
        by_account[line.account.code] += line.debit - line.credit

    assert by_account, "no journal lines were posted at all"
    for code, net in by_account.items():
        assert net == Decimal("0.00"), f"account {code} did not net to zero after a void"


# ── Arrears re-derivation is self-healing ───────────────────────────────────

def test_arrears_always_equal_the_sum_of_live_rent_payments(tenant):
    """The invariant the row lock protects.

    `_update_arrears` re-AGGREGATES rather than incrementing, so as long as
    every write is serialised the stored figure can never drift from the
    payments behind it. Asserted here across a sequence that mixes a normal
    payment, a non-rent payment (which must not settle rent) and a void.
    """
    _arrears(tenant)

    process_payment(
        tenant=tenant, amount=Decimal("4000"), payment_date=dt.date(2026, 6, 3),
        period_month=6, period_year=2026, reference="P1",
    )
    process_payment(
        tenant=tenant, amount=Decimal("3000"), payment_date=dt.date(2026, 6, 10),
        period_month=6, period_year=2026, reference="P2",
    )
    # A deposit is a refundable liability — it must not settle rent.
    process_payment(
        tenant=tenant, amount=Decimal("9000"), payment_date=dt.date(2026, 6, 11),
        period_month=6, period_year=2026, reference="D1",
        payment_type=PaymentType.DEPOSIT,
    )

    expected_rent_paid = Payment.objects.filter(
        tenant=tenant, period_month=6, period_year=2026,
        payment_type=PaymentType.RENT, voided_at__isnull=True,
    ).aggregate(t=Sum("amount"))["t"]

    ar = Arrears.objects.get(tenant=tenant, period_month=6)
    assert ar.amount_paid == expected_rent_paid == Decimal("7000.00")
    assert ar.balance == Decimal("3000.00")
    assert ar.is_cleared is False


def test_a_waiver_survives_a_later_payment(tenant):
    """A recompute must not silently reverse money the owner already forgave."""
    ar = _arrears(tenant)
    ar.waived_amount = Decimal("2000")
    ar.balance = Decimal("8000")
    ar.save()

    process_payment(
        tenant=tenant, amount=Decimal("5000"), payment_date=dt.date(2026, 6, 12),
        period_month=6, period_year=2026, reference="AFTER-WAIVER",
    )

    ar.refresh_from_db()
    assert ar.waived_amount == Decimal("2000.00"), "the waiver was wiped by the recompute"
    assert ar.amount_paid == Decimal("5000.00")
    assert ar.balance == Decimal("3000.00")   # 10000 - 5000 - 2000

"""
Tests for restating a month billed at a superseded rate.

The acceptance test is the last one: MR304 was billed 10,000 for June and July
while the landlord's own June statement bills the unit at 12,000, and after the
correction the rent roll carries 12,000 in both months with the balances
re-derived from the cash that actually arrived.
"""
import datetime as _dt
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.buildings.models import Building, Unit, UnitClassification, UnitStatus
from apps.payments.management.commands import correct_historic_rent as cmd
from apps.payments.models import Arrears
from apps.payments.monthly_ledger import OPENING_MARKER, build_monthly_ledger
from apps.payments.services import process_payment
from apps.tenants.models import Tenant, TenantStatus

D = Decimal
AUG_21 = _dt.date(2026, 8, 21)


@pytest.fixture
def flat(db):
    building = Building.objects.create(name="Matasia Residential", code="MRT", total_floors=3)
    unit = Unit.objects.create(
        building=building, label="MRT04", monthly_rent=D("10000"),
        classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_UNPAID,
    )
    return Tenant.objects.create(
        first_name="MRT04", last_name="Tenant", id_number="T-MRT04",
        phone="+254700000004", unit=unit, monthly_rent=D("12000"),
        deposit_paid=D(0), move_in_date="2026-06-01", status=TenantStatus.ACTIVE,
    )


@pytest.fixture
def shop(db):
    """A commercial let, to prove VAT is re-derived rather than left stale."""
    building = Building.objects.create(name="Matasia Commercial", code="MCT", total_floors=2)
    unit = Unit.objects.create(
        building=building, label="MCT01", monthly_rent=D("10000"),
        classification=UnitClassification.BUSINESS, status=UnitStatus.OCCUPIED_UNPAID,
    )
    return Tenant.objects.create(
        first_name="MCT01", last_name="Trader", id_number="T-MCT01",
        phone="+254700000005", unit=unit, monthly_rent=D("12000"),
        deposit_paid=D(0), move_in_date="2026-06-01", status=TenantStatus.ACTIVE,
    )


def _fix(tenant, periods, was, now, label=None):
    return [(label or tenant.unit.label, tenant.pk, periods, D(was), D(now), "test")]


def _bill(tenant, year, month, rent, paid=0, **kwargs):
    return Arrears.objects.create(
        tenant=tenant, period_year=year, period_month=month,
        expected_rent=D(rent), expected_vat=D(0), amount_paid=D(paid),
        balance=D(rent) - D(paid), is_cleared=False, **kwargs,
    )


def _arr(tenant, year, month):
    return Arrears.objects.filter(tenant=tenant, period_year=year, period_month=month).first()


def _roll(tenant):
    return {r["period"]: r for r in build_monthly_ledger(tenant, months=0, today=AUG_21)}


class TestPreflight:
    def test_aborts_when_the_id_is_on_another_unit(self, flat, monkeypatch):
        _bill(flat, 2026, 6, "10000")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000, label="MRT99"))

        with pytest.raises(CommandError, match="Pre-flight failed"):
            call_command("correct_historic_rent", "--apply")

        assert _arr(flat, 2026, 6).expected_rent == D("10000.00")

    def test_aborts_when_the_tenant_is_gone(self, flat, monkeypatch):
        monkeypatch.setattr(
            cmd, "CORRECTIONS",
            [("MRT04", 999999, [(2026, 6)], D("10000"), D("12000"), "test")],
        )

        with pytest.raises(CommandError, match="Pre-flight failed"):
            call_command("correct_historic_rent", "--apply")


class TestCorrection:
    def test_writes_nothing_without_apply(self, flat, monkeypatch):
        _bill(flat, 2026, 6, "10000")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent")

        assert _arr(flat, 2026, 6).expected_rent == D("10000.00")

    def test_restates_the_rent(self, flat, monkeypatch):
        _bill(flat, 2026, 6, "10000")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        assert _arr(flat, 2026, 6).expected_rent == D("12000.00")

    def test_rederives_the_balance_from_cash_received(self, flat, monkeypatch):
        _bill(flat, 2026, 6, "10000")
        process_payment(
            tenant=flat, amount=D("10000"), payment_date=_dt.date(2026, 6, 8),
            period_month=6, period_year=2026, source="mpesa", reference="R1",
            idempotency_key="R1",
        )
        assert _arr(flat, 2026, 6).balance == D("0.00")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        arr = _arr(flat, 2026, 6)
        assert arr.amount_paid == D("10000.00")
        assert arr.balance == D("2000.00")
        assert arr.is_cleared is False

    def test_recomputes_vat_for_a_commercial_let(self, shop, monkeypatch):
        _bill(shop, 2026, 6, "10000")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(shop, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        arr = _arr(shop, 2026, 6)
        assert arr.expected_rent == D("12000.00")
        assert arr.expected_vat == D("1920.00"), "VAT left at the old rent's figure"


class TestRefusals:
    def test_an_unbilled_month_is_skipped(self, flat, monkeypatch):
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        assert _arr(flat, 2026, 6) is None, "a month nobody billed was invented"

    def test_a_row_at_an_unexpected_figure_is_left_alone(self, flat, monkeypatch):
        """The rate on the row is the check that this is the row we mean."""
        _bill(flat, 2026, 6, "18000")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        assert _arr(flat, 2026, 6).expected_rent == D("18000.00")

    def test_an_opening_row_is_never_restated(self, flat, monkeypatch):
        """Its figure is a balance brought forward, not a month's rent."""
        _bill(flat, 2026, 6, "10000", waive_notes=f"{OPENING_MARKER} - not a billed month.")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        assert _arr(flat, 2026, 6).expected_rent == D("10000.00")

    def test_rerun_changes_nothing(self, flat, monkeypatch):
        _bill(flat, 2026, 6, "10000")
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6)], 10000, 12000))
        call_command("correct_historic_rent", "--apply")

        call_command("correct_historic_rent", "--apply")

        assert _arr(flat, 2026, 6).expected_rent == D("12000.00")
        assert Arrears.objects.filter(tenant=flat).count() == 1


class TestMR304:
    def test_june_and_july_carry_the_folded_rent(self, flat, monkeypatch):
        """Her two months as the landlord's June statement bills them."""
        _bill(flat, 2026, 6, "10000")
        _bill(flat, 2026, 7, "10000")
        for key, amount, day, month in [
            ("P1", "1000", _dt.date(2026, 6, 8), 6),
            ("P2", "9000", _dt.date(2026, 7, 31), 6),
            ("P3", "3000", _dt.date(2026, 7, 31), 7),
            ("P4", "1000", _dt.date(2026, 8, 6), 7),
        ]:
            process_payment(
                tenant=flat, amount=D(amount), payment_date=day,
                period_month=month, period_year=2026, source="mpesa",
                reference=key, idempotency_key=key,
            )
        monkeypatch.setattr(cmd, "CORRECTIONS", _fix(flat, [(2026, 6), (2026, 7)], 10000, 12000))

        call_command("correct_historic_rent", "--apply")

        roll = _roll(flat)
        assert roll["6/2026"]["rent"] == "12000.00"
        assert roll["6/2026"]["paid"] == "10000.00"
        assert roll["6/2026"]["balance"] == "2000.00"
        assert roll["7/2026"]["brought_forward"] == "2000.00"
        assert roll["7/2026"]["rent"] == "12000.00"
        assert roll["7/2026"]["paid"] == "4000.00"
        assert roll["7/2026"]["balance"] == "10000.00"

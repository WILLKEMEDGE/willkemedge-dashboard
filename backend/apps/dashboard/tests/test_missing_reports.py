"""The six report endpoints the Reports page called for months and never had.

Each tab rendered its error state in production. The tests below check two
things for each: the contract the frontend actually reads (it was written first,
so the field names are not negotiable), and — for the money ones — that the
figures agree with `monthly_ledger`, which is the one balance the rest of the
product reports.

A report that disagrees with the tenant's own statement is worse than a missing
one, so the agreement assertions are the point of this file.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase

from apps.buildings.models import Building, Unit, UnitClassification, UnitStatus
from apps.payments.models import Arrears, Payment, PaymentType
from apps.payments.monthly_ledger import current_balance
from apps.tenants.models import Tenant, TenantStatus

User = get_user_model()


class MissingReportsTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="r", email="r@test.com", password="testpass123!", role="owner",
        )
        cls.building = Building.objects.create(name="Report Block", code="RP", total_floors=2)

        # In arrears: billed 10,000, paid 4,000.
        cls.debtor_unit = Unit.objects.create(
            building=cls.building, label="RP1", monthly_rent=Decimal("10000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_PARTIAL,
        )
        cls.debtor = Tenant.objects.create(
            first_name="Owes", last_name="Money", id_number="RP-1",
            phone="+254700000601", unit=cls.debtor_unit, monthly_rent=Decimal("10000"),
            move_in_date=dt.date(2024, 1, 1), status=TenantStatus.ACTIVE,
        )
        Arrears.objects.create(
            tenant=cls.debtor, period_month=6, period_year=2026,
            expected_rent=Decimal("10000"), expected_vat=Decimal("0"),
            amount_paid=Decimal("4000"), balance=Decimal("6000"), is_cleared=False,
        )
        Payment.objects.create(
            tenant=cls.debtor, amount=Decimal("4000"), payment_date=dt.date(2026, 6, 5),
            period_month=6, period_year=2026, payment_type=PaymentType.RENT, reference="R1",
        )

        # In credit: billed 8,000, paid 12,000.
        cls.creditor_unit = Unit.objects.create(
            building=cls.building, label="RP2", monthly_rent=Decimal("8000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_PAID,
        )
        cls.creditor = Tenant.objects.create(
            first_name="Paid", last_name="Ahead", id_number="RP-2",
            phone="+254700000602", unit=cls.creditor_unit, monthly_rent=Decimal("8000"),
            move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
        )
        Arrears.objects.create(
            tenant=cls.creditor, period_month=6, period_year=2026,
            expected_rent=Decimal("8000"), expected_vat=Decimal("0"),
            amount_paid=Decimal("8000"), balance=Decimal("0"), is_cleared=True,
        )
        Payment.objects.create(
            tenant=cls.creditor, amount=Decimal("12000"), payment_date=dt.date(2026, 6, 5),
            period_month=6, period_year=2026, payment_type=PaymentType.RENT, reference="R2",
        )

        cls.vacant = Unit.objects.create(
            building=cls.building, label="RP3", monthly_rent=Decimal("15000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.VACANT,
        )
        cls.renovating = Unit.objects.create(
            building=cls.building, label="RP4", monthly_rent=Decimal("9000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.UNDER_MAINTENANCE,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _get(self, url, **params):
        response = self.client.get(url, params)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        return response.json()

    # ── Rent balances ───────────────────────────────────────────────────────

    def test_rent_balances_returns_the_shape_the_tab_reads(self):
        body = self._get("/api/reports/rent-balances/", month=6, year=2026)
        assert {"total_outstanding", "balances"} <= set(body)
        row = body["balances"][0]
        assert {"tenant", "unit", "monthly_rent", "paid", "balance", "status"} <= set(row)

    def test_rent_balances_agrees_with_the_rent_roll(self):
        """The figure here must be the figure on the tenant's own statement."""
        body = self._get("/api/reports/rent-balances/", month=6, year=2026)
        by_tenant = {r["tenant"]: r for r in body["balances"]}
        as_at = dt.date(2026, 6, 30)

        assert Decimal(str(by_tenant["Owes Money"]["balance"])) == current_balance(
            self.debtor, today=as_at
        )
        assert Decimal(str(by_tenant["Paid Ahead"]["balance"])) == current_balance(
            self.creditor, today=as_at
        )

    def test_a_tenant_in_credit_is_not_counted_as_outstanding(self):
        """Credit on one tenancy does not pay down what another owes.

        The dashboard makes the same distinction deliberately; if this report
        netted them, the two screens would report different arrears totals.
        """
        body = self._get("/api/reports/rent-balances/", month=6, year=2026)
        assert body["total_outstanding"] == 6000.0
        statuses = {r["tenant"]: r["status"] for r in body["balances"]}
        assert statuses["Owes Money"] == "In arrears"
        assert statuses["Paid Ahead"] == "In credit"

    # ── Overpayments ────────────────────────────────────────────────────────

    def test_overpayments_lists_only_tenants_in_credit(self):
        body = self._get("/api/reports/rent-overpayments/", month=6, year=2026)
        names = [r["tenant"] for r in body["overpayments"]]
        assert names == ["Paid Ahead"]
        assert body["total_overpaid"] == 4000.0

    def test_an_overpayment_is_reported_as_a_positive_figure(self):
        """It reads as "this tenant is 4,000 ahead", while the balance stays signed."""
        body = self._get("/api/reports/rent-overpayments/", month=6, year=2026)
        row = body["overpayments"][0]
        assert row["overpaid"] == 4000.0
        assert row["paid"] - row["expected"] == row["overpaid"]

    # ── Vacant units ────────────────────────────────────────────────────────

    def test_vacant_units_includes_units_held_for_renovation(self):
        """Both are off the market and neither is earning — the same question."""
        body = self._get("/api/reports/vacant-units/")
        labels = {u["label"] for u in body["units"]}
        assert labels == {"RP3", "RP4"}
        assert body["count"] == 2
        assert body["potential_rent"] == 24000.0

    def test_vacant_units_returns_the_shape_the_tab_reads(self):
        body = self._get("/api/reports/vacant-units/")
        row = body["units"][0]
        assert {"building", "label", "floor", "unit_type", "monthly_rent", "status"} <= set(row)

    # ── Expiring leases ─────────────────────────────────────────────────────

    def test_expiring_leases_flags_only_long_running_tenancies(self):
        body = self._get("/api/reports/expiring-leases/")
        names = [r["tenant"] for r in body["leases"]]
        assert "Owes Money" in names, "a 2024 move-in should be flagged"
        assert "Paid Ahead" not in names, "a 2026 move-in should not be"

    def test_expiring_leases_says_what_it_is_measuring(self):
        """There is no lease-end date on the model. The report must not imply one."""
        body = self._get("/api/reports/expiring-leases/")
        assert "move-in" in body["basis"]
        assert body["leases"][0]["months_active"] > 12

    # ── Statements ──────────────────────────────────────────────────────────

    def test_tenant_statement_returns_the_shape_the_tab_reads(self):
        body = self._get(f"/api/reports/tenant-statement/{self.debtor.pk}/")
        assert {"tenant", "total_expected", "total_paid", "total_arrears", "rows"} <= set(body)
        row = body["rows"][0]
        assert {"period", "expected", "paid", "balance", "status"} <= set(row)

    def test_tenant_statement_closes_on_the_rent_roll_balance(self):
        """Not the SUM of the monthly balances — the roll-forward is cumulative.

        Adding them would count the same debt once for every month it stayed
        open, which is how an arrears report comes to report triple the truth.
        """
        body = self._get(f"/api/reports/tenant-statement/{self.debtor.pk}/")
        assert Decimal(str(body["closing_balance"])) == current_balance(self.debtor)
        assert body["total_arrears"] == 6000.0

    def test_unit_statement_reports_the_current_occupant(self):
        body = self._get(f"/api/reports/unit-statement/{self.debtor_unit.pk}/")
        assert body["unit"]["label"] == "RP1"
        assert body["tenant"]["name"] == "Owes Money"
        assert all(row["tenant"] == "Owes Money" for row in body["rows"])

    def test_unit_statement_says_plainly_when_a_unit_is_empty(self):
        """An empty table would read as "nothing owed" rather than "nobody here"."""
        body = self._get(f"/api/reports/unit-statement/{self.vacant.pk}/")
        assert body["tenant"] is None
        assert body["rows"] == []
        assert "No current tenant" in body["detail"]

    def test_a_missing_tenant_or_unit_is_a_404_not_a_500(self):
        assert self.client.get("/api/reports/tenant-statement/999999/").status_code == 404
        assert self.client.get("/api/reports/unit-statement/999999/").status_code == 404

    # ── Access ──────────────────────────────────────────────────────────────

    def test_every_new_report_requires_authentication(self):
        anon = APIClient()
        for url in (
            "/api/reports/rent-balances/",
            "/api/reports/rent-overpayments/",
            "/api/reports/vacant-units/",
            "/api/reports/expiring-leases/",
            f"/api/reports/tenant-statement/{self.debtor.pk}/",
            f"/api/reports/unit-statement/{self.debtor_unit.pk}/",
        ):
            assert anon.get(url).status_code == 401, f"{url} was readable anonymously"

    def test_a_bad_period_falls_back_to_today_rather_than_500ing(self):
        response = self.client.get(
            "/api/reports/rent-balances/", {"month": "banana", "year": "-1"}
        )
        assert response.status_code == 200


class IncomeBasisConsistencyTests(APITestCase):
    """One month's income must not have two different answers.

    `views_reports` defines `_net_income_sum()` precisely so the P&L, the annual
    summary and the dashboard trend all strip VAT out of commercial rent the way
    the ledger does. The expense-breakdown report was summing the GROSS, so for
    any month containing commercial rent its `total_income` — and therefore the
    expense ratio drawn on the same page — was 16% higher than the P&L's figure
    for the same period.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="ib", email="ib@test.com", password="testpass123!", role="owner",
        )
        building = Building.objects.create(name="VAT Block", code="VB", total_floors=1)
        commercial = Unit.objects.create(
            building=building, label="VB1", monthly_rent=Decimal("24000"),
            classification=UnitClassification.BUSINESS, status=UnitStatus.OCCUPIED_PAID,
        )
        tenant = Tenant.objects.create(
            first_name="Comm", last_name="Ercial", id_number="VB-1",
            phone="+254700000901", unit=commercial, monthly_rent=Decimal("24000"),
            move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
        )
        # 27,840 gross = 24,000 net + 3,840 VAT.
        Payment.objects.create(
            tenant=tenant, amount=Decimal("27840"), payment_date=dt.date(2026, 6, 5),
            period_month=6, period_year=2026, payment_type=PaymentType.RENT, reference="VB-P1",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_expense_breakdown_reports_the_same_income_as_the_pnl(self):
        params = {"month": 6, "year": 2026}
        breakdown = self.client.get("/api/reports/expense-breakdown/", params).json()
        pnl = self.client.get("/api/reports/profit-loss/", params).json()

        assert breakdown["total_income"] == pnl["income"]
        # And that shared figure is the NET, not the VAT-inclusive cash.
        assert breakdown["total_income"] == 24000.0

    def test_the_annual_summary_agrees_too(self):
        annual = self.client.get("/api/reports/annual-income/", {"year": 2026}).json()
        june = next(m for m in annual["monthly"] if m["month"] == 6)
        assert june["total"] == 24000.0

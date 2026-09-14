"""Building/Unit API — the endpoints that had no API tests at all.

`test_services.py` covers the unit-status state machine thoroughly. The viewsets
that sit in front of it did not have a single test, which is how `adjust-rent`
came to be the only money path in the codebase using binary floating point, with
no lower bound, applied to every unit in a building at once.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase

from apps.buildings.models import Building, Unit, UnitClassification, UnitStatus
from apps.payments.models import Payment, PaymentType
from apps.tenants.models import Tenant, TenantStatus

User = get_user_model()


class AdjustRentTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="o", email="o@test.com", password="testpass123!", role="owner",
        )
        cls.building = Building.objects.create(name="Rent Block", code="RB", total_floors=1)
        # 3,333.33 is chosen because a float round-trip of a 3% rise on it is
        # exactly the kind of value that drifts.
        cls.a = Unit.objects.create(
            building=cls.building, label="RB-A", monthly_rent=Decimal("3333.33"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.VACANT,
        )
        cls.b = Unit.objects.create(
            building=cls.building, label="RB-B", monthly_rent=Decimal("10000.00"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.VACANT,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def _adjust(self, **body):
        return self.client.post(f"/api/buildings/{self.building.pk}/adjust-rent/", body)

    def test_a_fixed_rise_is_exact(self):
        response = self._adjust(amount="1000", type="fixed")
        assert response.status_code == 200
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        assert self.a.monthly_rent == Decimal("4333")
        assert self.b.monthly_rent == Decimal("11000")

    def test_a_percentage_rise_uses_decimal_arithmetic(self):
        """3% of 3,333.33 is 99.9999; the result rounds to 3,433, not 3,432.999…"""
        response = self._adjust(amount="3", type="percent")
        assert response.status_code == 200
        self.a.refresh_from_db()
        assert self.a.monthly_rent == Decimal("3433")
        self.b.refresh_from_db()
        assert self.b.monthly_rent == Decimal("10300")

    def test_an_adjustment_that_would_go_negative_changes_nothing(self):
        """Refused as a whole, not applied to the units it happens to survive."""
        response = self._adjust(amount="-20000", type="fixed")
        assert response.status_code == 400
        assert "negative rent" in response.json()["detail"]

        self.a.refresh_from_db()
        self.b.refresh_from_db()
        assert self.a.monthly_rent == Decimal("3333.33")
        assert self.b.monthly_rent == Decimal("10000.00")

    def test_a_reduction_of_100_percent_or_more_is_refused(self):
        assert self._adjust(amount="-100", type="percent").status_code == 400
        assert self._adjust(amount="-150", type="percent").status_code == 400
        self.a.refresh_from_db()
        assert self.a.monthly_rent == Decimal("3333.33")

    def test_a_non_numeric_amount_is_a_400_not_a_500(self):
        assert self._adjust(amount="lots", type="fixed").status_code == 400

    def test_an_unknown_type_is_rejected(self):
        """It used to fall through to "fixed", silently doing the wrong thing."""
        assert self._adjust(amount="10", type="multiply").status_code == 400

    def test_a_missing_amount_is_rejected(self):
        assert self._adjust(type="fixed").status_code == 400

    def test_zero_is_accepted_and_is_a_no_op(self):
        """`if not adj_amount` treated 0 as missing. It is a valid, if dull, input."""
        response = self._adjust(amount="0", type="fixed")
        assert response.status_code == 200
        self.a.refresh_from_db()
        assert self.a.monthly_rent == Decimal("3333")  # quantized, not changed

    def test_the_response_says_sitting_tenants_were_not_re_priced(self):
        """Billing reads Tenant.monthly_rent, which this endpoint never touches.

        Leaving the caller to assume the rent roll moved is how a landlord finds
        out in six weeks that a rent rise never actually billed.
        """
        response = self._adjust(amount="500", type="fixed")
        body = response.json()
        assert body["tenants_updated"] == 0
        assert "unchanged" in body["detail"]


class DeleteProtectionTests(APITestCase):
    """`on_delete=PROTECT` refusing a delete is correct — it must not be a 500."""

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            username="o2", email="o2@test.com", password="testpass123!", role="owner",
        )
        cls.building = Building.objects.create(name="Protect Block", code="PR", total_floors=1)
        cls.unit = Unit.objects.create(
            building=cls.building, label="PR1", monthly_rent=Decimal("10000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_UNPAID,
        )
        cls.tenant = Tenant.objects.create(
            first_name="Held", last_name="Fast", id_number="PR-1",
            phone="+254700000801", unit=cls.unit, monthly_rent=Decimal("10000"),
            move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
        )
        Payment.objects.create(
            tenant=cls.tenant, amount=Decimal("10000"), payment_date=dt.date(2026, 6, 5),
            period_month=6, period_year=2026, payment_type=PaymentType.RENT, reference="PR-P1",
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.owner)

    def test_deleting_a_tenant_with_payments_is_a_409_not_a_crash(self):
        """Financial history is never deleted. The refusal must be legible."""
        response = self.client.delete(f"/api/tenants/{self.tenant.pk}/")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "cannot be deleted" in detail
        assert "payments" in detail.lower()
        assert Tenant.objects.filter(pk=self.tenant.pk).exists()

    def test_deleting_a_unit_with_a_tenant_is_a_409_not_a_crash(self):
        response = self.client.delete(f"/api/units/{self.unit.pk}/")
        assert response.status_code == 409
        assert Unit.objects.filter(pk=self.unit.pk).exists()

    def test_a_duplicate_unit_label_is_a_400_from_the_serializer(self):
        """Caught before the database, with the message that explains why."""
        response = self.client.post("/api/units/", {
            "building": self.building.pk, "label": "pr1", "monthly_rent": "5000",
        })
        assert response.status_code == 400
        assert "already used" in str(response.json())

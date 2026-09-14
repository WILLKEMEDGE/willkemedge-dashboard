from decimal import Decimal

from django.db import migrations, models

DEFAULT_RATE = Decimal("150.00")
MATASIA_RATE = Decimal("200.00")


def raise_matasia_rate(apps, schema_editor):
    """Set the water tariff to 200/unit for the Matasia properties only.

    Donholm and every other building keep the 150/unit default — this must
    NOT be a blanket rate change (a prior version of this migration
    incorrectly raised every building still on the old default).
    """
    Building = apps.get_model("buildings", "Building")
    Building.objects.filter(name__icontains="Matasia").update(water_rate_per_unit=MATASIA_RATE)


def lower_matasia_rate(apps, schema_editor):
    Building = apps.get_model("buildings", "Building")
    Building.objects.filter(name__icontains="Matasia").update(water_rate_per_unit=DEFAULT_RATE)


class Migration(migrations.Migration):

    dependencies = [
        ('buildings', '0008_building_property_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='building',
            name='water_rate_per_unit',
            field=models.DecimalField(decimal_places=2, default=Decimal('150.00'), help_text='Tariff charged per unit of water consumed (KES). Matasia properties bill at 200/unit; other properties default to 150.', max_digits=8),
        ),
        migrations.RunPython(raise_matasia_rate, lower_matasia_rate),
    ]

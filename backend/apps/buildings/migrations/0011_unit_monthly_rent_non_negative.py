from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("buildings", "0010_financial_invariant_preflight"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="unit",
            constraint=models.CheckConstraint(
                condition=models.Q(("monthly_rent__gte", 0)),
                name="unit_monthly_rent_non_negative",
            ),
        ),
    ]

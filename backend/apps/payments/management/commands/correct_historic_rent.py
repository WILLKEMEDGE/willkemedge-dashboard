"""
Restate the rent on months that were already billed at a superseded rate.

``reconcile_aug_2026`` folded each Matasia residential service charge into
``Tenant.monthly_rent`` (the landlord's decision). That fixes every month billed
from then on, but an ``Arrears`` row already posted keeps the figure it was
raised with — ``_update_arrears`` deliberately never rewrites an obligation,
because recomputing it from the tenant's current rent is what restated the
opening balances last time. So the correction has to be made row by row, and
only where there is documentary evidence of the rate for that month.

MR304 is such a case. The landlord's own 22 June 2026 statement lists the unit
at 12,000 "Reserved Rent + S/Charge" and bills June at 12,000, while the system
billed June and July at 10,000 — the unit's old rate, service charge excluded.
Two months, 2,000 each.

Deliberately NOT swept in
-------------------------
The other six units in ``reconcile_aug_2026``'s rent table. Whether the folded
rate applies retrospectively is a per-unit question of fact, and for MR306 it is
one of the queries outstanding with the landlord. Evidence first, then a row
here — never a bulk restatement of history.

What this does not fix
----------------------
The balance this produces is still not the whole truth for MR304: her pre-June
opening credit was never carried in (``reconcile_matasia_residential`` flags it
now), so correcting the rent alone makes her read as owing MORE, not less.
Both halves have to land before the roll agrees with the statement.

The books are cash basis — ``post_arrear`` exists but nothing calls it — so
restating a billed obligation raises no journal entry and the GL is unaffected.

DRY-RUN BY DEFAULT. Nothing is written without --apply. Re-running is safe.

Usage:
    python manage.py correct_historic_rent
    python manage.py correct_historic_rent --apply
"""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.payments.monthly_ledger import OPENING_MARKER


def D(value):
    return Decimal(str(value)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# unit, tenant id, periods (year, month), rent as billed, rent as it should be,
# and the evidence for the change.
# ---------------------------------------------------------------------------
CORRECTIONS = [
    (
        "MR304", 146,
        [(2026, 6), (2026, 7)],
        D(10000), D(12000),
        "22 Jun 2026 statement bills MR304 at 12,000 (rent 10,000 + service "
        "charge 2,000); the system billed the unit's pre-fold rate",
    ),
]


class Command(BaseCommand):
    help = (
        "Restate expected_rent on already-billed arrears rows where the month was "
        "raised at a superseded rate, and re-derive the balance. Dry-run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the changes.")

    def _head(self, text):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{text}"))

    def _do(self, text):
        self.stdout.write(f"  {text}")
        self.changes += 1

    def _skip(self, text):
        self.stdout.write(self.style.WARNING(f"  skip  {text}"))

    def _note(self, text):
        self.stdout.write(self.style.NOTICE(f"  note  {text}"))

    def handle(self, *args, **opts):
        from apps.tenants.models import Tenant

        self.apply = opts["apply"]
        self.changes = 0

        # Primary keys are not portable between databases: the id must sit on
        # the unit the correction names, or this is the wrong database.
        wrong = []
        tenants = {}
        for label, tid, *_ in CORRECTIONS:
            tenant = Tenant.objects.filter(pk=tid).select_related("unit").first()
            if tenant is None:
                wrong.append(f"tenant #{tid} not found")
                continue
            actual = tenant.unit.label if tenant.unit else "(no unit)"
            if actual.upper() != label.upper():
                wrong.append(f"#{tid} is '{tenant.full_name}' on {actual}, expected {label}")
                continue
            tenants[tid] = tenant
        if wrong:
            raise CommandError(
                "Pre-flight failed — tenant ids do not match their units:\n  "
                + "\n  ".join(wrong)
                + "\n\nNothing was written."
            )

        for label, tid, periods, was, now, why in CORRECTIONS:
            self._head(f"{label} — {was} -> {now}  ({why})")
            tenant = tenants[tid]
            for year, month in periods:
                self._correct(tenant, label, year, month, was, now)
            self._show_roll(tenant, label)

        if not self.apply:
            self.stdout.write(self.style.WARNING(
                f"\nDRY-RUN — {self.changes} change(s) would be written. Re-run with --apply."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nApplied {self.changes} change(s)."))

    def _correct(self, tenant, label, year, month, was, now):
        from apps.payments.models import Arrears
        from apps.payments.services import _update_arrears, expected_vat_for

        arr = Arrears.objects.filter(
            tenant=tenant, period_year=year, period_month=month
        ).first()
        if arr is None:
            self._skip(f"{label} {month}/{year}: no arrears row — nothing billed to restate")
            return
        if OPENING_MARKER in (arr.waive_notes or ""):
            self._skip(
                f"{label} {month}/{year}: an opening row — its figure is a balance "
                f"brought forward, not a month's rent. Left alone."
            )
            return
        if arr.expected_rent == now:
            self._skip(f"{label} {month}/{year}: already billed {now}")
            return
        if arr.expected_rent != was:
            self._skip(
                f"{label} {month}/{year}: billed {arr.expected_rent}, expected to find {was} "
                f"— left for review rather than overwritten"
            )
            return

        vat = expected_vat_for(tenant, now)
        self._do(
            f"{label} {month}/{year}: {arr.expected_rent} -> {now}"
            + (f" (+ {vat} VAT)" if vat else "")
        )
        if not self.apply:
            return
        with transaction.atomic():
            Arrears.objects.filter(pk=arr.pk).update(expected_rent=now, expected_vat=vat)
            # The canonical routine re-derives amount_paid / balance / cleared
            # status and refreshes the unit, preserving any waiver or credit.
            _update_arrears(tenant, month, year)

    def _show_roll(self, tenant, label):
        """Print the rent roll as it now stands, so the effect is visible."""
        from apps.payments.monthly_ledger import build_monthly_ledger

        if not self.apply:
            self._note(f"{label}: rent roll shown after --apply")
            return
        for row in build_monthly_ledger(tenant, months=0):
            self._note(
                f"{label} {row['label']}: b/f {row['brought_forward']} + rent {row['rent']} "
                f"+ other {row['other_charges']} = {row['total_due']}, "
                f"paid {row['paid']}, balance {row['balance']}"
            )

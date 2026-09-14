"""The six report endpoints the Reports page has always called and never had.

`ReportsPage.tsx` shipped with seventeen tabs. Seven of them called routes that
were never written, so they have only ever rendered their error state — the same
situation `AgingArrearsReportView` documents for the tab that was fixed before
this one. Presenting seven broken tabs as working functionality is not
acceptable in production, so they are either implemented here or removed from
the UI.

Implemented (all six are thin compositions of building blocks that already exist
and are already tested — no new financial logic is invented):

    /reports/rent-balances/        who owes what, as at a chosen month
    /reports/rent-overpayments/    who is in credit, as at a chosen month
    /reports/expiring-leases/      tenancies past ~12 months with no move-out
    /reports/vacant-units/         units earning nothing, and what they could earn
    /reports/tenant-statement/<id>/  one tenant's month-by-month rent roll
    /reports/unit-statement/<id>/    the same, for whoever occupies a unit

Removed instead: the "Landlord Statement" tab. Its shape
(`{total_income, total_expenses, net, rows[]}`) is a monthly P&L, which
`/reports/profit-loss/` already produces from the ledger. Building a SECOND,
differently-derived statement of the same figures is how two reports come to
disagree about one month, and nothing in the repository says what it was meant
to contain that P&L does not. Guessing at a financial statement is not a
judgement call to make on the landlord's behalf.

Every balance here comes from `monthly_ledger` — the one balance the rest of the
product reports — so these tabs cannot disagree with the tenant list, the
dashboard, the arrears report or the tenant's own statement.
"""
import calendar
import datetime as _dt
from decimal import Decimal

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.buildings.models import Unit, UnitStatus
from apps.payments.monthly_ledger import build_monthly_ledger, current_balances
from apps.tenants.models import Tenant, TenantStatus

#: A tenancy is flagged for renewal once it passes this many days. Same
#: threshold the dashboard's `expiring_lease` alert uses, so the two agree.
LEASE_REVIEW_DAYS = 335


def _as_at(month, year, *, default=None) -> _dt.date:
    """The last day of the requested period — the date a balance is 'as at'.

    Falls back to today when the caller sends nothing or sends nonsense, rather
    than raising: a report with a bad query string should show the current
    position, not a 500.
    """
    today = default or timezone.localdate()
    try:
        month, year = int(month), int(year)
        return _dt.date(year, month, calendar.monthrange(year, month)[1])
    except (TypeError, ValueError):
        return today


def _paid_to_date(tenants, as_at) -> dict[int, Decimal]:
    """`{tenant_id: cash received up to as_at}`, in ONE query.

    Deposits and voided payments are excluded — the same two exclusions
    `monthly_ledger` makes, so this figure sits beside the balance without
    contradicting it.

    Grouped rather than per-tenant on purpose: the obvious shape here is an
    aggregate inside the row loop, which is a query per tenant on a page that
    renders the entire rent roll.
    """
    from django.db.models import Sum

    from apps.payments.models import Payment, PaymentType

    ids = [t.pk for t in tenants]
    rows = (
        Payment.objects.filter(
            tenant_id__in=ids, voided_at__isnull=True, payment_date__lte=as_at
        )
        .exclude(payment_type=PaymentType.DEPOSIT)
        .values("tenant_id")
        .annotate(total=Sum("amount"))
    )
    paid = {row["tenant_id"]: row["total"] or Decimal("0") for row in rows}
    return {tid: paid.get(tid, Decimal("0")) for tid in ids}


def _balance_rows(request):
    """`(as_at, [(tenant, balance, paid)])` for every tenant, as at the period."""
    as_at = _as_at(request.query_params.get("month"), request.query_params.get("year"))
    tenants = list(Tenant.objects.select_related("unit", "unit__building").all())
    balances = current_balances(tenants, today=as_at)
    paid = _paid_to_date(tenants, as_at)
    return as_at, [
        (t, balances.get(t.id, Decimal("0")), paid.get(t.id, Decimal("0")))
        for t in tenants
    ]


class RentBalancesReportView(APIView):
    """GET /api/reports/rent-balances/?month=&year= — who owes, as at a period.

    Sibling of `/reports/arrears/`, which lists only tenants in debit. This one
    lists EVERY tenancy with its position, so the landlord can see the whole
    rent roll on one page including the tenants who are square.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        as_at, rows = _balance_rows(request)
        out = []
        outstanding = Decimal("0")
        for tenant, balance, paid in rows:
            if balance > 0:
                outstanding += balance
                status_label = "In arrears"
            elif balance < 0:
                status_label = "In credit"
            else:
                status_label = "Settled"
            out.append({
                "tenant": tenant.full_name,
                "unit": f"{tenant.unit.building.name} — {tenant.unit.label}",
                "monthly_rent": float(tenant.monthly_rent),
                "paid": float(paid),
                "balance": float(balance),
                "status": status_label,
            })
        out.sort(key=lambda r: -r["balance"])
        return Response({
            "as_at": as_at.isoformat(),
            "total_outstanding": float(outstanding),
            "count": len(out),
            "balances": out,
        })


class RentOverpaymentsReportView(APIView):
    """GET /api/reports/rent-overpayments/?month=&year= — tenants in credit.

    A negative rent-roll balance is money the tenant has paid ahead. It is
    reported as a POSITIVE `overpaid` figure because that is how it reads to the
    person looking at it — "this tenant is 12,000 ahead" — while the balance
    itself stays signed everywhere else in the system.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        as_at, rows = _balance_rows(request)
        out = []
        total = Decimal("0")
        for tenant, balance, paid in rows:
            if balance >= 0:
                continue
            overpaid = -balance
            total += overpaid
            out.append({
                "tenant": tenant.full_name,
                "unit": f"{tenant.unit.building.name} — {tenant.unit.label}",
                # What was charged, derived so expected − paid == −overpaid and
                # the row is checkable on screen.
                "expected": float(paid - overpaid),
                "paid": float(paid),
                "overpaid": float(overpaid),
            })
        out.sort(key=lambda r: -r["overpaid"])
        return Response({
            "as_at": as_at.isoformat(),
            "total_overpaid": float(total),
            "count": len(out),
            "overpayments": out,
        })


class ExpiringLeasesReportView(APIView):
    """GET /api/reports/expiring-leases/ — tenancies due a renewal conversation.

    Same rule and threshold as the dashboard's `expiring_lease` alert: an active
    tenancy that has run past ~12 months with no move-out date recorded. There
    is no lease-end field on the model, so the move-in date is the only signal
    available — the report says so rather than implying a contractual date the
    system does not hold.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        today = timezone.localdate()
        threshold = today - _dt.timedelta(days=LEASE_REVIEW_DAYS)
        tenants = (
            Tenant.objects.filter(
                status=TenantStatus.ACTIVE,
                move_out_date__isnull=True,
                move_in_date__lte=threshold,
            )
            .select_related("unit", "unit__building")
            .order_by("move_in_date")
        )
        leases = []
        for t in tenants:
            months = (today.year - t.move_in_date.year) * 12 + (
                today.month - t.move_in_date.month
            )
            leases.append({
                "tenant": t.full_name,
                "unit": f"{t.unit.building.name} — {t.unit.label}",
                "move_in_date": t.move_in_date.isoformat(),
                "months_active": months,
                "status": t.get_status_display(),
            })
        return Response({
            "basis": (
                "Active tenancies more than 12 months old with no move-out date. "
                "The system holds no lease-end date; this is measured from move-in."
            ),
            "count": len(leases),
            "leases": leases,
        })


class VacantUnitsReportView(APIView):
    """GET /api/reports/vacant-units/ — units earning nothing.

    Includes units held for renovation as well as empty ones: both are off the
    market and neither is earning, which is the question this report answers.
    `potential_rent` is what they would bring in per month if let at the
    advertised rate — an opportunity figure, not income, and never mixed into
    one.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        units = (
            Unit.objects.filter(
                status__in=[UnitStatus.VACANT, UnitStatus.UNDER_MAINTENANCE]
            )
            .select_related("building")
            .order_by("building__name", "floor", "label")
        )
        rows = [{
            "building": u.building.name,
            "label": u.label,
            "floor": u.floor,
            "unit_type": u.get_unit_type_display(),
            "monthly_rent": float(u.monthly_rent),
            "status": u.get_status_display(),
        } for u in units]
        return Response({
            "count": len(rows),
            "potential_rent": float(sum(u.monthly_rent for u in units)),
            "units": rows,
        })


def _statement_payload(tenant):
    """One tenant's month-by-month rent roll, in the shape the Reports tab reads.

    Sourced from `build_monthly_ledger`, the same roll the tenant detail page
    and the tenant's own PDF statement are built from, so this tab cannot
    disagree with either.
    """
    ledger = build_monthly_ledger(tenant)
    rows = []
    total_expected = Decimal("0")
    total_paid = Decimal("0")
    for row in ledger:
        expected = Decimal(row["total_due"])
        paid = Decimal(row["paid"])
        balance = Decimal(row["balance"])
        total_expected += expected
        total_paid += paid
        if balance > 0:
            status_label = "Outstanding"
        elif balance < 0:
            status_label = "In credit"
        else:
            status_label = "Settled"
        rows.append({
            "period": row["label"],
            "expected": float(expected),
            "paid": float(paid),
            "balance": float(balance),
            "status": status_label,
        })
    closing = Decimal(ledger[-1]["balance"]) if ledger else Decimal("0")
    return {
        "tenant": {
            "id": tenant.id,
            "name": tenant.full_name,
            "unit": f"{tenant.unit.building.name} — {tenant.unit.label}",
        },
        "total_expected": float(total_expected),
        "total_paid": float(total_paid),
        # The closing balance, not the sum of the monthly ones: the roll-forward
        # is cumulative, so adding the per-month balances would count the same
        # debt once per month it stayed open.
        "total_arrears": float(max(closing, Decimal("0"))),
        "closing_balance": float(closing),
        "rows": rows,
    }


class TenantStatementReportView(APIView):
    """GET /api/reports/tenant-statement/<tenant_id>/"""
    permission_classes = [IsAuthenticated]

    def get(self, request, tenant_id):
        tenant = get_object_or_404(
            Tenant.objects.select_related("unit", "unit__building"), pk=tenant_id
        )
        return Response(_statement_payload(tenant))


class UnitStatementReportView(APIView):
    """GET /api/reports/unit-statement/<unit_id>/ — the statement for a unit.

    A unit has no financial history of its own; its tenants do. This reports the
    current occupant's roll, and says plainly when there is nobody in it rather
    than returning an empty table that reads as "no money owed".
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, unit_id):
        unit = get_object_or_404(Unit.objects.select_related("building"), pk=unit_id)
        tenant = (
            Tenant.objects.filter(
                unit=unit,
                status__in=[TenantStatus.ACTIVE, TenantStatus.NOTICE_GIVEN],
            )
            .select_related("unit", "unit__building")
            .order_by("-move_in_date")
            .first()
        )
        unit_block = {
            "id": unit.id,
            "label": unit.label,
            "building": unit.building.name,
            "status": unit.get_status_display(),
        }
        if tenant is None:
            return Response({
                "unit": unit_block,
                "tenant": None,
                "detail": "No current tenant — this unit has no open statement.",
                "total_expected": 0.0, "total_paid": 0.0, "total_arrears": 0.0,
                "rows": [],
            })

        payload = _statement_payload(tenant)
        payload["unit"] = unit_block
        # The unit tab shows a tenant column; the tenant tab does not.
        for row in payload["rows"]:
            row["tenant"] = tenant.full_name
        return Response(payload)

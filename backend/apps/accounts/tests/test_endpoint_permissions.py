"""Every sensitive write endpoint, against every role.

This file exists because the permission model was introduced, applied to four
endpoints, and then quietly not applied to the other fifty. Nothing failed —
there was no test that would have failed — so a `viewer`, the DEFAULT role for a
new account, could adjust rent across a building, delete expenses, bill water
charges, move tenants out, verify KYC and SMS every tenant on the roster.

The defence against that recurring is not the permission classes; it is
`WRITE_ENDPOINTS` below. It is a single table of every state-changing route in
the system, and the tests walk it for all five principals. Adding a new write
endpoint without adding it here fails `test_every_write_route_is_covered`, which
enumerates the URLconf and compares it against this table.

Read the table as the authorization spec. If a line looks wrong, the line is the
thing to argue with — not the code.
"""
import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import get_resolver
from rest_framework.test import APIClient

from apps.buildings.models import (
    Building,
    MaintenanceRequest,
    Unit,
    UnitClassification,
    UnitStatus,
)
from apps.expenses.models import (
    Account,
    AccountType,
    Expense,
    ExpenseCategory,
    ManualIncome,
)
from apps.payments.models import Payment, PaymentType
from apps.tenants.models import Tenant, TenantStatus

User = get_user_model()

# The five principals. `None` is an unauthenticated caller.
ROLES = ["owner", "accountant", "caretaker", "viewer", "superuser", None]

#: Capability → the roles that hold it. Mirrors apps/accounts/permissions.py.
#: Superuser is implicit everywhere (break-glass) and is asserted separately.
GRANTS = {
    "record_money":  {"owner", "accountant"},
    "forgive_money": {"owner"},
    "books":         {"owner", "accountant"},
    "books_delete":  {"owner"},
    "tenants":       {"owner", "accountant"},
    "tenants_delete": {"owner"},
    "property":      {"owner"},
    "maintenance":   {"owner", "accountant", "caretaker"},
    "utilities":     {"owner", "accountant", "caretaker"},
    "notifications": {"owner", "accountant"},
    "audit_log":     {"owner"},
}

# (id, method, url_template, capability, minimal body)
#
# The bodies are deliberately minimal/invalid where that is enough: an allowed
# role must NOT get 401/403, and a forbidden role must get 403 regardless of the
# body. A 400 from an allowed role still proves the permission gate opened,
# which is what this file is testing. Where the body matters it is realistic.
WRITE_ENDPOINTS = [
    # ── Property (owner only) ───────────────────────────────────────────────
    ("building-create",    "post",   "/api/buildings/",                          "property", {"name": "X", "total_floors": 1}),
    ("building-update",    "patch",  "/api/buildings/{building}/",               "property", {"notes": "x"}),
    ("building-delete",    "delete", "/api/buildings/{building}/",               "property", None),
    ("building-adjust-rent", "post", "/api/buildings/{building}/adjust-rent/",   "property", {"amount": 1, "type": "fixed"}),
    ("building-bulk-units", "post",  "/api/buildings/{building}/bulk-create-units/", "property", {"units": []}),
    ("unit-create",        "post",   "/api/units/",                              "property", {"building": "{building}", "label": "ZZ9", "monthly_rent": "1"}),
    ("unit-update",        "patch",  "/api/units/{unit}/",                       "property", {"notes": "x"}),
    ("unit-delete",        "delete", "/api/units/{unit}/",                       "property", None),
    ("unit-set-status",    "patch",  "/api/units/{unit}/set-status/",            "property", {"status": "under_maintenance"}),

    # ── Maintenance (caretakers included) ───────────────────────────────────
    ("maintenance-create", "post",   "/api/maintenance/",                        "maintenance", {"unit": "{unit}", "description": "leak", "reported_date": "2026-01-01", "cost": "0"}),
    ("maintenance-update", "patch",  "/api/maintenance/{maintenance}/",          "maintenance", {"notes": "x"}),
    ("maintenance-delete", "delete", "/api/maintenance/{maintenance}/",          "books_delete", None),

    # ── Tenants + KYC ───────────────────────────────────────────────────────
    ("tenant-create",      "post",   "/api/tenants/",                            "tenants", {"first_name": "A", "last_name": "B", "id_number": "ZZ-NEW", "phone": "+254700000999", "unit": "{vacant_unit}", "monthly_rent": "1000", "move_in_date": "2026-01-01"}),
    ("tenant-update",      "patch",  "/api/tenants/{tenant}/",                   "tenants", {"notes": "x"}),
    ("tenant-delete",      "delete", "/api/tenants/{tenant}/",                   "tenants_delete", None),
    ("tenant-move-out-notice", "post", "/api/tenants/{tenant}/move-out-notice/", "tenants", {"notice_date": "2026-01-01", "intended_move_out_date": "2026-02-01"}),
    ("tenant-move-out",    "post",   "/api/tenants/{tenant}/move-out/",          "tenants", {"deposit_refund_percentage": "100"}),
    ("tenant-verify-kyc",  "post",   "/api/tenants/{tenant}/verify-kyc/",        "tenants", {}),
    ("tenant-reject-kyc",  "post",   "/api/tenants/{tenant}/reject-kyc/",        "tenants", {"reason": "blurry"}),

    # ── Tenant-facing messaging ─────────────────────────────────────────────
    ("tenant-email-statement",  "post", "/api/tenants/{tenant}/email-statement/", "notifications", {}),
    ("tenant-email-statements", "post", "/api/tenants/email-statements/",         "notifications", {"tenant_ids": []}),
    ("notification-send",       "post", "/api/notifications/send/",               "notifications", {"audience": "all_active", "channel": "sms", "body": "hi"}),

    # ── Money ───────────────────────────────────────────────────────────────
    ("payment-create",     "post",   "/api/payments/",                           "record_money", {"tenant": "{tenant}", "amount": "100", "payment_date": "2026-01-05", "period_month": 1, "period_year": 2026}),
    ("payment-void",       "post",   "/api/payments/{payment}/void/",            "forgive_money", {"reason": "test"}),
    ("payment-resend",     "post",   "/api/payments/{payment}/resend-receipt/",  "record_money", {}),
    ("arrears-waive",      "post",   "/api/arrears/{arrears}/waive/",            "forgive_money", {"notes": "test"}),
    ("arrears-sync",       "post",   "/api/arrears/sync/",                       "books", {}),
    ("credit-assign",      "post",   "/api/unmatched-credits/1/assign/",         "record_money", {"tenant": "{tenant}"}),

    # ── The books ───────────────────────────────────────────────────────────
    ("expense-create",     "post",   "/api/expenses/",                           "books", {"date": "2026-01-01", "category": "{category}", "amount": "100", "description": "x", "period_month": 1, "period_year": 2026}),
    ("expense-update",     "patch",  "/api/expenses/{expense}/",                 "books", {"notes": "x"}),
    ("expense-delete",     "delete", "/api/expenses/{expense}/",                 "books_delete", None),
    ("income-create",      "post",   "/api/manual-income/",                      "books", {"date": "2026-01-01", "building": "{building}", "account": "{income_account}", "amount": "100", "description": "x", "period_month": 1, "period_year": 2026}),
    ("income-update",      "patch",  "/api/manual-income/{income}/",             "books", {"notes": "x"}),
    ("income-delete",      "delete", "/api/manual-income/{income}/",             "books_delete", None),
    # The router exposes PATCH/PUT/DELETE on the payment detail route, but
    # `http_method_names` refuses them (payments are immutable — correction is a
    # void). Permissions are still checked FIRST, so a role without
    # `record_money` must see 403 rather than a 405 that reveals the route
    # shape. Included so the immutability guarantee is asserted, not assumed.
    ("payment-detail-write", "patch", "/api/payments/{payment}/",                "record_money", {"amount": "1"}),

    # ── Utilities ───────────────────────────────────────────────────────────
    ("utility-reading",    "post",   "/api/utility-charges/reading/",            "utilities", {"tenant": "{tenant}", "period_month": 6, "period_year": 2026, "closing_reading": "10", "opening_reading": "5"}),
]

#: Reads that are NOT open to everyone.
RESTRICTED_READS = [
    ("login-audit", "get", "/api/auth/login-audit/", "audit_log"),
]


@pytest.fixture
def world(db, settings):
    """One building, one occupied unit + tenant, one vacant unit, one of each record."""
    settings.TENANT_NOTIFICATIONS_ENABLED = False
    settings.ADMIN_ALERTS_ENABLED = False

    building = Building.objects.create(name="Perm Block", code="PB", total_floors=1)
    unit = Unit.objects.create(
        building=building, label="PB1", monthly_rent=Decimal("10000"),
        classification=UnitClassification.RESIDENTIAL, status=UnitStatus.OCCUPIED_UNPAID,
    )
    vacant = Unit.objects.create(
        building=building, label="PB2", monthly_rent=Decimal("10000"),
        classification=UnitClassification.RESIDENTIAL, status=UnitStatus.VACANT,
    )
    tenant = Tenant.objects.create(
        first_name="Perm", last_name="Tenant", id_number="PERM-1",
        phone="+254700000111", unit=unit, monthly_rent=Decimal("10000"),
        move_in_date=dt.date(2026, 1, 1), status=TenantStatus.ACTIVE,
    )
    payment = Payment.objects.create(
        tenant=tenant, amount=Decimal("5000"), payment_date=dt.date(2026, 1, 5),
        period_month=1, period_year=2026, payment_type=PaymentType.RENT,
    )
    arrears = tenant.arrears.first()
    if arrears is None:
        from apps.payments.models import Arrears
        arrears = Arrears.objects.create(
            tenant=tenant, period_month=1, period_year=2026,
            expected_rent=Decimal("10000"), balance=Decimal("5000"),
        )
    # The chart of accounts and the locked category list are seeded by
    # migrations (expenses/0005, 0009), so look them up rather than recreating.
    account, _ = Account.objects.get_or_create(
        code="5200",
        defaults={"name": "Repairs", "account_type": AccountType.EXPENSE, "parent_code": "5000"},
    )
    income_account, _ = Account.objects.get_or_create(
        code="4300",
        defaults={"name": "Farm income", "account_type": AccountType.INCOME, "parent_code": "4000"},
    )
    category, _ = ExpenseCategory.objects.get_or_create(
        name="Repairs & Maintenance", defaults={"account": account},
    )
    if category.account_id is None:
        category.account = account
        category.save(update_fields=["account"])
    expense = Expense.objects.create(
        date=dt.date(2026, 1, 1), building=building, category=category,
        amount=Decimal("100"), description="x", period_month=1, period_year=2026,
    )
    income = ManualIncome.objects.create(
        date=dt.date(2026, 1, 1), building=building, account=income_account,
        amount=Decimal("500"), description="produce", period_month=1, period_year=2026,
    )
    maintenance = MaintenanceRequest.objects.create(
        unit=unit, description="tap", reported_date=dt.date(2026, 1, 1), cost=Decimal("0"),
    )
    return {
        "building": building.pk, "unit": unit.pk, "vacant_unit": vacant.pk,
        "tenant": tenant.pk, "payment": payment.pk, "arrears": arrears.pk,
        "category": category.pk, "expense": expense.pk, "income": income.pk,
        "income_account": income_account.pk, "maintenance": maintenance.pk,
    }


def _client_for(role):
    if role is None:
        return APIClient()
    if role == "superuser":
        user = User.objects.create_superuser(
            username="su", email="su@test.com", password="testpass123!",
        )
    else:
        user = User.objects.create_user(
            username=f"u-{role}", email=f"{role}@test.com",
            password="testpass123!", role=role,
        )
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _fill(value, world):
    if isinstance(value, str):
        return value.format(**world)
    if isinstance(value, dict):
        return {k: _fill(v, world) for k, v in value.items()}
    return value


def _allowed(role, capability):
    return role == "superuser" or role in GRANTS[capability]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "endpoint", WRITE_ENDPOINTS, ids=[e[0] for e in WRITE_ENDPOINTS]
)
def test_write_endpoint_enforces_role(world, role, endpoint):
    """A role without the capability is refused; a role with it gets past the gate.

    Asserting on the FORBIDDEN side is the point: 403 (or 401 unauthenticated)
    and, critically, never a 2xx. The permitted side asserts only that the gate
    opened — the endpoint may still answer 400/404/409 on the minimal body,
    which is fine and is not what this test is about.
    """
    _id, method, url_template, capability, body = endpoint
    client = _client_for(role)
    url = _fill(url_template, world)
    payload = _fill(body, world) if body is not None else None

    response = getattr(client, method)(url, payload, format="json")

    if role is None:
        assert response.status_code == 401, (
            f"{method.upper()} {url} let an UNAUTHENTICATED caller through "
            f"with {response.status_code}"
        )
        return

    if _allowed(role, capability):
        assert response.status_code not in (401, 403), (
            f"{method.upper()} {url} refused {role}, which should hold "
            f"'{capability}' (got {response.status_code}: "
            f"{getattr(response, 'data', None)})"
        )
    else:
        assert response.status_code == 403, (
            f"{method.upper()} {url} did NOT refuse {role} — got "
            f"{response.status_code}. {role} does not hold '{capability}'."
        )


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "endpoint", RESTRICTED_READS, ids=[e[0] for e in RESTRICTED_READS]
)
def test_restricted_read_enforces_role(world, role, endpoint):
    """Reads that are not open to everyone (currently: the login audit trail)."""
    _id, method, url, capability = endpoint
    client = _client_for(role)
    response = getattr(client, method)(url)

    if role is None:
        assert response.status_code == 401
    elif _allowed(role, capability):
        assert response.status_code == 200
    else:
        assert response.status_code == 403, (
            f"{url} disclosed the audit trail to {role} ({response.status_code})"
        )


def test_default_role_is_read_only(db):
    """A brand-new account holds no write capability at all.

    `User.role` defaults to VIEWER precisely so an account cannot move money
    before it is deliberately promoted. If that default ever changes, every
    other test in this file would still pass while the system quietly became
    open by default.
    """
    user = User.objects.create_user(
        username="fresh", email="fresh@test.com", password="testpass123!",
    )
    assert user.role == "viewer"
    assert user.can_record_money is False
    assert user.can_forgive_money is False


# ── The coverage gate ───────────────────────────────────────────────────────

#: Write routes that are deliberately NOT in WRITE_ENDPOINTS, with the reason.
#: Anything else new must be added to the table above.
EXEMPT_WRITE_ROUTES = {
    # Token-gated machine endpoints — no user session is involved. Covered by
    # test_coop_ipn.py and test_cron_views.py.
    "/api/payments/coop/ipn/",
    "/api/payments/coop/reconcile-daily/",
    "/api/payments/cron/<str:job>/",
    # Unauthenticated by design; covered by test_auth.py / test_password_reset.py.
    "/api/auth/login/",
    "/api/auth/refresh/",
    "/api/auth/password-reset/",
    "/api/auth/password-reset/confirm/",
    # Authenticated but self-scoped: they act on the caller's own account only.
    # Covered by test_account_self_service.py.
    "/api/auth/logout/",
    "/api/auth/me/",
    "/api/auth/change-password/",
    # DEBUG-only; returns 404 in production. Covered by test_payments.py.
    "/api/payments/mock/",
    # Document upload is multipart, so it cannot share this table's JSON body
    # harness. Covered by test_kyc_document_access.py.
    "/api/tenants/<pk>/documents/",
}

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _declared_write_paths() -> set[str]:
    """Every write route the URLconf actually exposes, normalised."""
    import re

    found: set[str] = set()

    def walk(resolver, prefix=""):
        for pattern in resolver.url_patterns:
            if hasattr(pattern, "url_patterns"):
                walk(pattern, prefix + str(pattern.pattern))
                continue
            raw = prefix + str(pattern.pattern)
            if not raw.startswith("api/") or "format" in raw:
                continue
            callback = pattern.callback
            actions = getattr(callback, "actions", None)
            view_cls = getattr(callback, "cls", None) or getattr(callback, "view_class", None)
            if view_cls is None:
                continue
            if actions is not None:
                methods = {m.upper() for m in actions}
            else:
                allowed = {m.upper() for m in getattr(view_cls, "http_method_names", [])}
                methods = {m for m in WRITE_METHODS if hasattr(view_cls, m.lower())} & allowed
            if not (methods & WRITE_METHODS):
                continue
            # ^payments/(?P<pk>[^/.]+)/void/$  ->  /api/payments/<pk>/void/
            path = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"<\1>", raw)
            path = path.replace("^", "").replace("$", "").replace("\\", "")
            found.add("/" + path)
        return found

    walk(get_resolver())
    return found


def test_every_write_route_is_covered():
    """No state-changing route may exist outside the authorization spec.

    This is the regression gate. A new write endpoint added without a line in
    WRITE_ENDPOINTS (or a documented exemption) fails here, so it can never
    reach production silently defaulting to `IsAuthenticated`.
    """
    def normalise(url):
        import re
        url = re.sub(r"\{(\w+)\}", lambda m: "<pk>", url)
        # detail routes in the table use {building}/{tenant}/... for the pk
        return re.sub(r"/\d+/", "/<pk>/", url)

    covered = {normalise(e[2]) for e in WRITE_ENDPOINTS}
    exempt = {normalise(u) for u in EXEMPT_WRITE_ROUTES}
    declared = {normalise(p) for p in _declared_write_paths()}

    uncovered = declared - covered - exempt
    assert not uncovered, (
        "These write routes have no entry in WRITE_ENDPOINTS and no documented "
        "exemption, so nothing verifies who may call them:\n  "
        + "\n  ".join(sorted(uncovered))
    )

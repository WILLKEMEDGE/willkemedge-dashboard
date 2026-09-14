"""Role-based DRF permissions.

Every endpoint used to be `IsAuthenticated` and nothing more, so any account —
a caretaker, a data-entry clerk, a stolen session — could record payments, waive
arrears and reconcile bank credits. The first pass at this file fixed exactly
those three things and stopped there, which left ~50 other write endpoints open:
a `viewer` (the DEFAULT role for a new account) could adjust rent across a whole
building, delete expenses, bill water charges, move tenants out, verify KYC,
download national-ID scans and SMS every tenant on the roster.

This module now covers every write path. The capability model, in one place:

    read anything          → any authenticated user
    manage maintenance     → OWNER, ACCOUNTANT, CARETAKER   (on-site work)
    bill utilities         → OWNER, ACCOUNTANT, CARETAKER   (caretakers read meters)
    record money           → OWNER, ACCOUNTANT              (payments, reconciliation)
    manage tenants         → OWNER, ACCOUNTANT              (tenancy + KYC)
    manage the books       → OWNER, ACCOUNTANT              (expenses, income, billing)
    send notifications     → OWNER, ACCOUNTANT              (costs money; tenant-facing)
    manage property        → OWNER                          (buildings, units, rent)
    forgive money          → OWNER                          (void, waive)
    view the audit trail   → OWNER
    destroy anything       → OWNER                          (see `destructive_roles`)

Superusers always pass; they are the break-glass account.

Reads are deliberately left open to every authenticated role. This is a
single-organisation product with a handful of staff logins, and the operators
need to see the portfolio to do their jobs. The line that matters here is who
may CHANGE things — and, for the audit trail, who may read other people's
sign-in activity.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Role

#: Everyone who is not read-only.
_STAFF = frozenset({Role.OWNER, Role.ACCOUNTANT, Role.CARETAKER})
#: Back-office: may move money and maintain the books.
_BACK_OFFICE = frozenset({Role.OWNER, Role.ACCOUNTANT})
#: The director alone.
_OWNER_ONLY = frozenset({Role.OWNER})
#: Any role at all — the default for reads.
_ANY_ROLE = frozenset(Role)


class RolePermission(BasePermission):
    """Base class: allow reads, gate writes on ``allowed_roles``.

    Subclasses set ``allowed_roles`` and, optionally, ``destructive_roles`` —
    the (usually narrower) set permitted to DELETE. Deleting a financial record
    posts a reversal to the ledger, so it is a different privilege from editing
    one even when the same person may do both.

    ``read_roles`` defaults to "any authenticated user"; set it to restrict a
    read as well (see :class:`CanViewAuditLog`).
    """

    allowed_roles: frozenset = frozenset()
    destructive_roles: frozenset | None = None
    read_roles: frozenset | None = None
    message = "Your role does not permit this action."

    def _roles_for(self, request) -> frozenset:
        if request.method in SAFE_METHODS:
            return self.read_roles if self.read_roles is not None else _ANY_ROLE
        if request.method == "DELETE" and self.destructive_roles is not None:
            return self.destructive_roles
        return self.allowed_roles

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if not (user and user.is_authenticated):
            return False
        # The break-glass account. Checked before the role so a superuser is
        # never locked out by a mis-set role field.
        if user.is_superuser:
            return True
        return getattr(user, "role", None) in self._roles_for(request)


# ── Money ───────────────────────────────────────────────────────────────────

class CanRecordMoney(RolePermission):
    """Create/modify financial records: payments, bank-credit reconciliation."""

    allowed_roles = _BACK_OFFICE
    message = "Your role does not permit recording payments."


class CanForgiveMoney(RolePermission):
    """Write debt off or unwind a receipt: waivers and voids. Owner only."""

    allowed_roles = _OWNER_ONLY
    message = "Only the owner/director may waive arrears or void a payment."


class CanManageBooks(RolePermission):
    """Expenses, manual income and the billing run.

    Recording a cost and recording a receipt are the same class of privilege —
    both write to the general ledger. DELETING one posts a reversal that takes a
    cost back off the books, which is forgiving money by another name, so it is
    reserved to the owner.
    """

    allowed_roles = _BACK_OFFICE
    destructive_roles = _OWNER_ONLY
    message = "Your role does not permit changing the books."


# ── Operations ──────────────────────────────────────────────────────────────

class CanManageTenants(RolePermission):
    """Tenancy lifecycle and KYC: create, edit, notice, move-out, verify/reject.

    Move-out sets the deposit refund and vacates the unit, and KYC verification
    is a compliance record — neither belongs to a read-only account. Deleting a
    tenant is owner-only: it is refused outright when any financial record
    references them (FK PROTECT), so the only tenants it can remove are ones
    with no history, and that is still not a data-entry decision.
    """

    allowed_roles = _BACK_OFFICE
    destructive_roles = _OWNER_ONLY
    message = "Your role does not permit changing tenant records."


class CanManageProperty(RolePermission):
    """Buildings and units — the structure the whole rent roll hangs off.

    Owner-only because a unit's label is what routes an inbound M-Pesa payment
    (see `payments.matching`) and its rent is the obligation every tenant is
    measured against. A mistake here misroutes money rather than mis-typing a
    record.
    """

    allowed_roles = _OWNER_ONLY
    message = "Only the owner/director may change buildings, units or rents."


class CanManageMaintenance(RolePermission):
    """Repair requests. The caretaker's own job, so they may file and update them."""

    allowed_roles = _STAFF
    destructive_roles = _OWNER_ONLY
    message = "Your role does not permit changing maintenance records."


class CanBillUtilities(RolePermission):
    """Capture a meter reading, which bills the tenant for the consumption.

    Caretakers read the meters, so they may enter them; the tariff and the
    resulting charge are derived server-side from the building's configured
    rate, which they cannot change.
    """

    allowed_roles = _STAFF
    message = "Your role does not permit billing utility charges."


class CanSendNotifications(RolePermission):
    """Broadcast SMS/email to tenants.

    Gated because it is tenant-facing, costs money per message, and the sender
    ID is registered as TRANSACTIONAL only — a promotional broadcast carries a
    regulatory penalty.
    """

    allowed_roles = _BACK_OFFICE
    message = "Your role does not permit sending tenant notifications."


# ── Read-restricted ─────────────────────────────────────────────────────────

class CanViewAuditLog(RolePermission):
    """The sign-in audit trail — every staff email, IP and failed attempt.

    The only READ in the system that is not open to every authenticated user:
    it discloses which accounts are being targeted and from where, which is
    reconnaissance rather than portfolio data.
    """

    allowed_roles = _OWNER_ONLY
    read_roles = _OWNER_ONLY
    message = "Only the owner/director may view the login audit trail."

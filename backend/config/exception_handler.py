"""Project-wide DRF exception handling.

Two database errors reach the API as an unhandled 500 with a traceback, which is
both an availability problem and an information leak. Both are ordinary,
expected outcomes of a legitimate request and deserve a real status code:

``ProtectedError``
    Raised by ``on_delete=PROTECT`` when a caller tries to delete a row that
    financial history still references — a tenant with payments, a building with
    expenses, an expense category in use. The refusal is CORRECT and must not be
    weakened; it is what stops accounting history being deleted out from under
    the ledger. It just needs to say so as a 409 instead of crashing.

``IntegrityError``
    A unique/check constraint the caller violated (a duplicate unit label, a
    duplicate meter reading for the same period). 409 with a generic message —
    deliberately generic, because the raw database error names tables, columns
    and constraint internals.
"""
import logging

from django.db import IntegrityError
from django.db.models import ProtectedError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)


def _protected_detail(exc: ProtectedError) -> str:
    """Name what is holding the row, without leaking table internals."""
    try:
        kinds = sorted({obj._meta.verbose_name_plural.lower() for obj in exc.protected_objects})
    except Exception:  # noqa: BLE001 — never let the error path raise
        kinds = []
    what = ", ".join(kinds) if kinds else "other records"
    return (
        f"This record cannot be deleted because {what} still reference it. "
        f"Financial history is never deleted — void or archive instead."
    )


def api_exception_handler(exc, context):
    """DRF handler + the two database errors DRF does not know about."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        return response

    if isinstance(exc, ProtectedError):
        return Response({"detail": _protected_detail(exc)}, status=status.HTTP_409_CONFLICT)

    if isinstance(exc, IntegrityError):
        # Logged in full (operators need the constraint name); returned generic.
        logger.warning("IntegrityError surfaced to the API: %s", exc)
        return Response(
            {"detail": "That change conflicts with a record that already exists."},
            status=status.HTTP_409_CONFLICT,
        )

    # Anything else keeps Django's default handling (500 + Sentry).
    return None

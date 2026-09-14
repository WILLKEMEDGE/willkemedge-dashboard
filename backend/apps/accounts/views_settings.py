"""Settings/admin views: login audit log."""
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import LoginAttempt
from .permissions import CanViewAuditLog


class LoginAuditView(APIView):
    """GET /api/auth/login-audit/ — recent login attempts.

    Owner-only. The trail carries every staff email, the source IP of each
    attempt and which accounts are being targeted by failures — reconnaissance
    material, not portfolio data, so it is the one read in the system that is
    not open to every authenticated role.
    """

    permission_classes = [CanViewAuditLog]

    def get(self, request):
        attempts = LoginAttempt.objects.all()[:50]
        data = [
            {
                "email": a.email,
                "ip_address": a.ip_address,
                "user_agent": a.user_agent[:80],
                "successful": a.successful,
                "attempted_at": a.attempted_at.isoformat(),
            }
            for a in attempts
        ]
        return Response(data)

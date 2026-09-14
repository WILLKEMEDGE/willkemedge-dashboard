"""Profile edit and password change — two features the Settings page could not use.

`PATCH /api/auth/me/` answered 405 (only `get` was implemented) and
`POST /api/auth/change-password/` did not exist at all, so both buttons reported
a generic failure. A password-change control that silently fails is worse than
no control: the operator believes the credential was rotated.

These tests cover both, and the security properties the password change has to
have — the current password is required, and every other session dies.
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

CURRENT = "correct-horse-battery"
NEW = "Tr0ubador&3-staple-x"


class ProfileUpdateTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="me", email="me@test.com", password=CURRENT, role="accountant",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_patch_updates_the_name(self):
        response = self.client.patch("/api/auth/me/", {"first_name": "Wilson", "last_name": "Osoro"})
        assert response.status_code == 200
        self.user.refresh_from_db()
        assert self.user.first_name == "Wilson"
        assert self.user.last_name == "Osoro"

    def test_patch_returns_the_full_user_so_the_client_can_refresh_its_cache(self):
        response = self.client.patch("/api/auth/me/", {"first_name": "W"})
        body = response.json()
        assert body["role"] == "accountant"
        assert body["can_record_money"] is True
        assert body["can_forgive_money"] is False

    def test_email_is_lowercased_and_must_be_unique(self):
        User.objects.create_user(
            username="other", email="taken@test.com", password=CURRENT, role="viewer",
        )
        clash = self.client.patch("/api/auth/me/", {"email": "TAKEN@test.com"})
        assert clash.status_code == 400

        ok = self.client.patch("/api/auth/me/", {"email": "NEW@Test.com"})
        assert ok.status_code == 200
        self.user.refresh_from_db()
        assert self.user.email == "new@test.com"

    def test_a_profile_edit_cannot_escalate_privilege(self):
        """The one thing this endpoint must never do.

        It is reachable by every authenticated role, including `viewer`. If
        `role`/`is_superuser`/`is_staff` were writable, the whole permission
        model would be one PATCH away from irrelevant.
        """
        viewer = User.objects.create_user(
            username="v", email="v@test.com", password=CURRENT, role="viewer",
        )
        client = APIClient()
        client.force_authenticate(user=viewer)

        response = client.patch("/api/auth/me/", {
            "first_name": "Sneaky",
            "role": "owner",
            "is_superuser": True,
            "is_staff": True,
        })
        assert response.status_code == 200

        viewer.refresh_from_db()
        assert viewer.first_name == "Sneaky"   # the legitimate part applied
        assert viewer.role == "viewer"          # the rest was ignored
        assert viewer.is_superuser is False
        assert viewer.is_staff is False

    def test_anonymous_cannot_patch(self):
        anon = APIClient()
        assert anon.patch("/api/auth/me/", {"first_name": "X"}).status_code == 401


class ChangePasswordTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="me", email="me@test.com", password=CURRENT, role="owner",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_password_is_changed(self):
        response = self.client.post("/api/auth/change-password/", {
            "current_password": CURRENT, "new_password": NEW,
        })
        assert response.status_code == 200
        self.user.refresh_from_db()
        assert self.user.check_password(NEW)

    def test_the_current_password_is_required(self):
        """A stolen access token must not be enough to take over the account."""
        response = self.client.post("/api/auth/change-password/", {
            "current_password": "not-it", "new_password": NEW,
        })
        assert response.status_code == 400
        assert "current_password" in response.json()
        self.user.refresh_from_db()
        assert self.user.check_password(CURRENT)

    def test_a_weak_password_is_rejected_by_the_project_validators(self):
        for weak in ("short", "password1234", "111111111111"):
            response = self.client.post("/api/auth/change-password/", {
                "current_password": CURRENT, "new_password": weak,
            })
            assert response.status_code == 400, f"{weak!r} was accepted"
        self.user.refresh_from_db()
        assert self.user.check_password(CURRENT)

    def test_reusing_the_same_password_is_rejected(self):
        response = self.client.post("/api/auth/change-password/", {
            "current_password": CURRENT, "new_password": CURRENT,
        })
        assert response.status_code == 400

    def test_other_sessions_are_invalidated(self):
        """The security property that makes this endpoint worth having.

        A password is usually changed BECAUSE a session is believed compromised.
        If the attacker's refresh token still works, the change achieved nothing.
        """
        stolen = RefreshToken.for_user(self.user)

        # It works before the change.
        anon = APIClient()
        before = anon.post("/api/auth/refresh/", {"refresh": str(stolen)})
        assert before.status_code == 200

        self.client.post("/api/auth/change-password/", {
            "current_password": CURRENT, "new_password": NEW,
        })

        after = anon.post("/api/auth/refresh/", {"refresh": str(stolen)})
        assert after.status_code == 401, "a pre-change refresh token still worked"

    def test_the_caller_is_handed_a_working_replacement_pair(self):
        """...and is NOT signed out by their own password change."""
        response = self.client.post("/api/auth/change-password/", {
            "current_password": CURRENT, "new_password": NEW,
        })
        body = response.json()
        assert body["access"] and body["refresh"]

        anon = APIClient()
        refreshed = anon.post("/api/auth/refresh/", {"refresh": body["refresh"]})
        assert refreshed.status_code == 200

        anon.credentials(HTTP_AUTHORIZATION=f"Bearer {body['access']}")
        assert anon.get("/api/auth/me/").status_code == 200

    def test_anonymous_cannot_change_a_password(self):
        anon = APIClient()
        response = anon.post("/api/auth/change-password/", {
            "current_password": CURRENT, "new_password": NEW,
        })
        assert response.status_code == 401


class LoginAuditExposureTests(APITestCase):
    """The audit trail carries every staff email, IP and failed attempt.

    It was readable by any authenticated account, which hands a compromised
    `viewer` a list of which admin emails are worth attacking and from where.
    """

    def setUp(self):
        self.client = APIClient()

    def _as(self, role):
        user = User.objects.create_user(
            username=f"u{role}", email=f"{role}@test.com",
            password=CURRENT, role=role,
        )
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_owner_can_read(self):
        assert self._as("owner").get("/api/auth/login-audit/").status_code == 200

    def test_accountant_caretaker_and_viewer_cannot(self):
        for role in ("accountant", "caretaker", "viewer"):
            response = self._as(role).get("/api/auth/login-audit/")
            assert response.status_code == 403, f"{role} could read the audit trail"

    def test_anonymous_cannot(self):
        assert self.client.get("/api/auth/login-audit/").status_code == 401

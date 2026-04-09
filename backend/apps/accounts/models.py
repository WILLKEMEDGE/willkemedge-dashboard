"""
Custom user model for the dashboard.

The system has a single admin user in v1, but we use a custom user model from
the start so we can extend it later without painful migrations.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Single-owner admin user."""

    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        db_table = "accounts_user"

    def __str__(self) -> str:
        return self.email


class LoginAttempt(models.Model):
    """Audit trail of every login attempt — successful and failed."""

    email = models.EmailField()
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    successful = models.BooleanField(default=False)
    attempted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_login_attempt"
        indexes = [
            models.Index(fields=["email", "attempted_at"]),
            models.Index(fields=["ip_address", "attempted_at"]),
        ]
        ordering = ["-attempted_at"]

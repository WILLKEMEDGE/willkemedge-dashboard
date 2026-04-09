from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import LoginAttempt, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = ("email", "username", "is_staff", "is_active", "date_joined")
    ordering = ("email",)


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("email", "ip_address", "successful", "attempted_at")
    list_filter = ("successful", "attempted_at")
    search_fields = ("email", "ip_address")
    readonly_fields = ("email", "ip_address", "user_agent", "successful", "attempted_at")

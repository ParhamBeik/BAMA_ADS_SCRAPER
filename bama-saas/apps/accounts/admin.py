"""Staff can deactivate an account or set a password without a shell.

Signup never grants staff. Password reset by email does not exist (no mail
backend); this change-password form is the operator path for a locked-out user.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.forms import ReadOnlyPasswordHashField, UserChangeForm

from apps.accounts.models import User


class UserEditForm(UserChangeForm):
    password = ReadOnlyPasswordHashField(
        help_text="Passwords are stored hashed. Use the change-password form."
    )

    class Meta(UserChangeForm.Meta):
        model = User
        fields = (
            "email", "full_name", "password", "is_active", "is_staff",
            "is_superuser", "groups", "user_permissions",
        )


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = UserEditForm
    ordering = ("email",)
    list_display = ("email", "is_staff", "is_active", "date_joined")
    list_filter = ("is_staff", "is_active")
    search_fields = ("email",)
    filter_horizontal = ("groups", "user_permissions")
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password", "full_name")}),
        ("Permissions", {"fields": (
            "is_active", "is_staff", "is_superuser", "groups", "user_permissions",
        )}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )

    def has_add_permission(self, request):
        # createsuperuser is the documented path; this form would need a
        # custom USERNAME_FIELD creation widget for no extra behaviour.
        return False

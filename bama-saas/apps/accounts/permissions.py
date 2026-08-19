from rest_framework.permissions import SAFE_METHODS, BasePermission


class ReadOnlyForDemo(BasePermission):
    """Authenticated for everything; the demo account additionally loses
    write access. Production's DEFAULT_PERMISSION_CLASSES (see
    config/settings/prod.py) — dev stays AllowAny, unchanged."""

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return True
        return not user.is_demo

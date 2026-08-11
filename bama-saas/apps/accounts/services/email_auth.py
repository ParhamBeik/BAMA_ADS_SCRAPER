"""Verification and password-reset email helpers."""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core import signing
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode


VERIFY_SALT = "bama-email-verify"
VERIFY_MAX_AGE = 60 * 60 * 24


def make_verification_token(user_id: str) -> str:
    return signing.dumps({"uid": str(user_id)}, salt=VERIFY_SALT)


def read_verification_token(token: str) -> str | None:
    try:
        payload = signing.loads(token, salt=VERIFY_SALT, max_age=VERIFY_MAX_AGE)
    except signing.BadSignature:
        return None
    return payload.get("uid")


_reset_generator = PasswordResetTokenGenerator()


def make_password_reset_token(user) -> tuple[str, str]:
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = _reset_generator.make_token(user)
    return uid, token


def check_password_reset_token(user, token: str) -> bool:
    return _reset_generator.check_token(user, token)


def _frontend(path: str) -> str:
    base = settings.FRONTEND_URL.rstrip("/")
    return f"{base}{path}"


def send_verification_email(user) -> None:
    token = make_verification_token(user.pk)
    link = _frontend(f"/verify?token={token}")
    send_mail(
        subject="Verify your Bama account",
        message=f"Open this link to verify your email:\n\n{link}\n",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def send_password_reset_email(user) -> None:
    uid, token = make_password_reset_token(user)
    link = _frontend(f"/reset-password?uid={uid}&token={token}")
    send_mail(
        subject="Reset your Bama password",
        message=f"Open this link to reset your password:\n\n{link}\n",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )


def send_deletion_recovery_email(user, token: str) -> None:
    link = _frontend(f"/restore-account?token={token}")
    send_mail(
        subject="Restore your Bama account",
        message=f"Your account is scheduled for deletion. Restore it here:\n\n{link}\n",
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )

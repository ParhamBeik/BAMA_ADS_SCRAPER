"""Staff control-center APIs under /api/admin/."""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Count, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.entitlements import PLAN_LIMITS
from apps.accounts.models import ProAccessRequest, StaffAuditLog, Subscription
from apps.accounts.permissions import IsStaff
from apps.core.models import Ad, Brand, IngestReject, Model
from apps.jobs.services.health import run_checks

User = get_user_model()


def _audit(actor, action, *, target_type="", target_id="", detail=None):
    StaffAuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        detail=detail or {},
    )


class AdminUsersView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        qs = User.objects.all().order_by("-date_joined")
        if q:
            qs = qs.filter(email__icontains=q)
        ordering = request.query_params.get("ordering", "-date_joined")
        if ordering.lstrip("-") in {"email", "date_joined", "is_staff", "is_active"}:
            qs = qs.order_by(ordering)
        page = max(int(request.query_params.get("page", 1)), 1)
        size = min(max(int(request.query_params.get("page_size", 25)), 1), 100)
        start = (page - 1) * size
        rows = list(qs[start : start + size])
        results = []
        for u in rows:
            sub = u.subscriptions.order_by("-started_at").first()
            results.append({
                "id": str(u.id),
                "email": u.email,
                "full_name": u.full_name,
                "is_staff": u.is_staff,
                "is_active": u.is_active,
                "email_verified_at": u.email_verified_at,
                "date_joined": u.date_joined,
                "plan": sub.plan_type if sub else None,
                "plan_status": sub.status if sub else None,
                "expires_at": sub.expires_at if sub else None,
            })
        return Response({"count": qs.count(), "results": results, "page": page})


class AdminUserDetailView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def patch(self, request, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        data = request.data
        updates = []
        if "is_active" in data:
            user.is_active = bool(data["is_active"])
            updates.append("is_active")
        if "email_verified" in data:
            user.email_verified_at = timezone.now() if data["email_verified"] else None
            updates.append("email_verified_at")
        if updates:
            user.save(update_fields=updates)
        if "plan_type" in data:
            sub = user.subscriptions.order_by("-started_at").first()
            if sub is None:
                sub = Subscription.objects.create(user=user)
            sub.plan_type = data["plan_type"]
            if "expires_at" in data:
                sub.expires_at = data["expires_at"] or None
            if data.get("clear_expiry"):
                sub.expires_at = None
            sub.status = Subscription.Status.ACTIVE
            sub.save()
        _audit(request.user, "user.update", target_type="user", target_id=user.id, detail=dict(data))
        return Response({"detail": "updated"})


class AdminProRequestsView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def get(self, request):
        status_filter = request.query_params.get("status", "pending")
        qs = ProAccessRequest.objects.select_related("user").order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return Response([
            {
                "id": str(r.id),
                "user_email": r.user.email,
                "user_id": str(r.user_id),
                "status": r.status,
                "message": r.message,
                "created_at": r.created_at,
            }
            for r in qs[:100]
        ])


class AdminProRequestActionView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def post(self, request, request_id):
        try:
            req = ProAccessRequest.objects.select_related("user").get(pk=request_id)
        except ProAccessRequest.DoesNotExist:
            return Response({"detail": "Not found."}, status=404)
        action = request.data.get("action")
        if action == "approve":
            days = request.data.get("days", 30)
            expires = None
            if days is not None and days != "" and int(days) > 0:
                expires = timezone.now() + timedelta(days=int(days))
            req.status = ProAccessRequest.Status.APPROVED
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.granted_expires_at = expires
            req.staff_note = request.data.get("note", "")
            req.save()
            sub = req.user.subscriptions.order_by("-started_at").first()
            if sub is None:
                sub = Subscription.objects.create(user=req.user)
            sub.plan_type = Subscription.PlanType.PRO
            sub.status = Subscription.Status.ACTIVE
            sub.expires_at = expires
            sub.save()
        elif action == "reject":
            req.status = ProAccessRequest.Status.REJECTED
            req.reviewed_by = request.user
            req.reviewed_at = timezone.now()
            req.staff_note = request.data.get("note", "")
            req.save()
        else:
            return Response({"detail": "action must be approve or reject"}, status=400)
        _audit(request.user, f"pro_request.{action}", target_type="pro_request", target_id=req.id)
        return Response({"status": req.status})


class AdminHealthView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def get(self, request):
        crawl = [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in run_checks()]
        with connection.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            db_size = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()")
            connections = cur.fetchone()[0]
        from django.db.migrations.recorder import MigrationRecorder
        applied = MigrationRecorder.Migration.objects.count()
        return Response({
            "database": {"size_bytes": db_size, "connections": connections, "migrations_applied": applied},
            "catalog": {
                "ads": Ad.objects.count(),
                "active_ads": Ad.objects.filter(status=Ad.Status.ACTIVE).count(),
                "brands": Brand.objects.count(),
                "models": Model.objects.count(),
                "rejects_24h": IngestReject.objects.filter(
                    observed_at__gte=timezone.now() - timedelta(hours=24)
                ).count(),
            },
            "crawl": crawl,
            "plan_limits": {k: v.__dict__ for k, v in PLAN_LIMITS.items()},
        })


class AdminReviewQueueView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def get(self, request):
        return Response({
            "unconfirmed_brands": list(
                Brand.objects.filter(is_confirmed=False).values("slug", "name_fa")[:50]
            ),
            "unconfirmed_models": list(
                Model.objects.filter(is_confirmed=False).values("id", "name_fa", "brand_id")[:50]
            ),
            "recent_rejects": list(
                IngestReject.objects.order_by("-id").values("id", "code", "rule", "observed_at")[:50]
            ),
        })


class AdminConfirmDimensionView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def post(self, request):
        kind = request.data.get("kind")
        if kind == "brand":
            brand = Brand.objects.get(slug=request.data["id"])
            brand.is_confirmed = True
            brand.save(update_fields=["is_confirmed"])
            _audit(request.user, "confirm.brand", target_type="brand", target_id=brand.slug)
        elif kind == "model":
            model = Model.objects.get(pk=request.data["id"])
            model.is_confirmed = True
            model.save(update_fields=["is_confirmed"])
            _audit(request.user, "confirm.model", target_type="model", target_id=model.id)
        else:
            return Response({"detail": "kind must be brand or model"}, status=400)
        return Response({"confirmed": True})


class AdminAuditLogView(APIView):
    permission_classes = [IsAuthenticated, IsStaff]

    @extend_schema(tags=["admin"])
    def get(self, request):
        qs = StaffAuditLog.objects.select_related("actor").order_by("-created_at")[:100]
        return Response([
            {
                "id": str(a.id),
                "actor": a.actor.email if a.actor else None,
                "action": a.action,
                "target_type": a.target_type,
                "target_id": a.target_id,
                "detail": a.detail,
                "created_at": a.created_at,
            }
            for a in qs
        ])

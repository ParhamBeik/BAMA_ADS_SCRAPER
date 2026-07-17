"""Registration and current-user views. JWT login/refresh come from SimpleJWT."""

from django.contrib.auth import get_user_model
from rest_framework.generics import CreateAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from .serializers import RegisterSerializer, SubscriptionSerializer, UserSerializer

User = get_user_model()


class RegisterView(CreateAPIView):
    """POST /api/auth/register/ — create a user + free-tier subscription."""

    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class MeView(RetrieveAPIView):
    """GET /api/auth/me/ — current user + active subscription."""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        user = request.user
        sub = user.subscriptions.order_by("-started_at").first()
        return Response({
            "user": UserSerializer(user).data,
            "subscription": SubscriptionSerializer(sub).data if sub else None,
        })

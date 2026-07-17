"""Market URL configuration."""

from django.urls import path

from . import views

app_name = "market"

urlpatterns = [
    path("markets/", views.markets, name="markets"),
    path("markets/<int:model_id>/true-mean/", views.market_true_mean, name="true-mean"),
    path("markets/<int:model_id>/bollinger/", views.market_bollinger, name="bollinger"),
    path("markets/<int:model_id>/price-trends/", views.market_price_trends, name="price-trends"),
    path("ads/<str:code>/price-history/", views.ad_price_history, name="ad-price-history"),
]

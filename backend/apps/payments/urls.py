"""Payment URL routes."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ArrearsViewSet, PaymentViewSet

router = DefaultRouter()
router.register("payments", PaymentViewSet, basename="payment")
router.register("arrears", ArrearsViewSet, basename="arrears")

app_name = "payments"

urlpatterns = [
    path("", include(router.urls)),
]

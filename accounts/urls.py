# accounts/urls.py
from rest_framework.routers import DefaultRouter
from django.urls import path, include
from . import views


# Create a router and register our viewset with it.
router = DefaultRouter()

router.register(r'users', views.UserViewSet, basename='user')


urlpatterns = [
    path('', include(router.urls)),
    path('check-email/', views.check_email, name='check_email'),
    path('register/', views.RegisterView.as_view(), name='register'),
    path('login/', views.LoginView.as_view(), name='login'),

    path('resend-confirmation/', views.resend_confirmation_email, name='resend_confirmation'),
]

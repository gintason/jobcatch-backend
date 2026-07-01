"""Auth routes mounted at /api/v1/auth/."""
from django.urls import path

from . import views

urlpatterns = [
    path("register/", views.RegisterView.as_view(), name="register"),
    path("verify-otp/", views.VerifyOTPView.as_view(), name="verify-otp"),
    path("resend-otp/", views.ResendOTPView.as_view(), name="resend-otp"),
    path("login/", views.LoginView.as_view(), name="login"),
    path("token/refresh/", views.CookieTokenRefreshView.as_view(), name="token-refresh"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("logout-all/", views.LogoutAllView.as_view(), name="logout-all"),
    path("password-reset/request/", views.PasswordResetRequestView.as_view(), name="pwreset-request"),
    path("password-reset/confirm/", views.PasswordResetConfirmView.as_view(), name="pwreset-confirm"),
    path("me/", views.MeView.as_view(), name="me"),
    path("sessions/", views.SessionListView.as_view(), name="sessions"),
    path("sessions/<uuid:session_id>/", views.SessionListView.as_view(), name="session-detail"),
]

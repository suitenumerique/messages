"""Authentication URLs for the Messages core app."""

from django.urls import include, path

from .views import OIDCLogoutView

urlpatterns = [
    # lasuite's logout views are not swappable through settings (unlike the
    # authenticate/callback views): override their paths by URL ordering, the
    # same way lasuite overrides mozilla-django-oidc's.
    path("logout/", OIDCLogoutView.as_view(), name="oidc_logout_custom"),
    path("", include("lasuite.oidc_login.urls")),
]

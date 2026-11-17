"""Checkouts started in the mobile app return to the app's callback page."""
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from apps.payments.middleware import ClientChannelMiddleware, current_client
from apps.payments.services import callback_url

WEB = "https://jobcatchonline.com/payments/callback"
APP = "https://jobcatchonline.com/payments/callback/app"


def _callback_during(**headers):
    seen = {}

    def view(request):
        seen["url"] = callback_url()
        return HttpResponse("ok")

    ClientChannelMiddleware(view)(RequestFactory().post("/x", **headers))
    return seen["url"]


@override_settings(PAYMENT_CALLBACK_URL=WEB, PAYMENT_APP_CALLBACK_URL=APP)
def test_web_requests_return_to_website():
    assert _callback_during() == WEB
    assert _callback_during(HTTP_X_JOBCATCH_CLIENT="something-else") == WEB


@override_settings(PAYMENT_CALLBACK_URL=WEB, PAYMENT_APP_CALLBACK_URL=APP)
def test_mobile_requests_return_to_app_page():
    assert _callback_during(HTTP_X_JOBCATCH_CLIENT="mobile") == APP


@override_settings(PAYMENT_CALLBACK_URL=WEB, PAYMENT_APP_CALLBACK_URL=APP)
def test_client_is_reset_after_the_request():
    _callback_during(HTTP_X_JOBCATCH_CLIENT="mobile")
    assert current_client() == "web"
    assert callback_url() == WEB

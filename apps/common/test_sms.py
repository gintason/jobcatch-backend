import io, urllib.error
from unittest.mock import patch
import pytest
from django.test import override_settings
from apps.common.sms import SMSError, send_sms

@override_settings(SMS_BACKEND="termii", TERMII_API_KEY="k", TERMII_SENDER_ID="OE Alert", TERMII_CHANNEL="dnd")
def test_termii_error_body_is_reported():
    err = urllib.error.HTTPError("u", 422, "Unprocessable Entity", {}, io.BytesIO(b'{"message":"ApplicationSenderId not found"}'))
    with patch("urllib.request.urlopen", side_effect=err), pytest.raises(SMSError) as e:
        send_sms("07030000000", "hi")
    assert "ApplicationSenderId not found" in str(e.value) and "OE Alert" in str(e.value)

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


@override_settings(SMS_BACKEND="termii", TERMII_API_KEY="k", TERMII_SENDER_ID="OE Alert", TERMII_CHANNEL="dnd")
def test_termii_success_logs_message_id(caplog):
    resp = io.BytesIO(b'{"code":"ok","message_id":"3017123","balance":512.5}')
    resp.status = 200
    with patch("urllib.request.urlopen") as urlopen, caplog.at_level("INFO", logger="apps.common.sms"):
        urlopen.return_value.__enter__.return_value = resp
        send_sms("07030000000", "hi")
    sent = urlopen.call_args[0][0]
    import json
    assert json.loads(sent.data)["to"] == "2347030000000"
    assert json.loads(sent.data)["channel"] == "dnd" and json.loads(sent.data)["from"] == "OE Alert"
    assert "message_id=3017123" in caplog.text and "balance=512.5" in caplog.text

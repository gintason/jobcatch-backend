"""Single entry point for creating notifications (in-app row + async email)."""


def notify(user, *, kind, title, body="", data=None):
    from .models import Notification
    from .tasks import send_notification_email

    notification = Notification.objects.create(
        user=user, kind=kind, title=title, body=body, data=data or {}
    )
    # Email is best-effort and must never block or break the triggering action.
    send_notification_email.delay(user.email, title, body)
    return notification

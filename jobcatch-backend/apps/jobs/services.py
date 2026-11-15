"""
Application recruitment pipeline.

Employers move an applicant through submitted -> shortlisted -> hired, with
rejection possible from either active state. `transition_application` rejects
illegal moves the same way bookings do.
"""
from rest_framework.exceptions import ValidationError

from .models import Application, ApplicationStatus

APPLICATION_TRANSITIONS = {
    ApplicationStatus.SUBMITTED: {ApplicationStatus.SHORTLISTED, ApplicationStatus.REJECTED},
    ApplicationStatus.SHORTLISTED: {ApplicationStatus.HIRED, ApplicationStatus.REJECTED},
    ApplicationStatus.REJECTED: set(),
    ApplicationStatus.HIRED: set(),
}


def transition_application(application: Application, to_status: str) -> Application:
    allowed = APPLICATION_TRANSITIONS.get(application.status, set())
    if to_status not in allowed:
        raise ValidationError(
            f"Illegal transition: a '{application.status}' application cannot become "
            f"'{to_status}'. Allowed: {sorted(s.value for s in allowed) or 'none'}."
        )
    application.status = to_status
    application.save(update_fields=["status", "updated_at"])
    return application

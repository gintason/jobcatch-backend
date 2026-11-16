"""Nigerian phone-number normalisation shared by SMS, WhatsApp and bill payments."""
import re

# Mobile prefixes after the leading 0 (e.g. 0803...). Kept broad on purpose:
# carriers keep adding ranges and a false "invalid number" blocks real users.
_LOCAL_RE = re.compile(r"^0[789][01]\d{8}$")


class InvalidPhone(ValueError):
    pass


def digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def to_local(raw: str) -> str:
    """'+234 803 123 4567' / '2348031234567' / '08031234567' -> '08031234567'."""
    d = digits(raw)
    if d.startswith("234") and len(d) == 13:
        d = "0" + d[3:]
    elif len(d) == 10 and d[0] in "789":
        d = "0" + d
    if not _LOCAL_RE.match(d):
        raise InvalidPhone("Enter a valid Nigerian mobile number, e.g. 0803 123 4567.")
    return d


def to_international(raw: str) -> str:
    """'08031234567' -> '2348031234567' (no plus — the format Termii/WhatsApp expect)."""
    return "234" + to_local(raw)[1:]


def mask(raw: str) -> str:
    d = digits(raw)
    return f"{d[:4]}****{d[-3:]}" if len(d) >= 7 else "****"

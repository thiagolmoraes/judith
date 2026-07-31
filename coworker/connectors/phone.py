"""Phone numbers as the allow-list sees them — pure functions, no I/O.

WhatsApp identities reach us as bare digits (`jid_to_number` strips the JID suffix), so
that IS the allow-list key. A number the owner types by hand must reduce to the exact
same string a webhook would produce, or they would authorize a person who then still
gets parked. `normalize_phone` is that reduction; `format_phone` is the readable form
for the UI, and never becomes the stored value.

Brazil-aware only where it must be: a number typed without a country code is assumed
local (+55), and the trunk `0` some people prefix is dropped. Everything else passes
through as digits — this is a normalizer, not a full libphonenumber.
"""

from __future__ import annotations

import re

_DIGITS_RE = re.compile(r"\d+")
_BR_CC = "55"
# E.164 allows 15 digits max; below ~10 there is no country+area+line to speak of.
_MIN_DIGITS = 10
_MAX_DIGITS = 15
# A local BR number is 10 (landline) or 11 (mobile, leading 9) digits: area code + line.
_BR_LOCAL_LENGTHS = (10, 11)


def normalize_phone(raw: str) -> str | None:
    """Digits-only key for the allow-list, or None when `raw` can't be a phone number.

    Accepts what people paste: `+55 11 99999-9999`, `(11) 99999-9999`, a full JID.
    """
    if not raw:
        return None
    # A pasted JID carries the number in front of the suffix.
    raw = raw.split("@", 1)[0]
    # A leading "+" means the number already carries its country code — decisive, because
    # "+1 415 555 0199" is 11 digits, the same length as a BR mobile written locally.
    explicit_cc = raw.lstrip().startswith("+")
    digits = "".join(_DIGITS_RE.findall(raw))
    if not digits:
        return None
    # Trunk prefix: "011999999999" → "11999999999" (only when what follows still looks
    # like a local number, so a legitimate leading 0 country-ish string isn't mangled).
    if digits.startswith("0") and len(digits) - 1 in _BR_LOCAL_LENGTHS:
        digits = digits[1:]
    # No country code (local BR): prepend it, so the key matches the webhook's.
    # LENGTH decides, never the leading digits: area code 55 (Santa Maria/RS) makes
    # "55 99123-4567" start with the country code it is still missing, and skipping it
    # there would store a key no webhook ever produces.
    if not explicit_cc and len(digits) in _BR_LOCAL_LENGTHS:
        digits = _BR_CC + digits
    if not (_MIN_DIGITS <= len(digits) <= _MAX_DIGITS):
        return None
    return digits


def format_phone(digits: str) -> str:
    """Readable form for display only. Anything that isn't a plain number (a group JID,
    an unparseable id) comes back untouched — better a raw id than a wrong phone."""
    if not digits or not digits.isdigit():
        return digits
    if digits.startswith(_BR_CC) and len(digits) in (12, 13):
        area, line = digits[2:4], digits[4:]
        half = 5 if len(line) == 9 else 4  # mobile (9 digits) vs landline (8)
        return f"+{_BR_CC} {area} {line[:half]}-{line[half:]}"
    if digits.startswith("1") and len(digits) == 11:  # NANP
        return f"+1 {digits[1:4]} {digits[4:7]} {digits[7:]}"
    return f"+{digits}"

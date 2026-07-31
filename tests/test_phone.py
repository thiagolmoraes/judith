"""Phone normalization/formatting — the allow-list stores bare digits (what Evolution
gives us as `user_id`), so anything the owner types has to reduce to the SAME string a
webhook would produce, or the person is authorized in name only."""

import pytest

from coworker.connectors.phone import format_phone, normalize_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("5511999999999", "5511999999999"),  # already normalized
        ("+55 11 99999-9999", "5511999999999"),  # the shape a human types
        ("(11) 99999-9999", "5511999999999"),  # local BR: country code implied
        ("011999999999", "5511999999999"),  # trunk prefix dropped
        ("+1 415 555 0199", "14155550199"),  # non-BR passes through
        ("5511999999999@s.whatsapp.net", "5511999999999"),  # pasted JID
        # Area code 55 (Santa Maria/RS) starts with the country code it still lacks:
        # length decides, not the leading digits, or the key never matches a webhook's.
        ("55991234567", "5555991234567"),
        ("(55) 99123-4567", "5555991234567"),
        ("+55 55 99123-4567", "5555991234567"),  # already complete: untouched
    ],
)
def test_normalize_accepts_what_people_actually_type(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "   ", "abc", "12", "+", "55", "1" * 20, "(11) 9999-999"]
)
def test_normalize_rejects_garbage_and_impossible_lengths(raw):
    assert normalize_phone(raw) is None


def test_normalize_is_idempotent():
    once = normalize_phone("+55 (11) 99999-9999")
    assert once is not None
    assert normalize_phone(once) == once


@pytest.mark.parametrize(
    "digits,expected",
    [
        ("5511999999999", "+55 11 99999-9999"),  # BR mobile (9 digits)
        ("551133334444", "+55 11 3333-4444"),  # BR landline (8 digits)
        ("14155550199", "+1 415 555 0199"),  # US
        ("999999999999999", "+999999999999999"),  # unknown shape: digits, one plus
    ],
)
def test_format_is_readable_and_never_lossy(digits, expected):
    assert format_phone(digits) == expected
    # Whatever we display must normalize back to what we store.
    assert normalize_phone(format_phone(digits)) in (digits, None)


def test_format_passes_through_non_numbers_untouched():
    # Group JIDs and ids we can't parse must render as-is rather than as a bad phone.
    assert format_phone("") == ""
    assert format_phone("120363@g.us") == "120363@g.us"

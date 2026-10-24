"""Conservative parsing of source-declared Gregorian dates and ISO datetimes.

This module validates the *notation* of a declared time. A normalized value
does not establish that an event occurred, was published, or took effect.
Callers remain responsible for retaining the original source text separately.
No local timezone, locale, or daylight-saving rules are consulted.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone


POLICY = "source-declared-time-v1"

_DATE = re.compile(r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})\Z")
_BASIC_DATE = re.compile(r"(?P<year>[0-9]{4})(?P<month>[0-9]{2})(?P<day>[0-9]{2})\Z")
_DATETIME = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    r"[Tt ](?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})"
    r"(?::(?P<second>[0-9]{2})(?:[.,](?P<fraction>[0-9]+))?)?"
    r"(?P<zone>.*)\Z"
)
_BASIC_DATETIME = re.compile(
    r"(?P<year>[0-9]{4})(?P<month>[0-9]{2})(?P<day>[0-9]{2})"
    r"[Tt](?P<hour>[0-9]{2})(?P<minute>[0-9]{2})"
    r"(?:(?P<second>[0-9]{2})(?:[.,](?P<fraction>[0-9]+))?)?"
    r"(?P<zone>.*)\Z"
)
_OFFSET = re.compile(r"(?P<sign>[+-])(?P<hours>[0-9]{2}):(?P<minutes>[0-9]{2})\Z")


def _result(status, precision=None, canonical=None, basis="none", reason=None):
    return {
        "status": status,
        "precision": precision,
        "canonical": canonical,
        "basis": basis,
        "reason": reason,
    }


def _calendar_date(parts, basis, precision):
    try:
        return date(int(parts["year"]), int(parts["month"]), int(parts["day"]))
    except ValueError:
        return _result("invalid", precision, basis=basis, reason="invalid_calendar_date")


def normalize_declared_time(value: object) -> dict:
    """Return a syntax-level interpretation without altering the given value.

    Date-only values remain dates. Datetimes require Z or an explicit numeric
    offset to produce a UTC canonical string; missing timezones stay ambiguous.
    The result never asserts the truth or effective date of a source claim.
    """
    if value is None:
        return _result("missing", reason="no_value")
    if not isinstance(value, str):
        return _result("unsupported", reason="non_string_value")
    if len(value) > 128:
        return _result("unsupported", reason="input_exceeds_128_characters")

    declared = value.strip()
    if not declared:
        return _result("missing", reason="empty_value")

    date_match = _DATE.fullmatch(declared) or _BASIC_DATE.fullmatch(declared)
    if date_match is not None:
        parsed = _calendar_date(date_match.groupdict(), "date_only", "day")
        if isinstance(parsed, dict):
            return parsed
        return _result("normalized", "day", parsed.isoformat(), "date_only")

    match = _DATETIME.fullmatch(declared) or _BASIC_DATETIME.fullmatch(declared)
    if match is None:
        reason = "unsupported_locale_or_natural_language_date" if re.search(r"[A-Za-z]", declared) else "unsupported_format"
        return _result("unsupported", reason=reason)

    fields = match.groupdict()
    fraction = fields["fraction"]
    precision = "microsecond" if fraction is not None else "second" if fields["second"] is not None else "minute"
    # datetime.fromisoformat would silently drop digits after microseconds.
    if fraction is not None and len(fraction) > 6:
        return _result("unsupported", reason="fraction_exceeds_six_digits")

    zone = fields["zone"]
    basis = "timezone_missing" if not zone else "explicit_offset" if zone.upper() == "Z" or _OFFSET.fullmatch(zone) else "none"
    parsed_date = _calendar_date(fields, basis, precision)
    if isinstance(parsed_date, dict):
        return parsed_date

    hour, minute = int(fields["hour"]), int(fields["minute"])
    second = int(fields["second"]) if fields["second"] is not None else 0
    if second == 60 and hour <= 23 and minute <= 59:
        return _result("unsupported", precision, basis=basis, reason="leap_second_context_required")
    rollover = hour == 24 and minute == second == 0 and (fraction is None or int(fraction) == 0)
    if (hour > 23 and not rollover) or minute > 59 or second > 59:
        return _result("invalid", precision, basis=basis, reason="invalid_clock_time")

    if not zone:
        return _result("ambiguous", precision, basis="timezone_missing", reason="timezone_missing")

    if zone.upper() == "Z":
        offset = timezone.utc
    else:
        offset_match = _OFFSET.fullmatch(zone)
        if offset_match is None:
            if zone.startswith(("+", "-")):
                return _result("invalid", precision, reason="invalid_offset_format")
            return _result("unsupported", precision, reason="unsupported_timezone_designator")
        offset_hours = int(offset_match["hours"])
        offset_minutes = int(offset_match["minutes"])
        if offset_hours > 23 or offset_minutes > 59:
            return _result("invalid", precision, reason="invalid_offset")
        # RFC 3339/9557: UTC is known for -00:00/Z while the local offset
        # remains unspecified. Preserve that distinction in the annotation.
        direction = -1 if offset_match["sign"] == "-" else 1
        offset = timezone(direction * timedelta(hours=offset_hours, minutes=offset_minutes))

    microsecond = int(fraction.ljust(6, "0")) if fraction is not None else 0
    local = datetime(
        parsed_date.year, parsed_date.month, parsed_date.day,
        0 if rollover else hour, minute, second, microsecond, tzinfo=offset,
    )
    try:
        if rollover:
            local += timedelta(days=1)
        utc = local.astimezone(timezone.utc)
    except OverflowError:
        return _result("unsupported", precision, basis="explicit_offset", reason="utc_out_of_range")

    # Append Z after removing tzinfo to make the representation predictable.
    return _result(
        "normalized",
        precision,
        utc.replace(tzinfo=None).isoformat(timespec={
            "minute": "minutes", "second": "seconds", "microsecond": "microseconds",
        }[precision]) + "Z",
        "explicit_offset",
        "unknown_local_offset" if zone.upper() == "Z" or zone == "-00:00" else None,
    )

"""Exact conversion of explicitly declared quantities, without entity inference.

Only an entire number and an entire, case-sensitive unit symbol are accepted.
The caller owns the original text, provenance, and any decisions about claims,
entities, or physical plausibility.  In particular, a negative quantity is
converted without claiming that the source's assertion is physically valid.

SI prefix factors follow BIPM; binary data prefixes follow IEC 80000-13.
This module deliberately supports only the small declared-unit table below.
"""

from decimal import Context, Decimal, Inexact, InvalidOperation, ROUND_HALF_EVEN, localcontext
import re


POLICY = "source-declared-quantity-v1"

_MAX_VALUE_CHARS = 128
_MAX_UNIT_CHARS = 16
_MAX_CANONICAL_CHARS = 128
_MAX_ABS_EXPONENT = 100
_MAX_INT_BITS = _MAX_VALUE_CHARS * 4

_DECIMAL_NUMBER = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    r"(?:[eE](?P<exponent>[+-]?[0-9]+))?\Z"
)

# Values are exact factors to the canonical g, m, s, and bit, respectively.
# Capitalization matters: mg (milli) and Mg (mega) are entirely different.
_UNITS = {
    "g": ("mass", Decimal("1")),
    "kg": ("mass", Decimal("1000")),
    "mg": ("mass", Decimal("0.001")),
    "Mg": ("mass", Decimal("1000000")),
    "µg": ("mass", Decimal("0.000001")),
    "μg": ("mass", Decimal("0.000001")),
    "m": ("length", Decimal("1")),
    "cm": ("length", Decimal("0.01")),
    "mm": ("length", Decimal("0.001")),
    "km": ("length", Decimal("1000")),
    "s": ("duration", Decimal("1")),
    "ms": ("duration", Decimal("0.001")),
    "µs": ("duration", Decimal("0.000001")),
    "μs": ("duration", Decimal("0.000001")),
    "min": ("duration", Decimal("60")),
    "h": ("duration", Decimal("3600")),
    "B": ("data", Decimal("8")),
    "kB": ("data", Decimal("8000")),
    "MB": ("data", Decimal("8000000")),
    "GB": ("data", Decimal("8000000000")),
    "KiB": ("data", Decimal(8 * 1024)),
    "MiB": ("data", Decimal(8 * 1024**2)),
    "GiB": ("data", Decimal(8 * 1024**3)),
    "bit": ("data", Decimal("1")),
    "kbit": ("data", Decimal("1000")),
    "Mbit": ("data", Decimal("1000000")),
    "Gbit": ("data", Decimal("1000000000")),
}

_CANONICAL_UNITS = {
    "mass": "g",
    "length": "m",
    "duration": "s",
    "data": "bit",
}


def _result(status, reason=None, dimension=None, canonical_value=None):
    """Return an invariant five-field response for every outcome."""
    return {
        "status": status,
        "dimension": dimension,
        "canonical_value": canonical_value,
        "canonical_unit": _CANONICAL_UNITS[dimension] if dimension else None,
        "reason": reason,
    }


def normalize_declared_quantity(value, unit) -> dict:
    """Normalize only a standalone decimal-string or integer plus a listed unit.

    ``missing`` means an absent/blank argument; ``unsupported`` means a unit
    outside the exact whitelist; ``invalid`` includes malformed or oversized
    numeric input. No parsing, extraction, source modification, or merging of
    records is performed. Failed results contain no dimension or quantity.
    """
    if value is None:
        return _result("missing", "value-missing")
    if unit is None:
        return _result("missing", "unit-missing")
    # Inspect O(1) string lengths before any O(n) trimming or token parsing.
    if isinstance(value, str) and len(value) > _MAX_VALUE_CHARS:
        return _result("invalid", "value-too-long")
    if isinstance(unit, str) and len(unit) > _MAX_UNIT_CHARS:
        return _result("invalid", "unit-too-long")
    if isinstance(value, str) and not value.strip():
        return _result("missing", "value-missing")
    if isinstance(unit, str) and not unit.strip():
        return _result("missing", "unit-missing")

    if type(value) is int:
        # Check before str(): an arbitrarily large Python integer must not be
        # materialized as a potentially enormous decimal string.
        if value.bit_length() > _MAX_INT_BITS:
            return _result("invalid", "value-too-long")
        number_text = str(value)
    elif isinstance(value, str):
        number_text = value
    else:
        # Includes bool, float, Decimal instances, and other coerced types.
        return _result("invalid", "value-type")

    if len(number_text) > _MAX_VALUE_CHARS:
        return _result("invalid", "value-too-long")
    match = _DECIMAL_NUMBER.fullmatch(number_text)
    if match is None:
        return _result("invalid", "value-not-decimal")

    declared_exponent = match.group("exponent")
    if declared_exponent is not None and abs(int(declared_exponent)) > _MAX_ABS_EXPONENT:
        return _result("invalid", "exponent-out-of-range")

    if not isinstance(unit, str):
        return _result("invalid", "unit-type")
    if len(unit) > _MAX_UNIT_CHARS:
        return _result("invalid", "unit-too-long")
    unit_definition = _UNITS.get(unit)
    if unit_definition is None:
        return _result("unsupported", "unsupported-unit")

    try:
        number = Decimal(number_text)
    except InvalidOperation:
        return _result("invalid", "value-not-decimal")
    if not number.is_finite():
        return _result("invalid", "value-not-finite")
    dimension, factor = unit_definition
    # The maximum coefficient length plus the largest factor fits this
    # precision. A local context avoids default-precision silent rounding.
    try:
        # A caller's Decimal exponent limits, clamp and traps must not change
        # the canonical value or leak exceptions for an otherwise valid unit.
        # Input bounds above are much smaller than this private exact context.
        exact = Context(prec=_MAX_VALUE_CHARS + 32, Emin=-1000, Emax=1000,
                        rounding=ROUND_HALF_EVEN, clamp=0, traps=[Inexact, InvalidOperation])
        with localcontext(exact):
            # Insignificant coefficient zeroes do not consume fractional
            # precision. Inspect the reduced representation in this exact
            # context, before and after conversion. The declared scientific
            # power and final canonical exponent still have their own bounds.
            # Do not reject large input magnitudes here: e.g. 100e100 mg is a
            # previously accepted down-conversion to the in-budget 1e99 g.
            reduced = number.normalize()
            if (number.as_tuple().exponent > _MAX_ABS_EXPONENT
                    or reduced.as_tuple().exponent < -_MAX_ABS_EXPONENT):
                return _result("invalid", "exponent-out-of-range")
            scaled = reduced * factor
            normalized = scaled.normalize()
            if abs(normalized.as_tuple().exponent) > _MAX_ABS_EXPONENT:
                return _result("invalid", "exponent-out-of-range")
            canonical_value = format(normalized, "f")
    except (Inexact, InvalidOperation):
        return _result("invalid", "conversion-budget-exceeded")

    if len(canonical_value) > _MAX_CANONICAL_CHARS:
        return _result("invalid", "canonical-too-long")
    return _result("normalized", dimension=dimension, canonical_value=canonical_value)

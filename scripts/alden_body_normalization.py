"""Bounded lexical annotations of supplied text, never facts or merge decisions.

Offsets index the exact supplied Python string (Unicode code points), not NFC,
UTF-8 bytes or UTF-16 units. Callers must bind the digest to a permitted current
source/version before exposing annotations. This parser performs no I/O.
"""
from decimal import Context, Decimal, DecimalException, Inexact, InvalidOperation, localcontext
import hashlib
import heapq
import re
import unicodedata

from alden_quantity_normalization import normalize_declared_quantity
from alden_time_normalization import normalize_declared_time

POLICY = 'body-notation-candidates-v1'
MAX_BODY_CHARS = 131072
MAX_CANDIDATES = 64
MAX_TOKEN_CHARS = 128
_SIGN_TRANSLATION = str.maketrans('−－﹣＋﹢', '---++')
_H = r'[ \t\u00a0]*'
_NUMBER = re.compile(r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')
_URL = re.compile(r'https?://[^\s<>]+', re.IGNORECASE)
_CLOCK = r'(?:[Tt ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:[.,][0-9]{1,9})?)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2}|[ \t]+[A-Za-z]{1,8})?)'
_DATE = re.compile(
    r'(?<![\w/.:+\-])(?:'
    r'(?P<ky>[0-9]{4}|[0-9]{2})년' + _H + r'(?P<km>[0-9]{1,2})월' + _H + r'(?P<kd>[0-9]{1,2})일'
    r'|(?P<iy>[0-9]{4})(?P<sep>[-/.])(?P<im>[0-9]{1,2})(?P=sep)(?P<id>[0-9]{1,2}))'
    r'(?P<clock>' + _CLOCK + r')?(?![A-Za-z0-9_/:]|\.[0-9])')
_SPECIAL_UNITS = {
    '원': ('money_won_unspecified', '원'), 'KRW': ('money_KRW', 'KRW'),
    'USD': ('money_USD', 'USD'), 'EUR': ('money_EUR', 'EUR'), 'JPY': ('money_JPY', 'JPY'),
    '명': ('person_count', '명'), '개': ('item_count', '개'), '건': ('case_count', '건'), '회': ('occurrence_count', '회'),
    '%': ('percent', '%'), '%p': ('percentage_point', '%p'), '%포인트': ('percentage_point', '%p'),
}
_ALIASES = {'초': 's', '분': 'min', '시간': 'h'}
_SI = ('GiB', 'MiB', 'KiB', 'Gbit', 'Mbit', 'kbit', 'bit', 'GB', 'MB', 'kB', 'B',
       'kg', 'mg', 'Mg', 'µg', 'μg', 'g', 'km', 'cm', 'mm', 'm', 'min', 'ms', 'µs', 'μs', 's', 'h')
_UNITS = '|'.join(re.escape(unit) for unit in sorted(set(_SI) | _SPECIAL_UNITS.keys() | _ALIASES.keys(), key=lambda value: (-len(value), value)))
_RUN = r'(?:[+\-−－﹣＋﹢]' + _H + r')?[0-9][0-9.,eE+\-−－﹣＋﹢~～–—조억만천백십 \t\u00a0]{0,127}'
_QUANTITY = re.compile(r'(?<![\w/.,:+\-−－﹣＋﹢])(?P<number>' + _RUN + r')' + _H + r'(?P<unit>' + _UNITS + r')(?![A-Za-z%])')
_BARE_SCALED = re.compile(r'(?<![\w/.,:+\-−－﹣＋﹢])(?P<number>' + _RUN + r')')
_BIG = {'조': 10**12, '억': 10**8, '만': 10**4}
_SMALL = {'천': 1000, '백': 100, '십': 10}


def _coefficient(value):
    if not _NUMBER.fullmatch(value):
        raise ValueError('malformed_number')
    exponent = re.search(r'[eE]([+-]?[0-9]+)\Z', value)
    if exponent and (len(exponent[1].lstrip('+-')) > 3 or abs(int(exponent[1])) > 100):
        raise ValueError('exponent_out_of_budget')
    return Decimal(value.replace(',', ''))


def _group(value):
    total, start, previous = Decimal(0), 0, 10000
    for index, character in enumerate(value):
        if character not in _SMALL:
            continue
        scale = _SMALL[character]
        if scale >= previous or index == start:
            raise ValueError('ambiguous_scale_sequence')
        total += _coefficient(value[start:index]) * scale
        start, previous = index + 1, scale
    if start < len(value):
        total += _coefficient(value[start:])
    elif start == 0:
        raise ValueError('missing_number')
    return total


def _scalar(raw):
    if len(raw) > MAX_TOKEN_CHARS or re.search(r'[0-9][ \t\u00a0]+[0-9]', raw):
        raise ValueError('number_budget_or_spacing')
    value = re.sub(r'[ \t\u00a0]', '', raw).translate(_SIGN_TRANSLATION)
    sign = -1 if value.startswith('-') else 1
    if value.startswith(('+', '-')):
        value = value[1:]
    if not value:
        raise ValueError('missing_number')
    total, start, previous = Decimal(0), 0, 10**16
    for index, character in enumerate(value):
        if character not in _BIG:
            continue
        scale = _BIG[character]
        if scale >= previous or index == start:
            raise ValueError('ambiguous_scale_sequence')
        total += _group(value[start:index]) * scale
        start, previous = index + 1, scale
    if start < len(value):
        total += _group(value[start:])
    elif start == 0:
        raise ValueError('missing_number')
    total *= sign
    total = total.normalize()
    if abs(total.as_tuple().exponent) > 100:
        raise ValueError('exponent_out_of_budget')
    rendered = format(total, 'f') if total else '0'
    if len(rendered) > 128:
        raise ValueError('canonical_value_out_of_budget')
    return rendered


def _exact_scalar(raw):
    # Private exact context: caller precision, exponent limits and traps cannot
    # silently round a money/count expression or change its interpretation.
    exact = Context(prec=160, Emin=-1000, Emax=1000, traps=[Inexact, InvalidOperation])
    with localcontext(exact):
        return _scalar(raw)


def _normalize(raw, unit):
    try:
        if unit not in _SPECIAL_UNITS and not any(character in raw for character in (*_BIG, *_SMALL)):
            if len(raw) > MAX_TOKEN_CHARS or re.search(r'[0-9][ \t\u00a0]+[0-9]', raw):
                raise ValueError('number_budget_or_spacing')
            literal = re.sub(r'[ \t\u00a0]', '', raw).translate(_SIGN_TRANSLATION)
            unsigned = literal[1:] if literal.startswith(('+', '-')) else literal
            _coefficient(unsigned)
            # Preserve the original declared exponent for the existing unit
            # converter: e.g. 100e100 mg may validly down-convert to 1e99 g.
            return normalize_declared_quantity(literal.replace(',', ''), _ALIASES.get(unit, unit))
        value = _exact_scalar(raw)
        if unit in _SPECIAL_UNITS:
            dimension, canonical_unit = _SPECIAL_UNITS[unit]
            return {'status': 'normalized', 'canonical_value': value, 'canonical_unit': canonical_unit,
                    'dimension': dimension, 'reason': None}
        return normalize_declared_quantity(value, _ALIASES.get(unit, unit))
    except (ValueError, DecimalException) as error:
        return {'status': 'invalid', 'canonical_value': None, 'canonical_unit': None,
                'dimension': None, 'reason': str(error) if isinstance(error, ValueError) else 'exact_arithmetic_budget'}


def _quantity_value(raw, unit):
    separators = [index for index, char in enumerate(raw)
                  if char in '~～–—' or char == '-' and index and raw[index - 1] not in 'eE']
    ranges = []
    for index in separators:
        first, last = raw[:index].strip(), raw[index + 1:].strip()
        left, right = _normalize(first, unit), _normalize(last, unit)
        if left['status'] == right['status'] == 'normalized':
            ranges.append((left, right, raw[index]))
    if len(ranges) == 1:
        left, right, marker = ranges[0]
        order = 'descending' if Decimal(left['canonical_value']) > Decimal(right['canonical_value']) else 'nondecreasing'
        return {'kind': 'quantity_relation' if marker == '-' else 'quantity_range',
                'status': 'ambiguous' if marker == '-' else 'normalized',
                'reason': 'hyphen_may_be_range_or_subtraction' if marker == '-' else None,
                'first': left, 'last': right, 'range_marker': marker, 'written_order': order}
    if separators:
        return {'kind': 'quantity', 'status': 'ambiguous', 'canonical_value': None,
                'canonical_unit': None, 'dimension': None, 'reason': 'range_not_fully_resolved'}
    return {'kind': 'quantity', **_normalize(raw, unit)}


def _dates(body):
    for match in _DATE.finditer(body):
        year = match['ky'] or match['iy']
        if len(year) == 2:
            result = {'status': 'ambiguous', 'canonical': None, 'reason': 'century_not_declared', 'precision': 'day', 'basis': 'body_notation'}
        else:
            month, day = match['km'] or match['im'], match['kd'] or match['id']
            iso = f'{year}-{int(month):02d}-{int(day):02d}'
            if match['clock']:
                iso += 'T' + match['clock'][1:]
            result = normalize_declared_time(iso)
            attached = body[match.end():match.end() + 1]
            unparsed_clock = not match['clock'] and re.match(r'[Tt ][0-9]{2}:', body[match.end():match.end() + 7])
            # A joined second zone, operator or bracketed zone is not a
            # delimiter: the matched timestamp is only a prefix of its token.
            unparsed_suffix = match['clock'] and attached and (
                attached in '+−－﹣＋﹢~～[' or unicodedata.category(attached) == 'Pd')
            if unparsed_clock or unparsed_suffix:
                result = {'status': 'unsupported', 'canonical': None, 'reason': 'time_suffix_not_fully_parsed', 'precision': None, 'basis': 'body_notation'}
        yield {'kind': 'time', 'start': match.start(), 'end': match.end(), 'raw': match[0], **result}


def _numeric_prefix(body, start):
    previous = start - 1
    while previous >= 0 and body[previous] in ' \t\u00a0':
        previous -= 1
    return previous >= 0 and (body[previous] in '0123456789+−－﹣＋﹢-~～–—조억만천백십'
            or unicodedata.category(body[previous]) == 'Pd'
            or body[previous] in '.,' and previous > 0 and body[previous - 1].isdigit())


def _quantities(body):
    for match in _QUANTITY.finditer(body):
        # A longer expression or separated sign must not yield a positive tail.
        if _numeric_prefix(body, match.start()):
            continue
        raw, unit = match['number'].strip(), match['unit']
        result = _quantity_value(raw, unit)
        yield {'start': match.start(), 'end': match.end(), 'raw': match[0], 'source_unit': unit,
               'source_sign': 'negative' if raw.startswith(('-', '−', '－', '﹣')) else 'explicit_positive' if raw.startswith(('+', '＋', '﹢')) else 'unsigned',
               **result}


def _untyped_numbers(body):
    for match in _BARE_SCALED.finditer(body):
        raw = match[0].rstrip(' \t\u00a0')
        if not any(character in raw for character in (*_BIG, *_SMALL)) or _numeric_prefix(body, match.start()):
            continue
        end = match.start() + len(raw)
        if end < len(body) and body[end] in '0123456789.,eE+−－﹣＋﹢-~～–—조억만천백십':
            continue  # Never normalize a budget-cut prefix as a whole number.
        try:
            value = _exact_scalar(raw)
        except (ValueError, DecimalException):
            value = None
        yield {'kind': 'untyped_number', 'start': match.start(), 'end': end, 'raw': raw,
               'status': 'ambiguous', 'reason': 'unit_not_declared', 'source_unit': None,
               'canonical_unit': None, 'dimension': None,
               'canonical_value': value}


def analyze_body_fields(body):
    """Extract bounded syntax candidates. Neither absence nor conversion is truth."""
    if not isinstance(body, str):
        raise ValueError('body_text_required')
    if len(body) > MAX_BODY_CHARS:
        return {'policy': POLICY, 'state': 'body_budget_exceeded', 'body_sha256': None,
                'body_characters': len(body), 'candidates': [], 'truncated': True,
                'offset_basis': 'exact_supplied_unicode_codepoints', 'semantic_validity': 'not_assessed',
                'client_offset_basis': 'exact_supplied_utf16_code_units',
                'source_authorization': 'caller_must_bind_permitted_current_source_version'}
    try:
        body_hash = hashlib.sha256(body.encode('utf-8')).hexdigest()
    except UnicodeEncodeError:
        raise ValueError('body_unicode_invalid') from None
    urls = [(match.start(), match.end()) for match in _URL.finditer(body)]
    candidates, url_index = [], 0
    merged = heapq.merge(_dates(body), _quantities(body), _untyped_numbers(body),
                        key=lambda item: (item['start'], -item['end']))
    truncated = False
    for candidate in merged:
        if candidates and candidate['start'] < candidates[-1]['end']:
            continue  # Prefer the full typed span over its untyped subexpression.
        while url_index < len(urls) and urls[url_index][1] <= candidate['start']:
            url_index += 1
        if url_index < len(urls) and urls[url_index][0] < candidate['end'] and candidate['start'] < urls[url_index][1]:
            continue
        if len(candidates) == MAX_CANDIDATES:
            truncated = True
            break
        assert body[candidate['start']:candidate['end']] == candidate['raw']
        candidates.append(candidate)
    # JS strings use UTF-16. Compute requested boundary offsets in one pass,
    # rather than encoding every candidate's potentially long body prefix.
    points = sorted({item[key] for item in candidates for key in ('start', 'end')})
    converted, cursor, code_units = {}, 0, 0
    for point in points:
        while cursor < point:
            code_units += 2 if ord(body[cursor]) > 0xffff else 1
            cursor += 1
        converted[point] = code_units
    for candidate in candidates:
        candidate['utf16_start'] = converted[candidate['start']]
        candidate['utf16_end'] = converted[candidate['end']]
    return {'policy': POLICY, 'state': 'bounded_candidates' if truncated else 'supported_notation_scan_complete',
            'body_sha256': body_hash, 'body_characters': len(body),
            'offset_basis': 'exact_supplied_unicode_codepoints', 'semantic_validity': 'not_assessed',
            'client_offset_basis': 'exact_supplied_utf16_code_units',
            'source_authorization': 'caller_must_bind_permitted_current_source_version',
            'candidates': candidates, 'truncated': truncated}

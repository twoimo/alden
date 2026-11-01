"""Versioned syntax conversions of explicit metadata fields, never entity truth."""
from collections import Counter

from alden_time_normalization import POLICY as TIME_POLICY, normalize_declared_time
from alden_quantity_normalization import POLICY as QUANTITY_POLICY, normalize_declared_quantity

POLICY = 'source-declared-fields-v1'
TIME_FIELDS = {
    'published_at': 'published', 'publishedAt': 'published',
    'created_at': 'created', 'createdAt': 'created',
    'updated_at': 'updated', 'updatedAt': 'updated',
    'observed_at': 'observed', 'observedAt': 'observed',
    'valid_from': 'valid_from', 'validFrom': 'valid_from',
    'valid_to': 'valid_to', 'validTo': 'valid_to',
}
MAX_QUANTITIES = 16
TIME_STATUSES = ('normalized', 'ambiguous', 'invalid', 'unsupported', 'missing')
QUANTITY_STATUSES = ('normalized', 'invalid', 'unsupported', 'missing')


def analyze_declared_fields(metadata):
    if not isinstance(metadata, dict):
        raise ValueError('normalization_metadata_invalid')
    times = [{'field': key, 'role': role, **normalize_declared_time(metadata[key])}
             for key, role in TIME_FIELDS.items() if key in metadata]
    quantities = []
    if 'quantity' in metadata:
        quantities.append(('quantity', 0, metadata['quantity']))
    omitted = 0
    if 'quantities' in metadata:
        items = metadata['quantities']
        if isinstance(items, list):
            remaining = MAX_QUANTITIES - len(quantities)
            quantities.extend(('quantities', i, item) for i, item in enumerate(items[:remaining]))
            omitted = max(0, len(items) - remaining)
        else:
            quantities.append(('quantities', 0, items))
    normalized = []
    for key, index, item in quantities:
        if isinstance(item, dict):
            value = normalize_declared_quantity(item.get('value'), item.get('unit'))
        else:
            value = {'status': 'unsupported', 'dimension': None, 'canonical_value': None,
                     'canonical_unit': None, 'reason': 'quantity_object_required'}
        normalized.append({'field': key, 'index': index, **value})
    # No generic date key, body token, URL, ID or current Mac zone is guessed.
    roles = {role: [item for item in times if item['role'] == role]
             for role in ('valid_from', 'valid_to')}
    range_review = 'none'
    if roles['valid_from'] and roles['valid_to']:
        start, end = roles['valid_from'], roles['valid_to']
        if (len(start) == len(end) == 1 and start[0]['status'] == end[0]['status'] == 'normalized'
                and start[0]['basis'] == end[0]['basis']
                and start[0]['basis'] in ('date_only', 'explicit_offset')
                and start[0]['precision'] == end[0]['precision']):
            range_review = 'inverted' if start[0]['canonical'] > end[0]['canonical'] else 'ordered'
        else:
            range_review = 'uncomparable'
    return {'time': times, 'quantity': normalized, 'quantity_omitted': omitted,
            'range_review': range_review}


class DeclaredFieldSummary:
    def __init__(self):
        self.records = 0
        self.time_absent = self.quantity_absent = self.quantity_omitted = 0
        self.time = Counter()
        self.quantity = Counter()
        self.date_only = self.explicit_offset = self.unknown_local_offset = 0
        self.ranges = Counter()

    def add(self, metadata):
        result = analyze_declared_fields(metadata)
        self.records += 1
        self.time_absent += not result['time']
        self.quantity_absent += not result['quantity']
        self.quantity_omitted += result['quantity_omitted']
        for item in result['time']:
            self.time[item['status']] += 1
            self.date_only += item['status'] == 'normalized' and item['basis'] == 'date_only'
            self.explicit_offset += item['status'] == 'normalized' and item['basis'] == 'explicit_offset'
            self.unknown_local_offset += item['status'] == 'normalized' and item['reason'] == 'unknown_local_offset'
        for item in result['quantity']:
            self.quantity[item['status']] += 1
        self.ranges[result['range_review']] += 1
        return result

    def report(self):
        return {'policy': POLICY, 'time_policy': TIME_POLICY, 'quantity_policy': QUANTITY_POLICY,
                'scope': 'explicit_metadata_fields_only',
                'truth_status': 'syntax_conversion_not_semantic_verdict',
                'records_checked': self.records,
                'time': {'fields': sum(self.time.values()),
                         'records_absent': self.time_absent,
                         'date_only': self.date_only, 'explicit_offset': self.explicit_offset,
                         'unknown_local_offset': self.unknown_local_offset,
                         **{key: self.time[key] for key in TIME_STATUSES}},
                'quantity': {'fields': sum(self.quantity.values()),
                             'records_absent': self.quantity_absent, 'omitted': self.quantity_omitted,
                             **{key: self.quantity[key] for key in QUANTITY_STATUSES}},
                'validity_ranges': {key: self.ranges[key] for key in ('none', 'ordered', 'inverted', 'uncomparable')}}


def validate_declared_summary(value, max_records):
    error = 'quality_report_unavailable'
    if not isinstance(value, dict) or not all(value.get(key) == expected for key, expected in {
            'policy': POLICY, 'time_policy': TIME_POLICY, 'quantity_policy': QUANTITY_POLICY,
            'scope': 'explicit_metadata_fields_only',
            'truth_status': 'syntax_conversion_not_semantic_verdict'}.items()):
        raise ValueError(error)
    def count(item):
        if type(item) is not int or not 0 <= item <= 2**53 - 1:
            raise ValueError(error)
        return item
    records = count(value.get('records_checked'))
    if records > max_records:
        raise ValueError(error)
    result = {key: value[key] for key in ('policy', 'time_policy', 'quantity_policy', 'scope', 'truth_status')}
    result['records_checked'] = records
    for kind, statuses, maximum, extra in (
            ('time', TIME_STATUSES, len(TIME_FIELDS), ('date_only', 'explicit_offset', 'unknown_local_offset')),
            ('quantity', QUANTITY_STATUSES, MAX_QUANTITIES, ('omitted',))):
        source = value.get(kind)
        if not isinstance(source, dict):
            raise ValueError(error)
        item = {key: count(source.get(key)) for key in ('fields', 'records_absent', *statuses, *extra)}
        present = records - item['records_absent']
        if (present < 0 or not present <= item['fields'] <= present * maximum
                or sum(item[key] for key in statuses) != item['fields']):
            raise ValueError(error)
        if kind == 'time' and item['date_only'] + item['explicit_offset'] != item['normalized']:
            raise ValueError(error)
        if kind == 'time' and item['unknown_local_offset'] > item['explicit_offset']:
            raise ValueError(error)
        if kind == 'quantity' and not item['fields'] and item['omitted']:
            raise ValueError(error)
        result[kind] = item
    source = value.get('validity_ranges')
    if not isinstance(source, dict):
        raise ValueError(error)
    ranges = {key: count(source.get(key)) for key in ('none', 'ordered', 'inverted', 'uncomparable')}
    if sum(ranges.values()) != records or (records - ranges['none']) * 2 > result['time']['fields']:
        raise ValueError(error)
    result['validity_ranges'] = ranges
    return result

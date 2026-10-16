import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_normalization_evidence import analyze_declared_fields, DeclaredFieldSummary, validate_declared_summary


class DeclaredEvidenceTests(unittest.TestCase):
    def test_explicit_fields_preserve_source_and_omit_body_ids_and_generic_date(self):
        metadata = {'published_at': '2026-10-13T10:00:00+09:00',
                    'quantity': {'value': '1', 'unit': 'kg'},
                    'date': '2026-02-30', 'original_id': '1kg',
                    'url': 'https://example.invalid/1kg?at=2026-02-30'}
        before = copy.deepcopy(metadata)
        result = analyze_declared_fields(metadata)
        self.assertEqual(metadata, before)
        self.assertEqual([item['field'] for item in result['time']], ['published_at'])
        self.assertEqual(result['time'][0]['canonical'], '2026-10-13T01:00:00Z')
        self.assertEqual(result['quantity'][0]['canonical_value'], '1000')
        self.assertNotIn('url', str(result))

    def test_missing_zone_and_precision_mismatch_do_not_prove_temporal_order(self):
        for metadata in (
            {'valid_from': '2026-10-13T10:00:00', 'valid_to': '2026-10-13T11:00:00'},
            {'valid_from': '2026-10-13', 'valid_to': '2026-10-14T00:00:00Z'},
            {'valid_from': '2026-10-13T10:00Z', 'valid_to': '2026-10-13T11:00:00Z'},
            {'valid_from': '2026-10-13', 'validFrom': '2026-10-14', 'valid_to': '2026-10-15'},
        ):
            with self.subTest(metadata=metadata):
                self.assertEqual(analyze_declared_fields(metadata)['range_review'], 'uncomparable')

    def test_inverted_calendar_and_explicit_offset_ranges_are_review_candidates(self):
        for metadata in (
            {'valid_from': '2026-10-14', 'valid_to': '2026-10-13'},
            {'valid_from': '2026-10-13T10:00:00+09:00', 'valid_to': '2026-10-13T00:00:00Z'},
        ):
            self.assertEqual(analyze_declared_fields(metadata)['range_review'], 'inverted')

    def test_quantity_capacity_is_explicit_and_not_silently_complete(self):
        source = {'quantity': {'value': '1', 'unit': 'g'},
                  'quantities': [{'value': str(i), 'unit': 'MB'} for i in range(20)]}
        result = analyze_declared_fields(source)
        self.assertEqual(len(result['quantity']), 16)
        self.assertEqual(result['quantity_omitted'], 5)
        self.assertEqual(len(source['quantities']), 20)

    def test_missing_declarations_are_separate_from_explicit_missing_values(self):
        summary = DeclaredFieldSummary(); summary.add({})
        summary.add({'published_at': None, 'quantity': None})
        report = validate_declared_summary(summary.report(), 2)
        self.assertEqual(report['time']['records_absent'], 1)
        self.assertEqual(report['time']['missing'], 1)
        self.assertEqual(report['quantity']['records_absent'], 1)
        self.assertEqual(report['quantity']['unsupported'], 1)
        self.assertEqual(report['truth_status'], 'syntax_conversion_not_semantic_verdict')

    def test_impossible_counts_and_truth_upgrade_fail_closed(self):
        summary = DeclaredFieldSummary(); summary.add({'published_at': '2026-10-13'})
        base = summary.report()
        for change in (
            lambda r: r.update(truth_status='verified'),
            lambda r: r.update(records_checked=True),
            lambda r: r['time'].update(fields=0),
            lambda r: r['time'].update(normalized=2),
            lambda r: r['time'].update(records_absent=2),
            lambda r: r['quantity'].update(omitted=1),
            lambda r: r['validity_ranges'].update(inverted=1),
        ):
            value = copy.deepcopy(base); change(value)
            with self.assertRaisesRegex(ValueError, 'quality_report_unavailable'):
                validate_declared_summary(value, 1)


if __name__ == '__main__':
    unittest.main()

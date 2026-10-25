import hashlib
from decimal import localcontext
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_body_normalization import analyze_body_fields, MAX_BODY_CHARS, MAX_CANDIDATES


class BodyNormalizationTests(unittest.TestCase):
    def candidates(self, body):
        report = analyze_body_fields(body)
        self.assertEqual(report['body_sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(report['semantic_validity'], 'not_assessed')
        for item in report['candidates']:
            self.assertEqual(body[item['start']:item['end']], item['raw'])
            self.assertEqual(body.encode('utf-16-le')[item['utf16_start'] * 2:item['utf16_end'] * 2].decode('utf-16-le'), item['raw'])
            self.assertGreater(item['end'], item['start'])
        return report['candidates']

    def test_source_offsets_are_exact_codepoints_without_unicode_or_line_rewriting(self):
        body = '😀 가\r\n기한 2026년 2월 23일, 수량 −5 명'
        result = self.candidates(body)
        self.assertEqual([item['raw'] for item in result], ['2026년 2월 23일', '−5 명'])
        self.assertEqual(result[0]['canonical'], '2026-02-23')
        self.assertEqual(result[1]['canonical_value'], '-5')
        self.assertEqual(result[1]['source_sign'], 'negative')
        self.assertGreater(result[0]['utf16_start'], result[0]['start'])

    def test_short_year_and_local_clock_remain_ambiguous(self):
        short = self.candidates('기한 26년 2월 23일까지')[0]
        self.assertEqual((short['status'], short['canonical']), ('ambiguous', None))
        self.assertEqual(short['reason'], 'century_not_declared')
        clock = self.candidates('시간 2026-02-23 12:30')[0]
        self.assertEqual(clock['status'], 'ambiguous')
        self.assertIsNone(clock['canonical'])

    def test_sentence_period_is_not_a_date_component(self):
        for body in ('기한 2026년 2월 23일.', '기한 2026.2.23.'):
            with self.subTest(body=body):
                items = self.candidates(body)
                self.assertEqual(items[0]['canonical'],'2026-02-23')
                self.assertFalse(items[0]['raw'].endswith('.'))
        self.assertEqual(self.candidates('기한 2026.2.23.24'),[])
        self.assertEqual(self.candidates('기한 26년 2월 23일.')[0]['status'],'ambiguous')

    def test_explicit_offset_and_gregorian_validity(self):
        value = self.candidates('시각 2026-02-23T12:30:00+09:00')[0]
        self.assertEqual(value['canonical'], '2026-02-23T03:30:00Z')
        for text, expected in [('2024/2/29', 'normalized'), ('2026.2.29', 'invalid'), ('2026-13-01', 'invalid')]:
            with self.subTest(text=text):
                self.assertEqual(self.candidates('일자 ' + text)[0]['status'], expected)

    def test_invalid_datetime_tail_cannot_become_a_normalized_date_only(self):
        value = self.candidates('시각 2026-02-23 12:30+09:00junk')[0]
        self.assertNotEqual(value['status'], 'normalized')
        self.assertIsNone(value['canonical'])

    def test_joined_datetime_suffix_cannot_normalize_a_valid_prefix(self):
        stems = ('2026-02-23T12:30Z', '2026-02-23T12:30:00+09:00', '2026-02-23t12:30:00z')
        suffixes = ('+09:00', '-05:00', '−05:00', '－1', '﹣1', '＋1', '﹢1',
                    '~2', '～2', '–2', '—2', '‐2', '[Asia/Seoul]')
        for stem in stems:
            for suffix in suffixes:
                with self.subTest(stem=stem, suffix=suffix):
                    body = '😀 가\r\n시각 ' + stem + suffix
                    items = self.candidates(body)
                    self.assertEqual(len(items), 1)
                    item = items[0]
                    self.assertEqual(item['raw'], stem)
                    self.assertEqual(item['status'], 'unsupported')
                    self.assertIsNone(item['canonical'])
                    self.assertEqual(item['reason'], 'time_suffix_not_fully_parsed')
                    self.assertGreater(item['utf16_start'], item['start'])

    def test_datetime_sentence_boundaries_keep_supported_times(self):
        stems = {'2026-02-23T12:30Z': '2026-02-23T12:30Z',
                 '2026-02-23T12:30:00+09:00': '2026-02-23T03:30:00Z',
                 '2026-02-23t12:30:00z': '2026-02-23T12:30:00Z'}
        for stem, canonical in stems.items():
            for suffix in ('.', ', 5kg', '; 9명', ')', ']', '에서', '\r\n', ' +09:00'):
                with self.subTest(stem=stem, suffix=suffix):
                    item = self.candidates('😀 시각 ' + stem + suffix)[0]
                    self.assertEqual(item['raw'], stem)
                    self.assertEqual((item['status'], item['canonical']), ('normalized', canonical))

    def test_urls_and_identifiers_are_not_number_sources(self):
        self.assertEqual(self.candidates('https://example.test/2026-02-23/5kg HTTPS://example.test/?mass=5kg abc2026-02-23def id_10kg 0x10B'), [])

    def test_exact_korean_magnitudes_and_no_currency_jurisdiction_guess(self):
        values = {
            '5천만원': '50000000', '1억5000만원': '150000000',
            '1억 2천만원': '120000000', '3천5백만원': '35000000',
            '-1억5000만원': '-150000000', '1.5만 명': '15000',
            '1,234.50 USD': '1234.5', '2.5e1 KRW': '25',
        }
        for raw, expected in values.items():
            with self.subTest(raw=raw):
                item = self.candidates('값 ' + raw)[0]
                self.assertEqual(item['status'], 'normalized')
                self.assertEqual(item['canonical_value'], expected)
                if raw.endswith('원'):
                    self.assertEqual(item['canonical_unit'], '원')
                    self.assertEqual(item['dimension'], 'money_won_unspecified')

    def test_si_units_use_existing_exact_converter_and_case(self):
        for raw, expected, unit in [('1.25kg', '1250', 'g'), ('1.25mg', '0.00125', 'g'),
                                    ('1Mg', '1000000', 'g'), ('1KiB', '8192', 'bit'), ('2분', '120', 's')]:
            with self.subTest(raw=raw):
                item = self.candidates('값 ' + raw)[0]
                self.assertEqual((item['canonical_value'], item['canonical_unit']), (expected, unit))
        self.assertEqual(self.candidates('값 10msec 또는 2mb'), [])

    def test_malformed_grouping_scales_and_spacing_never_normalize_a_tail(self):
        for raw in ['12,34원', '1.2.3kg', '5 10kg', '1백2천원', '2천백만원', '1억만원', '1e999g', '5--+2kg']:
            with self.subTest(raw=raw):
                items = self.candidates('값 ' + raw)
                self.assertTrue(items)
                self.assertTrue(all(item['status'] != 'normalized' for item in items))
                self.assertTrue(all(item.get('canonical_value') is None for item in items))

    def test_range_direction_is_preserved_without_sorting_or_truth_claims(self):
        for raw, first, last, direction in [('10~20kg', '10000', '20000', 'nondecreasing'),
                                          ('10~2kg', '10000', '2000', 'descending'),
                                          ('−5~-2kg', '-5000', '-2000', 'nondecreasing')]:
            with self.subTest(raw=raw):
                item = self.candidates('구간 ' + raw)[0]
                self.assertEqual(item['kind'], 'quantity_range')
                self.assertEqual((item['first']['canonical_value'], item['last']['canonical_value']), (first, last))
                self.assertEqual(item['written_order'], direction)

    def test_hyphen_does_not_resolve_range_versus_subtraction(self):
        item = self.candidates('수식 10-2kg')[0]
        self.assertEqual(item['kind'], 'quantity_relation')
        self.assertEqual(item['status'], 'ambiguous')
        self.assertEqual(item['reason'], 'hyphen_may_be_range_or_subtraction')
        self.assertEqual(item['first']['canonical_value'], '10000')
        self.assertEqual(item['last']['canonical_value'], '2000')

    def test_compound_magnitudes_have_independent_integer_sum_oracle(self):
        for a in [1, 2, 9]:
            for b in [1, 35, 9999]:
                value = self.candidates(f'금액 {a}억{b}만원')[0]
                self.assertEqual(value['canonical_value'], str(a * 100000000 + b * 10000))
        value = self.candidates('금액 1조2억3만4천5백6십7원')[0]
        self.assertEqual(value['canonical_value'], str(10**12 + 2 * 10**8 + 3 * 10**4 + 4567))

    def test_scientific_exponent_minus_is_not_a_range(self):
        item = self.candidates('측정 1e-2kg')[0]
        self.assertEqual(item['kind'], 'quantity')
        self.assertEqual(item['canonical_value'], '10')

    def test_missing_monetary_unit_is_explicit_rather_than_a_currency_guess(self):
        item = self.candidates('초봉 5천 이상으로 형성됩니다.')[0]
        self.assertEqual(item['kind'], 'untyped_number')
        self.assertEqual((item['canonical_value'], item['canonical_unit']), ('5000', None))
        self.assertEqual(item['status'], 'ambiguous')
        self.assertEqual(item['reason'], 'unit_not_declared')
        self.assertEqual(len(self.candidates('연봉 5천만원')), 1)

    def test_existing_unit_downconversion_owns_final_exponent_budget(self):
        item = self.candidates('측정 100e100 mg')[0]
        self.assertEqual(item['status'], 'normalized')
        self.assertEqual(item['canonical_value'], '1' + '0' * 99)

    def test_percent_and_percentage_points_are_distinct(self):
        values = self.candidates('변화 +5% 및 5%p, −0원')
        self.assertEqual([item['canonical_unit'] for item in values], ['%', '%p', '원'])
        self.assertEqual(values[-1]['canonical_value'], '0')
        self.assertEqual(values[-1]['source_sign'], 'negative')

    def test_fullwidth_and_small_signs_do_not_lose_direction(self):
        for sign in ['-', '−', '－', '﹣']:
            item = self.candidates('측정 ' + sign + '5kg')[0]
            self.assertEqual(item['canonical_value'], '-5000')
            self.assertEqual(item['source_sign'], 'negative')
        for sign in ['+', '＋', '﹢']:
            item = self.candidates('수량 ' + sign + '5명')[0]
            self.assertEqual(item['canonical_value'], '5')
            self.assertEqual(item['source_sign'], 'explicit_positive')
        self.assertEqual(self.candidates('미확인 부호 ‐5kg 또는 –5kg'), [])

    def test_callers_decimal_context_cannot_round_results(self):
        with localcontext() as context:
            context.prec = 2
            context.Emin, context.Emax = -2, 2
            value = self.candidates('금액 123456789.12345만원')[0]
        self.assertEqual(value['canonical_value'], '1234567891234.5')

    def test_budgets_are_explicit_and_do_not_claim_full_normalization(self):
        report = analyze_body_fields('가' * (MAX_BODY_CHARS + 1))
        self.assertEqual(report['state'], 'body_budget_exceeded')
        self.assertTrue(report['truncated'])
        self.assertIsNone(report['body_sha256'])
        report = analyze_body_fields(' '.join('수량 5명' for _ in range(MAX_CANDIDATES + 1)))
        self.assertEqual(len(report['candidates']), MAX_CANDIDATES)
        self.assertTrue(report['truncated'])

    def test_invalid_inputs_do_not_produce_annotations(self):
        for value in [None, 12, True, {}, '\ud800']:
            with self.subTest(value=repr(value)), self.assertRaises(ValueError):
                analyze_body_fields(value)

    def test_generated_grouping_and_magnitude_equivalence_with_independent_integer_oracle(self):
        for coefficient in [0, 1, 9, 99, 999, 1234, 999999]:
            for magnitude, scale in [('', 1), ('천', 1000), ('만', 10000), ('억', 100000000)]:
                for sign in ['', '+', '-', '−']:
                    item = self.candidates(f'금액 {sign}{coefficient:,}{magnitude}원')[0]
                    expected = coefficient * scale * (-1 if sign in ('-', '−') else 1)
                    self.assertEqual(item['canonical_value'], str(expected))


if __name__ == '__main__':
    unittest.main()

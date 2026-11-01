import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_time_normalization import normalize_declared_time


class TimeStandardEdges(unittest.TestCase):
    def test_rfc_unknown_local_offset_retains_a_known_utc_value(self):
        value = normalize_declared_time('2026-10-13T10:00:00-00:00')
        self.assertEqual(value['status'], 'normalized')
        self.assertEqual(value['canonical'], '2026-10-13T10:00:00Z')
        self.assertEqual(value['reason'], 'unknown_local_offset')

    def test_rfc_leap_second_example_is_not_misreported_as_invalid_clock(self):
        value = normalize_declared_time('1990-12-31T23:59:60Z')
        self.assertEqual(value['status'], 'unsupported')
        self.assertEqual(value['reason'], 'leap_second_context_required')
        self.assertIsNone(value['canonical'])

    def test_iso_end_of_day_is_converted_without_assuming_a_local_zone(self):
        value = normalize_declared_time('2026-10-13T24:00:00+09:00')
        self.assertEqual(value['status'], 'normalized')
        self.assertEqual(value['canonical'], '2026-10-13T15:00:00Z')
        self.assertEqual(normalize_declared_time('2026-10-13T24:00')['status'], 'ambiguous')
        self.assertEqual(normalize_declared_time('2026-10-13T24:01Z')['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()

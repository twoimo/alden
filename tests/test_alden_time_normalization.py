"""Counterexamples for conservative source-declared time normalization."""

import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from alden_time_normalization import POLICY, normalize_declared_time


class NormalizeDeclaredTimeTests(unittest.TestCase):
    def test_public_contract(self):
        self.assertEqual(POLICY, "source-declared-time-v1")
        self.assertEqual(
            set(normalize_declared_time("2024-02-29")),
            {"status", "precision", "canonical", "basis", "reason"},
        )

    def test_leap_day_and_basic_day_are_dates_only(self):
        for raw in ("2024-02-29", "20240229"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_declared_time(raw), {
                    "status": "normalized", "precision": "day", "canonical": "2024-02-29",
                    "basis": "date_only", "reason": None,
                })

    def test_invalid_month_day_and_non_leap_day(self):
        for raw in ("2023-02-29", "2024-02-30", "2024-00-15", "2024-13-01", "2024-04-31", "0000-01-01"):
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual((result["status"], result["basis"], result["reason"]),
                                 ("invalid", "date_only", "invalid_calendar_date"))
                self.assertIsNone(result["canonical"])

    def test_explicit_offsets_canonicalize_to_the_same_utc_instant(self):
        left = normalize_declared_time("2024-02-29T09:45:30+09:00")
        right = normalize_declared_time("2024-02-29T00:45:30Z")
        self.assertEqual({key:left[key] for key in ('status','precision','canonical','basis')},
                         {key:right[key] for key in ('status','precision','canonical','basis')})
        self.assertIsNone(left['reason'])
        self.assertEqual(right['reason'],'unknown_local_offset')
        self.assertEqual(left["canonical"], "2024-02-29T00:45:30Z")
        self.assertEqual(left["basis"], "explicit_offset")
        self.assertEqual(normalize_declared_time("20240301T004530+09:00")["canonical"], "2024-02-29T15:45:30Z")

    def test_precision_is_not_invented_or_dropped(self):
        examples = {
            "2024-02-29T10:45Z": ("minute", "2024-02-29T10:45Z"),
            "2024-02-29T10:45:00Z": ("second", "2024-02-29T10:45:00Z"),
            "2024-02-29T10:45:00.1Z": ("microsecond", "2024-02-29T10:45:00.100000Z"),
            "2024-02-29T10:45:00.123456-02:00": ("microsecond", "2024-02-29T12:45:00.123456Z"),
        }
        for raw, (precision, canonical) in examples.items():
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual(result["status"], "normalized")
                self.assertEqual((result["precision"], result["canonical"]), (precision, canonical))

    def test_timezone_absent_is_ambiguous_including_dst_transition(self):
        for raw in ("2024-02-29T10:45", "2026-03-08T02:30:00", "2026-11-01T01:30:00"):
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual(result["status"], "ambiguous")
                self.assertEqual(result["basis"], "timezone_missing")
                self.assertEqual(result["reason"], "timezone_missing")
                self.assertIsNone(result["canonical"])

    def test_no_dst_zone_name_inference(self):
        for raw in ("2026-11-01T01:30:00 EST", "2026-11-01T01:30:00 America/New_York", "2026-03-08T02:30:00 PST"):
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual((result["status"], result["reason"]),
                                 ("unsupported", "unsupported_timezone_designator"))
                self.assertIsNone(result["canonical"])

        # The repeated wall-clock hour is resolved only by the source offsets.
        self.assertEqual(normalize_declared_time("2026-11-01T01:30:00-04:00")["canonical"], "2026-11-01T05:30:00Z")
        self.assertEqual(normalize_declared_time("2026-11-01T01:30:00-05:00")["canonical"], "2026-11-01T06:30:00Z")

    def test_calendar_clock_and_bad_offsets_have_different_reasons(self):
        examples = {
            "2024-02-30T10:00Z": "invalid_calendar_date",
            "2024-02-29T24:01Z": "invalid_clock_time",
            "2024-02-29T10:60Z": "invalid_clock_time",
            "2024-02-29T10:00+24:00": "invalid_offset",
            "2024-02-29T10:00-02:60": "invalid_offset",
            "2024-02-29T10:00+9:00": "invalid_offset_format",
        }
        for raw, reason in examples.items():
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual((result["status"], result["reason"]), ("invalid", reason))
                self.assertIsNone(result["canonical"])

    def test_submicrosecond_digits_cannot_collapse_to_one_instant(self):
        for raw in ("2024-02-29T10:00:00.1234567Z", "2024-02-29T10:00:00.1234568Z", "2024-02-29T10:00:00.1234567"):
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual((result["status"], result["reason"]),
                                 ("unsupported", "fraction_exceeds_six_digits"))
                self.assertIsNone(result["precision"])
                self.assertIsNone(result["canonical"])

    def test_missing_type_limit_and_locale_words(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_declared_time(raw)["status"], "missing")
        for raw in (42, 42.5, b"2024-02-29", "2" * 129):
            with self.subTest(raw=raw):
                result = normalize_declared_time(raw)
                self.assertEqual(result["status"], "unsupported")
                self.assertEqual(result["basis"], "none")
                self.assertIsNone(result["canonical"])
        for raw in ("February 29, 2024", "tomorrow noon"):
            self.assertEqual(normalize_declared_time(raw)["reason"], "unsupported_locale_or_natural_language_date")

    def test_negative_zero_offset_is_unknown_and_utc_boundary_is_safe(self):
        self.assertEqual(normalize_declared_time("2024-02-29T10:00-00:00")["reason"], "unknown_local_offset")
        result = normalize_declared_time("0001-01-01T00:00+14:00")
        self.assertEqual((result["status"], result["reason"]), ("unsupported", "utc_out_of_range"))

    def test_source_string_remains_identical_and_no_source_claims_are_made(self):
        original = " 2024-02-29T09:45:30+09:00 "
        result = normalize_declared_time(original)
        self.assertEqual(original, " 2024-02-29T09:45:30+09:00 ")
        self.assertEqual(result["canonical"], "2024-02-29T00:45:30Z")
        self.assertNotIn("verified", result)
        self.assertNotIn("published", result)


if __name__ == "__main__":
    unittest.main()

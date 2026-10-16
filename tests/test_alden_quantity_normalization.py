"""Contract and boundary tests for the pure declared-quantity normalizer."""

from decimal import Decimal
import unittest

from scripts.alden_quantity_normalization import POLICY, normalize_declared_quantity


class DeclaredQuantityNormalizationTests(unittest.TestCase):
    def assert_normalized(self, value, unit, expected, dimension, canonical_unit):
        self.assertEqual(
            normalize_declared_quantity(value, unit),
            {
                "status": "normalized",
                "dimension": dimension,
                "canonical_value": expected,
                "canonical_unit": canonical_unit,
                "reason": None,
            },
        )

    def assert_rejected(self, value, unit, status):
        actual = normalize_declared_quantity(value, unit)
        self.assertEqual(actual["status"], status, (value, unit, actual))
        self.assertEqual(actual["dimension"], None)
        self.assertEqual(actual["canonical_value"], None)
        self.assertEqual(actual["canonical_unit"], None)
        self.assertIsInstance(actual["reason"], str)

    def test_policy_and_response_contract(self):
        self.assertEqual(POLICY, "source-declared-quantity-v1")
        source_value, source_unit = "1.00", "kg"
        result = normalize_declared_quantity(source_value, source_unit)
        self.assertEqual(
            set(result),
            {"status", "dimension", "canonical_value", "canonical_unit", "reason"},
        )
        self.assertEqual((source_value, source_unit), ("1.00", "kg"))
        self.assertEqual(result["canonical_value"], "1000")
        self.assertNotIn("entity", result)
        self.assertNotIn("source", result)

    def test_every_allowed_unit_has_an_exact_factor(self):
        samples = (
            ("g", "1", "mass", "g"),
            ("kg", "1000", "mass", "g"),
            ("mg", "0.001", "mass", "g"),
            ("Mg", "1000000", "mass", "g"),
            ("µg", "0.000001", "mass", "g"),
            ("μg", "0.000001", "mass", "g"),
            ("m", "1", "length", "m"),
            ("cm", "0.01", "length", "m"),
            ("mm", "0.001", "length", "m"),
            ("km", "1000", "length", "m"),
            ("s", "1", "duration", "s"),
            ("ms", "0.001", "duration", "s"),
            ("µs", "0.000001", "duration", "s"),
            ("μs", "0.000001", "duration", "s"),
            ("min", "60", "duration", "s"),
            ("h", "3600", "duration", "s"),
            ("B", "8", "data", "bit"),
            ("kB", "8000", "data", "bit"),
            ("MB", "8000000", "data", "bit"),
            ("GB", "8000000000", "data", "bit"),
            ("KiB", "8192", "data", "bit"),
            ("MiB", "8388608", "data", "bit"),
            ("GiB", "8589934592", "data", "bit"),
            ("bit", "1", "data", "bit"),
            ("kbit", "1000", "data", "bit"),
            ("Mbit", "1000000", "data", "bit"),
            ("Gbit", "1000000000", "data", "bit"),
        )
        for unit, expected, dimension, canonical_unit in samples:
            with self.subTest(unit=unit):
                self.assert_normalized("1", unit, expected, dimension, canonical_unit)

    def test_equivalent_declared_quantities_and_case_distinctions(self):
        self.assert_normalized("1000", "g", "1000", "mass", "g")
        self.assert_normalized(1, "kg", "1000", "mass", "g")
        self.assert_normalized("1", "mg", "0.001", "mass", "g")
        self.assert_normalized("1", "Mg", "1000000", "mass", "g")
        self.assert_normalized("1", "MB", "8000000", "data", "bit")
        self.assert_normalized("1", "MiB", "8388608", "data", "bit")
        self.assert_normalized("1", "Mbit", "1000000", "data", "bit")

    def test_exact_fractional_scaling_without_decimal_context_rounding(self):
        self.assert_normalized("0.000000125", "MB", "1", "data", "bit")
        self.assert_normalized("0.125", "MiB", "1048576", "data", "bit")
        self.assert_normalized("0.1", "cm", "0.001", "length", "m")
        self.assert_normalized("0.5", "µs", "0.0000005", "duration", "s")
        long_fraction = "0." + "123456789012345678901234567890" * 3 + "1"
        self.assert_normalized(long_fraction, "g", long_fraction, "mass", "g")

    def test_signed_values_are_not_credibility_assertions(self):
        self.assert_normalized("-1.25", "kg", "-1250", "mass", "g")
        self.assert_normalized("+0.5", "min", "30", "duration", "s")
        self.assert_normalized(-3, "h", "-10800", "duration", "s")
        self.assert_normalized("-0", "mg", "-0", "mass", "g")
        self.assert_normalized("1.5e+2", "mm", "0.15", "length", "m")

    def test_only_absent_or_blank_arguments_are_missing(self):
        for value, unit in ((None, "kg"), ("", "kg"), ("  ", "kg"),
                            ("1", None), ("1", ""), ("1", "  ")):
            with self.subTest(value=value, unit=unit):
                self.assert_rejected(value, unit, "missing")

    def test_unlisted_case_partial_and_compound_units_are_unsupported(self):
        for unit in ("MB/s", "mb", "Mb", "KB", "M", "MG", "Kg", "kg/m3",
                     "bits", "Kib", "m²", "m/s", "USD", "€", "kg?x=1",
                     "kg-extra", " kg", "kg ", "µG"):
            with self.subTest(unit=unit):
                self.assert_rejected("1", unit, "unsupported")

    def test_full_decimal_token_is_required_not_extraction(self):
        for value in ("1kg", "https://example.com/1", "10?x=1", "$10",
                      "€1", "1 m", "1/2", "1,000", "1_000", " 1", "1 ",
                      "NaN", "sNaN", "Infinity", "-Infinity", "1e2kg"):
            with self.subTest(value=value):
                self.assert_rejected(value, "kg", "invalid")

    def test_floats_bools_nonfinite_and_coercible_objects_are_invalid(self):
        for value in (True, False, 1.0, float("nan"), float("inf"),
                      float("-inf"), Decimal("1"), Decimal("NaN"),
                      Decimal("Infinity"), [1], {"value": 1}):
            with self.subTest(type=type(value).__name__, repr=repr(value)):
                self.assert_rejected(value, "kg", "invalid")
        self.assert_rejected("1", 1, "invalid")

    def test_value_unit_exponent_and_output_budgets(self):
        self.assert_rejected("9" * 129, "g", "invalid")
        self.assert_rejected(" " * 129, "g", "invalid")
        self.assert_rejected(10**1000, "g", "invalid")
        self.assert_rejected("1e100000000000", "g", "invalid")
        self.assert_rejected("1e-101", "g", "invalid")
        self.assert_rejected("0." + "0" * 101 + "1", "g", "invalid")
        self.assert_rejected("9" * 126, "Mg", "invalid")
        self.assert_rejected("1", "m" * 100, "invalid")
        self.assert_normalized("1e100", "g", "1" + "0" * 100, "mass", "g")
        self.assert_rejected("1e100", "kg", "invalid")


if __name__ == "__main__":
    unittest.main()

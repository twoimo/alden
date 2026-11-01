# Declared quantity boundary correction

`1e-90 µg` and `1.0000000000e-90 µg` have the same value. The installed 0.3.146 helper normalized the former and rejected the latter because insignificant coefficient zeroes consumed its exponent budget. `1e-100 g` versus `1.000e-100 g` reproduced the same discrepancy. Five expanded-spelling regression subcases failed before the correction.

The normalizer now reduces insignificant coefficient zeroes inside its private exact Decimal context before checking fractional precision, and checks the final reduced canonical exponent after conversion. The declared scientific-power limit, input/output length limits, finite/type/unit checks, exact arithmetic, signed zero and canonical exponent limits remain. Previously admitted down-conversions such as `100e100 mg → 1e99 g` are explicitly preserved. This corrects numeric conversion only; saved source text, significance/uncertainty, identity, credibility and entity merging remain separate.

[Python 3.11's Decimal documentation](https://docs.python.org/3.11/library/decimal.html#decimal.Decimal.normalize) specifies coefficient reduction and context-sensitive rounding. The private context has enough precision for the bounded coefficient and unit table, and traps inexact operations; this avoids importing the caller's precision, exponent limits or flags.

Evidence in `outputs/quantity-boundary-20261018/`:

- `before.json`, `installed-before.json` and `regression-before.log` retain the defect in source and the actual installed helper, using the pinned Python with `-B`.
- The deterministic 58,131-case comparison preserves all 44,587 previously normalized outcomes exactly. All 46,141 newly accepted total outcomes match an independent exact Fraction calculation. There are 1,554 newly admitted boundary spellings and no other outcome changes. Four contexts with every caller trap/flag set remain unchanged. This bounded corpus is not a universal proof.
- The pure normalizer, source metadata integration and affected graph-quality suites pass 86 tests. Input metadata remains unchanged and reports retain `syntax_conversion_not_semantic_verdict`.
- One read-only SQLite snapshot inspected all 26,623 current document versions: no explicit quantity fields were present, so no existing production-record improvement is claimed. The original mistaken-column diagnostic is retained separately from the successful snapshot.

The Gemini audit of source137461c remains in progress; this independently reproduced correction is not presented as a completed external report or adjudication. The later current-source/reverse audit must include this delta. Installation and signing continuity need their own delivery receipts.

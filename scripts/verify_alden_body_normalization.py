"""Bounded checks of the real parser and isolated, deliberately broken variants."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/alden_body_normalization.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    original = SOURCE.read_bytes()
    source = original.decode()
    replacements = {
        'lost_negative_sign': ("sign = -1 if value.startswith('-') else 1", 'sign = 1'),
        'wrong_magnitude': ("'만': 10**4", "'만': 10**3"),
        'wrong_js_offsets': ('code_units += 2 if ord(body[cursor]) > 0xffff else 1', 'code_units += 1'),
        'malformed_grouping_accepted': ('(?:,[0-9]{3})+', '(?:,[0-9]{1,3})+'),
        'guessed_century': ("year = match['ky'] or match['iy']", "year = match['ky'] or match['iy']\n        if len(year) == 2: year = '20' + year"),
        'url_numbers_admitted': ("if url_index < len(urls) and urls[url_index][0] < candidate['end'] and candidate['start'] < urls[url_index][1]:", 'if False:'),
        'joined_time_suffix_ignored': ('if unparsed_clock or unparsed_suffix:', 'if unparsed_clock:'),
        'fraction_ratio_prefix_ignored': ("if previous >= 0 and body[previous] in '/⁄∕÷／:∶：':", 'if False:'),
        'unicode_horizontal_prefix_ignored': ("body[index] == '\\t' or unicodedata.category(body[index]) == 'Zs'", "body[index] in ' \\t\\u00a0'"),
        'numbered_label_boundary_ignored': ("return end < 0 or not (body[end].isalnum() or body[end] == '_'\n                          or unicodedata.category(body[end]).startswith('M'))", 'return True'),
    }
    variants = {'current': source}
    for name, (before, after) in replacements.items():
        assert source.count(before) == 1, name + ': recheck code-to-property correspondence'
        variants[name] = source.replace(before, after)
    variants['restored'] = source
    results = {}
    if args.output:
        assert args.output.is_absolute()
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        assert not args.output.exists()
    with tempfile.TemporaryDirectory(prefix='alden-body-verification-') as folder:
        work = Path(folder)
        (work / 'scripts').mkdir()
        (work / 'tests').mkdir()
        for relative in ['scripts/alden_quantity_normalization.py', 'scripts/alden_time_normalization.py', 'tests/test_alden_body_normalization.py']:
            (work / relative).write_bytes((ROOT / relative).read_bytes())
        for name, text in variants.items():
            (work / 'scripts/alden_body_normalization.py').write_text(text)
            run = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_alden_body_normalization.py', '-v'],
                                 cwd=work, capture_output=True, text=True, timeout=30,
                                 env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(work / 'scripts')})
            expected = name in ('current', 'restored')
            results[name] = {'exit_code': run.returncode, 'passed': run.returncode == 0,
                             'source_sha256': hashlib.sha256(text.encode()).hexdigest()}
            if args.output:
                path = args.output.with_name('mutation-' + name + '.log')
                with path.open('x') as handle:
                    path.chmod(0o600)
                    handle.write(run.stdout + run.stderr)
            assert (run.returncode == 0) == expected, name + ': mutation sensitivity or current parser check failed'
    assert SOURCE.read_bytes() == original
    report = {'scope': 'Real parser source and ten isolated mutations; bounded unit/generated-input checks, not a theorem or semantic gold.',
              'source_sha256': hashlib.sha256(original).hexdigest(),
              'covered': ['exact source/codepoint/UTF16 span equality', 'signs', 'Korean magnitudes',
                          'grouping syntax', 'no century guess', 'URL exclusion', 'no normalized joined timestamp prefix',
                          'no independent numeric fraction/ratio denominator', 'horizontal Unicode spacing preserves numeric-prefix fences',
                          'numeric word/identity label suffixes remain separate from bare operands'],
              'bounds': {'body_characters': 131072, 'candidates_per_body': 64, 'token_characters': 128,
                         'integer_oracle_generated_cases': 112, 'compound_integer_oracle_cases': 10,
                         'joined_timestamp_suffix_cases': 39, 'timestamp_boundary_controls': 24,
                         'fraction_ratio_tail_cases': 960, 'quantity_label_rate_line_controls': 8,
                         'unsupported_sign_spacing_cases': 28, 'numbered_label_controls': 120},
              'results': results, 'python_version': sys.version, 'production_source_modified': False,
              'limits': ['No fact extraction/authorization/current-version integration, physical UI, liveness, full Unicode grammar or independent precision/recall proof.',
                         'Range/subtraction, relative dates, implicit timezone/currency and unsupported notation stay unresolved.']}
    if args.output:
        with args.output.open('x') as handle:
            args.output.chmod(0o600)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
    print(json.dumps({'current_and_restored_passed': True, 'rejected_mutants': len(replacements), 'source_unchanged': True}))


if __name__ == '__main__':
    main()

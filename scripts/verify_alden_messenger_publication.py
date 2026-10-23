"""Check the real publication boundary and reject three broken source variants."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/evaluation/messenger-stream-20261021/messenger_candidate.py'
CHILD = '''
from pathlib import Path
import importlib.util,sys,unittest
root=Path.cwd();sys.path[:0]=[str(root/'scripts'),str(root/'tests'),str(root/'docs/evaluation/messenger-stream-20261021')]
spec=importlib.util.spec_from_file_location('messenger_candidate',sys.argv[1])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
result=unittest.TextTestRunner(verbosity=1).run(unittest.TestLoader().loadTestsFromName('test_alden_messenger_publication'))
raise SystemExit(not result.wasSuccessful())
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source = SOURCE.read_text()
    deferred = "defer_activity=payload.get('activity_protocol') == 'frontier-outbox-v1'"
    outbox = '''db.execute("INSERT OR IGNORE INTO activity_releases(page,state) VALUES(?,'pending')", (job,))'''
    start, end = source.index('    def _source_access('), source.index('    def _retained_page(')
    guard = source[start:end]
    assert source.count(deferred) == source.count(outbox) == guard.count('m.current_version=?') == 1
    variants = {
        'current': source,
        'premature_stored_activity': source.replace(deferred, 'defer_activity=False'),
        'missing_durable_outbox': source.replace(outbox, 'db.execute("SELECT ?", (job,))'),
        'wrong_membership_version_allowed': source[:start] + guard.replace('m.current_version=?', '? IS NOT NULL') + source[end:],
    }
    results = {}
    with tempfile.TemporaryDirectory(prefix='alden-publication-verification-') as directory:
        for name, text in variants.items():
            path = Path(directory) / (name + '.py'); path.write_text(text)
            run = subprocess.run([sys.executable, '-B', '-c', CHILD, str(path)], cwd=ROOT,
                                 capture_output=True, text=True, timeout=120)
            passed = run.returncode == 0
            results[name] = {'passed': passed, 'exit_code': run.returncode,
                             'source_sha256': hashlib.sha256(text.encode()).hexdigest()}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                log = args.output.with_name('publication-' + name + '.log')
                log.write_text(run.stdout + run.stderr); log.chmod(0o600)
            assert passed == (name == 'current'), name + ': unexpected verification outcome'
    report = {'scope': 'Actual isolated SQLite commit/crash/cursor traces with controlled source readers; not a proof of the whole application or native current visibility.',
              'results': results, 'python_version': sys.version, 'production_files_modified_by_checker': False}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n'); args.output.chmod(0o600)
    print(json.dumps(report))


if __name__ == '__main__': main()

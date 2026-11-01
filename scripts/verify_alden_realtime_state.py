"""Check real watcher traces and prove the checks reject scoped source mutations."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/alden_realtime_collection.py'
CHILD = '''
from pathlib import Path
import importlib.util,sys,unittest
root=Path.cwd();sys.path[:0]=[str(root/'scripts'),str(root/'tests')]
spec=importlib.util.spec_from_file_location('alden_realtime_collection',sys.argv[1])
module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
suite=unittest.TestLoader().loadTestsFromNames(['test_alden_realtime_collection','test_alden_realtime_state'])
result=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    source = SOURCE.read_text()
    # Mutations change real production source in temporary files only. They do
    # not replace the watch function with a handwritten design model.
    busy = "except sqlite3.OperationalError as error:\n                if not storage_busy(error): raise"
    assert source.count(busy) == 1, 'Recheck source-to-property alignment before updating this mutation'
    before_cycle = 'if enabled or stamp - last_scheduled >= 60:\n                    result=scheduler.cycle('
    assert source.count(before_cycle) == 1
    variants = {
        'current': source,
        'busy_terminates_watcher': source.replace(busy, 'except sqlite3.OperationalError as error:\n                raise'),
        'failed_cycle_advances_success_clock': source.replace(before_cycle,
            'if enabled or stamp - last_scheduled >= 60:\n                    last_scheduled = stamp\n                    result=scheduler.cycle(')
            .replace('                    last_scheduled = stamp\n                    if enabled:', '                    if enabled:'),
    }
    results = {}
    with tempfile.TemporaryDirectory(prefix='alden-watch-verification-') as directory:
        for name, text in variants.items():
            path = Path(directory) / (name + '.py')
            path.write_text(text)
            run = subprocess.run([sys.executable, '-B', '-c', CHILD, str(path)], cwd=ROOT,
                                 capture_output=True, text=True, timeout=120)
            passed = run.returncode == 0
            results[name] = {'passed': passed, 'exit_code': run.returncode,
                             'source_sha256': hashlib.sha256(text.encode()).hexdigest()}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                log = args.output.with_name(name + '.log')
                log.write_text(run.stdout + run.stderr); log.chmod(0o600)
            assert passed == (name == 'current'), name + ': unexpected verification outcome'
    report = {'scope': 'Bounded generated traces of the actual Python watcher plus a real private SQLite-lock journey; not a formal proof or installed/live-host verification.',
              'results': results, 'generated_busy_cases': 84, 'periodic_retry_cases': 2,
              'fatal_error_cases': 8, 'max_ticks_per_trace': 2,
              'tool': 'Python unittest with deterministic boundary inputs and real-source mutation checks',
              'python_version': sys.version, 'production_files_modified_by_checker': False}
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + '\n'); args.output.chmod(0o600)
    print(json.dumps(report))


if __name__ == '__main__': main()

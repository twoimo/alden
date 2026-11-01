"""Check cursor retry and source authority with existing real-store tests.

Broken variants live only in this process. No native reader or product file writes.
"""
from pathlib import Path
from unittest.mock import patch
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'scripts')]
import alden_messenger_production as production
from alden_collection import CollectionStore, encoded
from tests.test_alden_messenger_production import ProductionTests

NAMES = [
    'test_new_live_content_keeps_stream_identity_and_delivers_its_committed_receipt',
    'test_content_change_during_activity_read_retries_the_same_cursor_without_losing_a_receipt',
    'test_current_source_edit_withdrawal_and_removal_hide_stored_body_without_waiting_for_collection',
]
COVERED = [
    'scripts/alden_collection.py', 'scripts/alden_messenger_production.py',
    'scripts/alden_messenger_runtime.py', 'tests/test_alden_messenger_production.py',
    'scripts/alden_messenger_quality.py', 'tests/test_alden_messenger_frontier.py',
    'scripts/verify_alden_activity_stream.py',
]


def run(names):
    log = io.StringIO()
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(
        unittest.TestSuite(ProductionTests(name) for name in names))
    return result, log.getvalue()


def authorization_access(self, target, document, version, metadata):
    return self._source(target)._source_access(target, document, version, metadata)


def authorization_revision(self, target):
    source = self._source(target)
    state, _ = production.binding(self.root, source.platform, source.account)
    return hashlib.sha256(encoded([state['revision'], source._source_revision(target)])).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in COVERED}
    normal, log = run(NAMES)
    assert normal.wasSuccessful(), log
    logs, results = {'current': log}, {'current': {'cases': normal.testsRun, 'passed': True}}

    def rejected(name, result, details):
        logs[name] = details
        assert len(result.failures) == 1 and not result.errors, name + ': wrong rejection\n' + details
        results[name] = {'cases': result.testsRun, 'expected_assertion_failures': 1, 'errors': 0}

    with patch.object(production.ProductionAuthority, 'stream_revision', production.ProductionAuthority.revision):
        result, details = run(NAMES[:1])
    rejected('data_bound_namespace', result, details)
    with patch.object(production.ProductionAuthority, 'access', authorization_access), \
            patch.object(production.ProductionAuthority, 'revision', authorization_revision):
        result, details = run(NAMES[2:])
    rejected('authorization_only_content_guard', result, details)
    install = CollectionStore._install_source_access

    def no_final_check(self, db, **options):
        install(self, db, **options)
        return lambda: None

    with patch.object(CollectionStore, '_install_source_access', no_final_check):
        result, details = run(NAMES[1:2])
    rejected('missing_end_of_read_check', result, details)
    restored, details = run(NAMES)
    assert restored.wasSuccessful(), details
    logs['restored'] = details
    results['restored'] = {'cases': restored.testsRun, 'passed': True}
    assert hashes == {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in COVERED}
    report = {
        'results': results, 'source_sha256': hashes, 'python_version': sys.version,
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'dirty_tracked_paths': subprocess.check_output(['git', 'diff', '--name-only'], cwd=ROOT, text=True).splitlines(),
        'untracked_paths': subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines(),
        'tool': 'Python unittest, controlled readers, actual private stores, process-local mutations',
        'bounds': 'Two rooms, three message identities, one live addition, one mid-read withdrawal; finite retry trace.',
        'scope': 'Backend stream/content/cursor. UI, native arrivals, full history and unbounded liveness are separate.',
        'source_files_modified': False, 'native_reads': 0, 'send_calls': 0,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        for name, details in logs.items():
            descriptor = os.open(args.output.with_name('stream-' + name + '.log'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as handle:
                handle.write(details)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps(report))


if __name__ == '__main__':
    main()

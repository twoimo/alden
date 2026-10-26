"""Replay bounded real graph schedules and reject isolated source mutations.

No model checker, native app, database, SDK, provider or installer is invoked.
The installed desktop test dependencies are read through a temporary symlink;
all source variants, caches and child process groups belong to this run.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import difflib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
TESTS = ('src/__tests__/knowledge-scope-model.test.ts',
         'src/__tests__/knowledge-scope-adversarial.test.ts')
COVERED = ('src/knowledge/collection-graph.ts', 'src/knowledge/refresh.ts',
           'src/knowledge/map-status.ts', 'src/knowledge/unified-graph.ts',
           'src/knowledge/graph-model.ts', 'src/ui.ts')
EXPECTED_TESTS = 22


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def source_inputs():
    names = subprocess.check_output(['git', '-c', 'core.fsmonitor=false', 'ls-files', '-z',
        '--', 'desktop/src', 'desktop/package.json', 'desktop/package-lock.json'], cwd=ROOT).decode().split('\0')
    names = sorted(set(filter(None, names)) | {'desktop/' + name for name in TESTS})
    values = {}
    for name in names:
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise RuntimeError('source must be an in-repository regular file: ' + name)
        raw = path.read_bytes()
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError('source file exceeds verification bound: ' + name)
        values[name] = raw
    if len(values) > 1024 or sum(map(len, values.values())) > 32 * 1024 * 1024:
        raise RuntimeError('source snapshot exceeds verification bound')
    return values


def variants():
    return {
        'current': [],
        'scope_clear_removed': [('src/knowledge/collection-graph.ts',
            '    this.graph.replaceGraph({ nodes: [], edges: [] }, this.state);',
            '    // Isolated negative control: retain the previous project graph.')],
        'obsolete_epoch_reply_allowed': [('src/knowledge/collection-graph.ts',
            'if (!this.active || this.disposed || epoch !== this.epoch) return null;',
            'if (!this.active || this.disposed) return null;'),
            ('src/knowledge/refresh.ts', 'if (payload && this.active && epoch === this.epoch) {',
             'if (payload && this.active) {')],
        'scope_result_invalidation_removed': [('src/knowledge/collection-graph.ts',
            "    this.pendingScopeStatus(); this.setText('knowledge-scope', '');",
            "    this.setText('knowledge-scope', '');")],
    }


def run_child(command, cwd, env, log):
    proc = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        output, _ = proc.communicate(timeout=150)
        log.write_bytes(output)
        return proc.returncode
    finally:
        # The child and Vitest forks are a new session owned by this invocation.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if proc.poll() is None:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def trial(name, edits, scratch, evidence, inputs, node, dependencies):
    root = scratch / name
    root.mkdir()
    for path, raw in inputs.items():
        target = root / Path(path).relative_to('desktop')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    os.symlink(dependencies, root / 'node_modules', target_is_directory=True)
    patches = []
    for name_in_source, old, new in edits:
        path = root / name_in_source
        original = path.read_text()
        if original.count(old) != 1:
            raise RuntimeError('mutation anchor needs source review: ' + name_in_source)
        modified = original.replace(old, new, 1)
        patches.extend(difflib.unified_diff(original.splitlines(True), modified.splitlines(True),
                       fromfile=name_in_source, tofile=name_in_source))
        path.write_text(modified)
    if patches:
        (evidence / (name + '.patch')).write_text(''.join(patches))
    version = json.loads(inputs['desktop/package.json'])['version']
    config = {'root': str(root), 'cacheDir': str(root / '.owned-vite-cache'),
        'define': {'__ALDEN_VERSION__': json.dumps(version)},
        'test': {'include': list(TESTS), 'watch': False, 'cache': False,
                 'maxWorkers': 1, 'fileParallelism': False}}
    config_path = root / 'scope-vitest.config.mjs'
    config_path.write_text('export default ' + json.dumps(config) + ';\n')
    report_path = evidence / (name + '-schedules.json')
    tests_path = evidence / (name + '-tests.json')
    command = [node, str(dependencies / 'vitest/vitest.mjs'), 'run',
        '--config', str(config_path), '--configLoader', 'native', '--reporter', 'json',
        '--outputFile', str(tests_path)]
    env = {**os.environ, 'ALDEN_SCOPE_REPORT': str(report_path)}
    code = run_child(command, root, env, evidence / (name + '.log'))
    data = json.loads(tests_path.read_text())
    generated = json.loads(report_path.read_text())
    if data['numTotalTests'] != EXPECTED_TESTS or data['numPendingTests'] != 0:
        raise RuntimeError('unexpected collected/skipped tests: ' + name)
    assertions = [case for suite in data['testResults'] for case in suite['assertionResults']]
    if len(assertions) != EXPECTED_TESTS:
        raise RuntimeError('unexpected assertion report: ' + name)
    if not edits:
        accepted = code == 0 and data['numFailedTests'] == 0 and data['numPassedTests'] == EXPECTED_TESTS
        accepted = accepted and generated['status'] == 'pass' and generated['completedSchedules'] > 0
        accepted = accepted and generated['acceptedProjects'] == ['A', 'B'] and generated['staleCompletions'] > 0
    else:
        accepted = code != 0 and data['numFailedTests'] > 0 and generated['status'] == 'fail'
        accepted = accepted and any('scope ownership violated' in message for message in generated['failures'])
    result = {'name': name, 'command': command, 'exit': code, 'expected': 'intended scope-oracle violation' if edits else 'all bounded checks pass',
        'accepted': bool(accepted), 'passed_tests': data['numPassedTests'], 'failed_tests': data['numFailedTests'],
        'schedules': generated, 'test_report': str(tests_path), 'schedule_report': str(report_path)}
    shutil.rmtree(root)  # Removes this run's symlink, not its dependency target.
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='New evidence directory; never overwrites a previous run')
    parser.add_argument('--node', default=shutil.which('node'))
    args = parser.parse_args()
    if not args.node or not Path(args.node).is_file():
        parser.error('an existing Node runtime is required; nothing will be installed')
    default = ROOT / '.agents/skills/verify-alden-graph-scope/runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = (args.output or default).absolute()
    output.mkdir(parents=True, exist_ok=False)
    dependencies = (ROOT / 'desktop/node_modules').resolve(strict=True)
    original_link = os.readlink(ROOT / 'desktop/node_modules') if (ROOT / 'desktop/node_modules').is_symlink() else None
    inputs = source_inputs()
    pins = {name: sha(raw) for name, raw in inputs.items()}
    result = {'kind': 'bounded actual-code checks with isolated mutations; not formal/refinement/liveness proof',
        'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'node': subprocess.check_output([args.node, '--version'], text=True).strip(),
        'versions': {name: json.loads((dependencies / name / 'package.json').read_text())['version']
                     for name in ('vitest', 'vite', 'happy-dom', 'typescript')},
        'covered_source_pins': {'desktop/' + name: pins['desktop/' + name] for name in COVERED},
        'source_input_pins': pins, 'trials': [], 'status': 'inconclusive',
        'native_app_calls': 0, 'original_data_calls': 0, 'source_mutations': False}
    try:
        with tempfile.TemporaryDirectory(prefix='alden-graph-scope-') as directory:
            scratch = Path(directory)
            for name, edits in variants().items():
                observed = trial(name, edits, scratch, output, inputs, args.node, dependencies)
                result['trials'].append(observed)
                if not observed['accepted']:
                    raise RuntimeError('verification did not meet its declared oracle: ' + name)
            # All broken source copies were removed before this fresh positive replay.
            result['trials'].append(trial('restored-current', [], scratch, output, inputs, args.node, dependencies))
            if not result['trials'][-1]['accepted']:
                raise RuntimeError('fresh positive replay failed after mutation cleanup')
        after = source_inputs()
        if {name: sha(raw) for name, raw in after.items()} != pins:
            raise RuntimeError('source changed concurrently; result cannot be bound to the starting bytes')
        if original_link is not None and os.readlink(ROOT / 'desktop/node_modules') != original_link:
            raise RuntimeError('dependency link changed during verification')
        result['scratch_copies_removed'] = not scratch.exists()
        result['main_source_bytes_unchanged'] = True
        result['status'] = 'pass_bounded_checks'
    except BaseException as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        save(output / 'verification.json', result)
        print(json.dumps({'status': result['status'], 'output': str(output),
            'trials': [{key: item[key] for key in ('name', 'exit', 'accepted')} for item in result['trials']]}, ensure_ascii=False))


if __name__ == '__main__':
    main()

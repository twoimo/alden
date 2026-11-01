"""Run the isolated candidate, or show the unchanged runtime's regressions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

assert sys.dont_write_bytecode, 'use_python_-B'
folder = Path(__file__).resolve().parent
repo = folder.parents[2]
source = repo / 'scripts/alden_collection_retrieval.py'
manifest = json.loads((folder / 'source-manifest.json').read_text())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert sha(source) == manifest['baseline_sha256']
assert sha(folder / 'candidate.patch') == manifest['patch_sha256']
assert sha(folder / 'context_cases.py') == manifest['fixture_sha256']
assert sha(folder.parent / 'named-source-207-20261021/named_source_cases.py') == manifest['named_fixture_sha256']
for name, expected in manifest['dependencies'].items():
    assert sha(repo / name) == expected, 'candidate_dependency_changed: ' + name
baseline = sys.argv[1:] == ['--baseline']
assert not sys.argv[1:] or baseline
with tempfile.TemporaryDirectory(prefix='alden-context-fixtures-') as temporary:
    root = Path(temporary); (root / 'scripts').mkdir()
    candidate = root / 'scripts/alden_collection_retrieval.py'
    shutil.copy2(source, candidate)
    if not baseline:
        subprocess.run(['patch', '-t', '-p1', '-d', str(root), '-i', str(folder / 'candidate.patch')],
                       capture_output=True, text=True, check=True)
        assert sha(candidate) == manifest['candidate_sha256']
    sys.path[:0] = [str(folder), str(folder.parent / 'named-source-207-20261021'),
                   str(repo / 'scripts'), str(repo / 'tests')]
    spec = importlib.util.spec_from_file_location('alden_collection_retrieval', candidate)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    import test_alden_collection_retrieval as existing
    import named_source_cases as named
    import context_cases as cases
    suite = unittest.TestSuite()
    if not baseline:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(existing))
        suite.addTests(named.NamedSources(name) for name in dir(named.NamedSources) if name.startswith('test_named_'))
    context_names = ([ 'test_context_long_first_source_does_not_starve_second_and_emits_late_matches' ]
                     if baseline else [name for name in dir(cases.MatchedContext) if name.startswith('test_context_')])
    suite.addTests(cases.MatchedContext(name) for name in context_names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = {'candidate_only': not baseline, 'installed': False, 'tests': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'success': result.wasSuccessful(),
        'source_sha256': sha(candidate), 'repository_source_unchanged': sha(source) == manifest['baseline_sha256'],
        'scope': 'Controlled local fixture stores; embedding/transport adapters are synthetic. No model inference.'}
print(json.dumps(receipt))
sys.exit(0 if result.wasSuccessful() else 1)

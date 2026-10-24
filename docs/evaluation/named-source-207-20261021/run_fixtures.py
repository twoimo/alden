"""Reproduce the uninstalled retrieval candidate in an isolated temporary module."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

folder = Path(__file__).resolve().parent
repo = folder.parents[2]
source = repo / 'scripts/alden_collection_retrieval.py'
manifest = json.loads((folder / 'source-manifest.json').read_text())
digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert digest(source) == manifest['baseline_sha256'], 'baseline_source_changed_rebase_candidate_before_running'
assert digest(folder / 'candidate.patch') == manifest['patch_sha256']
assert digest(folder / 'named_source_cases.py') == manifest['fixture_sha256']
for name, expected in manifest['dependencies'].items():
    assert digest(repo / name) == expected, 'candidate_dependency_changed: ' + name
patch_tool = shutil.which('patch')
assert patch_tool, 'standard_patch_tool_unavailable'
with tempfile.TemporaryDirectory(prefix='alden-named-source-fixtures-') as temporary:
    root = Path(temporary); (root / 'scripts').mkdir()
    candidate = root / 'scripts/alden_collection_retrieval.py'
    shutil.copy2(source, candidate)
    applied = subprocess.run([patch_tool, '-t', '-p1', '-d', str(root), '-i', str(folder / 'candidate.patch')],
                             capture_output=True, text=True, check=True)
    assert digest(candidate) == manifest['candidate_sha256']
    sys.path[:0] = [str(folder), str(repo / 'scripts'), str(repo / 'tests')]
    spec = importlib.util.spec_from_file_location('alden_collection_retrieval', candidate)
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    import test_alden_collection_retrieval as existing
    import named_source_cases as cases
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(existing))
    suite.addTests(cases.NamedSources(name) for name in dir(cases.NamedSources) if name.startswith('test_named_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    receipt = {'candidate_only': True, 'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'success': result.wasSuccessful(),
        'baseline_sha256': manifest['baseline_sha256'], 'candidate_sha256': digest(candidate),
        'repository_source_unchanged': digest(source) == manifest['baseline_sha256'],
        'scope': 'Controlled fixture stores and fake embedding/transport adapters; no live app data or model inference'}
assert receipt['repository_source_unchanged']
print(json.dumps(receipt))
sys.exit(0 if result.wasSuccessful() else 1)

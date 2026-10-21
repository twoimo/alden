"""Build isolated retrieval/consumer candidates, preserving installed modules."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

assert sys.dont_write_bytecode,'use_python_-B'
folder=Path(__file__).resolve().parent;repo=folder.parents[2]
manifest=json.loads((folder/'source-manifest.json').read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for path,expected in manifest['inputs'].items():
    assert sha(repo/path)==expected,'candidate_input_changed: '+path
baseline=sys.argv[1:]==['--baseline']
assert not sys.argv[1:] or baseline
with tempfile.TemporaryDirectory(prefix='alden-consumer-fixtures-')as temporary:
    work=Path(temporary);(work/'scripts').mkdir()
    for name in ('alden_collection_retrieval.py','alden_voice.py'):
        shutil.copy2(repo/'scripts'/name,work/'scripts'/name)
    patches=[repo/'docs/evaluation/matched-context-207-20261021/candidate.patch']
    if not baseline:patches.append(folder/'consumer.patch')
    for patch in patches:
        subprocess.run(['patch','-t','-p1','-d',str(work),'-i',str(patch)],capture_output=True,text=True,check=True)
    sys.path[:0]=[str(folder),str(repo/'docs/evaluation/matched-context-207-20261021'),
       str(repo/'docs/evaluation/named-source-207-20261021'),str(repo/'scripts'),str(repo/'tests')]
    for name in ('alden_collection_retrieval','alden_voice'):
        path=work/'scripts'/(name+'.py')
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        sys.modules[name]=module;spec.loader.exec_module(module)
        assert Path(module.__file__)==path
    import consumer_cases as consumer
    suite=unittest.TestSuite()
    names=[n for n in dir(consumer.ConsumerCases)if n.startswith('test_consumer_')]
    if baseline:names=[n for n in names if n in ('test_consumer_retains_source_body_coordinates_in_input_and_metrics','test_consumer_keeps_literal_query_coverage_within_existing_600_char_cap')]
    else:
        import test_alden_collection_retrieval as existing
        import test_alden_voice_retrieval as voice_cases
        import context_cases as context
        import named_source_cases as named
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(existing))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(voice_cases))
        for cls,prefix in ((context.MatchedContext,'test_context_'),(named.NamedSources,'test_named_')):
            suite.addTests(cls(n)for n in dir(cls)if n.startswith(prefix))
    suite.addTests(consumer.ConsumerCases(n)for n in names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    receipt={'candidate_only':True,'consumer_patched':not baseline,'installed':False,
      'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
      'modules_sha256':{name:sha(work/'scripts'/name)for name in ('alden_collection_retrieval.py','alden_voice.py')},
      'repository_sources_unchanged':all(sha(repo/path)==expected for path,expected in manifest['inputs'].items()if path.startswith('scripts/')),
      'scope':'Owned sources/actual parser paths; offline retrieval fixture adapters. No live model, source acquisition, physical voice, semantic truth or production promotion.'}
    print(json.dumps(receipt))
raise SystemExit(not result.wasSuccessful())

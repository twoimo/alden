"""Owned cache transitions and real process leases; no provider or app launch."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from alden_collection import CollectionStore, identity, read_action
import alden_gbrain_client as client
import alden_gbrain_cache as managed
from alden_gbrain_cache import ManagedGbrainCache, ManagedGbrainReader
from alden_gbrain_client import GbrainError
from alden_gbrain_projection import GbrainProjection


class ManagedCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'source'; self.source.mkdir(mode=0o700)
        self.store = CollectionStore(self.source)
        self.target = self.store.register(platform='graph',original_id='public-fixture',kind='source',
            label='Publisher',projects=['one'])
        self.update('CACHE_SECRET_OLD')
        runtime = self.base / 'reviewed'; runtime.mkdir(mode=0o700)
        (runtime/'home/.gbrain').mkdir(parents=True)
        (runtime/'brain').mkdir()
        binary = runtime/'gbrain'; binary.write_bytes(b'inert test bytes; never executed'); binary.chmod(0o700)
        self.sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        (runtime/'home/.gbrain/config.json').write_text(json.dumps({'engine':'pglite',
            'database_path':str(runtime/'brain'),'embedding_disabled':True,'self_upgrade':{'mode':'notify'}}))
        (runtime/'receipt.json').write_text(json.dumps({'repository':'garrytan/gbrain',
            'source_commit':client.SOURCE_COMMIT,'tag':'v'+client.VERSION,'asset_name':client.ASSET,
            'bytes':binary.stat().st_size,'sha256':self.sha,'github_asset_digest_matches':True,'runtime_path':str(binary)}))
        for module,name,value in [(client,'ASSET_BYTES',binary.stat().st_size),
                (client,'ASSET_SHA256',self.sha),(managed,'ASSET_SHA256',self.sha)]:
            self.enterContext(patch.object(module,name,value))
        self.create_options = dict(collection_root=self.source, projects=['one'],runtime_receipt=runtime/'receipt.json',
                                   runtime_root=runtime,runtime_home=runtime/'home')
        self.root = self.base/'managed'
        self.cache = ManagedGbrainCache.create(self.root, **self.create_options)

    def update(self, label):
        self.store.ingest(self.target, [{'original_id':'document','label':label,'text':label+' BODY'}])

    def current(self):
        return read_action(self.source,'collection-graph',json.dumps({'projects':['one'],'limit':50}))

    def build(self, generation, projection, **_):
        (generation/'audit').mkdir()
        (generation/'audit/metadata.txt').write_text('\n'.join(n['label'] for n in projection.nodes.values()))
        return {'pages':len(projection.pages()),'links':len(projection.links()),'verified_queries':len(projection.nodes)}

    def refresh(self, side_effect=None, **kwargs):
        with patch.object(self.cache,'_build',side_effect=side_effect or self.build):
            return self.cache.refresh(current=self.current, **kwargs)

    def assert_empty(self):
        self.assertFalse((self.root/'active.json').exists())
        self.assertEqual(list(self.cache.generations.iterdir()), [])

    def test_create_is_exclusive_root_and_project_bound(self):
        before=(self.root/'owner.json').read_bytes()
        with self.assertRaisesRegex(GbrainError,'requires_new_root'):
            ManagedGbrainCache.create(self.root, **self.create_options)
        self.assertEqual((self.root/'owner.json').read_bytes(), before)
        for root,projects in ((self.source,['other']),(self.base,['one'])):
            with self.subTest(root=root,projects=projects), self.assertRaisesRegex(GbrainError,'owner_mismatch'):
                ManagedGbrainCache(self.root,collection_root=root,projects=projects)

    def test_withdraw_cli_does_not_reopen_original_provider_authority(self):
        self.refresh()
        argv=['alden_gbrain_cache.py','withdraw','--root',str(self.root),'--state-root',str(self.source),'--project','one']
        with patch.object(sys,'argv',argv), patch('alden_knowledge_mcp.KnowledgeServer',side_effect=AssertionError('source provider reopened')), \
             patch('builtins.print'):
            managed.main()
        self.assert_empty()

    def test_runtime_copy_changed_after_source_admission_is_not_committed(self):
        destination=self.base/'tampered-copy'
        original=shutil.copyfile
        def corrupt(source,target):
            result=original(source,target);Path(target).write_bytes(b'changed');return result
        with patch('alden_gbrain_cache.shutil.copyfile',side_effect=corrupt):
            with self.assertRaisesRegex(GbrainError,'runtime_bytes_changed'):
                ManagedGbrainCache.create(destination, **self.create_options)
        self.assertFalse(destination.exists())

    def test_unchanged_refresh_performs_no_engine_writes_and_preserves_source(self):
        before=self.store.path.read_bytes();first=self.refresh();active=self.cache.active()
        with patch.object(self.cache,'_build',side_effect=AssertionError('unnecessary rebuild')):
            second=self.cache.refresh(current=self.current)
        self.assertEqual((first['state'],second['state']),('published','unchanged'))
        self.assertEqual(self.cache.active(),active)
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_ready_manifest_read_is_bounded_before_starting_an_engine(self):
        self.refresh()
        with patch.object(managed,'MANIFEST_BYTES',128), self.assertRaisesRegex(GbrainError,'record_invalid'):
            self.cache.active()
        self.cache.withdraw();self.assert_empty()

    def test_empty_permitted_snapshot_preserves_scope_targets_and_revision(self):
        before=self.store.path.read_bytes();current=self.current()
        packet=read_action(self.source,'collection-graph',json.dumps({'projects':['one'],'limit':50,'search':'unmatched-empty-fixture'}))
        self.assertEqual(packet['nodes'],[])
        self.assertEqual(packet.get('scope'),['one'])
        self.assertEqual(packet.get('revision'),current['revision'])
        self.assertEqual({t['id'] for t in packet.get('targets',[])},{self.target})
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_version_update_removes_old_database_audit_and_manifest_generation(self):
        self.refresh();old=self.cache._generation(self.cache.active()['generation'])
        self.update('CACHE_SECRET_NEW');result=self.refresh()
        self.assertEqual(result['removed_generations'],1)
        self.assertFalse(old.exists())
        for path in self.root.rglob('*'):
            if path.is_file():self.assertNotIn(b'CACHE_SECRET_OLD',path.read_bytes(),str(path))

    def test_permission_revocation_retires_old_cache_and_publishes_valid_empty_scope(self):
        self.store.register(platform='graph',original_id='still-permitted-empty-target',kind='source',
                            label='Empty permitted publisher',projects=['one'])
        self.refresh();old=self.cache._generation(self.cache.active()['generation'])
        self.store.register(platform='graph',original_id='public-fixture',kind='source',label='Publisher',
                            projects=['one'],permission='denied')
        result=self.refresh()
        self.assertEqual((result['state'],result['pages'],result['links']),('published',0,0))
        self.assertFalse(old.exists())
        reader=ManagedGbrainReader(self.root,collection_root=self.source,projects=['one'])
        with patch('alden_gbrain_bridge.GbrainReadClient') as engine:
            with self.assertRaisesRegex(GbrainError,'focus_unavailable'):
                reader.query(projects=['one'],focus=identity('graph','document'),current=self.current)
            engine.assert_not_called()

    def test_all_targets_denied_retires_cache_without_claiming_an_empty_success(self):
        self.refresh()
        self.store.register(platform='graph',original_id='public-fixture',kind='source',label='Publisher',
                            projects=['one'],permission='denied')
        with patch.object(self.cache,'_build') as build:
            with self.assertRaisesRegex(GbrainError,'scope_withdrawn'):self.cache.refresh(current=self.current)
            build.assert_not_called()
        self.assert_empty()

    def test_missing_original_store_withdraws_derived_cache(self):
        self.refresh()
        with self.assertRaisesRegex(GbrainError,'source_unavailable'):
            self.cache.refresh(current=lambda:{'ok':True,'state':'not_configured'})
        self.assert_empty()

    def test_original_read_failure_is_not_treated_as_empty_or_permission_granted(self):
        self.refresh();active=self.cache.active()
        def unavailable():raise PermissionError('fixture original source denial')
        with patch.object(self.cache,'_build') as build, self.assertRaises(PermissionError):
            self.cache.refresh(current=unavailable)
        build.assert_not_called();self.assertEqual(self.cache.active(),active)
        reader=ManagedGbrainReader(self.root,collection_root=self.source,projects=['one'])
        with patch('alden_gbrain_bridge.GbrainReadClient') as engine:
            with self.assertRaises(PermissionError):
                reader.query(projects=['one'],focus=identity('graph','document'),current=unavailable)
            engine.assert_not_called()

    def test_failure_after_retiring_old_generation_does_not_roll_back(self):
        self.refresh();self.update('CACHE_SECRET_NEW')
        def fail(generation,projection,**kwargs):
            self.build(generation,projection);raise RuntimeError('fixture interrupted import')
        with self.assertRaisesRegex(RuntimeError,'interrupted import'):self.refresh(fail)
        self.assert_empty()

    def test_source_change_during_build_or_publish_never_leaves_active_cache(self):
        def change(generation,projection,**kwargs):
            result=self.build(generation,projection);self.update('Changed during build');return result
        with self.assertRaisesRegex(GbrainError,'projection_stale'):self.refresh(change)
        self.assert_empty()
        original=self.cache.active
        def changed_after_publication():
            result=original()
            if result:self.update('Changed after publication')
            return result
        with patch.object(self.cache,'active',side_effect=changed_after_publication):
            with self.assertRaisesRegex(GbrainError,'projection_stale'):self.refresh()
        self.assert_empty()

    def test_noop_source_change_is_rechecked_and_withdrawn(self):
        self.refresh();calls=[0]
        def current():
            calls[0]+=1
            if calls[0]==2:self.update('Changed during no-op')
            return self.current()
        with self.assertRaisesRegex(GbrainError,'projection_stale'):
            self.cache.refresh(current=current)
        self.assert_empty()

    def test_cancellation_and_never_published_orphans_are_cleaned(self):
        self.refresh();self.update('Changed')
        stopped=[False]
        def check():
            if stopped[0]:raise RuntimeError('fixture cancellation')
        def build(generation,projection,**kwargs):
            result=self.build(generation,projection);stopped[0]=True;return result
        with self.assertRaisesRegex(RuntimeError,'fixture cancellation'):self.refresh(build,check=check)
        self.assert_empty()
        orphan=self.cache.generations/('g'+'a'*32);orphan.mkdir(mode=0o700)
        (orphan/'generation.json').write_text(json.dumps({'kind':managed.KIND,'owner':self.cache.owner['owner'],'generation':orphan.name}))
        (orphan/'private-audit.txt').write_text('Unpublished retained label')
        self.refresh();self.assertFalse(orphan.exists())

    def test_withdraw_recovers_mid_deletion_without_a_generation_marker(self):
        self.refresh();generation=self.cache._generation(self.cache.active()['generation'])
        def interrupted(path):
            self.assertTrue((self.root/'retiring.json').exists())
            self.assertIn(path.name,json.loads((self.root/'retiring.json').read_text())['generations'])
            (path/'generation.json').unlink();raise RuntimeError('fixture mid-removal crash')
        with patch('alden_gbrain_cache.shutil.rmtree',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'mid-removal'):self.cache.withdraw()
        self.assertFalse((generation/'generation.json').exists());self.assertTrue(generation.exists())
        self.assertEqual(self.cache.withdraw()['removed_generations'],1)
        self.assert_empty()
        self.assertEqual(json.loads((self.root/'retiring.json').read_text())['generations'],[])

    def test_unknown_or_symlinked_directory_is_never_followed_or_deleted(self):
        self.refresh();generation=self.cache._generation(self.cache.active()['generation'])
        outside=self.base/'outside';outside.mkdir();(outside/'keep.txt').write_text('external bytes')
        (generation/'external').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(GbrainError,'generation_unsafe'):self.cache.withdraw()
        self.assertEqual((outside/'keep.txt').read_text(),'external bytes')
        self.assertTrue(generation.exists());self.assertFalse((self.root/'active.json').exists())

    def test_changed_runtime_is_rejected_before_initialization(self):
        (self.root/'runtime/gbrain').write_bytes(b'changed runtime')
        generation=self.cache.generations/('g'+'b'*32);generation.mkdir(mode=0o700)
        with patch('alden_gbrain_cache.subprocess.Popen') as popen:
            with self.assertRaises(GbrainError):
                self.cache._build(generation,GbrainProjection(self.current(),['one']),fd=1,check=lambda:None)
            popen.assert_not_called()

    def test_engine_readback_uses_the_canonical_top_level_title(self):
        projection=GbrainProjection(self.current(),['one'])
        generation=self.cache.generations/('g'+'c'*32);generation.mkdir(mode=0o700)
        def command(_generation,args,**kwargs):
            if args[:2]==['schema','validate']:return b'valid manifest'
            if args[:2]==['schema','use']:return b'activated'
            if args[:2]==['schema','active']:return b'{"pack":"alden-scoped-graph"}'
            if args[0]=='put-pages':
                batch=json.loads(args[2])
                return json.dumps({'source_id':'default','state':'committed','terminal':True,
                    'counts':{'total':len(batch),'committed':len(batch),'pending':0,'failed':0},
                    'pages':[{'index':i,'slug':p['slug'],'state':'committed','embedding_state':'disabled',
                              'revision':'opaque-page-revision-'+str(i)} for i,p in enumerate(batch)]}).encode()
            if args[0]=='get':
                node=projection.by_slug[args[1]]
                fm={'alden_node_id':node['id'],'alden_snapshot':projection.fingerprint,
                    'alden_source_target':node['source_target']}
                if 'source_version' in node:fm['alden_source_version']=node['source_version']
                revision='opaque-page-revision-'+str(next(i for i,p in enumerate(projection.pages()) if p['slug']==args[1]))
                return json.dumps({'slug':args[1],'source_id':'default','revision':revision,'title':node['label'],
                                   'frontmatter':fm}).encode()
            return b'{"ok":true}'
        class Engine:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def graph(self,*args,**kwargs):
                return [{'from_slug':link['from'],'to_slug':link['to'],'from_source_id':'default',
                    'to_source_id':'default','link_type':link['link_type'],'context':link['context'],'depth':1}
                    for link in projection.links()]
        with patch.object(self.cache,'_command',side_effect=command), patch('alden_gbrain_cache.validate_runtime'), \
             patch('alden_gbrain_cache.GbrainReadClient',return_value=Engine()):
            result=self.cache._build(generation,projection,fd=1,check=lambda:None)
        self.assertEqual(result,{'pages':2,'links':1,'verified_queries':2})

    def test_child_keeps_reader_lease_after_its_parent_exits(self):
        release=self.base/'release-child';pid_file=self.base/'child-pid'
        script="""import os,subprocess,sys
from alden_gbrain_cache import ManagedGbrainCache
from alden_gbrain_client import _Stdio
c=ManagedGbrainCache(sys.argv[1],collection_root=sys.argv[2],projects=['one'])
with c.lease(exclusive=False) as fd:
 code='import os,sys,time;fd=int(sys.argv[1]);os.fstat(fd);path=sys.argv[2];stop=time.monotonic()+4\\nwhile not os.path.exists(path) and time.monotonic()<stop: time.sleep(.01)'
 peer=_Stdio([sys.executable,'-B','-c',code,str(fd),sys.argv[3]],cwd=sys.argv[1],env={'PATH':os.environ.get('PATH','')},check=lambda:None,timeout=5,pass_fds=(fd,))
 open(sys.argv[4],'w').write(str(peer.proc.pid))
 os._exit(0)
"""
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(Path(__file__).resolve().parents[1]/'scripts')}
        parent=subprocess.run([sys.executable,'-B','-c',script,str(self.root),str(self.source),str(release),str(pid_file)],
                              env=env,capture_output=True,text=True,timeout=5)
        self.assertEqual(parent.returncode,0,parent.stderr);self.assertTrue(pid_file.exists())
        class Cancelled(Exception):pass
        until=time.monotonic()+.1
        def check():
            if time.monotonic()>=until:raise Cancelled()
        try:
            with self.assertRaises(Cancelled):
                with self.cache.lease(exclusive=True,check=check):self.fail('orphan child lost its lease')
        finally:
            release.write_text('release')
        with self.cache.lease(exclusive=True):pass


if __name__=='__main__':
    unittest.main()

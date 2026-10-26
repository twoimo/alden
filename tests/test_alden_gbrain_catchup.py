"""Opt-in closed checkpoints: real synthetic collections and inert engines only."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock
import uuid

from tests import test_alden_gbrain_delta as fixture
import alden_gbrain_import as full
import alden_gbrain_delta as delta
import alden_collection_delta as source_delta
from alden_collection import CollectionStore, identity
from alden_collection_export import ExportError, export_graph
from alden_gbrain_cache import _write, _read, _UnreapedWorker
from alden_abort import AldenCancelled


class CatchupTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('subprocess.Popen',side_effect=AssertionError('synthetic engines only')))
        self.d=fixture.DeltaTests();self.d.setUp();self.addCleanup(self.d.doCleanups)
        self.f=self.d.f;self.cache=self.f.cache;self.sequence=0

    def change(self):
        self.sequence+=1
        self.f.store.ingest(self.f.target,[{'original_id':'a','label':'Changed '+str(self.sequence),
            'text':'synthetic version '+str(self.sequence)}])

    def once(self,action):
        fired=False
        def callback():
            nonlocal fired
            if not fired:fired=True;action()
        return callback

    def closed_existing(self):
        """Set up an unpublished, cleanly closed native import, not a marker."""
        with self.cache.lease(exclusive=True) as fd:
            generation=self.cache.generations/('g'+uuid.uuid4().hex);generation.mkdir(mode=0o700)
            _write(generation/'generation.json',{'kind':self.cache.KIND,'owner':self.cache.owner['owner'],
                'generation':generation.name})
            export_graph(self.f.root,['one'],generation/'source-archive')
            projection=full.FullProjection.prepare(generation,policy=full.STABLE_POLICY)
            self.cache._build(generation,projection,fd=fd,check=lambda:None)
            _write(generation/'projection.json',projection.manifest())
        return full.FullProjection.open(generation)

    def pending(self):
        callback=self.once(self.change)
        def engine(*args,**kwargs):
            value=self.f.engine(*args,**kwargs);value.on_graph=callback;return value
        with patch.object(full,'GbrainImportClient',side_effect=engine):
            result=self.cache.refresh(incremental=True,catch_up=True)
        self.assert_private(result,'pending')
        return full.FullProjection.open(self.cache._generation(result['generation']))

    def assert_private(self,result,state):
        self.assertEqual(result['state'],state)
        self.assertIs(result['published'],False)
        self.assertIs(result['current_source_verified'],False)
        self.assertIsNone(self.cache.active())
        generation=self.cache._generation(result['generation'])
        self.assertFalse((generation/'ready.json').exists())
        self.assertTrue((generation/'closed-verified.json').exists())
        self.assertEqual(_read(self.cache.root/'pending.json')['generation'],generation.name)
        with self.assertRaisesRegex(full.GbrainError,'gbrain_cache_unavailable'):self.f.query()

    def assert_purged(self):
        self.assertIsNone(self.cache.active())
        self.assertFalse((self.cache.root/'pending.json').exists())
        self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_stage_reverifies_existing_closed_engine_despite_old_asof_receipt(self):
        projection=self.closed_existing();self.change()
        # A claimed old acceptance is neither read nor trusted by staging.
        _write(projection.root/'asof-receipt.json',{'verified':True,'pages':999})
        result=self.cache.stage_existing(projection.root.name)
        self.assert_private(result,'staged')
        self.assertEqual(result['verified_queries'],projection.counts()['pages'])
        restored=full.FullGbrainCache(self.cache.root,collection_root=self.f.root,projects=['one'])
        with restored.lease(exclusive=True):
            self.assertEqual(restored._pending(lambda:None).fingerprint,projection.fingerprint)

    def test_stage_does_not_trust_even_a_valid_marker_after_engine_corruption(self):
        projection=self.pending()
        self.f.engines[projection.root].pages.pop(next(iter(self.f.engines[projection.root].pages)))
        with self.assertRaisesRegex(full.GbrainError,'enumeration_invalid'):
            self.cache.stage_existing(projection.root.name)
        self.assert_purged()

    def test_stage_rejects_incomplete_revision_and_never_saves_marker(self):
        projection=self.closed_existing()
        with full.closing(full._db(projection.path,True)) as db:
            db.execute('UPDATE pages SET revision=NULL');db.commit()
        _write(projection.root/'projection.json',projection.manifest())
        with self.assertRaises(full.GbrainError):self.cache.stage_existing(projection.root.name)
        self.assert_purged()

    def test_stage_rejects_coherent_manifest_index_engine_tampering(self):
        projection=self.closed_existing()
        page=next(p for p in projection.pages() if p['kind']=='record')
        node=json.loads(page['payload']);old=node['label'];node['label']='Forged title'
        page['payload']=full.encoded(node).decode()
        page['content']=page['content'].replace(json.dumps(old),json.dumps(node['label']))
        page['content']=page['content'].replace(full._digest(json.loads(next(
            p['payload'] for p in projection.pages() if p['slug']==page['slug']))),full._digest(node))
        saved=self.f.engines[projection.root].put([page],'synthetic-forgery')
        with full.closing(full._db(projection.path,True)) as db:
            db.execute('UPDATE pages SET payload=?,content=?,revision=? WHERE slug=?',
                (page['payload'],page['content'],saved['pages'][0]['revision'],page['slug']));db.commit()
        _write(projection.root/'projection.json',projection.manifest())
        with self.assertRaisesRegex(full.GbrainError,'source_index_mismatch'):
            self.cache.stage_existing(projection.root.name)
        self.assert_purged()

    def test_stage_cannot_take_over_active_generation(self):
        self.d.initial();before=self.cache.active()
        with self.assertRaisesRegex(full.GbrainError,'stage_active'):
            self.cache.stage_existing(before['generation'])
        self.assertEqual(self.cache.active(),before);self.assertTrue(self.f.query()['ok'])

    def test_full_pending_then_changed_delta_pending_then_quiet_publication(self):
        old=self.pending();first=old.fingerprint
        original=delta.clone_closed
        def clone(*args,**kwargs):
            result=original(*args,**kwargs)
            self.d.engine.engine.on_graph=self.once(self.change)
            return result
        with patch.object(delta,'clone_closed',side_effect=clone):
            result=self.cache.refresh(incremental=True,catch_up=True)
        self.assert_private(result,'pending');self.assertEqual(result['update_mode'],'engine_delta')
        self.assertEqual(result['engine_writes']['pages_put'],1)
        self.assertNotEqual(result['fingerprint'],first);self.assertFalse(old.root.exists())
        second=self.cache._generation(result['generation'])
        result=self.cache.refresh(incremental=True,catch_up=True)
        self.assertEqual(result['state'],'published');self.assertEqual(result['update_mode'],'engine_delta')
        self.assertEqual(result['source_update']['mode'],'journal_delta')
        self.assertFalse(second.exists());self.assertFalse((self.cache.root/'pending.json').exists())
        self.assertEqual(next(n['label'] for n in self.f.query('a')['nodes'] if n['id']==identity('graph','a')),'Changed 2')

    def test_complete_stale_export_can_build_private_but_never_publish(self):
        original=full.export_graph
        def export(*args,**kwargs):
            result=original(*args,**kwargs);self.change();return result
        with patch.object(full,'export_graph',side_effect=export):
            result=self.cache.refresh(incremental=True,catch_up=True)
        self.assert_private(result,'pending');self.assertEqual(result['verified_queries'],4)
        self.assertEqual(result['update_mode'],'full_rebuild')

    def test_advance_at_ready_or_active_is_demoted_to_private_pending(self):
        original=full.verify_current_export
        for boundary in ('ready','active'):
            with self.subTest(boundary=boundary):
                changed=False
                def current(root,projects,archive,**kwargs):
                    nonlocal changed
                    visible=(self.cache.root/'active.json').exists() if boundary=='active' else (archive.parent/'ready.json').exists()
                    if visible and not changed:changed=True;self.change()
                    return original(root,projects,archive,**kwargs)
                with patch.object(full,'verify_current_export',side_effect=current):
                    result=self.cache.refresh(incremental=True,catch_up=True)
                self.assertTrue(changed);self.assert_private(result,'pending')
                self.cache.withdraw()

    def test_stale_error_without_observed_content_advance_is_not_authorization(self):
        with patch.object(full,'verify_current_export',side_effect=ExportError('collection_export_stale')):
            with self.assertRaisesRegex(full.GbrainError,'source_advance_unconfirmed'):
                self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged();self.assertEqual(self.f.commands,[])

    def test_normal_refresh_never_uses_pending_as_delta_base(self):
        old=self.pending()
        result=self.cache.refresh(incremental=True)
        self.assertEqual(result['state'],'published');self.assertEqual(result['update_mode'],'full_rebuild')
        self.assertFalse(old.root.exists());self.assertFalse((self.cache.root/'pending.json').exists())
        self.assertTrue(self.f.query()['ok'])

    def test_normal_refresh_still_purges_on_advance_even_with_old_pending(self):
        self.pending();original=self.cache._build
        def build(*args,**kwargs):
            result=original(*args,**kwargs);self.change();return result
        with patch.object(self.cache,'_build',side_effect=build):
            with self.assertRaisesRegex(ExportError,'stale'):self.cache.refresh(incremental=True)
        self.assert_purged()

    def test_incomplete_new_export_retains_only_previously_closed_base(self):
        old=self.pending()
        # Neither a rejected incremental export nor a rejected complete fallback
        # can create a checkpoint. Only the previously closed proof may survive.
        with patch.object(source_delta,'export_incremental',side_effect=ExportError('collection_export_source_changed')), \
                patch.object(full,'export_graph',side_effect=ExportError('collection_export_source_changed')):
            result=self.cache.refresh(incremental=True,catch_up=True)
        self.assert_private(result,'pending');self.assertEqual(result['generation'],old.root.name)
        self.assertEqual(list(self.cache.generations.iterdir()),[old.root])

    def test_incomplete_first_export_never_creates_pending(self):
        with patch.object(full,'export_graph',side_effect=ExportError('collection_export_source_changed')):
            with self.assertRaises(ExportError):self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged()

    def test_failed_partial_delta_cannot_resurrect_retired_base(self):
        old=self.pending();self.d.failure='put'
        with self.assertRaisesRegex(full.GbrainError,'control_failure'):
            self.cache.refresh(incremental=True,catch_up=True)
        self.assertFalse(old.root.exists());self.assert_purged()

    def test_marker_and_pointer_bindings_are_strict(self):
        for key,value in [('owner','0'*32),('fingerprint','0'*64),('projects',['outside']),
                ('source_root_sha256','0'*64),('cache_root_sha256','0'*64),
                ('manifest_sha256','0'*64),('runtime_receipt_sha256','0'*64),('policy',full.POLICY),
                ('generation','g'+'0'*32),('marker_sha256','0'*64)]:
            with self.subTest(key=key):
                self.pending();path=self.cache.root/'pending.json';record=_read(path);record[key]=value;_write(path,record)
                with self.assertRaises((full.GbrainError,FileNotFoundError)):
                    self.cache.refresh(incremental=True,catch_up=True)
                self.assert_purged()

    def test_forged_acceptance_cannot_be_approved_by_rehashing_pointer(self):
        for value in (True,3,None):
            with self.subTest(value=value):
                projection=self.pending();path=projection.root/'closed-verified.json'
                record=_read(path);record['acceptance']['verified_queries']=value;_write(path,record)
                _write(self.cache.root/'pending.json',{**record,'marker_sha256':full._sha_file(path,lambda:None)})
                with self.assertRaisesRegex(full.GbrainError,'checkpoint_incomplete'):
                    self.cache.refresh(incremental=True,catch_up=True)
                self.assert_purged()

    def test_manifest_tampering_purges_checkpoint(self):
        projection=self.pending();path=projection.root/'projection.json';record=_read(path)
        record['counts']['pages']+=1;_write(path,record)
        with self.assertRaisesRegex(full.GbrainError,'projection_changed'):
            self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged()

    def test_runtime_tampering_purges_checkpoint(self):
        self.pending();binary=self.cache.root/'runtime/gbrain';binary.write_bytes(b'broken')
        with self.assertRaises(full.GbrainError):self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged()

    def test_denial_purges_pending_instead_of_falling_back(self):
        self.pending()
        self.f.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        with self.assertRaisesRegex(ValueError,'scope_invalid'):self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged()

    def test_authority_or_file_or_stream_loss_is_not_content_advance(self):
        original=full._stamp
        for field in ('authority','file_identity','stream_id'):
            with self.subTest(field=field):
                self.pending()
                def changed(*args,**kwargs):
                    value=original(*args,**kwargs)
                    if field=='stream_id':value['checkpoint']['stream_id']='stream:'+'0'*64
                    elif field=='file_identity':value[field]=[0,0]
                    else:value[field]='0'*64
                    return value
                with patch.object(full,'_stamp',side_effect=changed):
                    with self.assertRaisesRegex(full.GbrainError,'source_binding_changed'):
                        self.cache.refresh(incremental=True,catch_up=True)
                self.assert_purged()

    def guarded_factory(self):
        # Use the collection's real protected-source access rules, with only
        # synthetic callbacks and metadata; no messenger source is opened.
        return CollectionStore.open_existing(self.f.root,source_access=lambda *a:False,
            source_revision=lambda *a:'a'*64,source_stream=lambda *a:'b'*64)

    def test_withholding_hidden_behind_stale_error_is_terminal(self):
        with self.f.store.database() as db:db.execute("UPDATE targets SET platform='telegram'")
        allow=lambda:CollectionStore.open_existing(self.f.root,source_access=lambda *a:True,
            source_revision=lambda *a:'a'*64,source_stream=lambda *a:'b'*64)
        projection=self.closed_existing_guarded(allow)
        with self.assertRaisesRegex(ExportError,'source_withheld'):
            self.cache.stage_existing(projection.root.name,store_factory=self.guarded_factory)
        self.assert_purged()

    def test_withholding_after_complete_engine_readback_cannot_be_pending(self):
        with self.f.store.database() as db:db.execute("UPDATE targets SET platform='telegram'")
        allowed=True
        def factory():
            return CollectionStore.open_existing(self.f.root,source_access=lambda *a:allowed,
                source_revision=lambda *a:('a' if allowed else 'c')*64,source_stream=lambda *a:'b'*64)
        def withhold():
            nonlocal allowed
            allowed=False
        def engine(*args,**kwargs):
            value=self.f.engine(*args,**kwargs);value.on_graph=self.once(withhold);return value
        with patch.object(full,'GbrainImportClient',side_effect=engine):
            with self.assertRaisesRegex(ExportError,'source_withheld'):
                self.cache.refresh(incremental=True,catch_up=True,store_factory=factory)
        self.assert_purged()

    def test_runtime_change_after_readback_cannot_be_pending(self):
        def change_runtime():
            (self.cache.root/'runtime/gbrain').write_bytes(b'corrupt runtime')
            self.change()
        def engine(*args,**kwargs):
            value=self.f.engine(*args,**kwargs);value.on_graph=self.once(change_runtime);return value
        with patch.object(full,'GbrainImportClient',side_effect=engine):
            with self.assertRaises(full.GbrainError):self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged()

    def test_real_authority_change_after_complete_readback_cannot_be_pending(self):
        def change_authority():
            self.f.store.register(platform='graph',original_id='other',kind='source',label='Other',projects=['one'])
        def engine(*args,**kwargs):
            value=self.f.engine(*args,**kwargs);value.on_graph=self.once(change_authority);return value
        with patch.object(full,'GbrainImportClient',side_effect=engine):
            with self.assertRaisesRegex(full.GbrainError,'source_binding_changed'):
                self.cache.refresh(incremental=True,catch_up=True)
        self.assert_purged()

    def closed_existing_guarded(self,factory):
        with patch.object(full,'export_graph',side_effect=lambda *a,**k:export_graph(*a,**{**k,'store_factory':factory})):
            self.cache.refresh(incremental=True,store_factory=factory)
        projection=self.f.projection()
        (self.cache.root/'active.json').unlink();(projection.root/'ready.json').unlink()
        return projection

    def test_cancel_before_or_during_stage_purges_owned_state(self):
        for during in (False,True):
            with self.subTest(during=during):
                projection=self.closed_existing();cancelled=False
                def cancel():
                    nonlocal cancelled
                    cancelled=True
                if during:self.f.engines[projection.root].on_graph=cancel
                else:cancel()
                def check():
                    if cancelled:raise AldenCancelled('alden_global_abort')
                with self.assertRaises(AldenCancelled):self.cache.stage_existing(projection.root.name,check=check)
                self.assert_purged()

    def test_cancel_before_catchup_purges_pending(self):
        self.pending()
        with self.assertRaises(AldenCancelled):
            self.cache.refresh(incremental=True,catch_up=True,check=lambda:(_ for _ in ()).throw(AldenCancelled('cancel')))
        self.assert_purged()

    def test_unreaped_stage_preserves_recovery_without_new_acceptance(self):
        projection=self.closed_existing()
        class Unreaped(fixture.DeltaEngine):
            def __exit__(self,*args):raise _UnreapedWorker('gbrain_cache_worker_not_reaped')
        with patch.object(full,'GbrainImportClient',return_value=Unreaped(self.f.engines[projection.root])):
            with self.assertRaises(_UnreapedWorker):self.cache.stage_existing(projection.root.name)
        self.assertTrue(projection.root.exists());self.assertIsNone(self.cache.active())
        self.assertFalse((projection.root/'closed-verified.json').exists())
        self.assertFalse((self.cache.root/'pending.json').exists())

    def test_reaped_unclean_stage_is_purged_and_not_checkpointed(self):
        projection=self.closed_existing()
        class Unclean(fixture.DeltaEngine):
            def __exit__(self,*args):raise full.GbrainError('gbrain_unclean_shutdown')
        with patch.object(full,'GbrainImportClient',return_value=Unclean(self.f.engines[projection.root])):
            with self.assertRaisesRegex(full.GbrainError,'unclean_shutdown'):
                self.cache.stage_existing(projection.root.name)
        self.assert_purged()

    def test_unreaped_delta_preserves_new_generation_but_not_retired_base(self):
        old=self.pending()
        original=delta.clone_closed
        class Unreaped(fixture.DeltaEngine):
            def __exit__(self,*args):raise _UnreapedWorker('gbrain_cache_worker_not_reaped')
        def clone(*args,**kwargs):
            result=original(*args,**kwargs);self.d.engine=Unreaped(self.d.engine.engine);return result
        with patch.object(delta,'clone_closed',side_effect=clone):
            with self.assertRaises(_UnreapedWorker):self.cache.refresh(incremental=True,catch_up=True)
        self.assertFalse(old.root.exists());self.assertEqual(len(list(self.cache.generations.iterdir())),1)
        self.assertFalse((self.cache.root/'pending.json').exists());self.assertIsNone(self.cache.active())

    def test_withdraw_and_generation_removal_retire_pending_pointer(self):
        projection=self.pending()
        with self.cache.lease(exclusive=True):self.cache._remove(projection.root.name)
        self.assert_purged()
        self.pending();self.assertEqual(self.cache.withdraw()['state'],'withdrawn');self.assert_purged()

    def test_retiring_generation_is_not_resurrected_by_stage(self):
        projection=self.closed_existing()
        _write(self.cache.root/'retiring.json',{'owner':self.cache.owner['owner'],'generations':[projection.root.name]})
        with self.assertRaisesRegex(full.GbrainError,'checkpoint_invalid'):
            self.cache.stage_existing(projection.root.name)
        self.assert_purged()

    def test_invalid_options_leave_existing_state_untouched(self):
        self.pending();before=(self.cache.root/'pending.json').read_bytes()
        for options in ({'catch_up':True},{'incremental':True,'catch_up':1},{'incremental':1,'catch_up':True}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(full.GbrainError,'options_invalid'):self.cache.refresh(**options)
                self.assertEqual((self.cache.root/'pending.json').read_bytes(),before)


class NativeClosureTests(unittest.TestCase):
    def test_close_exception_cannot_hide_unreaped_process(self):
        client=fixture.NATIVE_IMPORT_CLIENT.__new__(fixture.NATIVE_IMPORT_CLIENT)
        client.transport=Mock();client.transport.close.side_effect=OSError('close failed')
        client.transport.proc.poll.return_value=None
        with self.assertRaises(_UnreapedWorker):client.__exit__(None,None,None)
        self.assertIsNotNone(client.transport)

    def test_clean_and_reaped_failure_shutdowns_are_distinct(self):
        for code in (0,1):
            with self.subTest(code=code):
                client=fixture.NATIVE_IMPORT_CLIENT.__new__(fixture.NATIVE_IMPORT_CLIENT)
                client.transport=Mock();client.transport.close.return_value=code
                client.transport.proc.poll.return_value=code
                if code:
                    with self.assertRaisesRegex(full.GbrainError,'unclean_shutdown'):client.__exit__(None,None,None)
                else:client.__exit__(None,None,None)
                self.assertIsNone(client.transport)

    def test_unknown_reap_status_preserves_recovery(self):
        client=fixture.NATIVE_IMPORT_CLIENT.__new__(fixture.NATIVE_IMPORT_CLIENT)
        client.transport=Mock();client.transport.close.return_value=0
        client.transport.proc.poll.side_effect=OSError('wait status unavailable')
        with self.assertRaises(_UnreapedWorker):client.__exit__(None,None,None)
        self.assertIsNotNone(client.transport)


class CatchupCliTests(unittest.TestCase):
    def test_stage_and_catchup_dispatch_with_explicit_nonpublished_output(self):
        for extra,state in ((['stage','--generation','g'+'a'*32],'staged'),
                (['refresh','--incremental','--catch-up'],'pending')):
            output=io.StringIO();manager=Mock()
            result={'ok':True,'state':state,'published':False,'current_source_verified':False}
            manager.stage_existing.return_value=result;manager.refresh.return_value=result
            with patch('sys.argv',['import',*extra,'--root','/unused','--state-root','/unused','--project','one']), \
                    patch('alden_knowledge_mcp.KnowledgeServer'),patch.object(full,'FullGbrainCache',return_value=manager),redirect_stdout(output):
                full.main()
            self.assertEqual(json.loads(output.getvalue()),result)
            if state=='pending':
                self.assertIs(manager.refresh.call_args.kwargs['catch_up'],True)
                self.assertIs(manager.refresh.call_args.kwargs['incremental'],True)
            else:self.assertEqual(manager.stage_existing.call_args.args,('g'+'a'*32,))

    def test_invalid_catchup_and_stage_combinations_stop_before_source_open(self):
        for extra in (['refresh','--catch-up'],['stage'],['query','--generation','g'+'a'*32],
                ['stage','--generation','g'+'a'*32,'--incremental']):
            with self.subTest(extra=extra),patch('sys.argv',['import',*extra,'--root','/unused','--state-root','/unused','--project','one']), \
                    patch('alden_knowledge_mcp.KnowledgeServer',side_effect=AssertionError('no source open')),redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:full.main()
                self.assertEqual(raised.exception.code,2)


if __name__=='__main__':unittest.main()

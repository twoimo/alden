"""Selective engine writes with full authority/publication/readback contracts."""
import copy
import json
import shutil
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_alden_gbrain_import as fixture
import alden_gbrain_delta as delta
import alden_gbrain_import as full
from alden_gbrain_client import GbrainError
from alden_collection import identity
from alden_collection_export import ExportError
from alden_gbrain_cache import _UnreapedWorker
from alden_gbrain_watch import GbrainSourceWatcher

NATIVE_IMPORT_CLIENT=full.GbrainImportClient


class DeltaEngine:
    def __init__(self,engine):self.engine=engine;self.puts=[];self.unlinks=[];self.adds=[];self.failure=None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def __getattr__(self,key):return getattr(self.engine,key)
    def put(self,pages,request):
        for page in pages:
            old=self.engine.pages.get(page['slug'])
            if old is not None and old['revision']!=page['revision']:raise GbrainError('gbrain_delta_revision_conflict')
        self.puts.extend(p['slug'] for p in pages)
        if self.failure=='put':raise GbrainError('gbrain_delta_control_failure')
        return self.engine.put(pages,request)
    def unlink(self,row,request):
        key=(row['a'],row['b'],row['verb']);self.unlinks.append(key)
        removed=int(key in self.engine.links)
        if self.failure!='unlink':
            self.engine.links.pop(key,None);self.engine.outgoing.get(row['a'],set()).discard(key)
            self.engine.incoming.get(row['b'],set()).discard(key)
        if self.failure=='unlink-count':removed=True
        return {'status':'ok','removed':removed}
    def link(self,row,request):
        self.adds.append((row['a'],row['b'],row['verb']))
        return self.engine.link(row,request)


class DeltaTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.FullImportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.cache=self.f.cache;self.engine=None;self.failure=None
        real=delta.clone_closed
        def clone(cache,previous,projection,generation,check):
            plan=real(cache,previous,projection,generation,check)
            self.f.engines[generation]=copy.deepcopy(self.f.engines[previous.root])
            self.engine=DeltaEngine(self.f.engines[generation]);self.engine.failure=self.failure
            return plan
        self.enterContext(patch.object(delta,'clone_closed',side_effect=clone))
        self.enterContext(patch.object(delta,'DeltaImportClient',side_effect=lambda *a,**k:self.engine))
        def command(generation,args,**kwargs):
            if args[0]!='delete':return self.f.command(generation,args,**kwargs)
            self.assertTrue(kwargs['write']);self.assertIn('--purge',args)
            engine=self.f.engines[generation];slug=args[1]
            self.assertEqual(engine.pages[slug]['revision'],args[args.index('--expected-revision')+1])
            if self.failure!='purge':
                engine.pages.pop(slug)
                for key in list(engine.links):
                    if slug in key[:2]:
                        engine.links.pop(key);engine.outgoing.get(key[0],set()).discard(key)
                        engine.incoming.get(key[1],set()).discard(key)
            return json.dumps({'status':'purged','slug':slug,'source_id':'default'}).encode()
        self.enterContext(patch.object(self.cache,'_command',side_effect=command))

    def initial(self):
        value=self.cache.refresh(incremental=True)
        self.assertEqual(value['update_mode'],'full_rebuild')
        self.assertEqual(self.f.projection().policy,full.STABLE_POLICY)
        return self.f.projection()
    def change(self):self.f.store.ingest(self.f.target,[{'original_id':'a','label':'Changed a','text':'new fixture'}])
    def remove(self):self.f.store.ingest(self.f.target,[],retractions=[{'platform':'graph','original_id':'c'}])

    def test_legacy_generation_is_readable_and_upgrades_by_a_full_build(self):
        self.cache.refresh();old=self.f.projection();self.assertEqual(old.policy,full.POLICY)
        self.assertTrue(self.f.query()['ok'])
        with patch.object(delta,'clone_closed',side_effect=AssertionError('must not reuse legacy')):
            self.initial()
        self.assertFalse(old.root.exists());self.assertTrue(self.f.query()['ok'])

    def test_unchanged_stable_refresh_preserves_generation_without_a_copy(self):
        old=self.initial()
        with patch.object(delta,'clone_closed',side_effect=AssertionError('no copy for unchanged input')):
            value=self.cache.refresh(incremental=True)
        self.assertEqual(value['state'],'unchanged');self.assertEqual(self.f.projection().root,old.root)

    def test_one_changed_record_writes_one_page_and_preserves_unaffected_revisions(self):
        old=self.initial();before={p['slug']:p for p in old.pages()};self.change()
        value=self.cache.refresh(incremental=True);new=self.f.projection()
        self.assertEqual(value['update_mode'],'engine_delta');self.assertEqual(value['engine_writes']['pages_put'],1)
        self.assertEqual(value['source_update']['mode'],'journal_delta')
        self.assertEqual(value['source_update']['archive_rows']['nodes_put'],1)
        self.assertEqual(value['index_updates'],{'mode':'index_delta','pages_put':1,'pages_deleted':0,'links_put':1,'links_deleted':0})
        changed=new.slug(identity('graph','a'))
        self.assertEqual(self.engine.puts,[changed]);self.assertFalse(old.root.exists())
        for page in new.pages():
            if page['slug']!=changed:self.assertEqual(page['revision'],before[page['slug']]['revision'])
        self.assertFalse((new.root/'delta-base.sqlite3').exists());self.assertTrue(self.f.query()['ok'])
        self.assertEqual(json.loads((new.root/'delta.json').read_text())['base_fingerprint'],old.fingerprint)

    def test_addition_puts_only_the_new_page_and_new_membership(self):
        self.initial();self.f.store.ingest(self.f.target,[{'original_id':'d','label':'New d','text':'fixture'}])
        value=self.cache.refresh(incremental=True)
        self.assertEqual(value['engine_writes'],{'pages_put':1,'pages_purged':0,'links_added':1,'links_removed':0})
        self.assertEqual(value['pages'],5);self.assertTrue(self.f.query('d')['ok'])

    def test_retraction_purges_the_page_and_all_incident_links(self):
        old=self.initial();self.remove();value=self.cache.refresh(incremental=True)
        self.assertEqual(value['engine_writes']['pages_purged'],1)
        self.assertEqual(value['engine_writes']['pages_put'],0)
        self.assertEqual(value['pages'],3);self.assertEqual(value['links'],3)
        self.assertNotIn(old.slug(identity('graph','c')),self.engine.pages)
        self.assertTrue(self.f.query()['ok'])

    def test_removed_relation_between_surviving_pages_is_explicitly_unlinked(self):
        self.initial()
        with self.f.store.database() as db:
            db.execute("UPDATE relations SET active=0 WHERE source=?",(identity('graph','a'),))
        value=self.cache.refresh(incremental=True)
        self.assertEqual(value['engine_writes'],{'pages_put':0,'pages_purged':0,'links_added':0,'links_removed':1})
        self.assertEqual(value['links'],4);self.assertEqual(len(self.engine.unlinks),1)

    def test_unconfirmed_purge_cannot_publish_or_keep_the_old_generation(self):
        self.initial();self.remove();self.failure='purge'
        with self.assertRaisesRegex(GbrainError,'enumeration_invalid'):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_incorrect_unlink_count_is_not_accepted(self):
        self.initial();self.failure='unlink-count'
        with self.f.store.database() as db:db.execute('UPDATE relations SET active=0')
        with self.assertRaisesRegex(GbrainError,'unlink_unconfirmed'):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active())

    def test_reported_unlink_without_actual_removal_is_detected_by_full_readback(self):
        self.initial();self.failure='unlink'
        with self.f.store.database() as db:db.execute('UPDATE relations SET active=0')
        with self.assertRaisesRegex(GbrainError,'result_unbound'):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active())

    def test_failed_delta_put_discards_both_generations_without_rollback(self):
        old=self.initial();self.change();self.failure='put'
        with self.assertRaisesRegex(GbrainError,'control_failure'):self.cache.refresh(incremental=True)
        self.assertFalse(old.root.exists());self.assertIsNone(self.cache.active())
        self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_forced_rebuild_does_not_reuse_a_previous_engine(self):
        self.initial();self.change()
        with patch.object(delta,'clone_closed',side_effect=AssertionError('rebuild must be independent')):
            value=self.cache.refresh(incremental=True,rebuild=True)
        self.assertEqual(value['update_mode'],'full_rebuild')

    def test_authority_change_does_not_clone_old_data(self):
        self.initial()
        self.f.store.register(platform='graph',original_id='other',kind='source',label='Other',projects=['one'])
        with patch.object(delta,'clone_closed',side_effect=AssertionError('changed authority')):
            value=self.cache.refresh(incremental=True)
        self.assertEqual(value['update_mode'],'full_rebuild')

    def test_non_incremental_caller_preserves_the_stable_format(self):
        self.initial();self.change();value=self.cache.refresh()
        self.assertEqual(self.f.projection().policy,full.STABLE_POLICY);self.assertTrue(self.f.query()['ok'])
        self.assertNotIn('engine_writes',value)

    def test_unknown_projection_policy_is_rejected(self):
        old=self.initial();p=old.root/'projection.json';value=json.loads(p.read_text());value['policy']='unknown'
        p.write_text(json.dumps(value))
        with self.assertRaisesRegex(GbrainError,'policy_invalid'):full.FullProjection.open(old.root)

    def test_copy_rejects_a_symlink_and_leaves_no_accepted_pointer(self):
        old=self.initial();(old.root/'brain.pglite/unsafe').symlink_to(self.f.root)
        self.change()
        with self.assertRaises(GbrainError):self.cache.refresh(incremental=True)
        # The existing retirement contract refuses unsafe trees, rather than
        # traversing the symlink or deleting an original source directory.
        self.assertTrue(self.f.root.exists());self.assertIsNone(self.cache.active())

    def test_clone_config_cannot_point_at_another_owned_generation(self):
        old=self.initial();p=old.root/'home/.gbrain/config.json';value=json.loads(p.read_text())
        value['database_path']=str(old.root/'home');p.write_text(json.dumps(value));self.change()
        with self.assertRaisesRegex(GbrainError,'binding_invalid'):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active())

    def test_source_change_during_delta_readback_prevents_publication(self):
        self.initial();self.change()
        original=delta.clone_closed
        def changed(*args,**kwargs):
            plan=original(*args,**kwargs)
            self.engine.engine.on_graph=lambda:self.f.store.ingest(self.f.target,[{'original_id':'late','label':'Late','text':'fixture'}])
            return plan
        with patch.object(delta,'clone_closed',side_effect=changed):
            with self.assertRaisesRegex(ExportError,'stale'):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active())

    def test_invalid_incremental_option_does_not_touch_source_or_cache(self):
        before=self.f.source_bytes()
        with self.assertRaisesRegex(GbrainError,'options_invalid'):self.cache.refresh(incremental=1)
        self.assertEqual(before,self.f.source_bytes());self.assertIsNone(self.cache.active())

    def test_unchanged_engine_pages_are_still_completely_verified(self):
        self.initial();self.change();original=delta.clone_closed
        def corrupt(*args,**kwargs):
            plan=original(*args,**kwargs)
            slug=self.f.projection().slug(identity('graph','b'))
            self.engine.pages[slug]['frontmatter']['alden_record_sha256']='0'*64
            return plan
        with patch.object(delta,'clone_closed',side_effect=corrupt):
            with self.assertRaisesRegex(GbrainError,'readback_invalid'):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active())

    def test_unreaped_delta_worker_keeps_only_the_unpublished_generation_for_recovery(self):
        old=self.initial();self.change()
        with patch.object(delta,'DeltaImportClient',side_effect=_UnreapedWorker('gbrain_cache_worker_not_reaped')):
            with self.assertRaises(_UnreapedWorker):self.cache.refresh(incremental=True)
        self.assertFalse(old.root.exists());self.assertIsNone(self.cache.active())
        retained=list(self.cache.generations.iterdir());self.assertEqual(len(retained),1)
        self.assertFalse((retained[0]/'ready.json').exists());self.assertTrue((retained[0]/'delta-base.sqlite3').exists())

    def test_all_record_retractions_purge_origin_and_records_into_a_valid_empty_graph(self):
        self.initial()
        self.f.store.ingest(self.f.target,[],retractions=[{'platform':'graph','original_id':k} for k in ('a','b','c')])
        value=self.cache.refresh(incremental=True)
        self.assertEqual(value['engine_writes'],{'pages_put':0,'pages_purged':4,'links_added':0,'links_removed':0})
        self.assertEqual(value['pages'],0);self.assertEqual(value['links'],0);self.assertEqual(self.engine.pages,{})

    def test_rewound_journal_uses_a_new_full_baseline(self):
        old=self.initial();cursor=old.receipt['as_of']['checkpoint']['cursor']
        with self.f.store.database() as db:db.execute('DELETE FROM events WHERE sequence>?',(cursor//2,))
        with patch.object(delta,'clone_closed',side_effect=AssertionError('rewound source must not reuse engine')):
            value=self.cache.refresh(incremental=True)
        self.assertEqual(value['update_mode'],'full_rebuild')

    def test_replaced_source_database_uses_a_new_full_baseline(self):
        self.initial();p=self.f.root/'replacement.sqlite3';shutil.copyfile(self.f.store.path,p)
        p.replace(self.f.store.path)
        with patch.object(delta,'clone_closed',side_effect=AssertionError('replacement must not reuse engine')):
            value=self.cache.refresh(incremental=True)
        self.assertEqual(value['update_mode'],'full_rebuild')

    def test_known_full_denial_removes_data_without_a_delta_clone(self):
        old=self.initial()
        self.f.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        with patch.object(delta,'clone_closed',side_effect=AssertionError('denied source')):
            with self.assertRaisesRegex(ValueError,'scope_invalid'):self.cache.refresh(incremental=True)
        self.assertFalse(old.root.exists());self.assertIsNone(self.cache.active())

    def test_delta_receipt_failure_cannot_publish_and_reconciles_by_a_fresh_build(self):
        self.initial();self.change();write=delta._write
        def fail(path,value):
            if Path(path).name=='delta.json':raise OSError('injected save failure')
            return write(path,value)
        with patch.object(delta,'_write',side_effect=fail):
            with self.assertRaises(OSError):self.cache.refresh(incremental=True)
        self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])
        self.assertEqual(self.cache.refresh(incremental=True)['update_mode'],'full_rebuild')

    def test_abort_during_engine_copy_leaves_no_new_acceptance(self):
        from alden_abort import AldenCancelled
        old=self.initial();self.change();seen=[False]
        def abort():
            if (self.cache.generations/old.root.name).exists():
                for path in self.cache.generations.iterdir():
                    if path!=old.root and (path/'brain.pglite').exists():seen[0]=True;raise AldenCancelled('control')
        with self.assertRaises(AldenCancelled):self.cache.refresh(incremental=True,check=abort)
        self.assertTrue(seen[0]);self.assertIsNone(self.cache.active())

    def test_replacement_wire_request_carries_the_observed_revision(self):
        client=NATIVE_IMPORT_CLIENT.__new__(NATIVE_IMPORT_CLIENT);calls=[]
        client._call=lambda name,args:calls.append((name,args)) or {'ok':True}
        client.put([{'slug':'sources/existing','content':'updated','revision':'observed-opaque-revision'},
                    {'slug':'sources/new','content':'new','revision':None}],'owned-request')
        self.assertEqual(calls[0][1]['pages'][0]['expected_revision'],'observed-opaque-revision')
        self.assertNotIn('expected_revision',calls[0][1]['pages'][1])
        self.assertEqual(calls[0][1]['request_id'],'owned-request')

    def test_live_consumer_option_reaches_delta_publication_and_keeps_checkpoint_bound(self):
        watcher=GbrainSourceWatcher(self.cache.root,collection_root=self.f.root,projects=['one'],incremental=True)
        watcher.cache=self.cache
        with watcher:
            first=watcher.tick();self.assertEqual(first['state'],'ready');self.change()
            next_value=watcher.tick();self.assertEqual(next_value['state'],'ready')
            projection=self.f.projection();receipt=json.loads((projection.root/'delta.json').read_text())
            self.assertEqual(receipt['engine_writes']['pages_put'],1)
            self.assertEqual(next_value['accepted']['fingerprint'],projection.fingerprint)

    def test_delta_page_batches_cross_fifty_without_dropping_later_records(self):
        self.initial()
        self.f.store.ingest(self.f.target,[{'original_id':'added-'+str(i),'label':'Added '+str(i),'text':'fixture'} for i in range(120)])
        value=self.cache.refresh(incremental=True)
        self.assertEqual(value['engine_writes'],{'pages_put':120,'pages_purged':0,'links_added':120,'links_removed':0})
        self.assertEqual(value['pages'],124);self.assertEqual(len(self.engine.puts),120)
        self.assertEqual(value['source_update']['candidate_nodes'],120)
        self.assertEqual(value['index_updates']['pages_put'],120)
        self.assertEqual(len(set(self.engine.puts)),120);self.assertTrue(self.f.query('added-119')['ok'])

    def test_one_update_in_a_large_stable_generation_keeps_all_other_revisions(self):
        for start,end in ((0,1400),(1400,2100)):
            self.f.store.ingest(self.f.target,[{'original_id':'large-'+str(i),'label':'Large '+str(i),'text':'fixture'} for i in range(start,end)])
        old=self.initial();before={p['slug']:p['revision'] for p in old.pages()};self.change()
        value=self.cache.refresh(incremental=True)
        self.assertEqual(value['pages'],2104);self.assertEqual(value['verified_queries'],2104)
        self.assertEqual(value['engine_writes']['pages_put'],1)
        self.assertEqual(value['source_update']['candidate_nodes'],1)
        self.assertEqual(value['source_update']['archive_rows']['nodes_put'],1)
        self.assertEqual(value['index_updates']['pages_put'],1)
        after={p['slug']:p['revision'] for p in self.f.projection().pages()}
        self.assertEqual(sum(before[k]==after[k] for k in before),2103)

    def test_reused_index_prunes_retracted_pages_before_engine_mutation(self):
        old=self.initial();self.remove();root=self.f.base/'next-index';root.mkdir(mode=0o700)
        full.export_graph(self.f.root,['one'],root/'source-archive')
        index=full.FullProjection.prepare(root,policy=full.STABLE_POLICY,previous=old)
        self.assertNotIn(index.slug(identity('graph','c')),{p['slug'] for p in index.pages()})
        self.assertEqual(index.index_updates['pages_deleted'],1);self.assertEqual(index.index_updates['links_deleted'],2)

    def test_new_observed_relation_type_updates_the_private_schema_pack(self):
        self.initial()
        relation=self.f.relation('a','c');relation['type']='contrasts-with'
        self.f.store.ingest(self.f.target,[],relations=[relation]);value=self.cache.refresh(incremental=True)
        self.assertEqual(value['engine_writes'],{'pages_put':0,'pages_purged':0,'links_added':1,'links_removed':0})
        index=self.f.projection();verb='alden_'+full._digest('contrasts-with')[:24]
        self.assertIn(verb,(index.root/'home/.gbrain/schema-packs/alden-scoped-graph/pack.yaml').read_text())
        self.assertTrue(self.f.query()['ok'])


if __name__=='__main__':unittest.main()

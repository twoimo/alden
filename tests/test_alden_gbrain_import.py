"""Full import and source-current read contracts over owned, synthetic sources."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from alden_collection import CollectionStore,identity
from alden_collection_export import export_graph
from alden_gbrain_cache import ManagedGbrainCache,_UnreapedWorker
import alden_gbrain_cache as managed
import alden_gbrain_client as transport
from alden_gbrain_client import GbrainError
import alden_gbrain_import as full
from alden_gbrain_import import FullProjection,FullGbrainCache,FullGbrainReader,GbrainImportClient
from alden_knowledge_mcp import KnowledgeServer


class FakeEngine:
    def __init__(self):
        self.pages={};self.links={};self.outgoing={};self.incoming={};self.fail=None;self.on_graph=lambda:None
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def put(self,pages,request):
        saved=[]
        for index,page in enumerate(pages):
            fields={}
            for line in page['content'].split('---\n')[1].splitlines():
                key,value=line.split(': ',1);fields[key]=json.loads(value)
            revision='opaque-'+hashlib.sha256(page['content'].encode()).hexdigest()
            self.pages[page['slug']]={'slug':page['slug'],'source_id':'default','title':fields['title'],
                'frontmatter':fields,'revision':revision}
            saved.append({'index':index,'slug':page['slug'],'state':'committed',
                'embedding_state':'disabled','revision':revision})
        result={'state':'committed','terminal':True,'source_id':'default','pages':saved,
            'counts':{'total':len(pages),'committed':len(pages),'pending':0,'failed':0}}
        if self.fail=='batch':result['terminal']=False
        if self.fail=='boolean-count':result['counts']['pending']=False
        return result
    def link(self,row,request):
        if self.fail!='missing-link':
            key=(row['a'],row['b'],row['verb']);self.links[key]=copy.deepcopy(row)
            self.outgoing.setdefault(row['a'],set()).add(key);self.incoming.setdefault(row['b'],set()).add(key)
        return {'status':'ok'}
    def get(self,slug):
        page=copy.deepcopy(self.pages[slug])
        if self.fail=='revision':page['revision']='wrong'
        if self.fail=='record-hash':page['frontmatter']['alden_record_sha256']='0'*64
        return page
    def list(self,offset):
        rows=[{'slug':s,'source_id':'default'} for s in sorted(self.pages)]
        if self.fail=='extra-page':rows.append({'slug':'zzzz-injected','source_id':'default'})
        if self.fail=='missing-page':rows=rows[1:]
        return rows[offset:offset+100]
    def graph(self,slug,*,depth=1,direction='both'):
        frontier={slug};seen={slug};result={}
        for layer in range(1,depth+1):
            neighbours=set()
            keys=set()
            for node in frontier:
                if direction in ('out','both'):keys.update(self.outgoing.get(node,()))
                if direction in ('in','both'):keys.update(self.incoming.get(node,()))
            for a,b,verb in sorted(keys):
                link=self.links[a,b,verb]
                result.setdefault((a,b,verb),{'from_slug':a,'to_slug':b,'link_type':verb,
                    'context':link['context'],'from_source_id':'default','to_source_id':'default','depth':layer})
                if direction in ('out','both'):neighbours.add(b)
                if direction in ('in','both'):neighbours.add(a)
            frontier=neighbours-seen;seen.update(frontier)
        if len(result)>=5000:raise GbrainError('gbrain_graph_truncated')
        self.on_graph();return list(result.values())


class FullImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.root=self.base/'source';self.root.mkdir(mode=0o700)
        self.store=CollectionStore(self.root)
        self.target=self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'])
        self.store.ingest(self.target,[{'original_id':k,'label':'Title '+k,'text':'PRIVATE_BODY_'+k} for k in ('a','b','c')],
            relations=[self.relation('a','b'),self.relation('b','c')])
        runtime=self.base/'runtime';(runtime/'home/.gbrain').mkdir(parents=True);runtime.chmod(0o700)
        (runtime/'brain').mkdir();binary=runtime/'gbrain';binary.write_bytes(b'inert test binary');binary.chmod(0o700)
        sha=hashlib.sha256(binary.read_bytes()).hexdigest()
        self.enterContext(patch.object(transport,'ASSET_SHA256',sha));self.enterContext(patch.object(transport,'ASSET_BYTES',binary.stat().st_size))
        self.enterContext(patch.object(managed,'ASSET_SHA256',sha))
        (runtime/'receipt.json').write_text(json.dumps({'repository':'garrytan/gbrain','source_commit':transport.SOURCE_COMMIT,
            'tag':'v'+transport.VERSION,'asset_name':transport.ASSET,'bytes':binary.stat().st_size,
            'sha256':sha,'github_asset_digest_matches':True,'runtime_path':str(binary)}))
        (runtime/'home/.gbrain/config.json').write_text(json.dumps({'engine':'pglite','database_path':str(runtime/'brain'),
            'embedding_disabled':True,'self_upgrade':{'mode':'notify'}}))
        self.cache=FullGbrainCache.create(self.base/'cache',collection_root=self.root,projects=['one'],
            runtime_root=runtime,runtime_home=runtime/'home',runtime_receipt=runtime/'receipt.json')
        self.engines={};self.failure=None;self.commands=[]
        self.enterContext(patch.object(self.cache,'_command',side_effect=self.command))
        def engine(receipt,*,home,**kwargs):
            generation=Path(home).parent
            if generation not in self.engines:self.engines[generation]=FakeEngine()
            self.engines[generation].fail=self.failure
            return self.engines[generation]
        self.engine=engine
        self.enterContext(patch.object(full,'GbrainImportClient',side_effect=engine))
        self.enterContext(patch.object(full,'GbrainReadClient',side_effect=engine))

    @staticmethod
    def relation(a,b):return {'source_platform':'graph','source_id':a,'target_platform':'graph','target_id':b,
                             'type':'reference','evidence':{'fixture':'explicit source reference'}}
    def command(self,generation,args,**kwargs):
        self.commands.append(args)
        if args[0]=='init':
            (generation/'home/.gbrain').mkdir(parents=True)
            (generation/'brain.pglite').mkdir();(generation/'audit').mkdir()
            (generation/'home/.gbrain/config.json').write_text(json.dumps({'engine':'pglite',
                'database_path':str(generation/'brain.pglite'),'embedding_disabled':True,'self_upgrade':{'mode':'notify'}}))
            return b'{"ok":true}'
        if args[:2]==['schema','validate']:return b'valid manifest'
        if args[:2]==['schema','use']:return b'activated'
        return b'{"pack":"alden-scoped-graph","degraded":false}'
    def projection(self):
        active=self.cache.active();return FullProjection.open(self.cache._generation(active['generation']))
    def reader(self):return FullGbrainReader(self.cache.root,collection_root=self.root,projects=['one'])
    def query(self,focus='b',**kwargs):return self.reader().query(projects=['one'],focus=identity('graph',focus),**kwargs)
    def source_bytes(self):return self.store.path.read_bytes()

    def test_full_import_preserves_every_source_record_proof_and_actual_origin(self):
        before=self.source_bytes();result=self.cache.refresh()
        self.assertEqual({k:result[k] for k in ('source_nodes','origins','observed_groups','membership_links','pages','links','verified_queries')},
            {'source_nodes':3,'origins':1,'observed_groups':2,'membership_links':3,'pages':4,'links':5,'verified_queries':4})
        projection=self.projection();self.assertEqual(len(list(projection.pages())),4)
        self.assertNotIn('PRIVATE_BODY',json.dumps(list(projection.pages())+list(projection.links())))
        result=self.query();self.assertEqual(len(result['edges']),3)
        self.assertEqual({e['relation'] for e in result['edges']},{'reference','sourced_from'})
        self.assertTrue(result['basis']['complete_within_snapshot']);self.assertEqual(before,self.source_bytes())

    def test_engine_record_proof_commits_all_exported_context_and_version_fields(self):
        self.cache.refresh();projection=self.projection();engine=self.engines[projection.root]
        for page in projection.pages():
            node=json.loads(page['payload'])
            expected=hashlib.sha256(json.dumps(node,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            self.assertEqual(engine.pages[page['slug']]['frontmatter']['alden_record_sha256'],expected)

    def test_full_and_bounded_roots_never_implicitly_adopt_each_other(self):
        with self.assertRaisesRegex(GbrainError,'owner_mismatch'):
            ManagedGbrainCache(self.cache.root,collection_root=self.root,projects=['one'])
        with self.assertRaisesRegex(GbrainError,'owner_mismatch'):
            FullGbrainCache(self.cache.root,collection_root=self.root,projects=['two'])

    def test_unchanged_refresh_does_not_write_engine_and_discards_probe_archive(self):
        self.cache.refresh();active=self.cache.active();commands=len(self.commands)
        result=self.cache.refresh();self.assertEqual(result['state'],'unchanged')
        self.assertEqual(self.cache.active(),active);self.assertEqual(len(self.commands),commands)
        self.assertEqual(len(list(self.cache.generations.iterdir())),1)

    def test_changed_source_replaces_all_owned_files_but_record_slugs_stay_stable(self):
        self.cache.refresh();old=self.projection();old_slug=old.slug(identity('graph','a'));old_root=old.root
        self.store.ingest(self.target,[{'original_id':'a','label':'New title','text':'changed retained body'}])
        with self.assertRaisesRegex(GbrainError,'projection_stale'):self.query('a')
        result=self.cache.refresh();new=self.projection()
        self.assertEqual(result['removed_generations'],1);self.assertFalse(old_root.exists())
        self.assertEqual(new.slug(identity('graph','a')),old_slug);self.assertNotEqual(old.fingerprint,new.fingerprint)
        self.assertEqual(next(n['label'] for n in self.query('a')['nodes'] if n['id']==identity('graph','a')),'New title')

    def test_full_permission_denial_removes_old_archive_index_and_database(self):
        self.cache.refresh();old=self.projection().root
        self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        with self.assertRaisesRegex(ValueError,'scope_invalid'):self.cache.refresh()
        self.assertIsNone(self.cache.active());self.assertFalse(old.exists());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_failed_batch_revision_link_or_record_binding_never_publishes(self):
        for failure in ('batch','boolean-count','missing-link','revision','record-hash','extra-page','missing-page'):
            with self.subTest(failure=failure):
                self.failure=failure
                with self.assertRaises(GbrainError):self.cache.refresh()
                self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_content_change_during_engine_read_fails_the_final_source_check(self):
        self.cache.refresh();projection=self.projection();engine=self.engines[projection.root]
        engine.on_graph=lambda:self.store.ingest(self.target,[{'original_id':'late','label':'Late','text':'fixture'}])
        with self.assertRaisesRegex(GbrainError,'projection_stale'):self.query()

    def test_source_change_during_build_cannot_publish_or_restore_old_generation(self):
        self.cache.refresh();original=self.cache._build
        self.store.ingest(self.target,[{'original_id':'a','label':'Changed','text':'changed'}])
        def changing(*args,**kwargs):
            value=original(*args,**kwargs)
            self.store.ingest(self.target,[{'original_id':'late','label':'Late','text':'fixture'}]);return value
        with patch.object(self.cache,'_build',side_effect=changing):
            with self.assertRaisesRegex(RuntimeError,'export_stale'):self.cache.refresh()
        self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_cancellation_discards_unpublished_generation(self):
        checks=[0]
        def check():
            checks[0]+=1
            if checks[0]>35:raise RuntimeError('fixture_cancelled')
        with self.assertRaisesRegex(RuntimeError,'fixture_cancelled'):self.cache.refresh(check=check)
        self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_empty_permitted_source_is_verified_without_invented_origin_nodes(self):
        self.store.register(platform='graph',original_id='empty',kind='source',label='Empty',projects=['empty'])
        cache=FullGbrainCache.create(self.base/'empty-cache',collection_root=self.root,projects=['empty'],
            runtime_root=self.base/'runtime',runtime_home=self.base/'runtime/home',runtime_receipt=self.base/'runtime/receipt.json')
        with patch.object(cache,'_command',side_effect=self.command):result=cache.refresh()
        self.assertEqual(result['pages'],0);self.assertEqual(result['links'],0);self.assertEqual(result['verified_queries'],0)

    def test_keyset_batches_can_commit_revisions_and_cross_the_2048_node_display_budget(self):
        self.store.ingest(self.target,[{'original_id':'large-'+str(i),'label':'Record '+str(i),'text':'fixture'} for i in range(1500)])
        self.store.ingest(self.target,[{'original_id':'large-'+str(i),'label':'Record '+str(i),'text':'fixture'} for i in range(1500,2100)])
        generation=self.cache.generations/('g'+'a'*32);generation.mkdir()
        export_graph(self.root,['one'],generation/'source-archive')
        projection=FullProjection.prepare(generation);iterator=projection.pages();first=[next(iterator) for _ in range(50)]
        projection.record_revisions(first,['opaque']*50)
        remaining=list(iterator)
        self.assertEqual(len(first)+len(remaining),2104);self.assertEqual(projection.counts()['source_nodes'],2103)
        self.assertEqual({p['id'] for p in first+remaining},
            {identity('graph',k) for k in ['a','b','c']+['large-'+str(i) for i in range(2100)]}|{projection.origin(self.target)})

    def test_large_source_origin_does_not_require_one_truncated_inbound_response(self):
        for start in range(0,5100,1700):
            self.store.ingest(self.target,[{'original_id':'fan-in-'+str(i),'label':'Record '+str(i),'text':'fixture'}
                for i in range(start,min(start+1700,5100))])
        result=self.cache.refresh()
        self.assertEqual((result['source_nodes'],result['origins'],result['membership_links'],result['pages'],result['verified_queries']),
                         (5103,1,5103,5104,5104))
        self.assertEqual(result['links'],5105)

    def test_disallowed_project_does_not_reach_engine(self):
        self.cache.refresh();self.engines.clear()
        with self.assertRaisesRegex(GbrainError,'scope_mismatch'):
            self.reader().query(projects=['outside'],focus=identity('graph','a'))
        self.assertEqual(self.engines,{})

    def test_sql_expected_incident_sets_reject_missing_extra_changed_source_depth_and_proof(self):
        self.cache.refresh();projection=self.projection();engine=self.engines[projection.root]
        rows=engine.graph(projection.slug(identity('graph','b')))
        self.assertEqual(len(rows),3)
        for direction,count in [('in',1),('out',2),('both',3)]:
            result=projection.bind(engine.graph(projection.slug(identity('graph','b')),direction=direction),focus=identity('graph','b'),direction=direction)
            self.assertEqual(len(result['edges']),count)
        variants=[rows[:-1],rows+[rows[0]],[]]
        for key,value in [('context','{}'),('depth',True),('from_source_id','outside')]:
            changed=copy.deepcopy(rows);changed[0][key]=value;variants.append(changed)
        for variant in variants:
            with self.subTest(variant=variant):
                with self.assertRaises(GbrainError):projection.bind(variant,focus=identity('graph','b'))

    def test_tampered_index_or_source_archive_is_not_served(self):
        self.cache.refresh();projection=self.projection()
        with sqlite3.connect(projection.path) as db:db.execute("UPDATE pages SET payload=json_set(payload,'$.label','changed')")
        with self.assertRaisesRegex(GbrainError,'projection_changed'):self.query()

    def test_index_change_during_source_revalidation_cannot_return_changed_labels(self):
        self.cache.refresh();projection=self.projection();original=full.verify_current_export
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            with sqlite3.connect(projection.path) as db:
                db.execute("UPDATE pages SET payload=json_set(payload,'$.label','LATE_CHANGED_LABEL')")
            return result
        with patch.object(full,'verify_current_export',side_effect=changed):
            with self.assertRaisesRegex(GbrainError,'projection_changed'):self.query()

    def test_partial_denial_keeps_only_actual_surviving_source_records_and_origins(self):
        other=self.store.register(platform='graph',original_id='other',kind='source',label='Other',projects=['one'])
        self.store.ingest(other,[{'original_id':'d','label':'Other record','text':'fixture'}])
        self.cache.refresh();old=self.projection().root
        self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        result=self.cache.refresh()
        self.assertEqual((result['source_nodes'],result['origins'],result['observed_groups'],result['membership_links']),(1,1,0,1))
        self.assertFalse(old.exists())
        self.assertEqual({p['id'] for p in self.projection().pages()},
            {identity('graph','d'),self.projection().origin(other)})

    def test_permission_change_after_pointer_publication_removes_every_generation(self):
        original=full._write
        def changed(path,value):
            original(path,value)
            if Path(path).name=='active.json':
                self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        with patch.object(full,'_write',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'scope_invalid'):self.cache.refresh()
        self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_native_withheld_records_abort_instead_of_importing_the_other_visible_records(self):
        self.cache.refresh()
        target=self.store.register(platform='kakaotalk',original_id='native',kind='room',label='Native',projects=['one'])
        self.store.ingest(target,[{'original_id':'n','label':'Native fixture','text':'native'}])
        factory=lambda:CollectionStore.open_existing(self.root,source_access=lambda *args:False,source_revision=lambda _: 'c'*64)
        with self.assertRaisesRegex(RuntimeError,'source_withheld'):self.cache.refresh(store_factory=factory)
        self.assertIsNone(self.cache.active());self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_full_mcp_routes_fresh_readonly_authority_and_advertises_no_writer(self):
        self.cache.refresh();server=KnowledgeServer(self.root,['one'],gbrain=self.reader())
        catalog=server.tool_catalog();self.assertTrue(all(t['annotations']['readOnlyHint'] for t in catalog))
        self.assertEqual([t['name'] for t in catalog if 'paths' in t['name']],['alden_knowledge_paths'])
        result=server.call_tool({'name':'alden_knowledge_paths','arguments':{'projects':['one'],'focus':identity('graph','b'),'depth':1}},None)
        self.assertEqual(len(result['edges']),3);self.assertEqual(result['basis']['policy'],full.POLICY)

    def test_unreaped_writer_keeps_generation_for_lease_protected_recovery(self):
        with patch.object(self.cache,'_build',side_effect=_UnreapedWorker('gbrain_cache_worker_not_reaped')):
            with self.assertRaises(_UnreapedWorker):self.cache.refresh()
        self.assertIsNone(self.cache.active());self.assertEqual(len(list(self.cache.generations.iterdir())),1)

    def test_import_transport_never_treats_an_unreaped_process_as_closed(self):
        from types import SimpleNamespace
        client=object.__new__(GbrainImportClient)
        client.transport=SimpleNamespace(close=lambda:None,proc=SimpleNamespace(poll=lambda:None))
        with self.assertRaises(_UnreapedWorker):client.__exit__(RuntimeError,RuntimeError('fixture'),None)

    def test_index_budget_is_failure_rather_than_a_capped_complete_index(self):
        generation=self.cache.generations/('g'+'b'*32);generation.mkdir()
        export_graph(self.root,['one'],generation/'source-archive')
        with self.assertRaisesRegex(GbrainError,'index_budget'):
            FullProjection.prepare(generation,max_index_records=4)
        self.assertFalse((generation/'projection.json').exists());self.assertIsNone(self.cache.active())

    def test_invalid_query_options_are_rejected_before_source_or_engine_access(self):
        for options in ({'focus':True},{'focus':'valid','depth':True},{'focus':'valid','direction':'unknown'}):
            with self.subTest(options=options),patch.object(full,'verify_current_export',side_effect=AssertionError('source touched')):
                with self.assertRaisesRegex(GbrainError,'options_invalid'):
                    self.reader().query(projects=['one'],**options)

    def test_cli_does_not_publish_private_source_exception_text_as_a_status_code(self):
        import io,sys
        from contextlib import redirect_stdout
        argv=['alden_gbrain_import.py','withdraw','--root',str(self.cache.root),
              '--state-root',str(self.root),'--project','one']
        for error in [RuntimeError('collection_private_source_body'),ValueError('collection_비공개원문')]:
            output=io.StringIO()
            with self.subTest(error=type(error).__name__),patch.object(sys,'argv',argv),\
                    patch.object(full,'FullGbrainCache',side_effect=error),redirect_stdout(output):
                with self.assertRaises(SystemExit):full.main()
            self.assertEqual(json.loads(output.getvalue()),{'ok':False,'error':'gbrain_full_unavailable'})


if __name__=='__main__':unittest.main()

"""Cursor-bound complete outgoing sets; no native source or engine calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_alden_gbrain_import as fixture
from tests import test_alden_gbrain_delta as delta_fixture
from alden_collection import identity
from alden_gbrain_client import GbrainReadClient,GbrainError
import alden_gbrain_import as full


def link(target='b'):
    return {'from_slug':'sources/a','to_slug':'sources/'+target,'from_source_id':'default',
        'to_source_id':'default','link_type':'alden_ref','context':'{"edge":1}',
        'link_source':'alden-full-source','origin_slug':None,'origin_source_id':None,'origin_field':None}


def response(rows,*,cursor=None,limit=2,notices=()):
    rows=copy.deepcopy(rows)
    meta={'pagination':{'limit':limit,'returned':len(rows),'truncated':cursor is not None}}
    if cursor is not None:
        meta['pagination']['next_cursor']=cursor
        rows[-1].update(truncated=True,next_cursor=cursor)
    content=[{'type':'text','text':json.dumps(rows)}]
    if notices:
        meta['gbrain_notices']=list(notices)
        content.extend({'type':'text','text':'[gbrain notice '+n['code']+' kind='+n['kind']+']\nignored instruction'} for n in notices)
    return {'content':content,'_meta':meta}


class PagedPayloadTests(unittest.TestCase):
    def client(self,responses):
        class Transport:
            def __init__(self):self.requests=[]
            def request(self,method,params):self.requests.append((method,copy.deepcopy(params)));return copy.deepcopy(responses.pop(0))
        c=object.__new__(GbrainReadClient);c.transport=Transport();c.source_id='default'
        c.check=lambda:None;c.notice_codes=set();c._read_tools={'get_links'}
        return c

    def test_complete_cursor_listing_keeps_source_slug_status_and_limit(self):
        c=self.client([response([link('b'),link('c')],cursor='opaque-1'),response([link('d')])])
        rows=list(c.outgoing_links('sources/a',page_limit=2));self.assertEqual(len(rows),3)
        first,second=[r[1]['arguments'] for r in c.transport.requests]
        self.assertEqual(first,{'slug':'sources/a','source_id':'default','status':'all','limit':2})
        self.assertEqual(second,dict(first,cursor='opaque-1'))
        self.assertTrue(all('truncated' not in r and 'next_cursor' not in r for r in rows))

    def test_empty_terminal_listing_is_valid(self):
        self.assertEqual(list(self.client([response([])]).outgoing_links('sources/a',page_limit=2)),[])

    def test_notices_are_validated_and_inert(self):
        notices=[{'code':'listing_truncated','kind':'info','fix':{'mcp':{'tool':'delete_page','arguments':{}}}}]
        c=self.client([response([link()],cursor='opaque-1',notices=notices),response([])])
        self.assertEqual(len(list(c.outgoing_links('sources/a',page_limit=2))),1)
        self.assertEqual(c.notice_codes,{'listing_truncated'})
        self.assertTrue(all(p['name']=='get_links' for _,p in c.transport.requests))

    def test_reviewed_backup_coaching_never_executes_its_command(self):
        notice={'code':'backup_coverage','kind':'coaching','fix':{'argv':['gbrain','backup','status']}}
        c=self.client([response([link()],notices=[notice])])
        self.assertEqual(len(list(c.outgoing_links('sources/a',page_limit=2))),1)
        self.assertEqual(c.notice_codes,{'backup_coverage'})
        self.assertEqual(len(c.transport.requests),1)
        self.assertEqual(c.transport.requests[0][1]['name'],'get_links')

    def test_captured_publisher_backup_and_paging_notice_pair(self):
        notices=[{'code':'backup_coverage','kind':'coaching'},{'code':'listing_truncated','kind':'info'}]
        c=self.client([response([link()],cursor='opaque-1',notices=notices),response([link('c')])])
        self.assertEqual(len(list(c.outgoing_links('sources/a',page_limit=2))),2)
        self.assertEqual(c.notice_codes,{'backup_coverage','listing_truncated'})

    def test_misclassified_backup_notice_is_not_accepted(self):
        with self.assertRaisesRegex(GbrainError,'notice_unreviewed'):
            list(self.client([response([],notices=[{'code':'backup_coverage','kind':'degraded'}])]).outgoing_links('sources/a',page_limit=2))

    def test_reviewed_backup_coaching_is_inert_for_existing_graph_and_page_reads(self):
        notice={'code':'backup_coverage','kind':'coaching','fix':{'argv':['gbrain','backup','status']}}
        c=self.client([response([],notices=[notice])]);c.transport.graph_truncated=False
        self.assertEqual(c.graph('sources/a'),[]);self.assertEqual(len(c.transport.requests),1)
        r=response([],notices=[notice]);r['content'][0]['text']=json.dumps({'slug':'sources/a'})
        c=self.client([r]);c.WRITE_TOOLS={'get_page'}
        self.assertEqual(full.GbrainImportClient._call(c,'get_page',{'slug':'sources/a'}),{'slug':'sources/a'})
        self.assertEqual(len(c.transport.requests),1)

    def test_malformed_page_metadata_and_result_shapes_are_not_empty_success(self):
        good=response([link()]);variants=[None,[],{},dict(good,isError=True)]
        for change in ({'limit':True},{'limit':3},{'returned':True},{'returned':0},{'truncated':'false'},
                       {'truncated':True},{'next_cursor':'unexpected'},{'extra':'unreviewed'}):
            v=copy.deepcopy(good);v['_meta']['pagination'].update(change);variants.append(v)
        v=copy.deepcopy(good);v['_meta'].pop('pagination');variants.append(v)
        variants.append(response([link('b'),link('c'),link('d')]))
        for v in variants:
            with self.subTest(value=v),self.assertRaises(GbrainError):
                list(self.client([v]).outgoing_links('sources/a',page_limit=2))

    def test_missing_mismatched_or_misplaced_row_cursor_is_rejected(self):
        values=[]
        for change in ({'truncated':False},{'next_cursor':'other'}):
            v=response([link('b'),link('c')],cursor='opaque-1');rows=json.loads(v['content'][0]['text']);rows[-1].update(change)
            v['content'][0]['text']=json.dumps(rows);values.append(v)
        v=response([dict(link(),truncated=True,next_cursor='surprise')]);values.append(v)
        v=response([link('b'),link('c')],cursor='opaque-1');rows=json.loads(v['content'][0]['text']);rows[0]['next_cursor']='misplaced';v['content'][0]['text']=json.dumps(rows);values.append(v)
        for v in values:
            with self.subTest(value=v),self.assertRaises(GbrainError):list(self.client([v]).outgoing_links('sources/a',page_limit=2))

    def test_repeated_cursor_cannot_loop_or_repeat_acceptance(self):
        c=self.client([response([link()],cursor='same'),response([link('c')],cursor='same')])
        with self.assertRaisesRegex(GbrainError,'cursor_invalid'):list(c.outgoing_links('sources/a',page_limit=2))
        self.assertEqual(len(c.transport.requests),2)

    def test_foreign_rows_invalid_types_and_long_context_are_rejected(self):
        for change in ({'from_slug':'sources/other'},{'from_source_id':'other'},{'to_source_id':'other'},
                       {'to_slug':'../bad'},{'link_type':True},{'context':'x'*2001}):
            with self.subTest(change=change),self.assertRaises(GbrainError):
                list(self.client([response([dict(link(),**change)])]).outgoing_links('sources/a',page_limit=2))

    def test_budget_and_cancellation_do_not_fetch_another_page(self):
        c=self.client([response([link('b'),link('c')],cursor='opaque-1')])
        with self.assertRaisesRegex(GbrainError,'row_budget'):list(c.outgoing_links('sources/a',page_limit=2,max_rows=1))
        self.assertEqual(len(c.transport.requests),1)
        c=self.client([response([link('b'),link('c')],cursor='opaque-1')]);it=c.outgoing_links('sources/a',page_limit=2);next(it)
        def cancelled():raise GbrainError('test_cancelled')
        c.check=cancelled
        with self.assertRaisesRegex(GbrainError,'test_cancelled'):next(it)
        self.assertEqual(len(c.transport.requests),1)

    def test_invalid_options_and_missing_surface_never_reach_transport(self):
        for options in ({'page_limit':True},{'page_limit':501},{'max_rows':-1},{'max_rows':True}):
            c=self.client([])
            with self.subTest(options=options),self.assertRaises(GbrainError):list(c.outgoing_links('sources/a',**options))
            self.assertEqual(c.transport.requests,[])
        c=self.client([]);c._read_tools=set()
        with self.assertRaisesRegex(GbrainError,'unavailable'):list(c.outgoing_links('sources/a'))
        self.assertEqual(c.transport.requests,[])

    def test_unknown_or_mispaired_notice_is_not_suppressed(self):
        for notice in ({'code':'unknown','kind':'info'},{'code':'listing_truncated','kind':'ask'}):
            with self.subTest(notice=notice),self.assertRaisesRegex(GbrainError,'notice_unreviewed'):
                list(self.client([response([],notices=[notice])]).outgoing_links('sources/a',page_limit=2))


class PagedEngine(fixture.FakeEngine):
    def __init__(self):super().__init__();self.paged_calls=[]
    def outgoing_links(self,slug,*,max_rows):
        self.paged_calls.append(slug);self.on_graph()
        rows=[dict(self.links[k],from_slug=k[0],to_slug=k[1],link_type=k[2],from_source_id='default',to_source_id='default',
            link_source='alden-full-source',origin_slug=None,origin_source_id=None,origin_field=None) for k in sorted(self.outgoing.get(slug,()))]
        if self.fail=='paged-missing':rows=rows[:-1]
        if self.fail=='paged-duplicate':rows+=rows[:1]
        if self.fail=='paged-extra' and rows:rows[0]['to_slug']='sources/unexpected'
        if self.fail=='paged-context' and rows:rows[0]['context']='{}'
        if self.fail=='paged-provenance' and rows:rows[0]['link_source']='unexpected'
        for row in rows:yield row


class FullPagedVerificationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(fixture,'FakeEngine',PagedEngine))
        self.f=fixture.FullImportTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def long_reference_context(self):
        # Stored source labels survive export. Astral characters also prove
        # the conservative UTF-16 budget, rather than only Python code points.
        self.f.store.ingest(self.f.target,[{'original_id':'a','label':'😀'*1024,'text':'PRIVATE_BODY_a'}])

    def test_small_source_preserves_existing_traversal_and_format(self):
        result=self.f.cache.refresh(incremental=True)
        self.assertNotIn('paged_outgoing_pages',result)
        self.assertEqual(self.f.engines[self.f.projection().root].paged_calls,[])
        self.assertEqual(self.f.projection().policy,full.STABLE_POLICY)

    def test_paged_full_source_outgoing_sets_are_complete(self):
        with patch.object(full,'TRAVERSE_ROW_LIMIT',2):result=self.f.cache.refresh(incremental=True)
        self.assertEqual(result['paged_outgoing_pages'],2);self.assertEqual(result['paged_outgoing_links'],4)
        self.assertEqual(result['verified_queries'],4)

    def test_fresh_rebuild_does_not_send_prior_engine_revision_preconditions(self):
        original=PagedEngine.put
        def revision_bound(engine,pages,request):
            for page in pages:
                if page.get('revision') is not None and engine.pages.get(page['slug'],{}).get('revision')!=page['revision']:
                    raise GbrainError('fresh_engine_revision_conflict')
            return original(engine,pages,request)
        with patch.object(full,'TRAVERSE_ROW_LIMIT',2),patch.object(PagedEngine,'put',revision_bound):
            first=self.f.cache.refresh(incremental=True)
            result=self.f.cache.refresh(incremental=True,rebuild=True)
        self.assertEqual(first['fingerprint'],result['fingerprint'])
        self.assertEqual(result['update_mode'],'full_rebuild')
        self.assertEqual(result['paged_outgoing_links'],4)

    def test_missing_extra_duplicate_proof_and_provenance_never_publish(self):
        for failure in ('paged-missing','paged-duplicate','paged-extra','paged-context','paged-provenance'):
            self.f.failure=failure
            with self.subTest(failure=failure),patch.object(full,'TRAVERSE_ROW_LIMIT',2),self.assertRaises(GbrainError):
                self.f.cache.refresh(incremental=True)
            self.assertIsNone(self.f.cache.active());self.assertEqual(list(self.f.cache.generations.iterdir()),[])

    def test_large_context_budget_fails_before_engine_creation(self):
        self.long_reference_context()
        with patch.object(full,'TRAVERSE_ROW_LIMIT',2),self.assertRaisesRegex(GbrainError,'paged_context_budget'):
            self.f.cache.refresh(incremental=True)
        self.assertEqual(self.f.commands,[]);self.assertIsNone(self.f.cache.active())

    def test_small_degree_long_context_still_has_full_traversal_readback(self):
        self.long_reference_context()
        result=self.f.cache.refresh(incremental=True);self.assertEqual(result['links'],5)
        self.assertGreater(max(len(r['context'].encode('utf-16-le'))//2 for r in self.f.projection().links()),2000)
        self.assertNotIn('paged_outgoing_pages',result)

    def test_actual_5000_boundary_is_not_limited_by_query_traversal(self):
        records=[{'original_id':'hub','label':'Explicit hub','text':'owned fixture'}]+[
            {'original_id':'leaf-'+str(i),'label':'Leaf '+str(i),'text':'owned fixture'} for i in range(4999)]
        relations=[self.f.relation('hub','leaf-'+str(i)) for i in range(4999)]
        for offset in range(0,len(records),1000):self.f.store.ingest(self.f.target,records[offset:offset+1000])
        for offset in range(0,len(relations),1000):self.f.store.ingest(self.f.target,[],relations=relations[offset:offset+1000])
        result=self.f.cache.refresh(incremental=True)
        self.assertEqual(result['paged_outgoing_pages'],1);self.assertEqual(result['paged_outgoing_links'],5000)
        projection=self.f.projection();engine=self.f.engines[projection.root]
        with self.assertRaisesRegex(GbrainError,'graph_truncated'):engine.graph(projection.slug(identity('graph','hub')),depth=1,direction='out')
        self.assertEqual(result['verified_queries'],5004)

    def change_during_paged_read(self,change):
        original=PagedEngine.outgoing_links;changed=False
        def changing(engine,slug,**kwargs):
            nonlocal changed
            if not changed:changed=True;change()
            yield from original(engine,slug,**kwargs)
        with patch.object(full,'TRAVERSE_ROW_LIMIT',2),patch.object(PagedEngine,'outgoing_links',changing):
            self.f.cache.refresh(incremental=True)

    def test_source_advancement_during_paged_verification_cannot_publish(self):
        with self.assertRaisesRegex(RuntimeError,'export_stale'):
            self.change_during_paged_read(lambda:self.f.store.ingest(self.f.target,
                [{'original_id':'late','label':'Late source change','text':'owned fixture'}]))
        self.assertIsNone(self.f.cache.active());self.assertEqual(list(self.f.cache.generations.iterdir()),[])

    def test_permission_withdrawal_during_paged_verification_cannot_publish(self):
        with self.assertRaisesRegex(ValueError,'scope_invalid'):
            self.change_during_paged_read(lambda:self.f.store.register(platform='graph',original_id='a',
                kind='source',label='Publisher',projects=['one'],permission='denied'))
        self.assertIsNone(self.f.cache.active());self.assertEqual(list(self.f.cache.generations.iterdir()),[])


class DeltaPagedVerificationTests(unittest.TestCase):
    def test_delta_still_uses_complete_paged_verification(self):
        with patch.object(fixture,'FakeEngine',PagedEngine),patch.object(full,'TRAVERSE_ROW_LIMIT',2):
            f=delta_fixture.DeltaTests();f.setUp()
            try:
                f.initial();f.change();result=f.cache.refresh(incremental=True)
                self.assertEqual(result['update_mode'],'engine_delta')
                self.assertEqual(result['paged_outgoing_pages'],2);self.assertEqual(result['paged_outgoing_links'],4)
            finally:f.doCleanups()


if __name__=='__main__':unittest.main()

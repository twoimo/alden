"""Collection source/version scopes, genuine hybrid mode and bounded MCP contracts."""
import io
import json
from pathlib import Path
import sys
import select
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, identity
from alden_collection_retrieval import index_dense, retrieve
from alden_knowledge_mcp import KnowledgeServer, CallToken
from alden_status_mcp import CallControl, StdioServer
import auto_reply_knowledge_graph as kg


class CollectionRetrievalTests(unittest.TestCase):
    def test_title_only_lookup_keeps_source_but_marks_body_unavailable(self):
        ids = self.title_matches(2)
        before = self.store.path.read_bytes()
        result = retrieve(self.root, '35년', projects=['one'], max_relations=0)
        self.assertEqual({p['entity_id'] for p in result['fact_provenance']}, set(ids))
        for proof in result['fact_provenance']:
            self.assertEqual(proof['content_evidence'],
                {'policy': 'source-content-evidence-v6', 'classification': 'empty_body'})
            self.assertEqual(proof['truth_status'], 'title_or_label_reference; original body unavailable')
            self.assertNotIn('selection_basis', proof)
        self.assertTrue(all(text.startswith('[제목·이름 참조 · 본문 없음] ') for text in result['facts']))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_bare_reference_is_preserved_but_never_claims_linked_body_or_body_grounding(self):
        link='https://example.invalid/채용?revision=2#section'
        reference=self.add(self.allowed,'bare-reference',link)
        result=retrieve(self.root,'채용',projects=['one'],max_relations=0)
        proof=next(p for p in result['fact_provenance'] if p.get('entity_id')==reference)
        self.assertEqual(proof['content_evidence'],{'policy':'source-content-evidence-v6','classification':'reference_only'})
        self.assertEqual(proof['truth_status'],'link_reference; referenced destination body unverified')
        self.assertNotIn('selection_basis',proof)
        self.assertEqual(result['body_evidence_coverage']['preferred'],0)
        self.assertIn('[링크 참조 · 연결된 페이지 본문 미확인]','\n'.join(result['facts']))
        self.assertIn(link,'\n'.join(result['facts']))

    def test_real_prose_is_preferred_over_references_and_explicit_reference_lookup_survives(self):
        references=[self.add(self.allowed,'reference-'+str(i),'https://example.invalid/채용/'+str(i)) for i in range(4)]
        document=self.add(self.allowed,'prose','채용 평가 기준을 설명하는 실제 보존 본문입니다.')
        result=retrieve(self.root,'채용',projects=['one'],max_entities=1,max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'],document)
        self.assertEqual(result['body_evidence_coverage']['preferred'],1)
        pinned=retrieve(self.root,'채용',projects=['one'],also=[references[0]],max_entities=1,max_relations=0)
        self.assertEqual(pinned['fact_provenance'][0]['entity_id'],references[0])
        self.assertEqual(pinned['fact_provenance'][0]['content_evidence']['classification'],'reference_only')

    def title_matches(self, count, query='35년'):
        self.store.ingest(self.allowed,[{'original_id':'title-'+str(i),'label':query+' 영상 '+str(i),'text':''}
                                       for i in range(count)])
        return [identity('graph','title-'+str(i)) for i in range(count)]

    def test_retained_body_is_not_crowded_out_by_title_only_candidate_budget(self):
        titles=self.title_matches(12)
        doc=self.add(self.allowed,'body-evidence','이자리35년336. 보관된 본문 진술입니다.')
        self.add(self.other,'forbidden-body','이자리35년336. 허용되지 않은 다른 프로젝트.')
        result=retrieve(self.root,'35년',projects=['one'],candidate_limit=3,max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'],doc)
        self.assertEqual(len(result['candidates']),3)
        self.assertEqual(set(result['candidates'][1:]).issubset(titles),True)
        proof=result['fact_provenance'][0]
        self.assertEqual(proof['truth_status'],'source_record; not independently verified')
        self.assertTrue(proof['provenance_valid']);self.assertEqual(proof['projects'],['one'])
        self.assertIn('matching_current_version_bound_body',proof['selection_basis'])

    def test_typed_metadata_body_is_not_preferred_over_verified_content(self):
        self.title_matches(4)
        self.store.ingest(self.allowed,[{'original_id':'metadata-body','label':'35년','text':'35년',
                                         'raw':{'kind':'metadata','content_indexed':False}}])
        doc=self.add(self.allowed,'real-body','이자리35년336. 본문을 보존했습니다.')
        result=retrieve(self.root,'35년',projects=['one'],max_entities=1,max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'],doc)
        self.assertEqual(result['body_evidence_coverage']['preferred'],1)

    def test_literal_body_preference_keeps_explicit_pins_and_dense_only_weights(self):
        titles=self.title_matches(5)
        doc=self.add(self.allowed,'body-evidence','이자리35년336. 본문 진술입니다.')
        with self.profile():
            index_dense(self.root,['one'],embed=lambda values:[[0.,1.] if '본문' in value else [1.,0.] for value in values])
            normal=retrieve(self.root,'35년',projects=['one'],max_entities=1,max_relations=0,query_embed=lambda _:[1.,0.])
            self.assertEqual(normal['fact_provenance'][0]['entity_id'],doc)
            dense=retrieve(self.root,'35년',projects=['one'],max_entities=1,max_relations=0,
                           query_embed=lambda _:[1.,0.],rrf_weights=[0.,1.])
            self.assertIn(dense['fact_provenance'][0]['entity_id'],titles)
            self.assertNotIn('ranking_policy',dense)
            pinned=retrieve(self.root,'35년',projects=['one'],max_entities=1,max_relations=0,
                            query_embed=lambda _:[1.,0.],also=[titles[0]])
            self.assertEqual(pinned['fact_provenance'][0]['entity_id'],titles[0])

    def test_title_lookup_still_works_without_a_matching_body(self):
        titles=self.title_matches(4)
        result=retrieve(self.root,'35년',projects=['one'],max_entities=2,max_relations=0)
        self.assertEqual(len(result['fact_provenance']),2)
        self.assertTrue(all(p['entity_id'] in titles for p in result['fact_provenance']))
        self.assertNotIn('ranking_policy',result)

    def test_grounding_requires_all_query_words_in_one_body_and_keeps_particles(self):
        self.store.ingest(self.allowed,[{'original_id':'split-title-body','label':'사과','text':'35년'},
            {'original_id':'complete-body','label':'','text':'이자리35년336과 사과에 대한 보관 본문'},
            {'original_id':'wrong-number','label':'','text':'2035년 사과에 대한 다른 보관 본문'}])
        result=retrieve(self.root,'35년 사과',projects=['one'],max_entities=1,max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'],identity('graph','complete-body'))
        self.assertEqual(result['body_evidence_coverage']['matched'],1)
        particle=retrieve(self.root,'35년은 사과',projects=['one'],max_entities=1,max_relations=0)
        self.assertEqual(particle['fact_provenance'][0]['entity_id'],identity('graph','complete-body'))

    def test_source_metadata_edit_cannot_promote_raw_noncontent(self):
        self.title_matches(5)
        self.store.ingest(self.allowed,[{'original_id':'noncontent','label':'35년','text':'35년',
            'metadata':{'source':{'kind':'metadata'}},'raw':{'kind':'metadata','content_indexed':False}}])
        other=self.add(self.allowed,'actual-body','이자리35년336 보관 본문')
        with self.store.database() as db:
            row=db.execute('SELECT id,metadata FROM versions WHERE body=?',('35년',)).fetchone()
            metadata=json.loads(row['metadata']);metadata['source']={'kind':'unknown'}
            db.execute('UPDATE versions SET metadata=? WHERE id=?',(json.dumps(metadata),row['id']))
        result=retrieve(self.root,'35년',projects=['one'],max_entities=1,max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'],other)
        self.assertEqual(result['body_evidence_coverage']['preferred'],1)

    def test_body_evidence_verification_is_bounded_and_reports_partial_coverage(self):
        self.store.ingest(self.allowed,[{'original_id':'body-'+str(i),'label':'','text':'35년 보관 본문 '+str(i)} for i in range(105)])
        result=retrieve(self.root,'35년',projects=['one'],candidate_limit=3,max_entities=1,max_relations=0)
        self.assertEqual(len(result['candidates']),3)
        self.assertEqual(result['body_evidence_coverage']['matched'],105)
        self.assertEqual(result['body_evidence_coverage']['verified'],100)
        self.assertTrue(result['body_evidence_coverage']['truncated'])

    def test_evidence_preference_preserves_rrf_order_inside_both_tiers(self):
        import alden_collection_retrieval as retrieval
        titles=self.title_matches(5)
        self.add(self.allowed,'body-a','이자리35년336. 첫 보관 본문입니다.')
        self.add(self.allowed,'body-b','이자리35년336. 다음 보관 본문입니다.')
        with self.profile():
            index_dense(self.root,['one'],embed=self.embed)
            kwargs={'projects':['one'],'max_entities':10,'max_relations':0,'query_embed':lambda _:[1.,0.]}
            with patch.object(retrieval,'_prefer_body_literal_evidence',side_effect=lambda store,rows,ranked,*args:(ranked,set(),None)):
                before=retrieve(self.root,'35년',**kwargs)
            after=retrieve(self.root,'35년',**kwargs)
        for group in (set(titles),set(before['candidates'])-set(titles)):
            self.assertEqual([i for i in before['candidates'] if i in group],[i for i in after['candidates'] if i in group])

    def test_explicit_latest_keeps_its_time_selection_before_body_preference(self):
        self.store.ingest(self.allowed,[{'original_id':'old-publication','label':'과거 기록',
            'text':'아카이브최신약관관련 기록','published_at':'2020-01-01T00:00:00Z'},
            {'original_id':'new-publication','label':'최신 약관','text':'','published_at':'2026-09-01T00:00:00Z'}])
        result=retrieve(self.root,'최신 약관',projects=['one'],candidate_limit=1,max_entities=1,max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['entity_id'],identity('graph','new-publication'))
        self.assertEqual(result['fact_provenance'][0]['observation_time'],'2026-09-01T00:00:00Z')
        self.assertNotIn('ranking_policy',result)

    def test_evidence_source_checks_honor_cancellation_and_current_version_changes(self):
        import alden_collection_retrieval as retrieval
        self.add(self.allowed,'body-a','35년 보관 본문')
        self.add(self.allowed,'body-b','35년 보관 본문')
        original=retrieval._verified_source;stopped=[False]
        def cancel_after_first(*args):
            result=original(*args);stopped[0]=True;return result
        with patch.object(retrieval,'_verified_source',side_effect=cancel_after_first),self.assertRaisesRegex(RuntimeError,'collection_retrieval_cancelled'):
            retrieve(self.root,'35년',projects=['one'],cancelled=lambda:stopped[0])
        changed=[False]
        def change_before_check(*args):
            if not changed[0]:
                changed[0]=True
                row=args[2]
                self.store.ingest(self.allowed,[{'original_id':row['original_id'],'label':'','text':'다른 개정 본문'}])
            return original(*args)
        with patch.object(retrieval,'_verified_source',side_effect=change_before_check),self.assertRaisesRegex(RuntimeError,'collection_graph_version_changed'):
            retrieve(self.root,'35년',projects=['one'])

    def test_cached_forged_body_cannot_win_the_new_evidence_preference(self):
        import sqlite3
        self.title_matches(5)
        self.store.ingest(self.allowed,[{'original_id':'cache-forgery','label':'',
           'text':'','raw':{'localOriginalText':'원래 보존된 다른 내용입니다.'}}])
        doc=identity('graph','cache-forgery')
        with self.profile():
            index_dense(self.root,['one'],embed=self.embed)
            with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
                db.execute('UPDATE texts SET body=? WHERE document_id=?',('가짜35년336 본문',doc))
            with self.assertRaisesRegex(RuntimeError,'collection_source_integrity'):
                retrieve(self.root,'35년',projects=['one'],candidate_limit=2,max_entities=1,max_relations=0,
                         query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))

    def test_attached_korean_source_is_naturally_retrieved_with_unverified_provenance(self):
        body='이자리35년336 그리고 두번째올해49년814. 서로 다른 식당의 진술입니다.'
        doc=self.add(self.allowed,'attached-claim',body)
        self.add(self.other,'foreign-claim',body)
        before=self.store.path.read_bytes()
        for query in ('35년','49년'):
            with self.subTest(query=query):
                result=retrieve(self.root,query,projects=['one'],max_entities=1,max_relations=0,
                                query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))
                self.assertEqual(result['search_mode'],'bm25_with_literals')
                self.assertEqual(result['fact_provenance'][0]['entity_id'],doc)
                self.assertEqual(result['fact_provenance'][0]['lexical_match'],'korean_literal_substring')
                self.assertEqual(result['fact_provenance'][0]['truth_status'],'source_record; not independently verified')
                self.assertEqual(result['fact_provenance'][0]['projects'],['one'])
                self.assertIn(body,result['facts'][0])
        self.assertEqual(self.store.path.read_bytes(),before)

    def test_retained_korean_text_fallback_keeps_raw_binding_and_rejects_forged_cache(self):
        body='이자리35년336. 원래 보존된 본문입니다.'
        self.store.ingest(self.allowed,[{'platform':'graph','original_id':'retained-korean',
            'label':'','text':'','raw':{'localOriginalText':body}}])
        doc=identity('graph','retained-korean')
        with self.profile():
            index_dense(self.root,['one'],embed=self.embed)
            result=retrieve(self.root,'35년',projects=['one'],max_entities=1,max_relations=0,
                            query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))
            self.assertEqual(result['search_mode'],'bm25_with_literals')
            self.assertEqual(result['facts'],[body])
            self.assertEqual(result['fact_provenance'][0]['body_source'],'retained_record.localOriginalText')
            import sqlite3
            with sqlite3.connect(self.store.root/'retrieval.sqlite3') as db:
                db.execute('UPDATE texts SET body=? WHERE document_id=?',('가짜올해49년814',doc))
            with self.assertRaisesRegex(RuntimeError,'collection_source_integrity'):
                retrieve(self.root,'49년',projects=['one'],max_entities=1,max_relations=0,
                         query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('disabled')))

    def test_large_shared_membership_proof_fails_with_a_budget_error_instead_of_partial_attribution(self):
        doc=None
        for i in range(257):
            target=self.store.register(platform='graph',original_id='shared-'+str(i),kind='source',
                label='shared',projects=['one'])
            doc=self.add(target,'widely-shared','original body')
        with self.assertRaisesRegex(RuntimeError,'collection_retrieval_budget'):
            retrieve(self.root,'shared',projects=['one'],also=[doc],max_entities=1,max_relations=0)

    def test_union_proof_does_not_borrow_another_current_version_membership(self):
        doc=self.add(self.allowed,'same-original','older original source')
        self.add(self.other,'same-original','newer other source')
        result=retrieve(self.root,'shared',projects=['one','two'],also=[doc],max_entities=1,max_relations=0)
        proof=result['fact_provenance'][0]
        self.assertEqual(proof['projects'],['two'])
        self.assertEqual({m['target_id'] for m in proof['source_memberships']},{self.other})
        self.assertIn('newer other source',result['facts'][0])
        self.assertNotIn('older original source',result['facts'][0])

    def test_shared_version_exposes_only_requested_permitted_memberships(self):
        shared=self.store.register(platform='graph',original_id='shared',kind='source',label='shared',
                                   projects=['one','two','not-requested'])
        doc=self.add(shared,'both','original shared source')
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=? AND project='two'",(shared,))
        result=retrieve(self.root,'shared',projects=['one','two'],also=[doc],max_entities=1,max_relations=0)
        proof=result['fact_provenance'][0]
        self.assertEqual(proof['projects'],['one'])
        self.assertEqual([m['project'] for m in proof['source_memberships']],['one'])
        with self.store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=? AND project='two'",(shared,))
        proof=retrieve(self.root,'shared',projects=['two','one'],also=[doc],max_entities=1,max_relations=0)['fact_provenance'][0]
        self.assertEqual(proof['projects'],['one','two'])
        self.assertEqual(proof['requested_projects'],['two','one'])
        self.assertEqual(len(proof['source_memberships']),2)

    def test_metadata_summary_stays_searchable_without_certifying_original_body(self):
        self.store.ingest(self.allowed,[{'platform':'graph','original_id':'description','label':'shared term',
            'text':'A file description','coverage':'existing privacy-filtered derived metadata; original bytes not opened',
            'raw':{'summary':'A file description','content_indexed':1}}])
        result=retrieve(self.root,'shared',projects=['one'],max_relations=0)
        self.assertEqual(result['fact_provenance'][0]['content_evidence']['classification'],'metadata_description')
        self.assertEqual(result['fact_provenance'][0]['truth_status'],'source_description; original body unverified')
        self.assertTrue(result['facts'][0].startswith('[자료 설명 · 원래 본문 미확인]'))

    def test_relation_proof_uses_its_current_stored_revision_and_project(self):
        docs=[{'platform':'graph','original_id':n,'label':'shared term '+n,'text':'original '+n} for n in ('a','b')]
        rel={'source_platform':'graph','source_id':'a','target_platform':'graph','target_id':'b',
             'type':'mentions','evidence':{'location':'synthetic declared source'}}
        self.store.ingest(self.allowed,docs,relations=[rel])
        result=retrieve(self.root,'shared',projects=['one','two'],also=[identity('graph','a')],max_entities=1)
        proof=next(p for p in result['fact_provenance'] if p['fact_type']=='relation')
        self.assertEqual(proof['projects'],['one'])
        self.assertEqual(proof['requested_projects'],['one','two'])
        self.assertEqual(proof['source_memberships'][0]['target_id'],self.allowed)
        with self.store.database() as db:
            db.execute("UPDATE relations SET evidence=? WHERE id=?",('{"location":"fabricated"}',proof['relation_id']))
        with self.assertRaisesRegex(RuntimeError,'collection_relation_integrity'):
            retrieve(self.root,'shared',projects=['one'],also=[identity('graph','a')],max_entities=1)

    def test_projection_field_changes_fail_and_json_key_order_does_not(self):
        doc=self.add(self.allowed,'bound-document','original body')
        with self.store.database() as db:
            original=dict(db.execute('SELECT * FROM versions WHERE document_id=?',(doc,)).fetchone())
        for field,value in [('label','changed label'),('processing_version','different-parser'),
                            ('projection_sha256','f'*64),('metadata','{"other":"changed"}')]:
            with self.subTest(field=field):
                with self.store.database() as db:db.execute('UPDATE versions SET '+field+'=? WHERE id=?',(value,original['id']))
                with self.assertRaisesRegex(RuntimeError,'collection_version_integrity'):
                    retrieve(self.root,'shared',projects=['one'],also=[doc],max_entities=1,max_relations=0)
                with self.store.database() as db:db.execute('UPDATE versions SET '+field+'=? WHERE id=?',(original[field],original['id']))
        metadata=json.loads(original['metadata'])
        with self.store.database() as db:
            db.execute('UPDATE versions SET metadata=? WHERE id=?',(json.dumps(dict(reversed(list(metadata.items())))),original['id']))
        self.assertTrue(retrieve(self.root,'shared',projects=['one'],also=[doc],max_entities=1,max_relations=0)['fact_provenance'][0]['provenance_valid'])

    def test_revised_version_and_revoked_permission_after_ranking_cannot_publish_old_proof(self):
        for mode in ('revision','permission'):
            with self.subTest(mode=mode):
                doc=self.add(self.allowed,'racing-'+mode,'original body')
                original_search=CollectionStore.search
                def changed(store,*args,**kwargs):
                    result=original_search(store,*args,**kwargs)
                    if mode=='revision':
                        self.store.ingest(self.allowed,[{'platform':'graph','original_id':'racing-'+mode,
                            'label':'shared term','text':'original body'}],processing_version='different-parser')
                    else:
                        with self.store.database() as db:
                            db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?",(self.allowed,))
                    return result
                with patch.object(CollectionStore,'search',changed),self.assertRaisesRegex(RuntimeError,'collection_graph_version_changed'):
                    retrieve(self.root,'shared',projects=['one'],also=[doc],max_entities=1,max_relations=0)


    def test_union_query_does_not_turn_request_scope_into_source_membership(self):
        doc=self.add(self.allowed,'research-only','original shared source')
        self.add(self.other,'separate-source','different evidence')
        result=retrieve(self.root,'shared',projects=['one','two'],also=[doc],max_entities=1,max_relations=0)
        proof=result['fact_provenance'][0]
        self.assertEqual(result['projects'],['one','two'])
        self.assertEqual(proof['projects'],['one'])
        self.assertEqual(proof['requested_projects'],['one','two'])
        self.assertEqual(proof['source_memberships'],[{'target_id':self.allowed,'project':'one',
            'permission':'local-private','source_version':proof['source_version']}])

    def test_modified_stored_body_cannot_be_certified_by_an_unchanged_raw_hash(self):
        doc=self.add(self.allowed,'corrupt-projection','original body')
        with self.store.database() as db:
            db.execute("UPDATE versions SET body='fabricated statement' WHERE document_id=?",(doc,))
        with self.assertRaisesRegex(RuntimeError,'collection_version_integrity'):
            retrieve(self.root,'shared',projects=['one'],also=[doc],max_entities=1,max_relations=0)

    def test_latest_source_uses_publication_not_collection_time_and_keeps_history(self):
        self.store.ingest(self.allowed,[{'original_id':'old','label':'shared term','text':'older terms','published_at':'2026-08-01T00:00:00Z'}])
        self.store.ingest(self.allowed,[{'original_id':'new','label':'shared term','text':'recent terms','published_at':'2026-09-01T00:00:00Z'}])
        result=retrieve(self.root,'shared term latest',projects=['one'])
        self.assertEqual([p['entity_id'] for p in result['fact_provenance']],[identity('graph','new')])
        self.assertEqual(result['temporal_selection']['observation_basis'],['source_published_at'])
        self.assertIn('효력의 증명은 아님',result['facts'][0])
        history=retrieve(self.root,'shared term 이전 기록',projects=['one'])
        self.assertEqual(len(history['fact_provenance']),2)
        with self.store.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM documents').fetchone()[0],2)
    def setUp(self):
        self.temp = TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve();self.store = CollectionStore(self.root)
        self.allowed = self.store.register(platform='graph', original_id='allowed', kind='source', label='allowed', projects=['one'])
        self.other = self.store.register(platform='graph', original_id='other', kind='source', label='other', projects=['two'])

    def add(self, target, original, text):
        self.store.ingest(target, [{'platform': 'graph', 'original_id': original, 'label': 'shared term', 'text': text}])
        return identity('graph', original)

    def embed(self, texts):
        return [[1., 0.] if 'original' in text else [0., 1.] for text in texts]

    def profile(self):
        return patch.object(kg, '_active_dense_embedding_model', return_value='fixed-test-encoder')

    def test_visible_version_is_project_specific_and_hash_verified(self):
        self.add(self.allowed, 'same', 'original body');self.add(self.other, 'same', 'private other body')
        result = retrieve(self.root, 'shared', projects=['one'])
        self.assertEqual(result['facts'], ['shared term: original body'])
        proof = result['fact_provenance'][0]
        self.assertEqual(proof['projects'], ['one']);self.assertTrue(proof['provenance_valid'])
        with self.store.database() as db:
            path = db.execute('SELECT raw_path FROM versions WHERE id=?', (proof['source_version'],)).fetchone()[0]
        (self.store.blobs / path).write_text('corrupt')
        with self.assertRaisesRegex(RuntimeError, 'source_integrity'):
            retrieve(self.root, 'shared', projects=['one'])

    def test_full_scope_vectors_required_for_rrf_and_old_revision_is_never_reused(self):
        self.add(self.allowed, 'a', 'original');self.add(self.allowed, 'b', 'another')
        with self.profile():
            result = index_dense(self.root, ['one'], embed=self.embed)
            self.assertEqual(result['documents'], 2);self.assertEqual(result['embedded_changed'], 2)
            self.assertEqual(index_dense(self.root, ['one'], embed=self.embed)['embedded_changed'], 0)
            ranked = retrieve(self.root, 'semantic only', projects=['one'], query_embed=lambda _: [1.,0.])
            self.assertEqual(ranked['search_mode'], 'rrf');self.assertIn('original', ranked['facts'][0])
            self.add(self.allowed, 'a', 'new revision')
            pending = retrieve(self.root, 'shared', projects=['one'], query_embed=lambda _: self.fail('stale vectors must not embed the query'))
            self.assertEqual(pending['search_mode'], 'bm25_only');self.assertIn('new revision', '\n'.join(pending['facts']))

    def test_index_batches_across_documents_and_cancelled_partial_work_resumes(self):
        for n in range(17):self.add(self.allowed, str(n), 'original')
        calls=[]
        def embed(texts):calls.append(len(texts));return self.embed(texts)
        with self.profile():
            report=index_dense(self.root,['one'],embed=embed)
        self.assertEqual(calls,[8,8,1]);self.assertEqual(report['embedded_changed'],17)
        self.add(self.allowed, 'new', 'original')
        with self.profile(), self.assertRaisesRegex(RuntimeError,'cancelled'):
            index_dense(self.root,['one'],embed=embed,cancelled=lambda:True)
        with self.profile():self.assertEqual(index_dense(self.root,['one'],embed=embed)['embedded_changed'],1)

    def test_processing_revision_reuses_only_exact_verified_text_and_encoder(self):
        records=[{'platform':'graph','original_id':'a','label':'shared term','text':'original'}]
        self.store.ingest(self.allowed,records,processing_version='parser-one')
        with self.profile():
            self.assertEqual(index_dense(self.root,['one'],embed=self.embed)['embedded_changed'],1)
            self.store.ingest(self.allowed,records,processing_version='parser-two')
            report=index_dense(self.root,['one'],embed=lambda _:self.fail('unchanged input must reuse its vector'))
            self.assertEqual(report['reused_versions'],1);self.assertEqual(report['embedded_changed'],0)
            result=retrieve(self.root,'shared',projects=['one'],query_embed=lambda _:[1.,0.])
            self.assertEqual(result['search_mode'],'rrf');self.assertIn('original',result['facts'][0])
            self.store.ingest(self.allowed,[{**records[0],'text':'changed content'}],processing_version='parser-three')
            self.assertEqual(index_dense(self.root,['one'],embed=self.embed)['embedded_changed'],1)
        with patch.object(kg,'_active_dense_embedding_model',return_value='different-encoder'):
            self.assertEqual(index_dense(self.root,['one'],embed=self.embed)['embedded_changed'],1)

    def test_mcp_scope_and_unknown_arguments_cannot_expand_startup_authority(self):
        self.add(self.allowed,'a','original');server=KnowledgeServer(self.root,['one'])
        for args in [{'projects':['two'],'query':'shared'}, {'projects':[],'query':'shared'},
                     {'projects':['one'],'query':'shared','state_root':'/outside'},
                     {'projects':['one'],'query':'shared','limit':True}]:
            self.assertFalse(server.valid_call({'name':'alden_knowledge_search','arguments':args}))
        self.assertFalse(server.valid_call({'name':[],'arguments':{}}))
        frames=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{}}},
                {'jsonrpc':'2.0','method':'notifications/initialized'},
                {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'alden_knowledge_search','arguments':{'projects':['one'],'query':'shared'}}}]
        script = Path(__file__).resolve().parents[1] / 'scripts/alden_knowledge_mcp.py'
        child = subprocess.Popen([sys.executable, '-B', str(script), '--state-root', str(self.root), '--allow-project', 'one'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            values = []
            for frame in frames:
                child.stdin.write(json.dumps(frame).encode() + b'\n');child.stdin.flush()
                if 'id' in frame:
                    self.assertTrue(select.select([child.stdout], [], [], 4)[0], 'owned MCP child did not reply')
                    values.append(json.loads(child.stdout.readline()))
            reply = next(x for x in values if x.get('id') == 2)
        finally:
            child.stdin.close()
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.wait()
            child.stdout.close();child.stderr.close()
        self.assertFalse(reply['result']['isError']);data=json.loads(reply['result']['content'][0]['text'])
        self.assertEqual(data['facts'],['shared term: original'])

    def test_status_default_catalog_still_exposes_no_knowledge(self):
        self.assertEqual([t['name'] for t in StdioServer(None).tool_catalog()],['alden_status'])
        self.assertFalse(StdioServer(None).valid_call({'name':'alden_knowledge_search','arguments':{}}))

    def test_individual_mcp_cancellation_does_not_latch_global_abort(self):
        control=CallControl();token=CallToken(self.root/'alden-abort.json',control)
        self.assertFalse(token.is_cancelled());control.stop();self.assertTrue(token.is_cancelled())
        self.assertFalse((self.root/'alden-abort.json').exists())

    def test_explicit_project_bundle_never_accepts_a_kakao_room_scope(self):
        with self.assertRaisesRegex(ValueError,'local_project_scope_only'):
            kg.retrieve_knowledge_bundle('shared',state_root=self.root,projects=['one'],chat_id='123')

    def test_voice_project_request_keeps_collection_version_proof(self):
        self.add(self.allowed, 'a', 'original')
        from alden_abort import AbortController
        from alden_voice import _voice_knowledge_reference
        reference, metrics = _voice_knowledge_reference('one 지식 shared 찾아줘', [], self.root, AbortController(self.root).token())
        self.assertEqual(metrics['state'], 'found')
        proof = metrics['graph_provenance'][0]
        self.assertEqual(proof['projects'], ['one']);self.assertTrue(proof['source_version'])
        self.assertIn('original', reference)

    def test_index_of_multiple_projects_retains_each_authorized_version(self):
        self.add(self.allowed, 'same', 'original');self.add(self.other, 'same', 'private other')
        with self.profile():
            result=index_dense(self.root,['one','two'],embed=self.embed)
            self.assertEqual(result['documents'],2)
            first=retrieve(self.root,'shared',projects=['one'],query_embed=lambda _: [1.,0.])
            second=retrieve(self.root,'shared',projects=['two'],query_embed=lambda _: [0.,1.])
        self.assertEqual(first['search_mode'],'rrf');self.assertEqual(second['search_mode'],'rrf')
        self.assertIn('original',first['facts'][0]);self.assertIn('private other',second['facts'][0])

    def test_unchanged_multi_project_index_does_not_rewrite_current_vectors(self):
        import sqlite3
        first={'platform':'graph','original_id':'same','label':'shared term','text':'original',
               'raw':{'viewCount':10}}
        second={**first,'text':'private other','raw':{'viewCount':20}}
        self.store.ingest(self.allowed,[first]);self.store.ingest(self.other,[second])
        self.store.register(platform='graph',original_id='empty-target',kind='source',label='empty',projects=['empty'])
        path=self.store.root/'retrieval.sqlite3'
        with self.profile():
            self.assertEqual(index_dense(self.root,['one','two'],embed=self.embed)['embedded_changed'],2)
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE vector_write_probe(writes INTEGER NOT NULL)')
                db.execute('INSERT INTO vector_write_probe VALUES(0)')
                db.execute('''CREATE TRIGGER vector_insert_probe AFTER INSERT ON vectors
                    BEGIN UPDATE vector_write_probe SET writes=writes+1; END''')
                saved=db.execute('SELECT * FROM vectors ORDER BY document_id,version').fetchall()
            source_before=self.store.path.read_bytes()
            for projects in (['one','two'],['two','one'],['one','one','two'],['one','two','empty'],['one']):
                with self.subTest(projects=projects):
                    with sqlite3.connect(path) as db:db.execute('UPDATE vector_write_probe SET writes=0')
                    report=index_dense(self.root,projects,embed=lambda _:self.fail('cached versions must not re-embed'))
                    with sqlite3.connect(path) as db:
                        self.assertEqual(db.execute('SELECT writes FROM vector_write_probe').fetchone()[0],0)
                        self.assertEqual(db.execute('SELECT * FROM vectors ORDER BY document_id,version').fetchall(),saved)
                    self.assertEqual(report['embedded_changed'],0)
                    self.assertEqual(report['reused_versions'],0)
                    self.assertEqual(self.store.path.read_bytes(),source_before)
            # A genuine raw-metadata revision keeps its own version and copies
            # exactly one admitted vector when encoder input/profile is equal.
            with sqlite3.connect(path) as db:db.execute('UPDATE vector_write_probe SET writes=0')
            self.store.ingest(self.other,[{**second,'raw':{'viewCount':21}}])
            report=index_dense(self.root,['one','two'],embed=lambda _:self.fail('same body must reuse only the changed version'))
            self.assertEqual(report['embedded_changed'],0);self.assertEqual(report['reused_versions'],1)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT writes FROM vector_write_probe').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0],3)
            first_result=retrieve(self.root,'shared',projects=['one'],query_embed=lambda _:[1.,0.])
            second_result=retrieve(self.root,'shared',projects=['two'],query_embed=lambda _:[0.,1.])
            self.assertIn('original',first_result['facts'][0]);self.assertIn('private other',second_result['facts'][0])
            self.assertEqual(first_result['fact_provenance'][0]['projects'],['one'])
            self.assertEqual(second_result['fact_provenance'][0]['projects'],['two'])
            with sqlite3.connect(path) as db:db.execute('UPDATE vector_write_probe SET writes=0')
            self.store.ingest(self.other,[{**second,'text':'changed body','raw':{'viewCount':22}}])
            report=index_dense(self.root,['one','two'],embed=self.embed)
            self.assertEqual(report['embedded_changed'],1);self.assertEqual(report['reused_versions'],0)
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT writes FROM vector_write_probe').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0],4)

    def test_collection_time_filter_applies_before_candidate_limit(self):
        self.add(self.allowed,'old','original');self.add(self.allowed,'new','newer')
        with self.store.database() as db:
            db.execute('UPDATE versions SET collected_at=CASE WHEN document_id=? THEN 10 ELSE 20 END', (identity('graph','old'),))
        result=retrieve(self.root,'shared',projects=['one'],time_from=0,time_to=15,candidate_limit=1)
        self.assertEqual(result['facts'],['shared term: original']);self.assertEqual(result['time_basis'],'collection_time')
        with self.assertRaisesRegex(ValueError,'time_filter_invalid'):
            retrieve(self.root,'shared',projects=['one'],time_from=float('nan'))

    def test_empty_source_text_is_retained_but_never_invented_for_embedding(self):
        self.store.ingest(self.allowed,[{'original_id':'blank','label':'','text':''}])
        with self.profile():
            result=index_dense(self.root,['one'],embed=lambda _:self.fail('empty source must not be embedded'))
        self.assertEqual(result['source_versions'],1);self.assertEqual(result['unsearchable_versions'],1)
        self.assertEqual(result['documents'],0)
        self.assertEqual(retrieve(self.root,'anything',projects=['one'])['facts'],[])
        with self.store.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0],1)

    def test_retained_thread_text_is_searchable_without_rewriting_source_versions(self):
        record={'platform':'graph','original_id':'post','label':'','text':'',
                'raw':{'localOriginalText':'retained source text','author':'actual source author'}}
        self.store.ingest(self.allowed,[record])
        with self.store.database() as db:before=tuple(db.execute('SELECT id,raw_sha256,label,body FROM versions').fetchone())
        with self.profile():
            report=index_dense(self.root,['one'],embed=self.embed)
            result=retrieve(self.root,'retained',projects=['one'],query_embed=lambda _: [0.,1.])
        self.assertEqual(report['documents'],1);self.assertEqual(report['unsearchable_versions'],0)
        self.assertEqual(result['facts'],['retained source text'])
        proof=result['fact_provenance'][0]
        self.assertEqual(proof['source_version'],before[0]);self.assertEqual(proof['note_hash'],before[1])
        self.assertEqual(proof['body_source'],'retained_record.localOriginalText')
        self.assertEqual(proof['source_author']['author'],'actual source author')
        with self.store.database() as db:self.assertEqual(tuple(db.execute('SELECT id,raw_sha256,label,body FROM versions').fetchone()),before)


if __name__=='__main__':unittest.main()

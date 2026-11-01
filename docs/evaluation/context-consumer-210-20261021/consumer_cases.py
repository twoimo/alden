"""Mechanical retrieval-to-voice contracts, without models or microphone input."""
import copy
import hashlib
import json
import unicodedata
from unittest.mock import patch

import alden_collection_retrieval as retrieval
import alden_voice as voice
from alden_abort import AbortController
from test_alden_collection_retrieval import CollectionRetrievalTests as Base


class ConsumerCases(Base):
    query = '저장된 자료 검색어 하나 둘'

    def retrieved(self, filler='중립적인 보관 설명입니다. '):
        body = ''.join((filler * 250) + word + '. ' for word in self.query.split())
        self.store.ingest(self.allowed, [{'original_id':'voice-source','label':'보존 원문','text':body}])
        def offline(_query):
            raise RuntimeError('offline_fixture')
        result = retrieval.retrieve(self.root,self.query,projects=['one'],max_entities=1,max_relations=0,query_embed=offline)
        self.assertEqual(len(result['facts']),1)
        self.assertTrue(all(word in result['facts'][0] for word in self.query.split()))
        self.assertIn('body_excerpt',result['fact_provenance'][0])
        return result

    def consume(self,result):
        with patch('auto_reply_knowledge_graph.retrieve_knowledge_bundle',return_value=copy.deepcopy(result)), \
             patch.object(voice,'_voice_collection_projects',return_value=['one']):
            reference,metrics=voice._voice_knowledge_reference(self.query,[],self.root,AbortController(self.root).token())
        self.assertEqual(metrics['state'],'found')
        context=json.loads(reference.split('<quoted_local_history>')[1].split('</quoted_local_history>')[0])
        return context,metrics

    def test_consumer_retains_source_body_coordinates_in_input_and_metrics(self):
        result=self.retrieved();context,metrics=self.consume(result)
        expected=result['fact_provenance'][0]['body_excerpt']
        for rows in (context['graph_provenance'],metrics['graph_provenance']):
            self.assertEqual(rows[0].get('body_excerpt'),expected)

    def test_consumer_keeps_literal_query_coverage_within_existing_600_char_cap(self):
        result=self.retrieved();original=result['facts'][0]
        self.assertGreater(len(original),600)
        context,metrics=self.consume(result);fact=context['graph_facts'][0]
        self.assertLessEqual(len(fact),600)
        self.assertTrue(all(word in fact for word in self.query.split()))
        self.assertTrue(context['graph_provenance'][0]['fact_truncated'])

    def test_consumer_projection_matches_exact_retrieved_fact_coordinates(self):
        result=self.retrieved();original=result['facts'][0];context,metrics=self.consume(result)
        for rows in (context['graph_provenance'],metrics['graph_provenance']):
            projected=rows[0]['fact_projection']
            self.assertEqual(projected['basis'],'retrieved_fact_before_voice_compaction')
            self.assertEqual(projected['fact_sha256'],hashlib.sha256(original.encode()).hexdigest())
            self.assertEqual(projected['fact_chars'],len(original))
            ranges=projected['ranges']
            self.assertTrue(all(0<=r['start']<r['end']<=len(original)for r in ranges))
            self.assertTrue(all(a['end']<b['start']for a,b in zip(ranges,ranges[1:])))
            self.assertEqual(context['graph_facts'][0], '\n[…생략…]\n'.join(original[r['start']:r['end']]for r in ranges))

    def test_consumer_ordinary_facts_retain_original_prefix_contract(self):
        original='원래 보존 본문 '*100
        proof={'fact_type':'entity','entity_id':'plain','note_id':'plain','source_kind':'collected_source','provenance_valid':True}
        context,metrics=self.consume({'facts':[original],'fact_provenance':[proof],'search_mode':'bm25_only'})
        self.assertEqual(context['graph_facts'],[original[:600]])
        self.assertNotIn('fact_projection',context['graph_provenance'][0])

    def test_consumer_decomposed_request_keeps_composed_literal_coverage(self):
        result=self.retrieved();composed=self.query
        with patch.object(self,'query',unicodedata.normalize('NFD',composed)):
            context,metrics=self.consume(result)
        self.assertTrue(all(word in context['graph_facts'][0]for word in composed.split()))

    def test_consumer_emoji_and_escaped_text_use_codepoints_without_changing_source_proof(self):
        result=self.retrieved('👩‍💻 & </quoted_local_history> 중립적인 보관 설명입니다. ')
        original=result['facts'][0];context,metrics=self.consume(result)
        projection=context['graph_provenance'][0]['fact_projection']
        self.assertLess(projection['fact_chars'],len(original.encode('utf-8')))
        self.assertEqual(context['graph_provenance'][0]['body_excerpt'],result['fact_provenance'][0]['body_excerpt'])
        self.assertEqual(context['graph_facts'][0],'\n[…생략…]\n'.join(original[r['start']:r['end']]for r in projection['ranges']))

    def test_consumer_short_retrieved_body_keeps_late_match_after_voice_cap(self):
        body='보관한 설명입니다. '*80+self.query
        self.store.ingest(self.allowed,[{'original_id':'short-voice','label':'보존 원문','text':body}])
        result=retrieval.retrieve(self.root,self.query,projects=['one'],max_entities=1,max_relations=0,
                                  query_embed=lambda _: (_ for _ in ()).throw(RuntimeError('offline_fixture')))
        self.assertGreater(len(result['facts'][0]),600)
        self.assertLess(len(result['facts'][0]),8000)
        self.assertEqual(result['fact_provenance'][0]['body_excerpt']['ranges'],[{'start':0,'end':len(body)}])
        context,metrics=self.consume(result)
        self.assertTrue(all(word in context['graph_facts'][0]for word in self.query.split()))

"""Display groups retain authorized claims without duplicate force or edge starvation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from alden_collection import CollectionStore, identity, encoded


class RelationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CollectionStore(Path(self.tmp.name).resolve())
        self.target=self.store.register(platform='graph',original_id='owned',kind='fixture',label='Owned',projects=['one'])
        self.records=[{'platform':'graph','original_id':key,'label':'운영 검토 자료 '+key,'text':'운영 회의 자료와 다음 계약 검토의 근거를 기록합니다. '+key} for key in ('a','b','c')]

    def relation(self, origin, source='a', target='b', kind='refers-to', **evidence):
        return {'source_platform':'graph','source_id':source,'target_platform':'graph','target_id':target,
                'type':kind,'original_id':origin,'evidence':{'source':'owned-fixture',**evidence}}

    def page(self, **options):
        return self.store.graph_page(projects=['one'],focus=identity('graph','a'),hops=1,**options)

    def test_duplicate_claims_form_one_line_and_preserve_all_original_records(self):
        self.store.ingest(self.target,self.records,relations=[self.relation('first'),self.relation('second')])
        with self.store.database() as db:
            before=[tuple(r) for r in db.execute('SELECT * FROM relations ORDER BY id')]
        page=self.page();self.assertEqual(len(page['edges']),1)
        edge=page['edges'][0];self.assertEqual(edge['weight'],1)
        self.assertEqual(page['total_edges'],2)
        self.assertEqual(edge['reference_group']['count'],2)
        self.assertEqual(len(edge['evidence']['source_event_ids']),2)
        self.assertFalse(edge['reference_group']['truncated'])
        self.assertEqual(len(self.store.graph(projects=['one'])['edges']),2)
        with self.store.database() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM relations ORDER BY id')],before)

    def test_many_copies_cannot_spend_the_display_budget_before_another_connection(self):
        relations=[self.relation('copy'+str(i)) for i in range(160)]
        def rid(origin):return identity('relation',encoded([self.target,identity('graph','a'),'refers-to',identity('graph','c'),origin]).decode())
        largest=max(identity('relation',encoded([self.target,identity('graph','a'),'refers-to',identity('graph','b'),r['original_id']]).decode()) for r in relations)
        last=next('distinct'+str(i) for i in range(100000) if rid('distinct'+str(i))>largest)
        relations.append(self.relation(last,target='c'))
        self.store.ingest(self.target,self.records,relations=relations)
        page=self.page();self.assertEqual(len(page['edges']),2)
        self.assertEqual({e['target'] for e in page['edges']},{identity('graph','b'),identity('graph','c')})
        copies=next(e for e in page['edges'] if e['target']==identity('graph','b'))
        self.assertEqual(copies['reference_group']['count'],160)
        self.assertEqual(len(copies['evidence']['source_event_ids']),16)
        self.assertTrue(copies['reference_group']['truncated'])

    def test_direction_and_relation_types_remain_distinct(self):
        self.store.ingest(self.target,self.records,relations=[self.relation('x'),self.relation('y'),
            self.relation('reverse',source='b',target='a'),self.relation('other-type',kind='corrects')])
        page=self.page();self.assertEqual(len(page['edges']),3)
        self.assertEqual(sorted(e['reference_group']['count'] for e in page['edges']),[1,1,2])

    def test_withdrawal_keeps_stable_display_identity_and_changes_evidence_revision(self):
        self.store.ingest(self.target,self.records,relations=[self.relation('first'),self.relation('second')])
        before=self.page()['edges'][0]
        self.store.ingest(self.target,self.records,relations=[self.relation('second')],complete_snapshot=True,
                          source_revision='a'*64,source_order=1)
        after=self.page()['edges'][0]
        self.assertEqual(after['display_id'],before['display_id'])
        self.assertNotEqual(after['reference_group']['revision'],before['reference_group']['revision'])
        self.assertEqual(after['reference_group']['count'],1)
        with self.store.database() as db:self.assertEqual(db.execute('SELECT count(*) FROM relations').fetchone()[0],2)

    def test_change_outside_the_id_sample_still_changes_group_revision(self):
        relations=[self.relation('copy'+str(i)) for i in range(24)]
        self.store.ingest(self.target,self.records,relations=relations)
        before=self.page()['edges'][0];sample=set(before['evidence']['source_event_ids'])
        def rid(r):return identity('relation',encoded([self.target,identity('graph','a'),'refers-to',identity('graph','b'),r['original_id']]).decode())
        changed=next(r for r in relations if rid(r) not in sample)
        changed['evidence']={'source':'owned-fixture','revised':'current source revision'}
        self.store.ingest(self.target,self.records,relations=relations)
        after=self.page()['edges'][0]
        self.assertEqual(after['evidence']['source_event_ids'],before['evidence']['source_event_ids'])
        self.assertEqual(after['reference_group']['count'],24)
        self.assertNotEqual(after['reference_group']['revision'],before['reference_group']['revision'])

    def test_scope_filter_precedes_grouping_and_exposes_no_denied_claim(self):
        private=self.store.register(platform='graph',original_id='private',kind='fixture',label='Private',projects=['private'])
        self.store.ingest(self.target,self.records,relations=[self.relation('visible')])
        self.store.ingest(private,self.records,relations=[self.relation('private')])
        page=self.page();self.assertEqual(page['edges'][0]['reference_group']['count'],1)
        with self.store.database() as db:
            secret=db.execute('SELECT id FROM relations WHERE target_id=?',(private,)).fetchone()[0]
        self.assertNotIn(secret,json.dumps(page))
        own=self.page(target_id=self.target)
        self.assertEqual(own['edges'][0]['reference_group']['count'],1)

    def test_overview_and_ordinary_pages_use_the_same_record_group(self):
        self.store.ingest(self.target,self.records,relations=[self.relation('first'),self.relation('second')])
        pages=[self.page(),self.store.graph_page(projects=['one']),self.store.graph_page(projects=['one'],overview=True,limit=1984)]
        self.assertTrue(all(len(p['edges'])==1 for p in pages))
        self.assertEqual(len({p['edges'][0]['reference_group']['revision'] for p in pages}),1)

    def test_corrupt_claim_outside_the_visible_sample_still_rejects_the_projection(self):
        self.store.ingest(self.target,self.records,relations=[self.relation('copy'+str(i)) for i in range(24)])
        sample=set(self.page()['edges'][0]['evidence']['source_event_ids'])
        with self.store.database() as db:
            hidden=next(r[0] for r in db.execute('SELECT id FROM relations') if r[0] not in sample)
            db.execute('UPDATE relations SET evidence=? WHERE id=?',(json.dumps({'tampered':True}),hidden))
        with self.assertRaisesRegex(RuntimeError,'collection_relation_integrity'):self.page()

    def test_internal_node_reference_reads_do_not_build_unused_edge_proofs(self):
        import alden_collection
        self.store.ingest(self.target,self.records,relations=[self.relation('first'),self.relation('second')])
        calls=[];original=alden_collection._RelationViewProof.step
        def observe(proof,relation_id,version):calls.append(relation_id);return original(proof,relation_id,version)
        with patch.object(alden_collection._RelationViewProof,'step',observe):
            for options in [{},{'overview':True},{'focus':identity('graph','a'),'hops':1}]:
                refs=self.store.graph_references(projects=['one'],**options)
                self.assertTrue(refs['nodes'])
        self.assertEqual(calls,[])

    def test_unselected_components_do_not_enter_the_display_proof_aggregate(self):
        import alden_collection
        self.store.ingest(self.target,self.records,relations=[self.relation('first')])
        other=[{**record,'original_id':'outside-'+record['original_id']}for record in self.records]
        self.store.ingest(self.target,other,relations=[self.relation('outside'+str(i),source='outside-a',target='outside-b')for i in range(40)])
        calls=[];original=alden_collection._RelationViewProof.step
        def observe(proof,relation_id,version):calls.append(relation_id);return original(proof,relation_id,version)
        with patch.object(alden_collection._RelationViewProof,'step',observe):
            self.assertEqual(len(self.page()['edges']),1)
        self.assertEqual(len(calls),1)


if __name__=='__main__':unittest.main()

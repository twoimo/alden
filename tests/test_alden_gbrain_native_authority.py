"""Production enrollment and live source guards; owned fixture reader only."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_alden_messenger_production as fixture
from alden_collection import CollectionStore
from alden_collection_export import _stamp, ExportError, export_graph, verify_current_export
from alden_knowledge_mcp import KnowledgeServer
from alden_messenger_factory import open_enrolled_messengers
from alden_messenger_runtime import SourceBlocked
import alden_messenger_production as production


class NativeAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ProductionTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.root=self.f.root;self.reader=self.f.reader
        self.target=self.f.enroll()['target_id']
        production.collect(self.root,self.target,reader=self.reader)

    def server(self, explicit=True):
        return KnowledgeServer(self.root,['messenger'],
            source_factory=open_enrolled_messengers if explicit else None)

    def stamp(self, server=None):
        store=(server or self.server()).collection_store()
        with store.database() as db:return _stamp(db,store,['messenger'])

    def test_production_factory_is_lazy_and_does_not_require_evaluation_marker(self):
        with patch.object(production,'canonical_reader',side_effect=AssertionError('unrequested SDK')):
            authority=open_enrolled_messengers(self.root)
            self.assertEqual(authority.collection_source_root(),self.root)
            self.assertFalse(authority.sources)
        self.assertFalse((self.root/'.messenger-evaluation').exists())

    def test_missing_enrollment_is_rejected_before_any_sdk(self):
        other=self.root.parent/'unregistered';other.mkdir(mode=0o700)
        with patch.object(production,'canonical_reader',side_effect=AssertionError('unrequested SDK')):
            with self.assertRaises((ValueError,OSError,SourceBlocked)):open_enrolled_messengers(other)

    def test_prepared_only_enrollment_is_rejected_without_sdk(self):
        path=self.root/'knowledge/collection/production-messengers.json'
        state=json.loads(path.read_text());state['sources'][0]['state']='prepared'
        path.write_text(json.dumps(state))
        with patch.object(production,'canonical_reader',side_effect=AssertionError('unrequested SDK')):
            with self.assertRaisesRegex(SourceBlocked,'production_binding_unavailable'):
                open_enrolled_messengers(self.root)

    def test_cancelled_factory_never_opens_a_reader(self):
        with patch.object(production,'canonical_reader',side_effect=AssertionError('unrequested SDK')):
            with self.assertRaisesRegex(SourceBlocked,'request_cancelled'):
                open_enrolled_messengers(self.root,lambda:True)

    def test_graph_only_scope_does_not_load_the_enrolled_messenger_reader(self):
        store=CollectionStore(self.root)
        target=store.register(platform='graph',original_id='only',kind='source',label='Owned',projects=['graph'])
        store.ingest(target,[{'original_id':'g','label':'Owned graph','text':'fixture'}])
        with patch.object(production,'canonical_reader',side_effect=AssertionError('unrequested SDK')):
            server=KnowledgeServer(self.root,['graph'],source_factory=open_enrolled_messengers)
            bound=server.collection_store()
            with bound.database() as db:self.assertTrue(_stamp(db,bound,['graph']))

    def test_factory_retains_exact_owner_stream_and_four_guard_api(self):
        server=self.server();guards=server._source_guards(lambda:False)
        self.assertEqual(len(guards),4)
        bound=server._collection_store_from_guards(guards)
        self.assertIsNotNone(bound.source_stream)
        self.assertIs(bound.source_stream.__self__,guards['source_revision'].__self__)
        self.assertTrue(bound.read_only)
        with bound.database() as db:self.assertEqual(db.execute('PRAGMA query_only').fetchone()[0],1)

    def test_default_existing_production_binding_also_retains_stream(self):
        self.assertIsNotNone(self.server(False).collection_store().source_stream)
        self.assertEqual(self.stamp(self.server(False)),self.stamp())

    def test_foreign_stream_callback_is_rejected(self):
        server=self.server();guards=server._source_guards(lambda:False)
        other=open_enrolled_messengers(self.root)
        guards['source_revision'].__self__.stream_revision=other.stream_revision
        with self.assertRaisesRegex(ValueError,'source_stream_authority_invalid'):
            server._collection_store_from_guards(guards)

    def test_content_change_preserves_authority_but_changes_content_checkpoint(self):
        before=self.stamp();self.reader.pages[('a',None)][0][0]['body']='Revised owned fixture'
        after=self.stamp()
        self.assertEqual(before['authority'],after['authority'])
        self.assertNotEqual(before['checkpoint']['content_revision'],after['checkpoint']['content_revision'])

    def test_authorization_change_changes_authority(self):
        before=self.stamp();self.reader.blocked=True
        after=self.stamp();self.assertNotEqual(before['authority'],after['authority'])

    def test_content_still_fences_export_and_never_serves_stale_retained_body(self):
        server=self.server();out=self.root.parent/'archive'
        receipt=export_graph(self.root,['messenger'],out,store_factory=server.collection_store)
        self.assertEqual(receipt['counts']['nodes'],2)
        self.reader.pages[('a',None)][0][0]['body']='Changed before collector publication'
        with self.assertRaises(ExportError):
            verify_current_export(self.root,['messenger'],out,store_factory=server.collection_store)
        next_out=self.root.parent/'current'
        with self.assertRaisesRegex(ExportError,'source_withheld'):
            export_graph(self.root,['messenger'],next_out,store_factory=server.collection_store)
        self.assertFalse(next_out.exists())

    def test_withheld_body_is_removed_even_when_authorization_stream_is_stable(self):
        before=self.stamp();self.reader.pages[('a',None)][0][0]['visible']=False
        self.reader.pages[('a',None)][0][0]['body']=None
        after=self.stamp();self.assertEqual(before['authority'],after['authority'])
        self.assertNotEqual(before['checkpoint'],after['checkpoint'])
        out=self.root.parent/'withheld'
        with self.assertRaisesRegex(ExportError,'source_withheld'):
            export_graph(self.root,['messenger'],out,store_factory=self.server().collection_store)
        self.assertFalse(out.exists())

    def test_cached_authority_stops_before_content_probe_after_account_switch(self):
        authority=open_enrolled_messengers(self.root);authority.revision(self.target)
        self.reader.account='999'
        with patch.object(self.reader,'content_revisions',side_effect=AssertionError('wrong-account content probe')):
            with self.assertRaisesRegex(SourceBlocked,'account_changed'):authority.revision(self.target)
            with self.assertRaisesRegex(SourceBlocked,'account_changed'):authority.stream_revision(self.target)
            self.assertFalse(authority.access(self.target,'x','y',{}))

    def test_cached_source_cannot_reuse_a_different_account(self):
        authority=open_enrolled_messengers(self.root);authority._source(self.target)
        self.reader.account='999'
        with self.assertRaisesRegex(SourceBlocked,'account_changed'):authority._source(self.target)

    def test_cached_valid_membership_is_withheld_after_account_switch(self):
        authority=open_enrolled_messengers(self.root);authority.revision(self.target)
        with CollectionStore(self.root).database() as db:
            row=db.execute('''SELECT m.document_id,m.current_version,v.metadata FROM memberships m
                JOIN versions v ON v.id=m.current_version WHERE m.target_id=? LIMIT 1''',(self.target,)).fetchone()
        args=(self.target,row['document_id'],row['current_version'],json.loads(row['metadata']))
        self.assertTrue(authority.access(*args));self.reader.account='999'
        self.assertFalse(authority.access(*args))

    def test_account_switch_during_content_probe_is_rejected(self):
        authority=open_enrolled_messengers(self.root);original=self.reader.content_revisions
        def switch(keys):
            result=original(keys);self.reader.account='999';return result
        with patch.object(self.reader,'content_revisions',side_effect=switch):
            with self.assertRaisesRegex(SourceBlocked,'account_changed'):authority.revision(self.target)
        self.assertNotIn(self.target,authority.current)

    def test_account_switch_after_authorization_is_rejected_before_content_probe(self):
        authority=open_enrolled_messengers(self.root);source=authority._source(self.target)
        original=source._source_revision
        def switch(target):
            result=original(target);self.reader.account='999';return result
        with patch.object(source,'_source_revision',side_effect=switch), \
             patch.object(self.reader,'content_revisions',side_effect=AssertionError('wrong-account content probe')):
            with self.assertRaisesRegex(SourceBlocked,'account_changed'):authority.revision(self.target)

    def test_account_switch_during_relation_revision_does_not_cache_content(self):
        import alden_messenger_references as references
        authority=open_enrolled_messengers(self.root)
        def switch(*args,**kwargs):self.reader.account='999';return []
        with patch.object(references,'scopes',side_effect=switch):
            with self.assertRaisesRegex(SourceBlocked,'account_changed'):authority.revision(self.target)
        self.assertNotIn(self.target,authority.current)

    def test_malformed_stream_cannot_replace_a_valid_full_content_guard(self):
        server=self.server();guards=server._source_guards(lambda:False)
        authority=guards['source_revision'].__self__
        with patch.object(type(authority),'stream_revision',return_value='invalid'):
            with self.assertRaises((ValueError,ExportError)):
                store=server._collection_store_from_guards(guards)
                with store.database() as db:_stamp(db,store,['messenger'])

    def test_all_public_cli_parsers_expose_existing_enrollment_explicitly(self):
        scripts=Path(__file__).resolve().parents[1]/'scripts'
        for name in ('alden_knowledge_mcp','alden_collection_export','alden_gbrain_cache','alden_gbrain_import','alden_gbrain_watch'):
            with self.subTest(name=name):
                result=subprocess.run([sys.executable,'-B',str(scripts/(name+'.py')),'--help'],
                    capture_output=True,text=True,timeout=10)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn('{kakaotalk,enrolled}',result.stdout)


if __name__=='__main__':unittest.main()

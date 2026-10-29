"""Durable consumer acknowledgments, live read-only sentinels and ownership."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

from alden_abort import AldenCancelled
from alden_collection import CollectionStore,identity
from alden_gbrain_client import GbrainError
from alden_gbrain_cache import _UnreapedWorker
import alden_gbrain_watch as watching
from alden_gbrain_watch import (GbrainSourceWatcher,SourceMonitor,SourceUnavailable,
    SourceUnstable,WatchReceiptError)
from tests import test_alden_gbrain_import as full_fixture


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.fixture=full_fixture.FullImportTests('test_full_import_preserves_every_source_record_proof_and_actual_origin')
        self.addCleanup(self.fixture.doCleanups);self.fixture.setUp()
        self.root=self.fixture.root;self.store=self.fixture.store;self.cache=self.fixture.cache
        self.target=self.fixture.target;self.now=[0]
        self.watcher=self.new_watcher();self.watcher.__enter__();self.addCleanup(self.watcher.__exit__,None,None,None)

    def new_watcher(self,**kwargs):
        watch=GbrainSourceWatcher(self.cache.root,collection_root=self.root,projects=['one'],
            clock=lambda:self.now[0],audit_seconds=5,**kwargs)
        watch.cache=self.cache
        return watch

    def ready(self):
        result=self.watcher.tick();self.assertEqual(result['state'],'ready');return result

    def update(self,label='Changed'):
        self.store.ingest(self.target,[{'original_id':'a','label':label,'text':'controlled fixture body'}])

    def test_startup_acknowledges_only_the_actual_published_source_checkpoint(self):
        before=self.store.path.read_bytes();result=self.ready()
        active=self.cache.active()
        self.assertEqual(result['accepted']['generation'],active['generation'])
        self.assertEqual(result['accepted']['fingerprint'],active['fingerprint'])
        self.assertEqual(result['observed']['source'],result['accepted']['source'])
        self.assertEqual(before,self.store.path.read_bytes())
        self.assertTrue(self.watcher.monitor.connection.execute('PRAGMA query_only').fetchone()[0])
        self.assertFalse(self.watcher.monitor.connection.in_transaction)
        with self.assertRaises(sqlite3.OperationalError):
            self.watcher.monitor.connection.execute("DELETE FROM events")

    def test_unchanged_live_probes_do_not_run_full_scans_or_engine_imports(self):
        accepted=self.ready()['accepted'];commands=len(self.fixture.commands)
        with patch.object(self.cache,'refresh',side_effect=AssertionError('unexpected full refresh')):
            for _ in range(4):
                receipt=self.watcher.tick();self.assertEqual(receipt['state'],'idle')
                self.assertEqual(receipt['accepted'],accepted)
        self.assertEqual(len(self.fixture.commands),commands)

    def test_new_committed_version_automatically_replaces_the_old_generation(self):
        initial=self.ready();old=self.cache._generation(initial['accepted']['generation'])
        self.update();result=self.ready()
        self.assertEqual(result['reason'],'source_changed');self.assertFalse(old.exists())
        self.assertGreater(result['accepted']['source']['checkpoint']['cursor'],initial['accepted']['source']['checkpoint']['cursor'])
        self.assertEqual(next(n['label'] for n in self.fixture.query('a')['nodes'] if n['id']==identity('graph','a')),'Changed')

    def test_unjournaled_target_label_commit_is_not_missed_by_an_unchanged_cursor(self):
        initial=self.ready();cursor=initial['accepted']['source']['checkpoint']['cursor']
        self.store.register(platform='graph',original_id='a',kind='source',label='New publisher label',projects=['one'])
        result=self.ready()
        self.assertEqual(result['accepted']['source']['checkpoint']['cursor'],cursor)
        self.assertNotEqual(result['accepted']['fingerprint'],initial['accepted']['fingerprint'])
        self.assertNotEqual(result['observed']['data_version'],initial['observed']['data_version'])

    def test_periodic_reconciliation_runs_even_when_source_tokens_stay_equal(self):
        self.ready();self.now[0]=6
        with patch.object(self.cache,'refresh',wraps=self.cache.refresh) as refresh:
            result=self.ready();self.assertEqual(result['reason'],'periodic_audit')
            self.assertEqual(result['refresh_state'],'unchanged');refresh.assert_called_once()

    def test_every_new_consumer_reconciles_instead_of_trusting_saved_connection_versions(self):
        first=self.ready();commands=len(self.fixture.commands)
        self.watcher.__exit__(None,None,None)
        with self.new_watcher() as resumed,patch.object(self.cache,'refresh',wraps=self.cache.refresh) as refresh:
            result=resumed.tick();self.assertEqual(result['state'],'ready');refresh.assert_called_once()
            self.assertEqual(result['refresh_state'],'unchanged')
            self.assertNotEqual(first['observed']['connection_id'],result['observed']['connection_id'])
        self.assertEqual(len(self.fixture.commands),commands)

    def test_source_replacement_resets_the_live_sentinel_and_baseline(self):
        initial=self.ready();copy=self.root/'replacement.sqlite3'
        shutil.copyfile(self.store.path,copy);os.replace(copy,self.store.path)
        result=self.ready()
        self.assertNotEqual(result['observed']['connection_id'],initial['observed']['connection_id'])
        self.assertEqual(result['reason'],'journal_reset')
        self.assertNotEqual(result['accepted']['source']['file_identity'],initial['accepted']['source']['file_identity'])

    def test_pruned_or_rewound_journal_is_a_new_baseline_not_a_forward_ack(self):
        initial=self.ready();cursor=initial['accepted']['source']['checkpoint']['cursor']
        with self.store.database() as db:db.execute('DELETE FROM events WHERE sequence>?',(cursor//2,))
        result=self.ready();self.assertEqual(result['reason'],'journal_reset')
        self.assertLess(result['accepted']['source']['checkpoint']['cursor'],cursor)

    def test_source_changes_across_refresh_remain_pending_until_the_next_confirmed_snapshot(self):
        first=self.ready();self.update('Version B');refresh=self.cache.refresh;once=[False]
        def race(**kwargs):
            result=refresh(**kwargs)
            if not once[0]:once[0]=True;self.update('Version C')
            return result
        with patch.object(self.cache,'refresh',side_effect=race):
            result=self.watcher.tick();self.assertEqual(result['state'],'pending')
            self.assertEqual(result['accepted'],first['accepted'])
            confirmed=self.ready()
        self.assertGreater(confirmed['accepted']['source']['checkpoint']['cursor'],first['accepted']['source']['checkpoint']['cursor'])

    def test_declared_full_denial_withdraws_files_without_an_empty_success_or_cursor(self):
        first=self.ready();old=self.cache._generation(first['accepted']['generation'])
        self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        with patch.object(self.cache,'refresh',side_effect=AssertionError('denial must not import')):
            result=self.watcher.tick()
        self.assertEqual(result['state'],'scope_withdrawn');self.assertIsNone(result['accepted'])
        self.assertIsNone(self.cache.active());self.assertFalse(old.exists())
        self.assertEqual(list(self.cache.generations.iterdir()),[])

    def test_explicitly_restored_project_permission_can_publish_again(self):
        self.ready()
        self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        self.assertEqual(self.watcher.tick()['state'],'scope_withdrawn')
        self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='local-private')
        self.assertEqual(self.ready()['refresh_state'],'published')

    def test_partial_authority_change_retires_old_files_before_building_survivors(self):
        other=self.store.register(platform='graph',original_id='other',kind='source',label='Other',projects=['one'])
        self.store.ingest(other,[{'original_id':'d','label':'Other fixture','text':'fixture'}]);first=self.ready()
        old=self.cache._generation(first['accepted']['generation']);refresh=self.cache.refresh
        self.store.register(platform='graph',original_id='a',kind='source',label='Publisher',projects=['one'],permission='denied')
        def checked(**kwargs):self.assertFalse(old.exists());return refresh(**kwargs)
        with patch.object(self.cache,'refresh',side_effect=checked):result=self.ready()
        self.assertEqual(result['reason'],'authority_changed')
        self.assertEqual(self.fixture.projection().counts()['source_nodes'],1)

    def test_live_native_revision_change_is_seen_without_a_collection_commit(self):
        target=self.store.register(platform='kakaotalk',original_id='native',kind='room',label='Native',projects=['one'])
        self.store.ingest(target,[{'original_id':'n','label':'Native fixture','text':'native body'}])
        revision=['a'*64]
        factory=lambda:CollectionStore.open_existing(self.root,source_access=lambda *args:True,source_revision=lambda _:revision[0])
        self.watcher.monitor.factory=factory;self.watcher.factory=factory
        initial=self.ready();revision[0]='b'*64;result=self.ready()
        self.assertEqual(initial['observed']['data_version'],result['observed']['data_version'])
        self.assertEqual(result['reason'],'authority_changed')
        self.assertNotEqual(initial['accepted']['fingerprint'],result['accepted']['fingerprint'])

    def test_missing_native_authority_purges_owned_data_and_stops_without_permission_repair(self):
        self.ready()
        with patch.object(self.watcher.monitor,'observe',side_effect=SourceUnavailable('gbrain_watch_authority_unavailable')):
            result=self.watcher.tick()
        self.assertEqual(result['state'],'source_unavailable');self.assertFalse(result['retryable'])
        self.assertIsNone(result['accepted']);self.assertIsNone(self.cache.active())

    def test_authority_factory_failure_is_handled_under_owner_lock_and_never_leaks_text(self):
        self.ready()
        def unavailable():raise ValueError('PRIVATE_AUTHORITY_TEXT')
        self.watcher.monitor.factory=unavailable
        result=self.watcher.tick()
        self.assertEqual(result['state'],'source_unavailable');self.assertFalse(result['retryable'])
        self.assertIsNone(self.cache.active());self.assertNotIn('PRIVATE',json.dumps(result))

    def test_transient_source_io_does_not_acknowledge_or_erase_a_previously_accepted_cache(self):
        initial=self.ready();active=self.cache.active()
        with patch.object(self.watcher.monitor,'observe',side_effect=sqlite3.OperationalError('PRIVATE_IO_TEXT')):
            result=self.watcher.tick()
        self.assertEqual(result['state'],'retry');self.assertEqual(result['accepted'],initial['accepted'])
        self.assertEqual(self.cache.active(),active);self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual(self.ready()['refresh_state'],'unchanged')

    def test_os_permission_failure_is_not_retried_or_routed_to_another_reader(self):
        initial=self.ready()
        with patch.object(self.watcher.monitor,'observe',side_effect=PermissionError('PRIVATE_DENIAL')) as observe:
            result=self.watcher.tick();observe.assert_called_once()
        self.assertEqual(result['state'],'permission_denied');self.assertFalse(result['retryable'])
        self.assertEqual(result['accepted'],initial['accepted']);self.assertNotIn('PRIVATE',json.dumps(result))

    def test_explicit_external_cache_withdrawal_is_not_automatically_undone(self):
        self.ready();self.cache.withdraw()
        with patch.object(self.cache,'refresh',side_effect=AssertionError('must not restore withdrawn cache')):
            result=self.watcher.tick()
        self.assertEqual(result['state'],'cache_withdrawn');self.assertFalse(result['retryable'])
        self.assertIsNone(self.cache.active())

    def test_uncertain_receipt_after_publication_reconciles_without_reimporting_engine_writes(self):
        original=watching._write
        def uncertain(path,value):
            original(path,value)
            if Path(path).name=='watch.json':raise RuntimeError('simulated lost acceptance')
        with patch.object(watching,'_write',side_effect=uncertain):
            with self.assertRaises(WatchReceiptError):self.watcher.tick()
        stored=json.loads((self.cache.root/'watch.json').read_text());self.assertEqual(stored['state'],'ready')
        commands=len(self.fixture.commands);result=self.ready()
        self.assertEqual(result['refresh_state'],'unchanged');self.assertEqual(len(self.fixture.commands),commands)
        self.assertGreater(result['sequence'],stored['sequence'])

    def test_failed_receipt_before_write_does_not_advance_the_saved_cursor(self):
        first=self.ready();self.update()
        with patch.object(watching,'_write',side_effect=OSError('simulated write failure')):
            with self.assertRaises(WatchReceiptError):self.watcher.tick()
        stored=json.loads((self.cache.root/'watch.json').read_text());self.assertEqual(stored['accepted'],first['accepted'])
        self.assertEqual(self.ready()['refresh_state'],'unchanged')

    def test_unreaped_worker_is_not_deleted_or_acknowledged(self):
        with patch.object(self.cache,'refresh',side_effect=_UnreapedWorker('gbrain_cache_worker_not_reaped')):
            result=self.watcher.tick()
        self.assertEqual(result['state'],'worker_unreaped');self.assertFalse(result['retryable'])
        self.assertIsNone(result['accepted'])

    def test_one_root_allows_only_one_watcher_and_never_steals_its_lock(self):
        other=self.new_watcher()
        with self.assertRaisesRegex(GbrainError,'watch_busy'):other.__enter__()
        self.ready()

    def test_replaced_lock_stops_before_source_read_or_state_write(self):
        self.ready();replacement=self.cache.root/'replacement.lock';replacement.write_bytes(b'');replacement.chmod(0o600)
        os.replace(replacement,self.cache.root/'watcher.lock')
        with patch.object(self.watcher.monitor,'observe',side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(GbrainError,'watch_lock_invalid'):self.watcher.tick()

    def test_abort_does_not_clear_the_latch_or_advance_a_checkpoint(self):
        initial=self.ready()
        def cancelled():raise AldenCancelled('fixture abort')
        with self.assertRaises(AldenCancelled):self.watcher.tick(cancelled)
        stored=json.loads((self.cache.root/'watch.json').read_text());self.assertEqual(stored['sequence'],initial['sequence'])

    def test_out_of_project_native_callbacks_are_not_probed(self):
        outside=self.store.register(platform='kakaotalk',original_id='outside',kind='room',label='Outside',projects=['other'])
        self.store.ingest(outside,[{'original_id':'outside','label':'Outside fixture','text':'outside'}]);seen=[]
        factory=lambda:CollectionStore.open_existing(self.root,source_access=lambda target,*args:seen.append(target) or True,
            source_revision=lambda target:seen.append(target) or 'a'*64)
        self.watcher.factory=factory;self.watcher.monitor.factory=factory
        self.ready();self.assertNotIn(outside,seen)

    def test_corrupt_receipt_shapes_are_not_resumed_or_echoed_as_accepted_data(self):
        self.ready();path=self.cache.root/'watch.json';before=path.read_text()
        variants=[]
        for key,value in [('accepted','PRIVATE_BODY'),('observed',{'private':'PRIVATE_BODY'}),('sequence',True),
                          ('reconciliation','PRIVATE_BODY'),('extra','PRIVATE_BODY'),('state',[]),
                          ('error',{}),('reason',[]),('refresh_state',[]),('reconciliation',{})]:
            item=json.loads(before);item[key]=value;variants.append(item)
        item=json.loads(before);item['accepted']['source']['checkpoint']['cursor']=True;variants.append(item)
        for item in variants:
            path.write_text(json.dumps(item))
            with self.subTest(item=item),self.assertRaisesRegex(GbrainError,'watch_state_invalid'):
                self.watcher.tick()
        path.write_text(before)
        self.assertEqual(self.watcher.tick()['state'],'idle')


if __name__=='__main__':unittest.main()

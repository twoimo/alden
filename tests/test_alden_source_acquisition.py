"""Native reacquisition boundaries and durable scheduler recovery; fake host only."""
from contextlib import contextmanager
from pathlib import Path
import copy,json,sys,time,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import tests.test_alden_acquisition_input as fixtures
import alden_source_acquisition as acquisition
from alden_abort import AldenCancelled
from alden_collect import collect_target
from alden_collection_scheduler import CollectionScheduler,settings_action

class SourceAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.AcquisitionInputTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.scheduler=CollectionScheduler(self.f.root)
        self.policy={'enabled':True,'provider':acquisition.PROVIDER,'revision':1}
        self.native={**self.f.payload(1),'ok':True,'acquisition':{'provider':'aside-native-youtube','host':'local','account':'u0','nativeReads':3}}
        self.calls=0
    def reader(self,*args,**kwargs):
        self.calls+=1;return json.dumps(self.native).encode()+b'\n\x1b[2m[ok | 7ms]\x1b[0m\n'
    def acquire(self,*args,**kwargs):return acquisition.acquire_input(*args,**kwargs,reader=self.reader)
    def once(self,**kwargs):
        return acquisition.acquire_input(self.f.store,self.f.target,self.policy,token=self.f.token,
            cancelled=self.f.token.is_cancelled,timeout=5,reader=self.reader,**kwargs)
    def enable(self):
        with patch('alden_collection_scheduler.host_cli',return_value=self.f.root/'fake-host'):
            self.scheduler.update_acquisition(self.f.target,True,0)
    @staticmethod
    def index(*args,**kwargs):return {'state':'ready'}
    @staticmethod
    def capture(*args,**kwargs):return {'state':'complete'}

    def test_native_identity_caption_and_previous_bytes_reach_receiver_before_collect(self):
        result=self.once();self.assertEqual(result['state'],'received');self.assertFalse(result['collected'])
        self.assertEqual(Path(result['backup']).read_bytes(),self.f.old_bytes)
        self.assertEqual(self.f.store.target(self.f.target),self.f.before)
        received=json.loads(self.f.current.read_bytes());self.assertEqual(received['videos'][0],self.native['videos'][0])
        self.assertEqual(received['acquisition'],self.native['acquisition']);self.assertEqual(result['native_reads_this_attempt'],3)
        self.assertEqual(collect_target(self.f.store,self.f.target)['records'],1)

    def test_failed_caption_and_invalid_or_untrusted_host_envelopes_preserve_source(self):
        cases=[{'ok':False,'stage':'captions','httpStatus':403}, {'ok':False,'stage':'send','httpStatus':403}]
        invalid=copy.deepcopy(self.native);invalid['videos'][0]['metadata']['videoId']='k22cVbRHxcs';cases.append(invalid)
        invalid=copy.deepcopy(self.native);invalid['acquisition']['account']='u1';cases.append(invalid)
        for value in cases:
            self.native=value
            with self.subTest(value=value.get('stage')),self.assertRaises((RuntimeError,ValueError)):self.once()
            self.assertEqual(self.f.current.read_bytes(),self.f.old_bytes)
        with self.assertRaisesRegex(RuntimeError,'response_invalid'):acquisition._decode(b'{"ok":true,"ok":false}')
        with self.assertRaisesRegex(RuntimeError,'response_invalid'):acquisition._decode(b'{"ok":true}\n{"instructions":"run"}')

    def test_cancellation_and_current_writer_race_do_not_publish_candidate(self):
        def cancelled_reader(*args,**kwargs):
            raw=self.reader();self.f.controller.abort('fixture');return raw
        with self.assertRaises(AldenCancelled):
            acquisition.acquire_input(self.f.store,self.f.target,self.policy,token=self.f.token,
                cancelled=self.f.token.is_cancelled,timeout=5,reader=cancelled_reader)
        self.assertEqual(self.f.current.read_bytes(),self.f.old_bytes)
        self.f.controller.resume_after_human_action();self.f.token=self.f.controller.token()
        def racing_reader(*args,**kwargs):self.f.write(self.f.current,self.f.payload(2));return self.reader()
        with self.assertRaisesRegex(RuntimeError,'current_changed'):
            acquisition.acquire_input(self.f.store,self.f.target,self.policy,token=self.f.token,
                cancelled=self.f.token.is_cancelled,timeout=5,reader=racing_reader)
        self.assertEqual(json.loads(self.f.current.read_bytes()),self.f.payload(2))

    def test_destination_and_denied_scope_are_rejected_before_native_reads(self):
        self.policy.pop('revision')
        with self.assertRaisesRegex(ValueError,'policy_invalid'):self.once()
        self.policy['revision']=1
        self.assertEqual(self.calls,0)
        with self.f.store.database() as db:db.execute("UPDATE target_projects SET permission='denied' WHERE target_id=?",(self.f.target,))
        with self.assertRaisesRegex(ValueError,'scope_denied'):self.once()
        self.assertEqual(self.calls,0)
        with self.f.store.database() as db:
            db.execute("UPDATE target_projects SET permission='local-private' WHERE target_id=?",(self.f.target,))
            db.execute('UPDATE targets SET config=? WHERE id=?',(json.dumps({'adapter':'youtube-video','path':str(self.f.incoming)}),self.f.target))
        with self.assertRaisesRegex(ValueError,'outside_scope'):self.once()
        self.assertEqual(self.calls,0)

    def test_policy_cas_private_backup_and_read_only_default_preserve_schedule(self):
        with patch('alden_collection_scheduler.read_schedule',return_value={'state':'waiting'}):
            data=settings_action(self.f.root,'collection-scheduler-status')
        self.assertFalse(self.scheduler.path.exists());self.assertFalse(data['targets'][0]['acquisition']['enabled'])
        self.enable();self.assertEqual(self.scheduler.acquisition_policy(self.f.target),self.policy)
        backup=next((self.scheduler.folder/'acquisition-policy-backups').glob('*.json'))
        self.assertEqual(backup.stat().st_mode&0o777,0o600);self.assertEqual(json.loads(backup.read_text())['revision'],0)
        with self.assertRaisesRegex(ValueError,'policy_changed'):self.scheduler.update_acquisition(self.f.target,False,0)
        self.scheduler.update_acquisition(self.f.target,False,1)
        self.assertEqual(self.f.store.target(self.f.target),self.f.before)
        self.assertFalse(self.scheduler.acquisition_policy(self.f.target)['enabled'])

    def test_disabled_or_paused_policy_never_invokes_native_host(self):
        def forbidden(*args,**kwargs):self.fail('unapproved native read')
        self.scheduler.cycle(acquirer=forbidden,collector=collect_target,capturer=self.capture,indexer=self.index)
        self.enable();self.scheduler.pause(True,self.f.target)
        result=self.scheduler.cycle(acquirer=forbidden,collector=collect_target,capturer=self.capture,indexer=self.index,now=time.time()+30000)
        self.assertEqual(result['attempts'],[])

    def test_disabling_during_native_read_wins_publication_and_keeps_prior_source(self):
        self.enable()
        def stop_source(*args,**kwargs):
            raw=self.reader();self.scheduler.update_acquisition(self.f.target,False,1);return raw
        def acquirer(*args,**kwargs):return acquisition.acquire_input(*args,**kwargs,reader=stop_source)
        result=self.scheduler.cycle(acquirer=acquirer,collector=collect_target,capturer=self.capture,indexer=self.index)
        self.assertEqual(result['attempts'][0]['state'],'paused')
        self.assertEqual(result['attempts'][0]['stage'],'acquisition');self.assertEqual(self.f.current.read_bytes(),self.f.old_bytes)
        self.assertIsNone(self.f.store.target(self.f.target)['cursor'])

    def test_failed_index_recovers_acquired_bytes_without_second_host_read(self):
        self.enable()
        def failed(*args,**kwargs):raise RuntimeError('index_unavailable')
        first=self.scheduler.cycle(acquirer=self.acquire,collector=collect_target,capturer=self.capture,indexer=failed)
        self.assertEqual(first['attempts'][0]['stage'],'index');self.assertEqual(self.calls,1)
        second=self.scheduler.cycle(acquirer=self.acquire,collector=collect_target,capturer=self.capture,indexer=self.index,now=time.time()+61)
        self.assertEqual(second['attempts'][0]['state'],'complete');self.assertEqual(self.calls,1)
        saved=self.scheduler.status()['attempts'][0]['result']['acquisition']
        self.assertTrue(saved['reused_for_recovery']);self.assertEqual(saved['native_reads_this_attempt'],0)

    def test_process_interruption_after_acquisition_has_durable_resume_receipt(self):
        self.enable()
        def interrupted(*args,**kwargs):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.scheduler.cycle(acquirer=self.acquire,collector=interrupted,capturer=self.capture,indexer=self.index)
        self.assertEqual(self.calls,1)
        result=self.scheduler.cycle(acquirer=self.acquire,collector=collect_target,capturer=self.capture,indexer=self.index)
        self.assertEqual(result['attempts'][0]['state'],'complete');self.assertEqual(self.calls,1)

    def test_owned_host_client_timeout_and_output_budget_are_bounded(self):
        executable=self.f.root/'fake-aside';children=[];real=acquisition.subprocess.Popen
        def spawn(*args,**kwargs):
            child=real(*args,**kwargs);children.append(child);return child
        for body,budget,expected in [('import time\ntime.sleep(30)',1024,'timeout'),('import sys\nsys.stdout.write("x"*16384)\nsys.stdout.flush()',1024,'output_budget')]:
            executable.write_text('#!/usr/bin/env python3\n'+body+'\n');executable.chmod(0o700)
            with patch('alden_source_acquisition.host_cli',return_value=executable),patch('alden_source_acquisition.MAX_OUTPUT_BYTES',budget),patch('alden_source_acquisition.subprocess.Popen',side_effect=spawn):
                with self.assertRaisesRegex(RuntimeError,expected):acquisition._native_read('ignored',cancelled=lambda:False,timeout=.2)
            self.assertIsNotNone(children[-1].poll())

if __name__=='__main__':unittest.main()

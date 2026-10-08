import fcntl
import json
import os
import plistlib
import subprocess
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from alden_collection import CollectionStore
from alden_collection_scheduler import CollectionScheduler, schedule_definition, install_schedule, AGENT_LABEL
from alden_collection_retrieval import index_dense
import auto_reply_knowledge_graph as kg

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        t=TemporaryDirectory(dir=Path('/private/tmp') if Path('/private/tmp').is_dir() else None);self.addCleanup(t.cleanup);self.root=Path(t.name).resolve()
        self.store=CollectionStore(self.root);self.scheduler=CollectionScheduler(self.root)
        self.target=self.store.register(platform='youtube',original_id='channel',kind='channel',label='fixture',projects=['allowed'],interval_seconds=60)

    def collect(self,store,target,**options):
        return store.ingest(target,[{'original_id':'doc','label':'문서','text':'검증 문장'}],cursor={'complete':True},**options)

    @staticmethod
    def capture(*args,**kwargs):return {'state':'verified'}

    @staticmethod
    def index(*args,**kwargs):return {'state':'ready','documents':1}

    def test_due_cycle_commits_once_and_observes_next_run(self):
        result=self.scheduler.cycle(indexer=self.index, collector=self.collect,capturer=self.capture)
        self.assertEqual(result['state'],'complete');self.assertEqual(len(result['attempts']),1)
        second=self.scheduler.cycle(indexer=self.index, collector=self.collect,capturer=self.capture)
        self.assertEqual(second['attempts'],[])
        with self.scheduler.database() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE state='complete'").fetchone()[0],1)
        with self.scheduler.database() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM cycles').fetchone()[0],1)

    def test_failed_capture_after_stored_checkpoint_is_recovered_despite_future_source_due(self):
        def failed(*args,**kwargs):raise RuntimeError('source_capture_failed')
        result=self.scheduler.cycle(indexer=self.index, collector=self.collect,capturer=failed)
        self.assertEqual(result['state'],'partial');self.assertIsNotNone(self.store.target(self.target)['cursor'])
        self.assertEqual(self.scheduler.cycle(indexer=self.index, collector=self.collect,capturer=self.capture)['attempts'],[])
        result=self.scheduler.cycle(indexer=self.index, now=time.time()+61,collector=self.collect,capturer=self.capture)
        self.assertEqual(result['attempts'][0]['state'],'complete')

    def test_permission_and_paused_targets_are_not_opened(self):
        self.scheduler.pause(True,self.target)
        def forbidden(*args,**kwargs):self.fail('paused or denied source opened')
        self.assertEqual(self.scheduler.cycle(indexer=self.index, collector=forbidden)['attempts'],[])
        self.scheduler.pause(False,self.target)
        with self.store.database() as db:db.execute("UPDATE target_projects SET permission='denied'")
        self.assertEqual(self.scheduler.cycle(indexer=self.index, collector=forbidden)['attempts'],[])

    def test_pause_winning_publication_preserves_checkpoint_and_global_resume_is_explicit(self):
        def pause_before_commit(store,target,**options):
            self.scheduler.pause(True,target)
            return self.collect(store,target,**options)
        result=self.scheduler.cycle(indexer=self.index, collector=pause_before_commit,capturer=self.capture)
        self.assertEqual(result['attempts'][0]['state'],'paused');self.assertIsNone(self.store.target(self.target)['cursor'])
        self.scheduler.pause(True)
        self.assertEqual(self.scheduler.cycle(indexer=self.index, )['state'],'paused')
        self.scheduler.pause(False);self.scheduler.pause(False,self.target)
        self.assertEqual(self.scheduler.cycle(indexer=self.index, collector=self.collect,capturer=self.capture)['state'],'complete')

    def test_stale_revision_conflict_blocks_only_its_target_until_explicit_resume(self):
        def conflict(*args,**kwargs):raise RuntimeError('collection_source_revision_conflict')
        result=self.scheduler.cycle(indexer=self.index, collector=conflict)
        self.assertEqual(result['attempts'][0]['state'],'blocked')
        self.assertEqual(self.scheduler.cycle(indexer=self.index, now=time.time()+36000,collector=self.collect,capturer=self.capture)['attempts'],[])
        self.scheduler.pause(False,self.target)
        self.assertEqual(self.scheduler.cycle(indexer=self.index, collector=self.collect,capturer=self.capture)['attempts'][0]['state'],'complete')

    def test_cycle_owner_lock_prevents_duplicate_producers(self):
        fd=os.open(self.scheduler.folder/'scheduler.lock',os.O_CREAT|os.O_RDWR,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.assertEqual(self.scheduler.cycle(indexer=self.index, )['state'],'busy')
        finally:os.close(fd)

    def test_index_failure_preserves_source_and_retries_missing_stage(self):
        def unavailable(*args,**kwargs):raise RuntimeError('collection_embedding_unavailable')
        result=self.scheduler.cycle(indexer=unavailable,collector=self.collect,capturer=self.capture)
        self.assertEqual(result['attempts'][0]['stage'],'index')
        self.assertEqual(result['attempts'][0]['state'],'failed')
        saved=self.scheduler.status()['attempts'][0]['result']
        self.assertIn('collection',saved);self.assertIn('capture',saved);self.assertNotIn('index',saved)
        self.assertEqual(self.scheduler.cycle(indexer=self.index,collector=self.collect,capturer=self.capture)['attempts'],[])
        recovered=self.scheduler.cycle(indexer=self.index,now=time.time()+61,collector=self.collect,capturer=self.capture)
        self.assertEqual(recovered['attempts'][0]['state'],'complete')

    def test_interrupted_process_recovers_even_after_source_checkpoint_advanced(self):
        def interrupted(*args,**kwargs):raise SystemExit('owned producer interrupted')
        with self.assertRaises(SystemExit):
            self.scheduler.cycle(indexer=self.index,collector=self.collect,capturer=interrupted)
        self.assertEqual(self.scheduler.status()['attempts'][0]['state'],'running')
        result=self.scheduler.cycle(indexer=self.index,collector=self.collect,capturer=self.capture)
        self.assertEqual(result['attempts'][0]['state'],'complete')
        self.assertIn('interrupted',[r['state'] for r in self.scheduler.status()['attempts']])

    def test_declared_snapshot_original_capture_and_real_index_producer(self):
        snapshot=self.root/'source.json'
        snapshot.write_text(json.dumps({'nodes':[{'id':'one','label':'검증','summary':'확인된 원문'}],'edges':[]}))
        target=self.store.register(platform='graph',original_id='snapshot',kind='source',label='snapshot',projects=['scoped'],config={'path':str(snapshot),'adapter':'source-graph'})
        scopes=[]
        def index(root,projects,**options):
            scopes.append(projects)
            return index_dense(root,projects,embed=lambda texts:[[1.,0.] for _ in texts],**options)
        with patch.object(kg,'_active_dense_embedding_model',return_value='fixture-encoder'):
            result=self.scheduler.cycle(indexer=index,target_id=target)
        self.assertEqual(result['attempts'][0]['state'],'complete');self.assertEqual(scopes,[['scoped']])
        report=self.scheduler.status()['attempts'][0]['result']
        self.assertEqual(report['index']['embedded_changed'],1)
        self.assertIn('capture',report)
        self.assertEqual(snapshot.read_text(),json.dumps({'nodes':[{'id':'one','label':'검증','summary':'확인된 원문'}],'edges':[]}))

    def test_status_does_not_create_missing_store_and_bounds_results(self):
        other=CollectionScheduler(self.root/'missing')
        self.assertEqual(other.status()['state'],'not_configured')
        self.assertFalse(other.state_root.exists())
        with self.assertRaises(ValueError):self.scheduler.status(limit=201)

    def test_pending_index_is_not_reported_as_completed(self):
        result=self.scheduler.cycle(indexer=lambda *a,**kw:{'state':'pending'},collector=self.collect,capturer=self.capture)
        self.assertEqual(result['attempts'][0]['state'],'pending');self.assertEqual(result['state'],'partial')

    def test_fresh_voice_activity_defers_work_but_stale_receipt_does_not(self):
        path=self.root/'alden-voice-status.json'
        path.write_text(json.dumps({'state':'generating','updated_at':time.time()}))
        self.assertEqual(self.scheduler.cycle(indexer=self.index,collector=self.collect,capturer=self.capture)['state'],'deferred')
        self.assertIsNone(self.store.target(self.target)['cursor'])
        path.write_text(json.dumps({'state':'generating','updated_at':time.time()-60}))
        self.assertEqual(self.scheduler.cycle(indexer=self.index,collector=self.collect,capturer=self.capture)['state'],'complete')

    def test_schedule_install_readback_and_identical_reuse(self):
        home=self.root/'home';app=self.root/'Alden.app'
        python=home/'Library/Application Support/openkakao/runtimes/menubar/bin/python3.11'
        script=app/'Contents/Resources/scripts/alden_collection_scheduler.py'
        for file in [python,script]:file.parent.mkdir(parents=True,exist_ok=True);file.write_text('fixture')
        loaded=[False];bootstraps=[]
        def run(args,**options):
            if args[1]=='bootstrap':loaded[0]=True;bootstraps.append(args)
            code=1 if args[1]=='print' and not loaded[0] else 0
            return subprocess.CompletedProcess(args,code,'','')
        with patch('alden_collection_scheduler.INSTALLED_APP',app),patch.object(Path,'home',return_value=home),patch('alden_collection_scheduler.subprocess.run',side_effect=run):
            self.assertFalse(install_schedule(self.root)['reused'])
            self.assertTrue(install_schedule(self.root)['reused'])
        self.assertEqual(len(bootstraps),1)
        persisted=plistlib.loads((home/'Library/LaunchAgents'/ (AGENT_LABEL+'.plist')).read_bytes())
        self.assertEqual(persisted,schedule_definition(self.root,python,script))
        self.assertEqual(persisted['ProgramArguments'][1],'-B');self.assertNotIn('KeepAlive',persisted)
        self.assertNotIn('StandardOutPath',persisted)

    def test_foreign_schedule_is_preserved(self):
        home=self.root/'home';app=self.root/'Alden.app'
        for file in [home/'Library/Application Support/openkakao/runtimes/menubar/bin/python3.11',app/'Contents/Resources/scripts/alden_collection_scheduler.py']:
            file.parent.mkdir(parents=True,exist_ok=True);file.write_text('fixture')
        file=home/'Library/LaunchAgents'/(AGENT_LABEL+'.plist');file.parent.mkdir();file.write_bytes(plistlib.dumps({'Label':AGENT_LABEL,'ProgramArguments':['foreign']}));before=file.read_bytes()
        with patch('alden_collection_scheduler.INSTALLED_APP',app),patch.object(Path,'home',return_value=home),patch('alden_collection_scheduler.subprocess.run',return_value=subprocess.CompletedProcess([],0,'','')):
            with self.assertRaisesRegex(RuntimeError,'foreign_definition'):install_schedule(self.root)
        self.assertEqual(file.read_bytes(),before)

if __name__=='__main__':unittest.main()

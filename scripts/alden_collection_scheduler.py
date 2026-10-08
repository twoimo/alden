"""Bounded native collection cycles. No LLM calls, account discovery or sends.

Own scheduler state is separate from the source/version store. One process
owns a cycle, while CollectionStore retains its per-target publication locks.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
from alden_abort import AbortToken, ABORT_STATE_NAME, AldenCancelled
from alden_collection import CollectionStore, TargetBusy, safe_directory
from alden_collect import collect_target, capture_source
from alden_collection_retrieval import index_dense

AGENT_LABEL='com.openkakao.alden.collection'
INSTALLED_APP=Path('/Applications/Alden.app')

def read_schedule(state_root):
    """Observe only the fixed owned launchd definition, never load a job."""
    path=Path.home()/'Library/LaunchAgents'/(AGENT_LABEL+'.plist')
    if not path.exists():return {'state':'not_installed'}
    if path.is_symlink() or path.stat().st_size>16384:return {'state':'unverified'}
    try:
        definition=plistlib.loads(path.read_bytes())
        python=Path.home()/'Library/Application Support/openkakao/runtimes/menubar/bin/python3.11'
        script=INSTALLED_APP/'Contents/Resources/scripts/alden_collection_scheduler.py'
        if definition!=schedule_definition(Path(state_root).absolute(),python,script):return {'state':'different_definition'}
        result=subprocess.run(['/bin/launchctl','print','gui/'+str(os.getuid())+'/'+AGENT_LABEL],capture_output=True,text=True,timeout=2)
        if result.returncode:return {'state':'unloaded'}
        return {'state':'running' if re.search(r'^\s*state = running\s*$',result.stdout,re.M) else 'waiting',
                'check_seconds':60}
    except (OSError,ValueError,subprocess.TimeoutExpired):return {'state':'unverified'}

def settings_action(state_root, action, query=None, *, explicit_opt_in=False):
    """Bounded local UI projection and explicit pause controls. No work starts."""
    if action not in {'collection-scheduler-status','collection-scheduler-control'}:
        raise ValueError('collection_scheduler_action_invalid')
    options=json.loads(query) if query else {}
    if not isinstance(options,dict):raise ValueError('collection_scheduler_query_invalid')
    scheduler=CollectionScheduler(Path(state_root))
    if action=='collection-scheduler-control':
        if explicit_opt_in is not True:raise ValueError('collection_scheduler_opt_in_required')
        if set(options)-{'operation','target_id'} or options.get('operation') not in {'pause','resume'}:
            raise ValueError('collection_scheduler_control_invalid')
        target=options.get('target_id')
        if target is not None and (not isinstance(target,str) or not target or len(target)>128):
            raise ValueError('collection_scheduler_target_invalid')
        # Resuming the schedule never clears the independent emergency latch.
        scheduler.pause(options['operation']=='pause',target)
    elif options:raise ValueError('collection_scheduler_query_invalid')
    data=scheduler.status(limit=200)
    store=CollectionStore.open_existing(Path(state_root))
    declared=[];total=0
    if store:
        with store.database() as db:
            declared=[dict(row) for row in db.execute('''SELECT t.id,t.label,t.platform,t.enabled,t.interval_seconds,t.next_run,t.last_success,
              EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=t.id AND p.permission!='denied') AS permitted
              FROM targets t ORDER BY t.label,t.id LIMIT 200''')]
            total=db.execute('SELECT COUNT(*) FROM targets').fetchone()[0]
    states={row['target_id']:row for row in data.get('targets',[])}
    latest={}
    for attempt in data.get('attempts',[]):latest.setdefault(attempt['target_id'],attempt)
    controls=data.get('controls',{})
    targets=[]
    for target in declared:
        state=states.get(target['id'],{});last=latest.get(target['id'],{})
        stage=(last.get('result') or {}).get('finished_stage')
        targets.append({**target,'paused':bool(controls.get(target['id'])),
            'blocked':bool(state.get('blocked')),'retry_at':state.get('retry_at',0),
            'last_state':last.get('state'),'last_started':last.get('started'),'last_finished':last.get('finished'),
            'last_stage':stage if stage in {'collection','capture','index'} else None})
    aborted=AbortToken(Path(state_root)/ABORT_STATE_NAME).is_cancelled()
    return {'ok':True,'state':data['state'],'global_paused':bool(controls.get('global')),
            'abort_latched':aborted,'targets':targets,'total_targets':total,'time_zone':time.tzname[0],
            'schedule':read_schedule(state_root),'observed_at':time.time()}

def schedule_definition(state_root, python, script):
    return {'Label':AGENT_LABEL,'ProgramArguments':[str(python),'-B',str(script),'--state-root',str(state_root),
            '--once','--max-targets','1','--max-seconds','180'], 'RunAtLoad':True,'StartInterval':60,
            'ProcessType':'Standard','Nice':10,'LowPriorityIO':False,'ThrottleInterval':30,
            'EnvironmentVariables':{'PYTHONDONTWRITEBYTECODE':'1'}}

def install_schedule(state_root):
    """Install the fixed app-owned job; never start models or resume global abort."""
    python=Path.home()/'Library/Application Support/openkakao/runtimes/menubar/bin/python3.11'
    script=INSTALLED_APP/'Contents/Resources/scripts/alden_collection_scheduler.py'
    for path in [python,script]:
        if not path.is_file() or any(p.is_symlink() for p in [path,*path.parents]):
            raise RuntimeError('collection_schedule_runtime_unavailable')
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(INSTALLED_APP)],capture_output=True,check=True)
    definition=schedule_definition(Path(state_root).absolute(),python,script)
    folder=safe_directory(Path.home()/'Library/LaunchAgents');path=folder/(AGENT_LABEL+'.plist')
    if path.is_symlink():raise RuntimeError('collection_schedule_path_unsafe')
    domain='gui/'+str(os.getuid());service=domain+'/'+AGENT_LABEL
    current=subprocess.run(['/bin/launchctl','print',service],capture_output=True,text=True)
    rewrite=not path.exists()
    if path.exists():
        prior=plistlib.loads(path.read_bytes())
        if prior.get('Label')!=AGENT_LABEL or prior.get('ProgramArguments',[])[:3]!=definition['ProgramArguments'][:3]:
            raise RuntimeError('collection_schedule_foreign_definition')
        if prior!=definition:
            legacy={**definition,'ProcessType':'Background','LowPriorityIO':True}
            if prior!=legacy:raise RuntimeError('collection_schedule_existing_settings_differ')
            if current.returncode==0:raise RuntimeError('collection_schedule_reload_requires_unloaded_owned_job')
            # Migrate only the exact earlier app definition. Preserve its
            # bytes once; caller has already stopped this owned producer.
            raw=path.read_bytes();backup_folder=safe_directory(Path(state_root)/'knowledge/collection/schedule-backups')
            backup=backup_folder/(hashlib.sha256(raw).hexdigest()+'.plist')
            if not backup.exists():
                fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
            if backup.is_symlink() or backup.read_bytes()!=raw:raise RuntimeError('collection_schedule_backup_mismatch')
            rewrite=True
        elif current.returncode==0:return {'state':'scheduled','label':AGENT_LABEL,'interval_seconds':60,'reused':True}
    if rewrite:
        fd,temporary=tempfile.mkstemp(prefix='alden-collection.',dir=folder)
        try:
            with os.fdopen(fd,'wb') as handle:plistlib.dump(definition,handle);handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,path)
        finally:
            if os.path.exists(temporary):os.unlink(temporary)
    subprocess.run(['/bin/launchctl','bootstrap',domain,str(path)],capture_output=True,check=True)
    subprocess.run(['/bin/launchctl','print',service],capture_output=True,check=True)
    if plistlib.loads(path.read_bytes())!=definition:raise RuntimeError('collection_schedule_readback_mismatch')
    return {'state':'scheduled','label':AGENT_LABEL,'interval_seconds':60,'reused':False}

class CollectionScheduler:
    def __init__(self, state_root: Path):
        self.state_root=Path(state_root)
        self.folder=self.state_root/'knowledge'/'collection'
        self.path=self.folder/'scheduler.sqlite3'

    @contextmanager
    def database(self):
        safe_directory(self.folder)
        if self.path.is_symlink():raise RuntimeError('collection_scheduler_state_unsafe')
        if not self.path.exists():
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        db.executescript('''CREATE TABLE IF NOT EXISTS controls(id TEXT PRIMARY KEY,paused INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS target_state(target_id TEXT PRIMARY KEY,failures INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0,blocked INTEGER NOT NULL DEFAULT 0,last_error TEXT,last_run TEXT);
          CREATE TABLE IF NOT EXISTS cycles(id TEXT PRIMARY KEY,started REAL NOT NULL,finished REAL,state TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,cycle_id TEXT NOT NULL,target_id TEXT NOT NULL,
            started REAL NOT NULL,finished REAL,state TEXT NOT NULL,result TEXT,error TEXT);
          INSERT OR IGNORE INTO controls VALUES('global',0);''')
        try:yield db;db.commit()
        except BaseException:db.rollback();raise
        finally:db.close()

    @contextmanager
    def control_lock(self):
        safe_directory(self.folder)
        fd=os.open(self.folder/'scheduler-controls.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX)
            yield
        finally:os.close(fd)

    def pause(self, paused: bool, target_id: str | None=None):
        if type(paused) is not bool:raise ValueError('collection_scheduler_pause_invalid')
        key=target_id or 'global'
        if target_id:
            store=CollectionStore.open_existing(self.state_root)
            if store is None:raise ValueError('collection_target_missing')
            store.target(target_id)
        with self.control_lock(), self.database() as db:
            db.execute('INSERT INTO controls VALUES(?,?) ON CONFLICT(id) DO UPDATE SET paused=excluded.paused',(key,int(paused)))
            if target_id and not paused:db.execute('UPDATE target_state SET blocked=0,failures=0,retry_at=0 WHERE target_id=?',(target_id,))
        return {'state':'paused' if paused else 'enabled','target_id':target_id}

    def _paused(self,target_id=None):
        with self.database() as db:
            keys=['global',target_id] if target_id else ['global']
            return any(row[0] for row in db.execute('SELECT paused FROM controls WHERE id IN ('+','.join('?' for _ in keys)+')',keys))

    def _voice_busy(self):
        path=self.state_root/'alden-voice-status.json'
        if not path.exists():return False
        if path.is_symlink() or path.stat().st_size>8192:return True
        try:
            data=json.loads(path.read_text())
            return data.get('state') in {'user_listen','transcribing','generating','speaking'} and time.time()-float(data.get('updated_at',0))<30
        except (OSError,ValueError,TypeError):return True

    def status(self, *, limit=30):
        """Read bounded durable results without creating files or running work."""
        if type(limit) is not int or not 1<=limit<=200:raise ValueError('collection_scheduler_limit_invalid')
        if any(path.is_symlink() for path in [self.path,*self.path.parents]):
            raise ValueError('collection_scheduler_state_unsafe')
        if not self.path.exists():return {'state':'not_configured','attempts':[],'targets':[]}
        db=sqlite3.connect(self.path.absolute().as_uri()+'?mode=ro',uri=True,timeout=.15);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            controls={r['id']:bool(r['paused']) for r in db.execute('SELECT * FROM controls')}
            attempts=[dict(r) for r in db.execute('SELECT * FROM attempts ORDER BY started DESC,id DESC LIMIT ?',(limit,))]
            for row in attempts:
                row['result']=json.loads(row['result']) if row['result'] else None
            targets=[dict(r) for r in db.execute('SELECT * FROM target_state ORDER BY target_id LIMIT 200')]
            store=CollectionStore.open_existing(self.state_root)
            declared=[]
            if store:
                with store.database() as source:
                    declared=[dict(r) for r in source.execute('SELECT id,label,platform,enabled,interval_seconds,next_run FROM targets ORDER BY id LIMIT 200')]
            aborted=AbortToken(self.state_root/ABORT_STATE_NAME).is_cancelled()
            return {'state':'paused' if controls.get('global') or aborted else 'enabled','abort_latched':aborted,
                    'controls':controls,'attempts':attempts,'targets':targets,'declared_targets':declared,'time_zone':time.tzname[0]}
        finally:db.close()

    def cycle(self, *, max_targets=1, max_seconds=300, target_id=None, collector=collect_target,
              capturer=capture_source, indexer=index_dense, now=None, token=None):
        if type(max_targets) is not int or not 1<=max_targets<=16 or type(max_seconds) is not int or not 1<=max_seconds<=3600:
            raise ValueError('collection_scheduler_budget_invalid')
        store=CollectionStore.open_existing(self.state_root)
        if store is None:return {'state':'not_configured','attempts':[]}
        selected=target_id
        if selected:store.target(selected)
        store=CollectionStore(self.state_root)
        token=token or AbortToken(self.state_root/ABORT_STATE_NAME)
        safe_directory(self.folder)
        lock=os.open(self.folder/'scheduler.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {'state':'busy','attempts':[]}
            if token.is_cancelled() or self._paused():return {'state':'paused','attempts':[]}
            if self._voice_busy():return {'state':'deferred','reason':'voice_active','attempts':[]}
            stamp=time.time() if now is None else now
            cycle=uuid.uuid4().hex;started=time.monotonic();attempts=[]
            with self.database() as db:
                pending={row[0] for row in db.execute("SELECT target_id FROM attempts WHERE state='running'")}
                pending.update(row[0] for row in db.execute("SELECT s.target_id FROM target_state s JOIN attempts a ON a.id=s.last_run WHERE a.state!='complete'"))
                db.execute("UPDATE cycles SET state='interrupted',finished=? WHERE state='running'",(stamp,))
                db.execute("UPDATE attempts SET state='interrupted',finished=? WHERE state='running'",(stamp,))
                retry={row['target_id']:dict(row) for row in db.execute('SELECT * FROM target_state')}
            with store.database() as db:
                due=[row[0] for row in db.execute('''SELECT t.id FROM targets t WHERE t.enabled=1 AND (t.next_run<=? OR t.id IN ('PLACEHOLDER'))
                  AND EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=t.id AND p.permission!='denied')
                  ORDER BY t.next_run,t.id'''.replace("'PLACEHOLDER'",','.join('?' for _ in pending) or 'NULL'),(stamp,*pending))]
            due=[target for target in due if (not selected or target==selected) and not retry.get(target,{}).get('blocked')
                 and retry.get(target,{}).get('retry_at',0)<=stamp and not self._paused(target)]
            if not due:return {'state':'idle','attempts':[]}
            with self.database() as db:db.execute('INSERT INTO cycles VALUES(?,?,NULL,?)',(cycle,stamp,'running'))
            for target_id in due:
                if selected and target_id!=selected:continue
                if len(attempts)>=max_targets or time.monotonic()-started>=max_seconds:break
                previous=retry.get(target_id,{})
                if previous.get('blocked') or previous.get('retry_at',0)>stamp or self._paused(target_id):continue
                if token.is_cancelled() or self._paused():break
                attempt=uuid.uuid4().hex
                with self.database() as db:db.execute('INSERT INTO attempts VALUES(?,?,?,?,NULL,?,NULL,NULL)',(attempt,cycle,target_id,time.time(),'running'))
                pause_check=[-1.0,False]
                def cancelled():
                    if token.is_cancelled() or time.monotonic()-started>=max_seconds:return True
                    current=time.monotonic()
                    if current-pause_check[0]>=.25:
                        pause_check[:]=[current,self._paused(target_id) or self._voice_busy()]
                    return pause_check[1]
                @contextmanager
                def publication_guard():
                    with self.control_lock(), token.commit_guard():
                        if self._paused(target_id) or self._voice_busy() or time.monotonic()-started>=max_seconds:
                            raise AldenCancelled('collection_scheduler_paused')
                        yield
                state='complete';error=None;result={};stage='collection'
                try:
                    result['collection']=collector(store,target_id,cancelled=cancelled,publication_guard=publication_guard)
                    if cancelled():raise AldenCancelled('collection_scheduler_cancelled')
                    stage='capture';result['capture']=capturer(store,target_id,cancelled=cancelled)
                    if cancelled():raise AldenCancelled('collection_scheduler_cancelled')
                    stage='index'
                    projects=[p['project'] for p in store.target(target_id)['projects'] if p['permission']!='denied']
                    result['index']=indexer(self.state_root,projects,cancelled=cancelled)
                    if result['index'].get('state') not in {'ready','empty'}:
                        state='pending';error='collection_index_pending'
                except (AldenCancelled,TargetBusy) as exc:
                    state='paused' if isinstance(exc,AldenCancelled) else 'busy';error=type(exc).__name__
                except Exception as exc:
                    error=str(exc)[:160];state='paused' if error in {'collection_cancelled','collection_target_paused','collection_scheduler_paused','collection_retrieval_cancelled'} else 'blocked' if error in {'collection_source_revision_stale','collection_source_revision_conflict','collection_source_scope_denied'} else 'failed'
                if state=='paused' and time.monotonic()-started>=max_seconds:error='collection_budget_exhausted'
                result['finished_stage']=stage
                failures=0 if state=='complete' else int(previous.get('failures',0))+1
                delay=min(21600,60*2**min(8,failures-1)) if failures else 0
                finished=time.time()
                with self.database() as db:
                    db.execute('UPDATE attempts SET finished=?,state=?,result=?,error=? WHERE id=?',(finished,state,json.dumps(result,ensure_ascii=False) if result else None,error,attempt))
                    db.execute('''INSERT INTO target_state VALUES(?,?,?,?,?,?) ON CONFLICT(target_id) DO UPDATE SET
                      failures=excluded.failures,retry_at=excluded.retry_at,blocked=excluded.blocked,last_error=excluded.last_error,last_run=excluded.last_run''',
                      (target_id,failures,finished+delay,int(state=='blocked'),error,attempt))
                attempts.append({'id':attempt,'target_id':target_id,'state':state,'stage':stage,'error':error})
            state='paused' if token.is_cancelled() or self._paused() else 'partial' if any(row['state']!='complete' for row in attempts) else 'complete'
            with self.database() as db:db.execute('UPDATE cycles SET finished=?,state=? WHERE id=?',(time.time(),state,cycle))
            return {'id':cycle,'state':state,'attempts':attempts,'scope':'declared permitted snapshots, retained originals and confirmed scoped index'}
        finally:os.close(lock)

def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--once',action='store_true');parser.add_argument('--pause',action='store_true');parser.add_argument('--resume',action='store_true');parser.add_argument('--status',action='store_true')
    parser.add_argument('--install-schedule',action='store_true')
    parser.add_argument('--target');parser.add_argument('--max-targets',type=int,default=1);parser.add_argument('--max-seconds',type=int,default=300)
    args=parser.parse_args()
    if sum([args.once,args.pause,args.resume,args.status,args.install_schedule])!=1:parser.error('choose once, pause, resume, status, or install-schedule')
    scheduler=CollectionScheduler(args.state_root)
    result=install_schedule(args.state_root) if args.install_schedule else scheduler.status() if args.status else scheduler.cycle(max_targets=args.max_targets,max_seconds=args.max_seconds,target_id=args.target) if args.once else scheduler.pause(args.pause,args.target)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()

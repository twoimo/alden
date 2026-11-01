"""Explicit source-change consumer for an existing private full GBrain cache.

Reads source commits/authority; writes only owned derived cache/watch receipts.
This does not install a service, restart collectors, or implement engine deltas.
"""
from contextlib import closing
import fcntl
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import uuid

from alden_abort import ABORT_STATE_NAME,AbortToken,AldenCancelled
from alden_collection_export import ExportError,_open,_stamp,_file_token
from alden_gbrain_client import GbrainError,_path
from alden_gbrain_cache import _read,_write,_UnreapedWorker
from alden_gbrain_import import FullGbrainCache

KIND='alden-gbrain-source-watch-v1'


class SourceWithdrawn(GbrainError):pass
class SourceUnavailable(GbrainError):pass
class SourceUnstable(GbrainError):pass
class WatchStopped(GbrainError):pass
class WatchReceiptError(GbrainError):pass


def _hex(value,size=64):return isinstance(value,str) and re.fullmatch('[0-9a-f]{'+str(size)+'}',value) is not None
def _integers(value,count):return isinstance(value,list) and len(value)==count and all(type(n) is int and n>=0 for n in value)


def _source_stamp(value):
    if (not isinstance(value,dict) or set(value)!={'authority','checkpoint','file_identity'}
            or not _hex(value['authority']) or not _integers(value['file_identity'],2)):return False
    checkpoint=value['checkpoint']
    return (isinstance(checkpoint,dict) and {'stream_id','cursor'}<=set(checkpoint)<= {'stream_id','cursor','content_revision'}
        and isinstance(checkpoint['stream_id'],str) and re.fullmatch('stream:[0-9a-f]{64}',checkpoint['stream_id']) is not None
        and type(checkpoint['cursor']) is int and checkpoint['cursor']>=0
        and ('content_revision' not in checkpoint or _hex(checkpoint['content_revision'])))


class SourceMonitor:
    """One persistent query-only sentinel; fresh scoped authority each probe.

    data_version is connection-local and is never trusted across a restart.
    It covers unjournaled writes; the file token also covers replacement and
    main-file changes. No idle read transaction pins a source WAL snapshot.
    """
    def __init__(self,root,projects,*,store_factory=None):
        self.root=_path(root);self.projects=projects;self.factory=store_factory
        self.connection=None;self.identity=None;self.connection_id=None

    def close(self):
        if self.connection is not None:self.connection.close()
        self.connection=None;self.identity=None;self.connection_id=None

    def observe(self,check=lambda:None):
        check()
        try:store=_open(self.root,self.factory)
        except (PermissionError,AldenCancelled,WatchStopped,sqlite3.OperationalError,BlockingIOError,TimeoutError):raise
        except ExportError as error:
            if str(error)=='collection_export_readonly_source_required':
                raise SourceUnavailable('gbrain_watch_source_unavailable') from None
            raise
        except Exception:
            raise SourceUnavailable('gbrain_watch_authority_unavailable') from None
        token=_file_token(store.path);identity=token[:2]
        if self.connection is None or self.identity!=identity:
            self.close()
            self.connection=sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True,timeout=.15)
            self.connection.execute('PRAGMA query_only=ON')
            self.identity=identity;self.connection_id=uuid.uuid4().hex
        before=self.connection.execute('PRAGMA data_version').fetchone()[0]
        if type(before) is not int or before<1:raise SourceUnavailable('gbrain_watch_source_unavailable')
        try:
            with store.database(cancelled=lambda:check() or False) as db:
                observed=_stamp(db,store,self.projects)
        except ValueError as error:
            if str(error)=='collection_project_scope_invalid':
                raise SourceWithdrawn('gbrain_watch_scope_withdrawn') from None
            raise
        except ExportError as error:
            if str(error)=='collection_export_authority_unavailable':
                raise SourceUnavailable('gbrain_watch_authority_unavailable') from None
            raise
        after=self.connection.execute('PRAGMA data_version').fetchone()[0]
        if before!=after or _file_token(store.path)!=token or observed['file_identity']!=list(identity):
            raise SourceUnstable('gbrain_watch_source_changed_during_probe')
        check()
        return {'connection_id':self.connection_id,'data_version':after,'file_token':list(token),'source':observed}


class GbrainSourceWatcher:
    def __init__(self,root,*,collection_root,projects,store_factory=None,audit_seconds=60,clock=time.monotonic,incremental=False):
        if type(incremental) is not bool:raise GbrainError('gbrain_watch_options_invalid')
        if type(audit_seconds) not in (int,float) or not math.isfinite(audit_seconds) or not 5<=audit_seconds<=3600:
            raise GbrainError('gbrain_watch_options_invalid')
        self.cache=FullGbrainCache(root,collection_root=collection_root,projects=projects)
        self.monitor=SourceMonitor(collection_root,self.cache.owner['projects'],store_factory=store_factory)
        self.factory=store_factory;self.clock=clock;self.audit_seconds=audit_seconds
        self.fd=None;self.trusted=None;self.last_audit=None
        self.process_id=uuid.uuid4().hex
        self.incremental=incremental

    def _verify_lock(self):
        if self.fd is None:raise GbrainError('gbrain_watch_not_started')
        root=_path(self.cache.root).stat();path=_path(self.cache.root/'watcher.lock').stat();fd=os.fstat(self.fd)
        if ((root.st_dev,root.st_ino)!=self.cache.root_identity
                or root.st_uid!=os.getuid() or stat.S_IMODE(root.st_mode)!=0o700
                or (path.st_dev,path.st_ino)!=(fd.st_dev,fd.st_ino)
                or _read(self.cache.root/'owner.json')!=self.cache.owner):
            raise GbrainError('gbrain_watch_lock_invalid')

    def _binding(self):
        return {'kind':KIND,'owner':self.cache.owner['owner'],
            'source_root_sha256':self.cache.owner['collection_root_sha256'],'projects':self.cache.owner['projects']}

    def _state(self):
        path=self.cache.root/'watch.json'
        if not path.exists():return {**self._binding(),'sequence':0,'accepted':None}
        value=_read(path)
        required=set(self._binding())|{'sequence','process_id','pid','state','observed','accepted','error','retryable','observed_at','basis'}
        allowed=required|{'reason','reconciliation','refresh_state','removed_generations'}
        if (not isinstance(value,dict) or not required<=set(value)<=allowed or any(value.get(k)!=v for k,v in self._binding().items())
                or type(value.get('sequence')) is not int or value['sequence']<0
                or not _hex(value.get('process_id'),32) or type(value.get('pid')) is not int or value['pid']<1
                or value.get('state') not in ('ready','idle','pending','retry','scope_withdrawn','source_unavailable','permission_denied','worker_unreaped','cache_withdrawn')
                or type(value.get('retryable')) is not bool
                or type(value.get('observed_at')) not in (int,float) or not math.isfinite(value['observed_at']) or value['observed_at']<0
                or value.get('basis')!='owned consumer receipt; not a current source access grant'
                or value.get('error') not in (None,'gbrain_watch_scope_withdrawn','gbrain_watch_source_unavailable',
                    'gbrain_watch_authority_unavailable','gbrain_watch_source_unstable','gbrain_watch_permission_denied',
                    'gbrain_cache_worker_not_reaped','gbrain_watch_source_io','gbrain_watch_reconciliation_failed','gbrain_watch_cache_withdrawn')):
            raise GbrainError('gbrain_watch_state_invalid')
        accepted,observed=value['accepted'],value['observed']
        if accepted is not None and (not isinstance(accepted,dict) or set(accepted)!={'owner','generation','fingerprint','source'}
                or accepted['owner']!=self.cache.owner['owner'] or not _hex(accepted['fingerprint'])
                or not isinstance(accepted['generation'],str) or re.fullmatch('g[0-9a-f]{32}',accepted['generation']) is None
                or not _source_stamp(accepted['source'])):
            raise GbrainError('gbrain_watch_state_invalid')
        if observed is not None and (not isinstance(observed,dict) or set(observed)!={'connection_id','data_version','file_token','source'}
                or not _hex(observed['connection_id'],32) or type(observed['data_version']) is not int or observed['data_version']<1
                or not _integers(observed['file_token'],6) or not _source_stamp(observed['source'])
                or observed['file_token'][:2]!=observed['source']['file_identity']):
            raise GbrainError('gbrain_watch_state_invalid')
        if ('reason' in value and value['reason'] not in ('startup_or_recovery','authority_changed','journal_reset','periodic_audit','source_changed')
                or 'refresh_state' in value and value['refresh_state'] not in ('published','unchanged')
                or 'removed_generations' in value and (type(value['removed_generations']) is not int or value['removed_generations']<0)
                or 'reconciliation' in value and value['reconciliation'] not in (
                    'unchanged live connection; no full scan or engine operation',
                    'accepted cache was externally withdrawn; explicit restart required',
                    'source changed across refresh; not acknowledged',
                    'full source/cache publication confirmed before checkpoint')):
            raise GbrainError('gbrain_watch_state_invalid')
        return value

    def __enter__(self):
        if self.fd is not None:raise GbrainError('gbrain_watch_already_started')
        path=_path(self.cache.root/'watcher.lock')
        fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            info=os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                    or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1):
                raise GbrainError('gbrain_watch_lock_invalid')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise GbrainError('gbrain_watch_busy') from None
            root_info=_path(self.cache.root).stat();lock_info=_path(path).stat()
            if ((root_info.st_dev,root_info.st_ino)!=self.cache.root_identity
                    or (lock_info.st_dev,lock_info.st_ino)!=(info.st_dev,info.st_ino)
                    or _read(self.cache.root/'owner.json')!=self.cache.owner):
                raise GbrainError('gbrain_watch_lock_invalid')
            self._state();self.fd=fd;return self
        except BaseException:
            os.close(fd);raise

    def __exit__(self,*args):
        self.monitor.close()
        if self.fd is not None:os.close(self.fd)
        self.fd=None;self.trusted=None;self.last_audit=None

    def _persist(self,state,*,observed=None,accepted=None,error=None,retryable=True,**details):
        prior=self._state()
        value={**self._binding(),'sequence':prior['sequence']+1,'process_id':self.process_id,'pid':os.getpid(),
            'state':state,'observed':observed,'accepted':accepted,'error':error,'retryable':retryable,
            'observed_at':time.time(),'basis':'owned consumer receipt; not a current source access grant',**details}
        try:
            _write(self.cache.root/'watch.json',value)
            if _read(self.cache.root/'watch.json')!=value:raise GbrainError('gbrain_watch_receipt_changed')
        except Exception:
            # Reconcile this receipt on restart; do not overwrite uncertainty
            # or replay the already accepted cache's engine writes.
            raise WatchReceiptError('gbrain_watch_receipt_unconfirmed') from None
        return value

    def _accepted(self,check):
        with self.cache.lease(exclusive=False,check=check):
            active=self.cache.active()
            if active is None:return None
            generation=self.cache._generation(active['generation'])
            receipt=_read(generation/'source-archive/receipt.json')
            if (receipt.get('complete') is not True or receipt.get('projects')!=self.cache.owner['projects']
                    or receipt.get('source_root_sha256')!=self.cache.owner['collection_root_sha256']):
                raise GbrainError('gbrain_watch_cache_binding_invalid')
            return {**active,'source':receipt['as_of']}

    def _withdraw(self,check):
        result=self.cache.withdraw(check=check)
        with self.cache.lease(exclusive=False,check=check):
            if self.cache.active() is not None or any(self.cache.generations.iterdir()):
                raise GbrainError('gbrain_watch_withdrawal_unconfirmed')
        self.trusted=None;self.last_audit=None
        return result

    def tick(self,check=lambda:None):
        self._verify_lock()
        check();prior=self._state();accepted=prior['accepted']
        try:
            observed=self.monitor.observe(check)
            current=self._accepted(check)
            if self.trusted is not None and current is None:
                self.trusted=None
                return self._persist('cache_withdrawn',observed=observed,accepted=accepted,
                    error='gbrain_watch_cache_withdrawn',retryable=False,
                    reconciliation='accepted cache was externally withdrawn; explicit restart required')
            due=self.last_audit is None or self.clock()-self.last_audit>=self.audit_seconds
            if (not due and self.trusted==observed and current is not None and accepted==current
                    and current['source']==observed['source']):
                return self._persist('idle',observed=observed,accepted=current,
                    reconciliation='unchanged live connection; no full scan or engine operation')
            previous_checkpoint=self.trusted['source']['checkpoint'] if self.trusted else None
            checkpoint=observed['source']['checkpoint']
            reset=(previous_checkpoint is not None and (previous_checkpoint['stream_id']!=checkpoint['stream_id']
                or checkpoint['cursor']<previous_checkpoint['cursor']
                or self.trusted['source']['file_identity']!=observed['source']['file_identity']))
            reason=('startup_or_recovery' if self.trusted is None else 'authority_changed'
                if self.trusted['source']['authority']!=observed['source']['authority'] else 'journal_reset' if reset else 'periodic_audit' if due
                else 'source_changed')
            # A known authority change invalidates files before preparing an
            # updated graph. Never keep revoked metadata through that rebuild.
            removed=0
            if current is not None and (reset or current['source']['authority']!=observed['source']['authority']):
                removed=self._withdraw(check)['removed_generations']
            result=self.cache.refresh(store_factory=self.factory,check=check,
                **({'incremental':True} if self.incremental else {}))
            confirmed=self._accepted(check)
            after=self.monitor.observe(check)
            if (confirmed is None or confirmed['fingerprint']!=result['fingerprint']
                    or confirmed['source']!=after['source'] or observed!=after):
                self.trusted=None
                return self._persist('pending',observed=after,accepted=accepted,
                    reconciliation='source changed across refresh; not acknowledged',reason=reason)
            receipt=self._persist('ready',observed=after,accepted=confirmed,reason=reason,
                refresh_state=result['state'],removed_generations=removed+result.get('removed_generations',0),
                reconciliation='full source/cache publication confirmed before checkpoint')
            self.trusted=after;self.last_audit=self.clock()
            return receipt
        except SourceWithdrawn:
            result=self._withdraw(check)
            return self._persist('scope_withdrawn',accepted=None,error='gbrain_watch_scope_withdrawn',
                removed_generations=result['removed_generations'])
        except SourceUnavailable as error:
            result=self._withdraw(check)
            code=str(error) if str(error) in ('gbrain_watch_source_unavailable','gbrain_watch_authority_unavailable') else 'gbrain_watch_source_unavailable'
            return self._persist('source_unavailable',accepted=None,error=code,retryable=False,
                removed_generations=result['removed_generations'])
        except SourceUnstable:
            self.trusted=None
            return self._persist('retry',accepted=accepted,error='gbrain_watch_source_unstable')
        except PermissionError:
            # No alternate reader or permission repair is attempted.
            self.trusted=None
            return self._persist('permission_denied',accepted=accepted,error='gbrain_watch_permission_denied',retryable=False)
        except _UnreapedWorker:
            self.trusted=None
            return self._persist('worker_unreaped',accepted=accepted,error='gbrain_cache_worker_not_reaped',retryable=False)
        except (sqlite3.OperationalError,BlockingIOError,TimeoutError):
            self.trusted=None
            return self._persist('retry',accepted=accepted,error='gbrain_watch_source_io')
        except (AldenCancelled,WatchStopped):
            self.trusted=None;raise
        except WatchReceiptError:
            self.trusted=None;raise
        except (GbrainError,ExportError,ValueError,RuntimeError):
            self.trusted=None
            # A source/cache error carries no permission grant and no private
            # exception text. Refresh may already have discarded its generation.
            return self._persist('retry',accepted=accepted,error='gbrain_watch_reconciliation_failed')


def main():
    import argparse,json,signal
    from alden_collection import CollectionStore
    from alden_knowledge_mcp import KnowledgeServer
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('once','run','status'))
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--project',action='append',required=True)
    parser.add_argument('--messenger-source',choices=('kakaotalk','enrolled'))
    parser.add_argument('--interval-seconds',type=float,default=5)
    parser.add_argument('--audit-seconds',type=float,default=60)
    parser.add_argument('--max-ticks',type=int)
    parser.add_argument('--incremental',action='store_true',help='use verified engine row/link deltas when eligible')
    args=parser.parse_args()
    if not math.isfinite(args.interval_seconds) or not .25<=args.interval_seconds<=300 or args.max_ticks is not None and (args.action!='run' or not 1<=args.max_ticks<=1_000_000):
        parser.error('interval .25–300 seconds; max-ticks1–1000000 only for run')
    stopped=False
    def stop(*_):
        nonlocal stopped;stopped=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    token=AbortToken(args.state_root/ABORT_STATE_NAME)
    def check():
        if stopped:raise WatchStopped('gbrain_watch_stopped')
        token.raise_if_cancelled()
    try:
        check()
        if args.action=='status':
            watcher=GbrainSourceWatcher(args.root,collection_root=args.state_root,projects=args.project,audit_seconds=args.audit_seconds)
            print(json.dumps({'ok':True,'basis':'stored consumer receipt only','receipt':watcher._state()}));return
        source_factory=None
        if args.messenger_source:
            from alden_messenger_factory import open_existing_kakao, open_enrolled_messengers
            source_factory=open_enrolled_messengers if args.messenger_source=='enrolled' else open_existing_kakao
        authority=[]
        def factory():
            check()
            if not authority:authority.append(KnowledgeServer(args.state_root,args.project,source_factory=source_factory))
            return authority[0].collection_store(lambda:token.is_cancelled() or stopped)
        with GbrainSourceWatcher(args.root,collection_root=args.state_root,projects=args.project,
                store_factory=factory,audit_seconds=args.audit_seconds,incremental=args.incremental) as watcher:
            count=0
            while True:
                receipt=watcher.tick(check);count+=1
                print(json.dumps({'ok':receipt['state'] in ('ready','idle'),'retryable':receipt['retryable'],
                    'state':receipt['state'],'sequence':receipt['sequence'],'error':receipt['error'],
                    'accepted':receipt['accepted'],'reason':receipt.get('reason')}),flush=True)
                if not receipt['retryable']:raise SystemExit(1)
                if args.action=='once':
                    if receipt['state'] not in ('ready','idle'):raise SystemExit(1)
                    break
                if args.max_ticks is not None and count>=args.max_ticks:break
                until=time.monotonic()+args.interval_seconds
                while time.monotonic()<until:check();time.sleep(min(.1,max(0,until-time.monotonic())))
    except (WatchStopped,KeyboardInterrupt):
        print(json.dumps({'ok':True,'state':'stopped'}))
    except Exception as error:
        code='alden_global_abort' if isinstance(error,AldenCancelled) else 'gbrain_watch_permission_denied' if isinstance(error,PermissionError) else 'gbrain_watch_unavailable'
        print(json.dumps({'ok':False,'error':code}));raise SystemExit(1) from None


if __name__=='__main__':main()

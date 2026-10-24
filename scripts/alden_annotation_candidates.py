"""Bounded retained-tag candidate pages; never a tag verdict or merge command."""
from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time

from alden_collection import CollectionStore, encoded, digest, identity, safe_directory
from alden_source_text import project_source_text
from alden_content_evidence import POLICY as CONTENT_POLICY, content_evidence_class, declared_source_annotations

POLICY='retained-annotation-candidates-v1'
PAGE_SIZE=5
MAX_CACHE_BYTES=64*1024*1024
MAX_CACHE_FILES=16
TTL_SECONDS=1800


def scope_fingerprint(db, projects, check=lambda:None):
    marks=','.join('?' for _ in projects);h=hashlib.sha256();rows=0
    for row in db.execute('''SELECT p.target_id,p.project,p.permission,m.document_id,
        m.current_version,m.availability,d.availability,v.raw_sha256,v.projection_sha256,v.collected_at
        FROM target_projects p LEFT JOIN memberships m ON m.target_id=p.target_id
        LEFT JOIN documents d ON d.id=m.document_id LEFT JOIN versions v ON v.id=m.current_version
        WHERE p.project IN ('''+marks+''') ORDER BY p.project,p.target_id,m.document_id''',projects):
        rows+=1;check()
        if rows>100000:raise ValueError('annotation_review_scan_budget')
        h.update(encoded(list(row)));h.update(b'\n')
    return h.hexdigest()


def _scope(query):
    if not isinstance(query,dict):raise ValueError('annotation_review_query_invalid')
    mode=query.get('scope');project=query.get('project')
    if mode=='all' and project is None:return {'scope':'all'}
    if mode=='project' and isinstance(project,str) and 0<len(project)<=128 and project==project.strip() and not any(ord(c)<32 for c in project):
        return {'scope':'project','project':project}
    raise ValueError('annotation_review_query_invalid')


def _folder(store, create=False):
    folder=store.root/'annotation-reviews'
    if create:safe_directory(folder)
    if any(p.is_symlink() for p in [folder,*folder.parents]):raise ValueError('annotation_review_cache_invalid')
    info=folder.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)&0o077:
        raise ValueError('annotation_review_cache_invalid')
    return folder


def _load(folder, review_id):
    from alden_graph_quality import _json
    if not isinstance(review_id,str) or re.fullmatch('[a-f0-9]{64}',review_id) is None:
        raise ValueError('annotation_review_query_invalid')
    path=folder/(review_id+'.json')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid()
                or stat.S_IMODE(before.st_mode)&0o077 or not 0<before.st_size<=MAX_CACHE_BYTES):
            raise ValueError('annotation_review_cache_invalid')
        data=f.read(MAX_CACHE_BYTES+1);after=os.fstat(f.fileno())
    current=os.stat(path,follow_symlinks=False)
    sig=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if sig(before)!=sig(after) or sig(after)!=sig(current) or digest(data)!=review_id:
        raise ValueError('annotation_review_cache_invalid')
    value=_json(data)
    if not isinstance(value,dict) or value.get('policy')!=POLICY:raise ValueError('annotation_review_cache_invalid')
    created=value.get('created_at')
    if type(created) not in (float,int) or not math.isfinite(created) or created<=0:
        raise ValueError('annotation_review_cache_invalid')
    if not 0<=time.time()-created<=TTL_SECONDS:raise ValueError('annotation_review_expired')
    return value


@contextmanager
def _publication_lock(folder):
    """Keep capacity admission and publication under one cross-process owner."""
    directory=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    fd=None
    try:
        fd=os.open('.publication.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,
                   0o600,dir_fd=directory)
        meta=os.fstat(fd)
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid!=os.geteuid()
                or stat.S_IMODE(meta.st_mode)!=0o600 or meta.st_nlink!=1 or meta.st_size!=0):
            raise ValueError('annotation_review_cache_invalid')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('annotation_review_cache_busy') from None
        yield
    finally:
        # Keep the inode: unlinking it could give another publisher a different
        # lock while an existing owner still holds the original descriptor.
        if fd is not None:os.close(fd)
        os.close(directory)


def create_review(state_root, scope_dict, report):
    scope=_scope(scope_dict);store=CollectionStore.open_existing(Path(state_root))
    if store is None:raise ValueError('annotation_review_scope_unavailable')
    if (report.get('schema')!=1 or report.get('mode')!='read_only_source_annotations'
            or report.get('coverage',{}).get('status')!='complete'
            or not isinstance(report.get('candidate_groups'),list)
            or re.fullmatch('[a-f0-9]{64}',str(report.get('scope_digest',''))) is None):
        raise ValueError('annotation_review_report_invalid')
    projects=report.get('projects')
    if (not isinstance(projects,list) or not 1<=len(projects)<=16
            or projects!=sorted(set(projects))
            or any(not isinstance(p,str) or not p or len(p)>128 for p in projects)
            or scope['scope']=='project' and projects!=[scope['project']]):
        raise ValueError('annotation_review_report_invalid')
    groups=report['candidate_groups']
    if len(groups)!=report['source_annotation_review']['counts']['same_projected_body_groups_with_tag_differences']:
        raise ValueError('annotation_review_report_invalid')
    if len(groups)>50000:raise ValueError('annotation_review_scan_budget')
    count=0
    for group in groups:
        if (not isinstance(group,dict) or group.get('project') not in projects
                or re.fullmatch('[a-f0-9]{64}',str(group.get('projected_body_sha256',''))) is None
                or not isinstance(group.get('source_versions'),list)
                or type(group.get('members')) is not int or group['members']!=len(group['source_versions']) or group['members']<2
                or not group.get('different_fields') or any(k not in ('topics','entityTags') for k in group['different_fields'])):
            raise ValueError('annotation_review_report_invalid')
        count+=group['members']
        if count>100000:raise ValueError('annotation_review_scan_budget')
        for member in group['source_versions']:
            if (not isinstance(member,dict) or set(member)!= {'document_id','version'}
                    or any(not isinstance(member[k],str) or not 0<len(member[k])<=256 for k in member)):
                raise ValueError('annotation_review_report_invalid')
    groups=[{key:group[key] for key in ('project','projected_body_sha256','different_fields','members','source_versions')}
            for group in groups]
    with store.database() as db:
        if scope_fingerprint(db,projects)!=report['scope_digest']:raise ValueError('annotation_review_stale')
    owner=store.path.stat()
    value={'policy':POLICY,'created_at':time.time(),'store_identity':{'path':str(store.path.absolute()),'device':owner.st_dev,'inode':owner.st_ino},'scope':scope,'projects':projects,
        'scope_digest':report['scope_digest'],'groups':groups,'semantic_status':'unverified'}
    raw=encoded(value)
    if len(raw)>MAX_CACHE_BYTES:raise ValueError('annotation_review_cache_budget')
    folder=_folder(store,True)
    with _publication_lock(folder):
        files=list(folder.glob('*.json'))
        if len(files)>=MAX_CACHE_FILES:
            # Only exact-hash, expired generated metadata can be reclaimed here.
            for path in files:
                try:_load(folder,path.stem)
                except ValueError as error:
                    if str(error)=='annotation_review_expired':path.unlink()
            if len(list(folder.glob('*.json')))>=MAX_CACHE_FILES:raise ValueError('annotation_review_cache_budget')
        review_id=digest(raw);fd,temp=tempfile.mkstemp(prefix='.review-',dir=folder)
        try:
            with os.fdopen(fd,'wb') as f:os.fchmod(f.fileno(),0o600);f.write(raw);f.flush();os.fsync(f.fileno())
            os.replace(temp,folder/(review_id+'.json'))
        finally:
            if os.path.exists(temp):os.unlink(temp)
    return review_id


def read_review(state_root, query, cancelled=None):
    scope={'scope':None};started=time.monotonic()
    try:
        scope=_scope(query)
        allowed=set(scope)|{'review_id','offset'}
        if 'group' in query:allowed.add('group')
        if set(query)!=allowed:raise ValueError('annotation_review_query_invalid')
        offset=query.get('offset');index=query.get('group')
        if 'group' in query and index is None:raise ValueError('annotation_review_query_invalid')
        if type(offset) is not int or not 0<=offset<=100000 or offset%PAGE_SIZE:
            raise ValueError('annotation_review_query_invalid')
        if index is not None and (type(index) is not int or not 0<=index<50000):
            raise ValueError('annotation_review_query_invalid')
        store=CollectionStore.open_existing(Path(state_root))
        if store is None:raise ValueError('annotation_review_scope_unavailable')
        review=_load(_folder(store),query['review_id'])
        owner=store.path.stat()
        if review.get('store_identity')!={'path':str(store.path.absolute()),'device':owner.st_dev,'inode':owner.st_ino}:
            raise ValueError('annotation_review_stale')
        if review['scope']!=scope:raise ValueError('annotation_review_scope_mismatch')
        next_poll=started
        def check():
            nonlocal next_poll
            now=time.monotonic()
            if cancelled is not None and now>=next_poll:
                next_poll=now+.05
                if cancelled():raise ValueError('annotation_review_cancelled')
            if now-started>20:raise ValueError('annotation_review_time_budget')
        check();groups=review['groups']
        with store.database() as db:
            if scope_fingerprint(db,review['projects'],check)!=review['scope_digest']:
                raise ValueError('annotation_review_stale')
            if index is None:
                total=len(groups)
                if offset>=total and total>0 or total==0 and offset!=0:raise ValueError('annotation_review_query_invalid')
                items=[{k:v for k,v in group.items() if k!='source_versions'}|{'group':i}
                    for i,group in enumerate(groups[offset:offset+PAGE_SIZE],offset)]
                kind='annotation_candidate_groups'
            else:
                if index>=len(groups):raise ValueError('annotation_review_query_invalid')
                group=groups[index];members=group['source_versions'];total=len(members)
                if offset>=total:raise ValueError('annotation_review_query_invalid')
                from alden_entity_resolution import _source_rows
                from alden_graph_quality import _json
                items=[]
                for member in members[offset:offset+PAGE_SIZE]:
                    check();project=group['project']
                    row=db.execute('''SELECT m.target_id,v.* FROM memberships m
                        JOIN target_projects p ON p.target_id=m.target_id AND p.project=?
                        JOIN versions v ON v.id=m.current_version
                        WHERE m.document_id=? AND m.current_version=? AND m.availability='available'
                        AND p.permission!='denied' ORDER BY m.target_id LIMIT 1''',
                        (project,member['document_id'],member['version'])).fetchone()
                    if row is None:raise ValueError('annotation_review_stale')
                    metadata=_json(row['metadata']);projection=digest(encoded([row['label'],row['body'],
                        {k:v for k,v in metadata.items() if k!='source'}]))
                    expected=identity('version',encoded([member['document_id'],row['raw_sha256'],row['processing_version'],projection]).decode())
                    if projection!=row['projection_sha256'] or expected!=member['version']:
                        raise ValueError('annotation_review_source_invalid')
                    _fingerprint,records=_source_rows(db,store,project,[member|{'target_id':row['target_id']}],
                        read_blobs=True,show_records=True,include_annotations=True)
                    record=records[0]
                    raw=store._verified_source_blob(store.blobs,row['raw_path'],16*1024*1024);source=_json(raw)
                    original=source.get('localOriginalText') if isinstance(source,dict) else None
                    if not isinstance(original,str):
                        raise ValueError('annotation_review_source_invalid')
                    projected=project_source_text(original,source)
                    if digest(projected['body'].encode())!=group['projected_body_sha256']:
                        raise ValueError('annotation_review_source_invalid')
                    record.pop('source_url',None);record['body']=projected['body'][:4000]
                    record['truncated']=len(projected['body'])>4000
                    record['content_evidence']={'policy':CONTENT_POLICY,'classification':content_evidence_class(
                        metadata,projected['body'],source=source,body_source='retained_record.localOriginalText')}
                    record['projected_body_sha256']=group['projected_body_sha256']
                    record['project']=project;items.append(record)
                kind='annotation_candidate_records'
        check()
        return {'ok':True,'schema':3,'kind':kind,'policy':POLICY,'scope':scope,
            'review_id':query['review_id'],'group':index,'total':total,'offset':offset,
            'next_offset':offset+PAGE_SIZE if offset+PAGE_SIZE<total else None,
            'items':items,'checked_at':time.time(),'semantic_status':'unverified',
            'source_verification':'displayed_current_records' if index is not None else 'current_scope_metadata_only'}
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,AttributeError,sqlite3.Error) as error:
        reason=str(error) if str(error).startswith('annotation_review_') else 'annotation_review_source_invalid'
        return {'ok':False,'schema':3,'reason':reason,'scope':scope}


def initial_page(scope, report, review_id):
    """Metadata from the just-created verified inspection, not a second scan."""
    groups=report['candidate_groups'];total=len(groups)
    items=[{k:v for k,v in group.items() if k!='source_versions'}|{'group':i}
        for i,group in enumerate(groups[:PAGE_SIZE])]
    return {'ok':True,'schema':3,'kind':'annotation_candidate_groups','policy':POLICY,
        'scope':scope,'review_id':review_id,'group':None,'total':total,'offset':0,
        'next_offset':PAGE_SIZE if total>PAGE_SIZE else None,'items':items,'checked_at':time.time(),
        'semantic_status':'unverified','source_verification':'inspection_snapshot_metadata'}

"""Complete, unmerged source-record graph archives from one read-only snapshot.

Archives preserve evidence; they do not grant later access to an original source
or replace display resolution decisions. This module never opens originals for
writing and has no host registration, model, embedding or message operation.
"""
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid

from alden_collection import (CollectionStore, encoded, identity, overview_display_label,
    relation_evidence_digest, _read_cancellation, _relation_proof_update)

KIND = 'alden-source-graph-export-v1'
APPLICATION = 0x414C4758
MAX_ROW_BYTES = 2 * 1024 * 1024
TABLES = {'targets': 'id', 'nodes': 'id', 'edges': 'source,relation,target'}


class ExportError(RuntimeError):
    pass


def _path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ExportError('collection_export_path_invalid')
    return path


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:raise ValueError('duplicate')
            result[key] = value
        return result
    def number(value):
        result = float(value)
        if not math.isfinite(result):raise ValueError('nonfinite')
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=number,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
    except (ValueError, UnicodeError, RecursionError):
        raise ExportError('collection_export_record_invalid') from None


def _bytes(path, maximum=65536):
    fd = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ExportError('collection_export_record_invalid')
        raw = source.read(maximum + 1)
    if len(raw) > maximum:raise ExportError('collection_export_record_invalid')
    return raw


def _sha_file(path, check):
    digest = hashlib.sha256()
    fd = os.open(_path(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):raise ExportError('collection_export_record_invalid')
        while raw := source.read(1024 * 1024):
            check();digest.update(raw)
        after = os.fstat(source.fileno())
    if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns) != (
            after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns):
        raise ExportError('collection_export_changed')
    return digest.hexdigest()


def _file_token(path):
    info=_path(path).stat()
    if not stat.S_ISREG(info.st_mode):raise ExportError('collection_export_record_invalid')
    return info.st_dev,info.st_ino,info.st_size,info.st_mode,info.st_mtime_ns,info.st_ctime_ns


def _write(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.export-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as destination:
            destination.write(encoded(value));destination.flush();os.fsync(destination.fileno())
        os.replace(temporary, _path(path))
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def _projects(projects):
    if (not isinstance(projects, list) or not 1 <= len(projects) <= 16
            or any(not isinstance(p, str) or not p or len(p)>128 for p in projects)
            or len(set(projects)) != len(projects)):
        raise ExportError('collection_export_scope_invalid')
    return sorted(projects)


def _open(root, factory):
    store = CollectionStore.open_existing(root) if factory is None else factory()
    if (not isinstance(store, CollectionStore) or store.read_only is not True
            or store.path != root/'knowledge/collection/collection.sqlite3'):
        raise ExportError('collection_export_readonly_source_required')
    return store


def _stamp(db, store, projects):
    marks = ','.join('?' for _ in projects)
    known = {r[0] for r in db.execute('SELECT DISTINCT project FROM target_projects WHERE permission!=\'denied\' AND project IN ('+marks+')',projects)}
    if known != set(projects):raise ValueError('collection_project_scope_invalid')
    authority = store._source_authority_snapshot(db, projects)
    if any(permission!='denied' and scope is not None and
           (not isinstance(scope,str) or not re.fullmatch('[0-9a-f]{64}',scope))
           for _,_,permission,scope in authority):
        raise ExportError('collection_export_authority_unavailable')
    # A declared authorization stream can remain stable while current content
    # changes. Preserve the full content revision in the checkpoint below;
    # never weaken its per-record guards or the final snapshot comparison.
    if callable(getattr(store,'source_stream',None)):
        access=tuple(tuple(r) for r in db.execute('''SELECT target_id,project,permission,
            CASE WHEN permission!='denied' THEN alden_source_stream(target_id) END
            FROM target_projects WHERE project IN ('''+marks+') ORDER BY target_id,project',projects))
        if any(scope is not None for _,_,permission,scope in access if permission!='denied'):
            if any(permission!='denied' and scope is not None and
                    (not isinstance(scope,str) or re.fullmatch('[0-9a-f]{64}',scope) is None)
                    for _,_,permission,scope in access):
                raise ExportError('collection_export_authority_unavailable')
            authority={'policy':'declared-source-access-stream-v1','scope':access}
    checkpoint = store._activity_scope(db, projects)[0]
    info = store.path.stat()
    return {'authority':hashlib.sha256(encoded(authority)).hexdigest(),
            'checkpoint':checkpoint,'file_identity':[info.st_dev,info.st_ino]}


def _records(db, store, projects, check, *, node_ids=None, edge_keys=None):
    """No visual filtering, page cap, degree sampling, or resolution overlay."""
    db.create_function('alden_relation_digest',1,relation_evidence_digest,deterministic=True)
    db.create_function('alden_display_label',2,overview_display_label,deterministic=True)
    cte, visible, relations, _ = store._graph_scope_sql(projects)
    args = [*visible,*relations]
    marks = ','.join('?' for _ in projects)
    nodes, claims, groups, invalid, readable, candidates, relationship_candidates = db.execute(cte+''' SELECT
        (SELECT COUNT(*) FROM shown), (SELECT COUNT(*) FROM permitted_edges),
        (SELECT COUNT(*) FROM (SELECT 1 FROM permitted_edges GROUP BY source,type,target)),
        (SELECT COUNT(*) FROM permitted_relationships WHERE history_bound IS NOT 1),
        (SELECT COUNT(*) FROM ranked r JOIN documents d ON d.id=r.document_id WHERE d.availability='available'),
        (SELECT COUNT(*) FROM scoped_memberships r JOIN documents d ON d.id=r.document_id WHERE d.availability='available'),
        (SELECT COUNT(*) FROM scoped_relationships)''',args).fetchone()
    if invalid:raise ExportError('collection_relation_integrity')
    if readable != candidates or claims != relationship_candidates:
        raise ExportError('collection_export_source_withheld')
    targets = db.execute('''SELECT t.id,t.label,t.platform FROM targets t WHERE EXISTS(
        SELECT 1 FROM target_projects p WHERE p.target_id=t.id AND p.permission!='denied'
        AND p.project IN ('''+marks+')) ORDER BY t.id COLLATE BINARY',projects).fetchall()
    expected = {'targets':len(targets),'nodes':nodes,'edges':groups,'claim_records':claims}
    def rows():
        for target in targets:
            check();yield 'targets',dict(target)
        contexts = ''', contexts AS MATERIALIZED (SELECT document_id,json_group_array(json(value)) AS value FROM (
            SELECT r.document_id,json_object('project',p.project,'target_id',r.target_id,
                'version',r.current_version,'label',v.label) AS value
            FROM ranked r JOIN target_projects p ON p.target_id=r.target_id JOIN versions v ON v.id=r.current_version
            WHERE p.permission!='denied' AND p.project IN ('''+marks+''')
            ORDER BY r.document_id COLLATE BINARY,p.project COLLATE BINARY,r.target_id COLLATE BINARY,r.current_version COLLATE BINARY)
            GROUP BY document_id)'''
        node_filter='' if node_ids is None else ' WHERE s.id IN (SELECT value FROM json_each(?))'
        cursor = db.execute(cte+contexts+''' SELECT s.id,s.visible_version,s.target_id,s.original_id,
            alden_display_label(v.label,v.body) AS label,json_extract(v.metadata,'$.kind') AS category,c.value AS contexts
            FROM shown s JOIN versions v ON v.id=s.visible_version JOIN contexts c ON c.document_id=s.id'''+node_filter+
            ' ORDER BY s.id COLLATE BINARY',(*args,*projects,*(() if node_ids is None else (encoded(node_ids).decode(),))))
        for row in cursor:
            check()
            category = row['category']
            yield 'nodes',{'id':row['id'],'label':row['label'] or row['original_id'],
                'category':category[:256] if isinstance(category,str) and category else 'document',
                'source_version':row['visible_version'],'source_target':row['target_id'],
                'source_contexts':_json(row['contexts'])}
        previous, sample, digest, count, first = None, [], None, 0, None
        def edge():
            source, relation, target = previous
            return {'id':first,'source':source,'target':target,'relation':relation,
                'reference_group':{'kind':'directed-relation-group-v1','count':count,
                                  'truncated':count>len(sample),'revision':digest.hexdigest()},
                'evidence':{'kind':'snapshot','source_event_ids':sample,'chat_id':'',
                            'confirmed_at':None,'retracted':False}}
        last = None
        edge_filter='' if edge_keys is None else ''' WHERE (source,type,target) IN (
            SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]'),json_extract(value,'$[2]') FROM json_each(?))'''
        for row in db.execute(cte+'SELECT id,source,type,target,version FROM permitted_edges'+edge_filter+
            ' ORDER BY source COLLATE BINARY,type COLLATE BINARY,target COLLATE BINARY,id COLLATE BINARY',
            (*args,*(() if edge_keys is None else (encoded(edge_keys).decode(),)))):
            check();key=(row['source'],row['type'],row['target'])
            if key != previous:
                if previous is not None:yield 'edges',edge()
                previous, sample, count, first, last = key, [], 0, row['id'], None
                digest=hashlib.sha256(b'alden-directed-relation-view-v1\x00')
            if last is not None and row['id']<=last:raise ExportError('collection_export_relation_order')
            last=row['id'];count+=1
            if len(sample)<16:sample.append(row['id'])
            _relation_proof_update(digest,row['id'],row['version'])
        if previous is not None:yield 'edges',edge()
    return expected, rows()


SCHEMA = '''PRAGMA application_id=1095518040; PRAGMA user_version=1; PRAGMA foreign_keys=ON;
CREATE TABLE targets(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE nodes(id TEXT PRIMARY KEY,version TEXT NOT NULL,target TEXT NOT NULL REFERENCES targets(id),payload TEXT NOT NULL);
CREATE TABLE edges(id TEXT PRIMARY KEY,source TEXT NOT NULL REFERENCES nodes(id),relation TEXT NOT NULL,
 target TEXT NOT NULL REFERENCES nodes(id),claims INTEGER NOT NULL CHECK(claims>0),payload TEXT NOT NULL,
 UNIQUE(source,relation,target));'''


def _connect(path):
    db=sqlite3.connect(_path(path).as_uri()+'?mode=ro',uri=True,timeout=.15)
    db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
    return db


def _verify_data(path, expected, hashes, check):
    with closing(_connect(path)) as db:
        if db.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION or db.execute('PRAGMA user_version').fetchone()[0]!=1:
            raise ExportError('collection_export_schema_invalid')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise ExportError('collection_export_endpoint_invalid')
        actual={'targets':0,'nodes':0,'edges':0,'claim_records':0}
        for table,order in TABLES.items():
            digest=hashlib.sha256()
            for row in db.execute('SELECT * FROM '+table+' ORDER BY '+order):
                check();payload=row['payload'].encode();value=_json(payload)
                if encoded(value)!=payload or not isinstance(value,dict) or value.get('id')!=row['id']:
                    raise ExportError('collection_export_record_invalid')
                if table=='nodes' and (value.get('source_version')!=row['version'] or value.get('source_target')!=row['target']):
                    raise ExportError('collection_export_record_invalid')
                if table=='edges':
                    if any(value.get(k)!=row[k] for k in ('source','relation','target')) or value.get('reference_group',{}).get('count')!=row['claims']:
                        raise ExportError('collection_export_record_invalid')
                    actual['claim_records']+=row['claims']
                digest.update(len(payload).to_bytes(8,'big'));digest.update(payload);actual[table]+=1
            if digest.hexdigest()!=hashes[table]:raise ExportError('collection_export_digest_mismatch')
        if actual!=expected:raise ExportError('collection_export_count_mismatch')


def export_graph(state_root, projects, output, *, store_factory=None, cancelled=lambda:False,
                 max_records=2_000_000, max_bytes=2*1024**3):
    root,output,projects=_path(state_root),_path(output),_projects(projects)
    if type(max_records) is not int or not 1<=max_records<=10_000_000 or type(max_bytes) is not int or not 65536<=max_bytes<=8*1024**3:
        raise ExportError('collection_export_budget_invalid')
    def check():
        if cancelled():raise ExportError('collection_export_cancelled')
    check();store=_open(root,store_factory)
    if output.exists() or not output.parent.is_dir():raise ExportError('collection_export_new_output_required')
    output.mkdir(mode=0o700);owner={'kind':KIND,'owner':uuid.uuid4().hex,
        'source_root_sha256':hashlib.sha256(str(root).encode()).hexdigest(),'projects':projects}
    created_identity=(output.stat().st_dev,output.stat().st_ino)
    complete=False
    try:
        _write(output/'owner.json',owner)
        with _read_cancellation(cancelled), store.database() as source:
            stamp=_stamp(source,store,projects)
            expected,rows=_records(source,store,projects,check)
            if expected['targets']+expected['nodes']+expected['claim_records']>max_records:
                raise ExportError('collection_export_record_budget')
            hashes={table:hashlib.sha256() for table in TABLES}
            counts={'targets':0,'nodes':0,'edges':0,'claim_records':0}
            path=output/'graph.sqlite3'
            with closing(sqlite3.connect(path)) as destination:
                path.chmod(0o600);destination.execute('PRAGMA journal_mode=DELETE');destination.execute('PRAGMA synchronous=FULL')
                destination.executescript(SCHEMA);destination.execute('BEGIN IMMEDIATE')
                for table,value in rows:
                    raw=encoded(value)
                    if len(raw)>MAX_ROW_BYTES:raise ExportError('collection_export_row_budget')
                    text=raw.decode()
                    if table=='targets':destination.execute('INSERT INTO targets VALUES(?,?)',(value['id'],text))
                    elif table=='nodes':destination.execute('INSERT INTO nodes VALUES(?,?,?,?)',(value['id'],value['source_version'],value['source_target'],text))
                    else:
                        claims=value['reference_group']['count'];counts['claim_records']+=claims
                        destination.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',(value['id'],value['source'],value['relation'],value['target'],claims,text))
                    hashes[table].update(len(raw).to_bytes(8,'big'));hashes[table].update(raw);counts[table]+=1
                    if sum(counts[t] for t in TABLES)%128==0:
                        size=destination.execute('PRAGMA page_count').fetchone()[0]*destination.execute('PRAGMA page_size').fetchone()[0]
                        if size>max_bytes:raise ExportError('collection_export_byte_budget')
                if counts!=expected:raise ExportError('collection_export_count_mismatch')
                destination.commit()
            if path.stat().st_size>max_bytes:raise ExportError('collection_export_byte_budget')
        check();fresh=_open(root,store_factory)
        with _read_cancellation(cancelled), fresh.database() as source:
            observed=_stamp(source,fresh,projects)
        if any(observed[k]!=stamp[k] for k in ('authority','file_identity')) or observed['checkpoint']['stream_id']!=stamp['checkpoint']['stream_id']:
            raise ExportError('collection_export_source_changed')
        hashes={table:value.hexdigest() for table,value in hashes.items()}
        token=_file_token(path)
        _verify_data(path,expected,hashes,check)
        database_sha256=_sha_file(path,check)
        # Persisted readback and hashing can be lengthy. Recheck authority after
        # both, and bind the final receipt to the very same verified file.
        fresh=_open(root,store_factory)
        with _read_cancellation(cancelled), fresh.database() as source:
            observed=_stamp(source,fresh,projects)
        if any(observed[k]!=stamp[k] for k in ('authority','file_identity')) or observed['checkpoint']['stream_id']!=stamp['checkpoint']['stream_id']:
            raise ExportError('collection_export_source_changed')
        if _file_token(path)!=token:raise ExportError('collection_export_changed')
        receipt={'kind':KIND,'owner':owner['owner'],'source_root_sha256':owner['source_root_sha256'],
            'projects':projects,'complete':True,'basis':'all eligible current source records; unmerged; no visual sampling',
            'as_of':stamp,'observed_at_completion':observed,
            'checkpoint_unchanged_at_completion':stamp['checkpoint']==observed['checkpoint'],
            'counts':expected,'row_hashes':hashes,'database_sha256':database_sha256,'database_bytes':token[2]}
        check();_write(output/'receipt.json',receipt)
        if _json(_bytes(output/'receipt.json'))!=receipt:raise ExportError('collection_export_publish_invalid')
        complete=True
        return receipt
    finally:
        if not complete:
            # Only the directory created by this invocation is removable.
            # A missing/replaced owner is refused rather than guessed.
            info=_path(output).stat()
            if (info.st_dev,info.st_ino)!=created_identity:
                raise ExportError('collection_export_owner_changed')
            if (output/'owner.json').exists() and _json(_bytes(output/'owner.json'))!=owner:
                raise ExportError('collection_export_owner_changed')
            if output.is_symlink() or any(p.is_symlink() for p in output.rglob('*')):
                raise ExportError('collection_export_output_unsafe')
            shutil.rmtree(output)


def verify_export(output, *, check=lambda:None):
    """Integrity only. This receipt is not permission to read an original source."""
    output=_path(output);info=output.stat()
    if info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
        raise ExportError('collection_export_private_output_required')
    owner,receipt=(_json(_bytes(output/name)) for name in ('owner.json','receipt.json'))
    if (not isinstance(owner,dict) or owner.get('kind')!=KIND or not isinstance(receipt,dict)
            or receipt.get('kind')!=KIND or receipt.get('complete') is not True
            or any(receipt.get(k)!=owner.get(k) for k in ('owner','source_root_sha256','projects'))):
        raise ExportError('collection_export_receipt_invalid')
    counts,hashes=receipt.get('counts'),receipt.get('row_hashes')
    if (not isinstance(counts,dict) or set(counts)!={'targets','nodes','edges','claim_records'}
            or any(type(n) is not int or n<0 for n in counts.values()) or counts['claim_records']<counts['edges']
            or not isinstance(hashes,dict) or set(hashes)!=set(TABLES)
            or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes.values())
            or type(receipt.get('database_bytes')) is not int or not 1<=receipt['database_bytes']<=8*1024**3):
        raise ExportError('collection_export_receipt_invalid')
    path=output/'graph.sqlite3'
    token=_file_token(path)
    if token[2]!=receipt['database_bytes'] or _sha_file(path,check)!=receipt.get('database_sha256'):
        raise ExportError('collection_export_digest_mismatch')
    _verify_data(path,receipt['counts'],receipt['row_hashes'],check)
    if _file_token(path)!=token:raise ExportError('collection_export_changed')
    return receipt


def verify_current_export(state_root, projects, output, *, store_factory=None, cancelled=lambda:False):
    """Explicit full source revalidation, not a fast event/delta checkpoint."""
    def check():
        if cancelled():raise ExportError('collection_export_cancelled')
    receipt=verify_export(output,check=check)
    root,projects=_path(state_root),_projects(projects)
    if receipt['projects']!=projects or receipt['source_root_sha256']!=hashlib.sha256(str(root).encode()).hexdigest():
        raise ExportError('collection_export_scope_mismatch')
    store=_open(root,store_factory)
    with _read_cancellation(cancelled), store.database() as source:
        stamp=_stamp(source,store,projects)
        if stamp!=receipt['as_of']:raise ExportError('collection_export_stale')
        expected,rows=_records(source,store,projects,check)
        if expected!=receipt['counts']:raise ExportError('collection_export_stale')
        hashes={table:hashlib.sha256() for table in TABLES}
        actual={'targets':0,'nodes':0,'edges':0,'claim_records':0}
        for table,value in rows:
            raw=encoded(value);check()
            hashes[table].update(len(raw).to_bytes(8,'big'));hashes[table].update(raw);actual[table]+=1
            if table=='edges':actual['claim_records']+=value['reference_group']['count']
        if actual!=expected or {table:value.hexdigest() for table,value in hashes.items()}!=receipt['row_hashes']:
            raise ExportError('collection_export_stale')
    fresh=_open(root,store_factory)
    with _read_cancellation(cancelled), fresh.database() as source:
        if _stamp(source,fresh,projects)!=stamp:raise ExportError('collection_export_source_changed')
    return receipt


def main():
    import argparse
    import time
    from alden_abort import ABORT_STATE_NAME,AbortToken
    from alden_knowledge_mcp import KnowledgeServer
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('export','verify'))
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--project',action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--messenger-source',choices=('kakaotalk','enrolled'))
    parser.add_argument('--max-records',type=int,default=2_000_000)
    parser.add_argument('--max-bytes',type=int,default=2*1024**3)
    args=parser.parse_args();until=time.monotonic()+300
    token=AbortToken(args.state_root/ABORT_STATE_NAME)
    def cancelled():return token.is_cancelled() or time.monotonic()>=until
    try:
        factory=None
        if args.messenger_source:
            from alden_messenger_factory import open_existing_kakao, open_enrolled_messengers
            factory=open_enrolled_messengers if args.messenger_source=='enrolled' else open_existing_kakao
        authority=KnowledgeServer(args.state_root,args.project,source_factory=factory)
        def store_factory():
            return authority.collection_store(cancelled)
        if args.action=='export':
            result=export_graph(args.state_root,args.project,args.output,store_factory=store_factory,
                cancelled=cancelled,max_records=args.max_records,max_bytes=args.max_bytes)
        else:
            result=verify_current_export(args.state_root,args.project,args.output,store_factory=store_factory,cancelled=cancelled)
        print(json.dumps({'ok':True,'counts':result['counts'],'as_of':result['as_of']['checkpoint'],
            'checkpoint_unchanged_at_completion':result['checkpoint_unchanged_at_completion'],
            'database_sha256':result['database_sha256'],'database_bytes':result['database_bytes']}))
    except Exception as error:
        code=str(error) if isinstance(error,ExportError) else 'collection_export_unavailable'
        if isinstance(error,PermissionError):code='collection_export_permission_denied'
        if str(error)=='collection_project_scope_invalid':code=str(error)
        print(json.dumps({'ok':False,'error':code}));raise SystemExit(1) from None


if __name__=='__main__':main()

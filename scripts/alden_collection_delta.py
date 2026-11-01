"""Scoped journal-assisted source archive updates, with complete source checks.

Only private derived archives are copied/written. The original stays query-only.
An untracked change requires a full export; a denial never selects another reader.
"""
from contextlib import closing
import hashlib
import os
import re
import shutil
import sqlite3
import uuid

from alden_collection import encoded,_read_cancellation
from alden_collection_export import (KIND,TABLES,MAX_ROW_BYTES,ExportError,_path,_projects,_open,_stamp,
    _records,_connect,_file_token,_sha_file,_write,_bytes,_json,_verify_data,verify_export,export_graph)

MAX_EVENTS=100_000
MAX_KEYS=20_000


class _UntrackedChange(ExportError):pass
class _WindowBudget(ExportError):pass


def _compatible(a,b):
    def valid(value):
        if (not isinstance(value,dict) or set(value)!={'authority','checkpoint','file_identity'}
                or not isinstance(value['authority'],str) or re.fullmatch('[0-9a-f]{64}',value['authority']) is None
                or not isinstance(value['file_identity'],list) or len(value['file_identity'])!=2
                or any(type(n) is not int or n<0 for n in value['file_identity'])):return False
        c=value['checkpoint']
        return (isinstance(c,dict) and {'stream_id','cursor'}<=set(c)<= {'stream_id','cursor','content_revision'}
            and isinstance(c['stream_id'],str) and re.fullmatch('stream:[0-9a-f]{64}',c['stream_id']) is not None
            and type(c['cursor']) is int and c['cursor']>=0
            and ('content_revision' not in c or isinstance(c['content_revision'],str)
                 and re.fullmatch('[0-9a-f]{64}',c['content_revision']) is not None))
    if not valid(a) or not valid(b):raise ExportError('collection_delta_checkpoint_invalid')
    return (a['authority']==b['authority'] and a['file_identity']==b['file_identity']
        and a['checkpoint']['stream_id']==b['checkpoint']['stream_id']
        and a['checkpoint']['cursor']<=b['checkpoint']['cursor'])


def _window(source,store,projects,previous,current,base,check):
    _,_,where,args=store._activity_scope(source,projects)
    rows=source.execute('''SELECT e.sequence,e.event_id,e.target_id,e.document_id,e.stage,
        CASE WHEN length(CAST(e.details AS BLOB))<=65536 THEN e.details END AS details,
        r.state AS run_state FROM events e JOIN runs r ON r.id=e.run_id
        WHERE '''+where+' AND e.sequence>? AND e.sequence<=? ORDER BY e.sequence LIMIT ?',
        (*args,previous['checkpoint']['cursor'],current['checkpoint']['cursor'],MAX_EVENTS+1)).fetchall()
    if len(rows)>MAX_EVENTS:raise _WindowBudget('collection_delta_window_budget')
    documents=set();targets=set();published=set()
    for row in rows:
        check()
        if row['details'] is None:raise ExportError('collection_delta_journal_invalid')
        details=_json(row['details'])
        if not isinstance(details,dict):raise ExportError('collection_delta_journal_invalid')
        if row['run_state']=='complete' and row['stage']=='published':published.add(details.get('stored_event_id'))
    for row in rows:
        check()
        if row['run_state']!='complete':continue
        targets.add(row['target_id']);details=_json(row['details'])
        if (row['stage']=='stored' and details.get('activity_deferred') is True
                and details.get('change') in ('added','revised','removed','relations_changed')
                and row['event_id'] not in published):
            raise ExportError('collection_delta_publication_deferred')
        if row['stage'] in ('stored','published') and row['document_id'] is not None:documents.add(row['document_id'])
    if len(documents)>MAX_KEYS:raise _WindowBudget('collection_delta_window_budget')
    keys=set()
    # Inactive relation history covers group removals and changed proofs.
    # Relations touching a revised/deleted document can belong to another
    # permitted target, and must be re-aggregated as complete groups.
    query='''SELECT DISTINCT r.source,r.type,r.target FROM relations r WHERE
        EXISTS(SELECT 1 FROM target_projects p WHERE p.target_id=r.target_id AND p.permission!='denied'
            AND p.project IN (SELECT value FROM json_each(?)))
        AND (r.target_id IN (SELECT value FROM json_each(?))
            OR r.source IN (SELECT value FROM json_each(?)) OR r.target IN (SELECT value FROM json_each(?)))
        ORDER BY r.source,r.type,r.target LIMIT ?'''
    docs=encoded(sorted(documents)).decode()
    for row in source.execute(query,(encoded(projects).decode(),encoded(sorted(targets)).decode(),docs,docs,MAX_KEYS+1)):
        check();keys.add(tuple(row))
    with closing(_connect(base)) as db:
        for row in db.execute('''SELECT source,relation,target FROM edges WHERE
                source IN (SELECT value FROM json_each(?)) OR target IN (SELECT value FROM json_each(?))
                ORDER BY source,relation,target LIMIT ?''',(docs,docs,MAX_KEYS+1)):
            check();keys.add(tuple(row))
    if len(keys)>MAX_KEYS:raise _WindowBudget('collection_delta_window_budget')
    return sorted(documents),sorted(keys),len(rows)


def _summary(path,check):
    counts={'targets':0,'nodes':0,'edges':0,'claim_records':0};hashes={}
    with closing(_connect(path)) as db:
        for table,order in TABLES.items():
            digest=hashlib.sha256()
            for row in db.execute('SELECT * FROM '+table+' ORDER BY '+order):
                check();raw=row['payload'].encode();digest.update(len(raw).to_bytes(8,'big'));digest.update(raw)
                counts[table]+=1
                if table=='edges':counts['claim_records']+=row['claims']
            hashes[table]=digest.hexdigest()
    return counts,hashes


def _matches(source,store,projects,path,check):
    expected,rows=_records(source,store,projects,check)
    counts={'targets':0,'nodes':0,'edges':0,'claim_records':0};hashes={t:hashlib.sha256() for t in TABLES}
    for table,value in rows:
        check();raw=encoded(value);hashes[table].update(len(raw).to_bytes(8,'big'));hashes[table].update(raw);counts[table]+=1
        if table=='edges':counts['claim_records']+=value['reference_group']['count']
    actual,digests=_summary(path,check)
    return expected==counts==actual and digests=={t:h.hexdigest() for t,h in hashes.items()}


def export_incremental(state_root,projects,output,*,previous,store_factory=None,cancelled=lambda:False,
                       max_records=2_000_000,max_bytes=2*1024**3):
    root,output,projects,previous=_path(state_root),_path(output),_projects(projects),_path(previous)
    if (type(max_records) is not int or not 1<=max_records<=10_000_000 or type(max_bytes) is not int
            or not 65536<=max_bytes<=8*1024**3):raise ExportError('collection_export_budget_invalid')
    def check():
        if cancelled():raise ExportError('collection_export_cancelled')
    def full(reason):
        value=export_graph(root,projects,output,store_factory=store_factory,cancelled=cancelled,
            max_records=max_records,max_bytes=max_bytes)
        value['source_update']={'mode':'full_export','reason':reason}
        _write(output/'receipt.json',value)
        if _json(_bytes(output/'receipt.json'))!=value:raise ExportError('collection_export_publish_invalid')
        return value
    check();base=verify_export(previous,check=check)
    if base['projects']!=projects or base['source_root_sha256']!=hashlib.sha256(str(root).encode()).hexdigest():
        raise ExportError('collection_export_scope_mismatch')
    if output.exists() or not output.parent.is_dir():raise ExportError('collection_export_new_output_required')
    store=_open(root,store_factory)
    with _read_cancellation(cancelled),store.database() as source:stamp=_stamp(source,store,projects)
    if not _compatible(base['as_of'],stamp):return full('source_baseline_changed')
    output.mkdir(mode=0o700);owner={'kind':KIND,'owner':uuid.uuid4().hex,
        'source_root_sha256':base['source_root_sha256'],'projects':projects}
    created=(output.stat().st_dev,output.stat().st_ino);complete=False;fallback=None
    try:
        _write(output/'owner.json',owner);path=output/'graph.sqlite3';base_path=previous/'graph.sqlite3'
        base_token=_file_token(base_path)
        with closing(_connect(base_path)) as original,closing(sqlite3.connect(path)) as destination:
            path.chmod(0o600);original.backup(destination,pages=128,progress=lambda *_:check(),sleep=.01)
            destination.execute('PRAGMA journal_mode=DELETE');destination.execute('PRAGMA synchronous=FULL')
        if _file_token(base_path)!=base_token:raise ExportError('collection_export_changed')
        writes={'targets_put':0,'nodes_put':0,'nodes_deleted':0,'edges_put':0,'edges_deleted':0}
        with _read_cancellation(cancelled),store.database() as source:
            stamp=_stamp(source,store,projects)
            if not _compatible(base['as_of'],stamp):raise ExportError('collection_export_source_changed')
            nodes,keys,event_count=_window(source,store,projects,base['as_of'],stamp,base_path,check)
            expected,rows=_records(source,store,projects,check,node_ids=nodes,edge_keys=keys)
            if expected['targets']+expected['nodes']+expected['claim_records']>max_records:
                raise ExportError('collection_export_record_budget')
            seen_nodes=set();seen_edges=set()
            with closing(sqlite3.connect(path)) as db:
                db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
                # Insert/update first; remove absent edges before absent nodes.
                for table,value in rows:
                    check();raw=encoded(value)
                    if len(raw)>MAX_ROW_BYTES:raise ExportError('collection_export_row_budget')
                    text=raw.decode()
                    if table=='targets':
                        changed=db.execute('''INSERT INTO targets VALUES(?,?) ON CONFLICT(id) DO UPDATE SET
                            payload=excluded.payload WHERE targets.payload!=excluded.payload''',(value['id'],text)).rowcount
                    elif table=='nodes':
                        seen_nodes.add(value['id'])
                        changed=db.execute('''INSERT INTO nodes VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                            version=excluded.version,target=excluded.target,payload=excluded.payload
                            WHERE nodes.payload!=excluded.payload''',(value['id'],value['source_version'],value['source_target'],text)).rowcount
                    else:
                        seen_edges.add((value['source'],value['relation'],value['target']))
                        changed=db.execute('''INSERT INTO edges VALUES(?,?,?,?,?,?) ON CONFLICT(source,relation,target) DO UPDATE SET
                            id=excluded.id,claims=excluded.claims,payload=excluded.payload WHERE edges.payload!=excluded.payload''',
                            (value['id'],value['source'],value['relation'],value['target'],value['reference_group']['count'],text)).rowcount
                    writes[table+'_put']+=changed
                for key in keys:
                    check()
                    if key not in seen_edges:writes['edges_deleted']+=db.execute('DELETE FROM edges WHERE source=? AND relation=? AND target=?',key).rowcount
                for node in nodes:
                    check()
                    if node not in seen_nodes:writes['nodes_deleted']+=db.execute('DELETE FROM nodes WHERE id=?',(node,)).rowcount
                if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise _UntrackedChange('collection_delta_untracked_change')
                db.commit()
            if path.stat().st_size>max_bytes:raise ExportError('collection_export_byte_budget')
            # Do not trust journal completeness. Compare every canonical
            # source row against the resulting archive in this same snapshot.
            if not _matches(source,store,projects,path,check):raise _UntrackedChange('collection_delta_untracked_change')
        fresh=_open(root,store_factory)
        with _read_cancellation(cancelled),fresh.database() as source:
            observed=_stamp(source,fresh,projects)
        if observed!=stamp:raise ExportError('collection_export_source_changed')
        counts,hashes=_summary(path,check);_verify_data(path,counts,hashes,check)
        token=_file_token(path);digest=_sha_file(path,check)
        fresh=_open(root,store_factory)
        with _read_cancellation(cancelled),fresh.database() as source:
            if _stamp(source,fresh,projects)!=stamp:raise ExportError('collection_export_source_changed')
        if _file_token(path)!=token:raise ExportError('collection_export_changed')
        if _file_token(base_path)!=base_token:raise ExportError('collection_export_changed')
        receipt={'kind':KIND,'owner':owner['owner'],'source_root_sha256':owner['source_root_sha256'],'projects':projects,
            'complete':True,'basis':'all eligible current source records; unmerged; no visual sampling',
            'as_of':stamp,'observed_at_completion':observed,'checkpoint_unchanged_at_completion':True,
            'counts':counts,'row_hashes':hashes,'database_sha256':digest,'database_bytes':token[2],
            'source_update':{'mode':'journal_delta','base_database_sha256':base['database_sha256'],
                'after':base['as_of']['checkpoint'],'through':stamp['checkpoint'],'events':event_count,
                'candidate_nodes':len(nodes),'candidate_groups':len(keys),'archive_rows':writes,
                'complete_source_comparison':True}}
        check();_write(output/'receipt.json',receipt)
        if _json(_bytes(output/'receipt.json'))!=receipt:raise ExportError('collection_export_publish_invalid')
        complete=True;return receipt
    except (_UntrackedChange,sqlite3.IntegrityError):fallback='untracked_source_change'
    except _WindowBudget:fallback='journal_window_budget'
    finally:
        if not complete:
            info=_path(output).stat()
            if ((info.st_dev,info.st_ino)!=created
                    or (output/'owner.json').exists() and _json(_bytes(output/'owner.json'))!=owner):
                raise ExportError('collection_export_owner_changed')
            if any(p.is_symlink() for p in output.rglob('*')):raise ExportError('collection_export_output_unsafe')
            shutil.rmtree(output)
    # The rejected candidate is terminal and absent. The same source reader,
    # not an alternate backend, supplies a complete newly verified baseline.
    return full(fallback)

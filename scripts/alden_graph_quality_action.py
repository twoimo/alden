"""Bounded product-facing quality observations; no blob reads or repairs."""
import math
from pathlib import Path
import re
import sqlite3
import time
import unicodedata

from alden_collection import CollectionStore
from alden_graph_quality import _json, audit

COUNT_FIELDS=('memberships','project_current_versions','relations','distinct_current_documents','bytes_examined')
ERRORS=frozenset(('quality_scope_unavailable','quality_scan_budget','quality_time_budget',
                 'quality_cancelled','quality_report_unavailable','quality_query_invalid'))

def _count(value):
    if type(value) is not int or not 0<=value<=9_007_199_254_740_991:
        raise ValueError('quality_report_unavailable')
    return value

def _query(raw):
    if not isinstance(raw,str) or len(raw.encode('utf-8'))>4096:
        raise ValueError('quality_query_invalid')
    try: value=_json(raw)
    except (ValueError,TypeError,RecursionError):raise ValueError('quality_query_invalid') from None
    if not isinstance(value,dict):raise ValueError('quality_query_invalid')
    scope=value.get('scope')
    if scope=='all' and set(value)=={'scope'}:return scope,None
    project=value.get('project')
    if (scope=='project' and set(value)=={'scope','project'} and isinstance(project,str)
            and 0<len(project)<=128 and project==project.strip()
            and not any(unicodedata.category(c)=='Cc' for c in project)):
        return scope,project
    raise ValueError('quality_query_invalid')

def _bindings(store,projects):
    marks=','.join('?' for _ in projects)
    with store.database() as db:
        rows=[tuple(r) for r in db.execute('SELECT target_id,project,permission FROM target_projects WHERE project IN ('+marks+') ORDER BY target_id,project LIMIT 4097',projects)]
    if len(rows)>4096 or any(not all(isinstance(v,str) and 0<len(v)<=128 for v in row) for row in rows):
        raise ValueError('quality_scope_unavailable')
    return rows

def _projects(store,mode,project):
    with store.database() as db:
        if mode=='all':
            return [r[0] for r in db.execute("SELECT DISTINCT project FROM target_projects WHERE permission!='denied' ORDER BY project LIMIT 17")]
        return [r[0] for r in db.execute("SELECT DISTINCT project FROM target_projects WHERE permission!='denied' AND project=? LIMIT 1",(project,))]

def _summary(report,projects):
    if (not isinstance(report,dict) or report.get('schema')!=2 or report.get('mode')!='read_only_current_versions'
            or report.get('projects')!=projects or report.get('source_verification_requested') is not False):
        raise ValueError('quality_report_unavailable')
    coverage=report.get('coverage',{})
    if (coverage.get('status')!='complete' or coverage.get('scope')!='current_permitted_memberships_only'
            or coverage.get('source_verification')!='hash_references_only'
            or coverage.get('projects')!=len(projects)):
        raise ValueError('quality_report_unavailable')
    counts={key:_count(report['counts'][key]) for key in COUNT_FIELDS}
    if (counts['memberships']+counts['relations']>100000 or counts['bytes_examined']>512*1024*1024
            or counts['distinct_current_documents']>counts['project_current_versions']
            or counts['project_current_versions']>counts['memberships']):
        raise ValueError('quality_report_unavailable')
    if (_count(report['counts']['source_blobs_verified'])!=0
            or _count(coverage.get('memberships_scanned'))!=counts['memberships']
            or _count(coverage.get('relations_scanned'))!=counts['relations']):
        raise ValueError('quality_report_unavailable')
    findings=report.get('findings')
    if not isinstance(findings,dict) or len(findings)>64:
        raise ValueError('quality_report_unavailable')
    if any(not isinstance(key,str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}',key) for key in findings):
        raise ValueError('quality_report_unavailable')
    findings={key:_count(value) for key,value in findings.items()}
    duplicates={}
    for key in ('same_raw_bytes','same_normalized_search_text','cross_context_search_text'):
        candidate=report['duplicate_candidates'][key]
        if candidate.get('status')!='review_candidates_not_confirmed_entities':
            raise ValueError('quality_report_unavailable')
        groups=_count(candidate['groups'])
        if key=='same_raw_bytes' and (candidate.get('classification')!='identical_canonical_record_hash_only'
                or _count(candidate.get('verified_groups'))!=0 or _count(candidate.get('hash_only_groups'))!=groups):
            raise ValueError('quality_report_unavailable')
        duplicates[key]={'groups':groups,'status':'hash_references_only' if key=='same_raw_bytes' else 'review_candidate'}
    numeric=report['numeric_review_candidates']['mixed_vector_dimensions']
    if numeric.get('status')!='review_candidate_not_semantic_outlier':
        raise ValueError('quality_report_unavailable')
    return {'counts':counts,'findings':findings,'duplicate_candidates':duplicates,
        'numeric_review_candidates':{'mixed_vector_dimensions':{'profiles':_count(numeric['profiles']),'status':'review_candidate'}},
        'normalization':{'method':'NFC_line_endings_preserving_body_spacing','semantic_validity':'unverified'}}

def read_action(state_root,query_raw,cancelled=None):
    started=time.monotonic()
    scope={'requested_scope':None,'requested_project':None}
    try:
        mode,project=_query(query_raw)
        scope={'requested_scope':mode,'requested_project':project}
        if cancelled is not None and cancelled():raise RuntimeError('quality_cancelled')
        store=CollectionStore.open_existing(Path(state_root))
        if store is None or store.schema!=3:raise ValueError('quality_scope_unavailable')
        before=store.path.stat()
        available=_projects(store,mode,project)
        if not available or len(available)>16 or any(not isinstance(p,str) or not p or len(p)>128 for p in available):
            raise ValueError('quality_scope_unavailable')
        projects=available if mode=='all' else [project]
        if project is not None and project not in available:raise ValueError('quality_scope_unavailable')
        permissions=_bindings(store,projects)
        report=audit(state_root,projects,verify_sources=False,sample_cap=0,
                     timeout=10,max_rows=100000,max_bytes=512*1024*1024,cancelled=cancelled)
        summary=_summary(report,projects)
        if cancelled is not None and cancelled():raise RuntimeError('quality_cancelled')
        after=store.path.stat()
        if ((before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino)
                or available!=_projects(store,mode,project) or permissions!=_bindings(store,projects)):
            raise ValueError('quality_scope_unavailable')
        checked=time.time();elapsed=(time.monotonic()-started)*1000
        if not math.isfinite(checked) or checked<=0 or not math.isfinite(elapsed) or elapsed<0:
            raise ValueError('quality_report_unavailable')
        return {'ok':True,'schema':1,'scope':{**scope,'projects':projects,
            'coverage':'permitted_current_collection_memberships','filters':'project_only',
            'source_verification':'hash_references_only'},'checked_at':checked,'elapsed_ms':round(elapsed,3),**summary}
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,RecursionError,AttributeError,sqlite3.Error) as error:
        code=str(error) if str(error) in ERRORS else 'quality_report_unavailable'
        return {'ok':False,'schema':1,'scope':scope,'reason':code}

"""Offline numerical neighbor oracle over an explicitly exported private snapshot.

Requires an explicitly selected NumPy backend. Never opens the collection,
starts inference, writes graph data or certifies semantic truth/source authority.
"""
from array import array
from dataclasses import asdict
import argparse
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import stat
import sys

from alden_semantic_affinity import E5Profile

MAX_NODES=2048
MAX_CANDIDATES=4096
TOLERANCE=1e-12

def _json(data):
    def constant(_):raise ValueError('oracle_json_invalid')
    def fields(rows):
        result={}
        for key,value in rows:
            if key in result:raise ValueError('oracle_json_invalid')
            result[key]=value
        return result
    return json.loads(data,parse_constant=constant,object_pairs_hook=fields)

def _bytes(path,cap):
    path=Path(path)
    if any(p.is_symlink()for p in (path,*path.parents)):raise ValueError('oracle_path_invalid')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb')as f:
        before=os.fstat(f.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid() or not 0<before.st_size<=cap:
            raise ValueError('oracle_file_invalid')
        data=f.read(cap+1);after=os.fstat(f.fileno())
    current=os.stat(path,follow_symlinks=False)
    signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    if signature(before)!=signature(after) or signature(after)!=signature(current) or len(data)!=before.st_size:
        raise ValueError('oracle_file_changed')
    return data

def _identifier(value,cap=256):
    return isinstance(value,str) and 0<len(value)<=cap and not any(ord(c)<32 for c in value)

def load_snapshot(path):
    """Validate exported bytes; this does not re-perform live source admission."""
    data=_bytes(path,16*1024*1024);value=_json(data)
    if (not isinstance(value,dict) or type(value.get('schema'))is not int
            or value['schema']!=1 or value.get('profile')!=asdict(E5Profile())):
        raise ValueError('oracle_snapshot_invalid')
    nodes=value.get('nodes');binding=value.get('vectors');threshold=value.get('threshold')
    if (not isinstance(nodes,list)or not 1<=len(nodes)<=MAX_NODES or not isinstance(binding,dict)
            or binding.get('file')!='vectors.f32le' or binding.get('encoding')!='little-endian float32'
            or type(threshold)not in (int,float)or not math.isfinite(threshold)or not -1<=threshold<=1):
        raise ValueError('oracle_snapshot_invalid')
    raw=_bytes(Path(path).parent/'vectors.f32le',8*1024*1024)
    if binding.get('bytes')!=len(raw) or binding.get('sha256')!=hashlib.sha256(raw).hexdigest():
        raise ValueError('oracle_vector_binding_invalid')
    seen=set();vectors=[];offset=0;dimension=E5Profile().dimension
    for node in nodes:
        if (not isinstance(node,dict)or not _identifier(node.get('id')) or node['id']in seen
                or type(node.get('offset'))is not int or node['offset']!=offset
                or type(node.get('bytes'))is not int or node['bytes']!=dimension*4
                or not isinstance(node.get('projects'),list)or not 1<=len(node['projects'])<=16
                or any(not _identifier(p,128)for p in node['projects'])
                or len(set(node['projects']))!=len(node['projects'])):
            raise ValueError('oracle_node_invalid')
        block=raw[offset:offset+dimension*4];offset+=dimension*4;seen.add(node['id'])
        if hashlib.sha256(block).hexdigest()!=node.get('vector_sha256'):raise ValueError('oracle_vector_binding_invalid')
        vector=array('f');vector.frombytes(block)
        if sys.byteorder!='little':vector.byteswap()
        if len(vector)!=dimension or not all(math.isfinite(v)for v in vector):raise ValueError('oracle_vector_invalid')
        norm=math.sqrt(sum(v*v for v in vector));declared=node.get('norm')
        if (not math.isfinite(norm)or abs(norm-1)>.001 or type(declared)not in(int,float)
                or not math.isfinite(declared)or abs(norm-declared)>TOLERANCE):raise ValueError('oracle_vector_norm_invalid')
        vectors.append(vector)
    if offset!=len(raw):raise ValueError('oracle_vector_binding_invalid')
    return value,vectors,hashlib.sha256(data).hexdigest()

def evaluate_matrix(nodes,matrix,candidates,threshold):
    """Tie-aware numerical recall; matrix is computed from validated vectors."""
    n=len(nodes);k=3;ids={node['id']:i for i,node in enumerate(nodes)}
    if len(ids)!=n or not 1<=n<=MAX_NODES:
        raise ValueError('oracle_nodes_invalid')
    projects=[frozenset(node['projects'])for node in nodes]
    available=[];cutoffs=[];best=[];eligible_pairs=0
    for i in range(n):
        values=[]
        for j in range(n):
            if i!=j and not projects[i].isdisjoint(projects[j]):
                score=float(matrix[i][j])
                if not math.isfinite(score)or not -1<=score<=1:raise ValueError('oracle_matrix_invalid')
                eligible_pairs+=i<j
                if score>=threshold:values.append(score)
        top=heapq.nlargest(k,values);available.append(len(top));cutoffs.append(min(top)if top else math.inf)
        best.append(max(top)if top else None)
    if not isinstance(candidates,list)or len(candidates)>MAX_CANDIDATES:raise ValueError('oracle_candidates_invalid')
    degree=[0]*n;hits=[0]*n;found=[None]*n;seen=set();score_delta=0
    for row in candidates:
        if not isinstance(row,dict)or not {'source','target','cosine','projects'}<=row.keys():raise ValueError('oracle_candidate_invalid')
        a,b=row['source'],row['target'];score=row['cosine']
        if (not isinstance(a,str)or not isinstance(b,str)or a not in ids or b not in ids or a==b
                or type(score)not in(int,float)or not math.isfinite(score)or not -1<=score<=1):
            raise ValueError('oracle_candidate_invalid')
        i,j=ids[a],ids[b];key=min(i,j),max(i,j);common=sorted(projects[i]&projects[j])
        if key in seen or not common or row['projects']!=common:raise ValueError('oracle_candidate_scope_or_duplicate')
        seen.add(key);actual=float(matrix[i][j]);delta=abs(actual-score);score_delta=max(score_delta,delta)
        if delta>TOLERANCE or score<threshold:raise ValueError('oracle_candidate_score_invalid')
        for owner in (i,j):
            degree[owner]+=1;hits[owner]+=actual>=cutoffs[owner]-TOLERANCE
            found[owner]=actual if found[owner]is None else max(found[owner],actual)
    numerator=sum(min(hit,count)for hit,count in zip(hits,available));denominator=sum(available)
    regret=[max(0,best[i]-score)for i,score in enumerate(found)if score is not None and best[i]is not None]
    return {'pairs':len(candidates),'eligible_pairs':eligible_pairs,'incident_nodes':sum(d>0 for d in degree),
        'max_degree':max(degree,default=0),'min_degree':min(degree,default=0),
        'tie_aware_recall_at3':numerator/denominator if denominator else None,
        'recall_hits':numerator,'recall_denominator':denominator,
        'mean_best_cosine_regret_on_incident_nodes':sum(regret)/len(regret)if regret else None,
        'candidate_score_max_delta':score_delta,'tie_tolerance':TOLERANCE}

def evaluate(path,extra):
    if len(extra)>8:raise ValueError('oracle_candidate_declaration_invalid')
    value,vectors,snapshot_sha=load_snapshot(path)
    try:import numpy as np
    except ImportError:raise ValueError('oracle_numpy_backend_unavailable')from None
    x=np.asarray(vectors,dtype=np.float64);norms=np.array([n['norm']for n in value['nodes']])
    matrix=np.clip((x@x.T)/np.outer(norms,norms),-1,1)
    if not np.all(np.isfinite(matrix))or np.max(abs(matrix-matrix.T))>TOLERANCE:raise ValueError('oracle_matrix_invalid')
    alternatives={'baseline':value.get('baseline_candidates')}
    for declaration in extra:
        name,separator,file=declaration.partition('=')
        if not separator or re.fullmatch('[a-z][a-z0-9_]{0,47}',name)is None or name in alternatives:
            raise ValueError('oracle_candidate_declaration_invalid')
        alternatives[name]=_json(_bytes(Path(file),4*1024*1024))
    return {'schema':1,'scope':'offline numerical vector neighbors; not semantic gold, source authority or product performance',
        'backend':'numpy_float64','backend_version':np.__version__,
        'snapshot_sha256':snapshot_sha,'nodes':len(vectors),'dimension':E5Profile().dimension,
        'source_admission':'not_reperformed; exported snapshot provenance only',
        'results':{name:evaluate_matrix(value['nodes'],matrix,rows,value['threshold'])for name,rows in alternatives.items()}}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--backend',choices=('numpy',),required=True)
    parser.add_argument('--candidate',action='append',default=[],metavar='NAME=JSON_PATH')
    args=parser.parse_args()
    try:result=evaluate(args.snapshot,args.candidate)
    except (ValueError,KeyError,TypeError,OSError,RecursionError)as error:
        code=str(error)if isinstance(error,ValueError)and re.fullmatch('oracle_[a-z_]+',str(error))else'oracle_input_unavailable'
        print(json.dumps({'ok':False,'reason':code}));return 2
    print(json.dumps({'ok':True,**result},ensure_ascii=False,allow_nan=False));return 0

if __name__=='__main__':raise SystemExit(main())

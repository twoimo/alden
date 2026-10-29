#!/usr/bin/env python3
"""Isolated structural reference experiment; never opens product state/models.

CPM is selected only on dev. A frozen selection and script hash are required
for held-out generation seeds. These are synthetic partitions, not human gold.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from fractions import Fraction
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import statistics
import time

import igraph as ig
import leidenalg as la

GAMMAS = (.001, .002, .005, .01, .02, .05, .1)
CASES = {'clean-blocks', 'clean-plus-three-bridges', 'sparse-blocks',
         'weak-bridges', 'noisy-bridges', 'structural-hub'}
METHODS = ('current', 'components', 'leiden-global-modularity', 'leiden-component-modularity')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def choose(n):
    return n * (n-1) // 2

def adjusted_rand(a, b):
    cells=Counter(zip(a,b));rows=Counter(a);cols=Counter(b)
    left=sum(map(choose,rows.values()));right=sum(map(choose,cols.values()))
    actual=sum(map(choose,cells.values()))
    expected=Fraction(left*right,choose(len(a)))
    upper=Fraction(left+right,2)
    return 1.0 if upper==expected else float((actual-expected)/(upper-expected))

def evaluate(g, truth, labels):
    groups=defaultdict(list)
    for i,label in enumerate(labels):groups[label].append(i)
    disconnected=sum(len(g.induced_subgraph(group).connected_components())>1 for group in groups.values())
    truth_pairs=sum(map(choose,Counter(truth).values()))
    predicted_pairs=sum(map(choose,Counter(labels).values()))
    matched=sum(map(choose,Counter(zip(truth,labels)).values()))
    return {'ari':adjusted_rand(truth,labels),'communities':len(groups),
            'disconnected':disconnected,'singletons':sum(len(x)==1 for x in groups.values()),
            'pair_precision':matched/predicted_pairs if predicted_pairs else None,
            'pair_recall':matched/truth_pairs if truth_pairs else None}

def partition(g, method, gamma=None):
    if not g.ecount():return list(range(g.vcount()))
    if method=='components':return g.connected_components().membership
    if method=='leiden-component-modularity':
        result=[None]*g.vcount();offset=0
        for vertices in g.connected_components():
            sub=g.induced_subgraph(vertices)
            labels=list(range(len(vertices))) if not sub.ecount() else la.find_partition(sub,la.ModularityVertexPartition,weights='weight',seed=0,n_iterations=2).membership
            for i,label in zip(vertices,labels):result[i]=offset+label
            offset+=max(labels)+1
        return result
    kind=la.CPMVertexPartition if gamma is not None else la.ModularityVertexPartition
    extra={'resolution_parameter':gamma} if gamma is not None else {}
    return la.find_partition(g,kind,weights='weight',seed=0,n_iterations=2,**extra).membership

def load(path, phase):
    data=json.loads(Path(path).read_text());expected=set(range(1,6) if phase=='dev' else range(101,121))
    rows=data['results'];assert {(x['scenario'],x['seed']) for x in rows}=={(case,seed) for case in CASES for seed in expected}
    assert len(rows)==len(CASES)*len(expected)
    return rows

def run(rows, gammas):
    results=[]
    for row in rows:
        raw=row['referenceInput'];ids=raw['ids'];truth=raw['plantedMembership'];current=raw['currentMembership']
        assert len(ids)==96 and len(set(ids))==96 and len(truth)==len(current)==len(ids)
        index={id:i for i,id in enumerate(ids)};edges=raw['communityEdges']
        assert len(edges)<=4096 and all(math.isfinite(e['weight']) and e['weight']>0 and e['source']!=e['target'] for e in edges)
        endpoints=[(index[e['source']],index[e['target']]) for e in edges]
        assert len(set(tuple(sorted(e)) for e in endpoints))==len(endpoints)
        g=ig.Graph(n=len(ids),edges=endpoints,directed=False);g.es['weight']=[e['weight'] for e in edges]
        assert abs(adjusted_rand(truth,current)-row['adjustedRand'])<1e-12
        for method,gamma in [(m,None) for m in METHODS]+[('leiden-cpm',v) for v in gammas]:
            start=time.perf_counter()
            labels=current if method=='current' else partition(g,method,gamma)
            elapsed=(time.perf_counter()-start)*1000 if method!='current' else None
            result={'scenario':row['scenario'],'seed':row['seed'],'method':method,'gamma':gamma,
                    **evaluate(g,truth,labels),'partition_ms':elapsed,'membership':labels}
            # Same vertices/weights under a different deterministic index order.
            # Labels are mapped back before comparison; coordinates are excluded.
            if method=='current':result['index_permutation_ari']=row['relabelPartitionARI']
            else:
                order=sorted(range(len(ids)),key=lambda i:(i*37+row['seed']*11)%len(ids))
                new_index={old:new for new,old in enumerate(order)}
                other=ig.Graph(n=len(ids),edges=[(new_index[a],new_index[b]) for a,b in endpoints],directed=False)
                other.es['weight']=g.es['weight']
                other_labels=partition(other,method,gamma)
                mapped=[other_labels[new_index[i]] for i in range(len(ids))]
                result['index_permutation_ari']=adjusted_rand(labels,mapped)
            results.append(result)
    return results

def aggregate(rows):
    out=[]
    for key in sorted({(r['method'],r['gamma']) for r in rows},key=str):
        subset=[r for r in rows if (r['method'],r['gamma'])==key]
        out.append({'method':key[0],'gamma':key[1],'n':len(subset),
          'macro_ari':statistics.mean(r['ari'] for r in subset),
          'disconnected':sum(r['disconnected'] for r in subset),
          'median_index_permutation_ari':statistics.median(r['index_permutation_ari'] for r in subset),
          'by_scenario':{case:{'median_ari':statistics.median(r['ari'] for r in subset if r['scenario']==case),
            'median_communities':statistics.median(r['communities'] for r in subset if r['scenario']==case)} for case in sorted(CASES)}})
    return out

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['dev','heldout'],required=True)
    parser.add_argument('--input',type=Path,required=True);parser.add_argument('--selection',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();versions={name:importlib.metadata.version(name) for name in ['igraph','leidenalg']}
    assert adjusted_rand([0,0,1,1],['a','a','b','b'])==1 and adjusted_rand([0,0,1,1],['a','b','a','b'])==-.5
    rows=load(args.input,args.phase)
    if args.phase=='heldout':
        selection=json.loads(args.selection.read_text());assert selection['script_sha256']==sha(__file__) and selection['versions']==versions
        gammas=[selection['gamma']]
    else:
        assert not args.selection.exists(),'Never overwrite a frozen development selection'
        gammas=GAMMAS
    results=run(rows,gammas);summary=aggregate(results)
    if args.phase=='dev':
        candidates=[r for r in summary if r['method']=='leiden-cpm']
        winner=max(candidates,key=lambda r:(-r['disconnected'],r['macro_ari'],-r['gamma']))
        selection={'gamma':winner['gamma'],'selection_order':['fewest disconnected','macro ARI','smaller gamma'],
                   'input_sha256':sha(args.input),'script_sha256':sha(__file__),'versions':versions,'phase':'dev','n':len(rows)}
        args.selection.write_text(json.dumps(selection,indent=2)+'\n')
    report={'phase':args.phase,'input_sha256':sha(args.input),'script_sha256':sha(__file__),'versions':versions,
      'selection':selection,'scope':'Synthetic structural partitions only; no real-source semantic gold or installed algorithm change',
      'limits':['Same graph weights, but global/component/CPM objectives differ; compare explicitly',
        'Reference partition timings exclude product normalization/layout; no speedup comparison',
        'CPM chosen on development only; repeated generator scenarios are not external-domain generalization',
        'Fixed Leiden seed0 and n_iterations2 do not establish equal computational budget to native8passes'],
      'summary':summary,'results':results}
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'phase':args.phase,'selection':selection,'macro_ari':[{k:r[k] for k in ('method','gamma','macro_ari','disconnected')} for r in summary]}))

if __name__=='__main__':main()

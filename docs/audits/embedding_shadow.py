#!/usr/bin/env python3
"""Bounded synthetic shadow evaluation through installed Alden's warm E5 adapter.

Never starts a service or changes product state, models, settings or indices.
Prepare freezes inputs/methods before run. All producer writes use an audit-only
store, and its embed callback replays exact real responses (not fake vectors).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=Path('/Applications/Alden.app/Contents/Resources/scripts')
MODEL='mlx-community/multilingual-e5-small-mlx@5030c7625865046d350eeea28f427d80353d0ac0'
URL='http://127.0.0.1:11236/v1/embeddings'
METHODS=('pooled','max_chunk','top2_mean','half_pool_half_max')

def sha(raw):return hashlib.sha256(raw).hexdigest()
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
def windows(text):return [text[i:i+256] for i in range(0,len(text),192)]
def dot(a,b):return sum(x*y for x,y in zip(a,b))

def fixture():
    topics=[
      ('면접에서 자신의 역할과 성과를 어떻게 설명해야 하나?',
       '이 가상 기록의 면접 준비 항목은 본인의 프로젝트 역할과 성과를 구체적인 사례로 설명하고 지원 직무와 연결하는 연습이다.'),
      ('삭제된 문서의 이전 버전을 어떻게 복원하나?',
       '이 가상 기록의 파일 복원 항목은 삭제 전에 남긴 문서 버전과 원본 해시를 확인하고 보관한 사본으로 이전 내용을 되살리는 절차다.'),
      ('말하는 도중 새 요청이 들어오면 이전 음성을 어떻게 취소하나?',
       '이 가상 기록의 음성 취소 항목은 새 요청의 턴 식별자를 확인하고 이전 발화 재생을 멈춘 뒤 오래된 응답이 덮어쓰지 못하게 하는 절차다.'),
      ('지식 그래프에서 원문 근거를 어떻게 확인하나?',
       '이 가상 기록의 그래프 탐색 항목은 노드를 선택해 원문 출처와 저장 버전을 열고 관계의 근거 문장과 유효 시각을 확인하는 절차다.'),
    ]
    docs=[];cases=[];queries={}
    def add(body):
        id='shadow-'+str(len(docs)).zfill(3);docs.append({'id':id,'label':'실험 자료','body':body});return id
    for topic,(query,target) in enumerate(topics):
        qid='topic-'+str(topic);queries[qid]=query;background=topics[(topic+1)%len(topics)][1]
        for repeats in (2,10):
            blocks=[background]*repeats
            for position,at in [('start',0),('middle',repeats//2),('end',repeats)]:
                body=blocks[:at]+[target]+blocks[at:]
                positive=add('\n'.join(body));negative=add('\n'.join(blocks))
                cases.append({'id':f'topic-{topic}-{repeats}-{position}','kind':'topic','topic':topic,
                  'position':position,'background_repeats':repeats,'query':qid,'positive':positive,'negative':negative,
                  'oracle':'Target sentence intentionally present in positive and absent from negative; synthetic relevance only'})
    contrasts=[
      ('number','가상 상자 A의 물품 수는 다섯 개인가?','가상 상자 A의 물품 수는 5개다.','가상 상자 A의 물품 수는 50개다.'),
      ('unit','가상 시료 B의 질량은 5밀리그램인가?','가상 시료 B의 질량은 5 mg이다.','가상 시료 B의 질량은 5 g이다.'),
      ('date','가상 검토일은 2026년10월20일인가?','가상 검토일은 2026-10-20이다.','가상 검토일은 2026-10-21이다.'),
      ('polarity','가상 기능 C는 내보내기를 지원하는가?','가상 기능 C는 내보내기를 지원한다.','가상 기능 C는 내보내기를 지원하지 않는다.'),
      ('negation','가상 옵션 D는 기본으로 꺼져 있는가?','가상 옵션 D는 기본으로 꺼져 있다.','가상 옵션 D는 기본으로 켜져 있다.'),
      ('bound','가상 작업 E의 허용 개수는 최대 5개인가?','가상 작업 E의 허용 개수는 최대 5개다.','가상 작업 E의 허용 개수는 최소 5개다.'),
      ('identity','가상 인물 김연구는 파랑 팀 소속인가?','가상 인물 김연구는 파랑 팀 소속이다.','가상 인물 김연구는 초록 팀 소속이다.'),
      ('decimal','가상 규격 F의 길이는 0.5미터인가?','가상 규격 F의 길이는 0.5 m이다.','가상 규격 F의 길이는 5 m이다.'),
    ]
    for kind,query,positive,negative in contrasts:
        qid='contrast-'+kind;queries[qid]=query;p=add(positive);n=add(negative)
        cases.append({'id':qid,'kind':'contrast','slice':kind,'query':qid,'positive':p,'negative':n,
                      'oracle':'Explicit fictional value/polarity assertion, not independently annotated real-world truth'})
    return {'schema':1,'scope':'Fictional32paired probes/64documents; not human semantic gold or actual corpus','documents':docs,'queries':queries,'cases':cases}

def prepare(out):
    if (out/'shadow-fixture.json').exists():raise RuntimeError('Do not overwrite prepared inputs')
    data=fixture();dump(out/'shadow-fixture.json',data)
    unique={('passage',piece) for d in data['documents'] for piece in windows(d['label']+'\n'+d['body'])}
    unique.update(('query',q) for q in data['queries'].values())
    if len(unique)>512:raise RuntimeError('Request budget insufficient; no model calls made')
    dump(out/'shadow-protocol.json',{'fixture_sha256':sha((out/'shadow-fixture.json').read_bytes()),
      'script_sha256':sha(Path(__file__).read_bytes()),'model':MODEL,'endpoint':URL,'methods':METHODS,
      'top_k_chunks':2,'fusion_weight':.5,'max_embedding_requests':64,'max_unique_inputs':512,'wall_seconds':120,
      'actual_unique_inputs':len(unique),'selection':'No tuning/selection; all4methods reported',
      'source_policy':'Original label+body and char256/stride192, normalize each vector, weight by original piece length, normalize sum',
      'limits':['Synthetic preference probes only','Existing warm service; missing on-disk snapshot means no fresh weight provenance/restartability proof','No performance comparison or identity merge']})
    print(json.dumps({'prepared':True,'documents':len(data['documents']),'paired_probes':len(data['cases']),'unique_inputs':len(unique)}))

def listener():
    r=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:11236','-sTCP:LISTEN','-Fp'],capture_output=True,text=True)
    if r.returncode:raise RuntimeError('Existing E5 listener unavailable; never start/restart it')
    return sorted({int(line[1:]) for line in r.stdout.splitlines() if line.startswith('p')})

def require_warm_status(status, pids):
    if status.get('launchd')!='loaded' or pids!=[status.get('pid')] or status.get('readiness',{}).get('model')!=MODEL or status.get('readiness',{}).get('state')!='ready':
        raise RuntimeError('Managed warm-service identity/readiness not observed')
    if status.get('ok') is not True:
        issues=status.get('issues')
        if not isinstance(issues,list) or len(issues)!=1 or not isinstance(issues[0],str) or not issues[0].startswith('pinned model snapshot is missing: '):
            raise RuntimeError('Only the explicitly observed missing-snapshot warm case is permitted; other manager failures stop the audit')

def run(out):
    if (out/'shadow-results.json').exists():raise RuntimeError('Do not repeat completed inference; use saved outputs')
    protocol=json.loads((out/'shadow-protocol.json').read_text());raw=(out/'shadow-fixture.json').read_bytes()
    if sha(raw)!=protocol['fixture_sha256'] or sha(Path(__file__).read_bytes())!=protocol['script_sha256']:
        raise RuntimeError('Prepared inputs/script changed')
    status=json.loads((out/'manager-status.json').read_text());pid=status.get('pid')
    require_warm_status(status,listener())
    sys.path.insert(0,str(SCRIPTS))
    import auto_reply_knowledge_graph as kg
    import alden_collection_retrieval as collection
    from alden_collection import CollectionStore,identity
    if kg.DENSE_EMBEDDING_URL!=URL:raise RuntimeError('Do not substitute embedding endpoint')
    data=json.loads(raw);cache={};requests=[];started=time.monotonic();budget=kg._DenseCycleBudget()
    budget.deadline=started+protocol['wall_seconds']
    state=out/'isolated-store'
    groups=[('query',list(data['queries'].values())),('passage',[p for d in data['documents'] for p in windows(d['label']+'\n'+d['body'])])]
    vector_file=out/'shadow-vectors.json';cache_replayed=vector_file.exists()
    if cache_replayed:
        saved_raw=vector_file.read_bytes();saved=json.loads(saved_raw)
        if saved.get('model')!=MODEL or saved.get('endpoint')!=URL or saved.get('pid')!=pid:
            raise RuntimeError('Existing actual response cache belongs to another model/service')
        if protocol.get('resume_cache_sha256')!=sha(saved_raw):raise RuntimeError('Resume needs the exact preserved actual-response receipt')
        observed={(r['role'],r['text_sha256']):r['vector'] for r in saved['vectors']}
        expected={(role,sha(text.encode())):text for role,texts in groups for text in texts}
        if set(observed)!=set(expected):raise RuntimeError('Cached inputs differ from frozen inputs')
        for (role,key),text in expected.items():
            vector=observed[(role,key)]
            if len(vector)!=384 or any(not math.isfinite(x) for x in vector):raise RuntimeError('Cached actual vector invalid')
            cache[(role,text)]=tuple(vector)
        requests=saved['requests']
    else:
        for role,texts in groups:
            unique=list(dict.fromkeys(texts))
            for offset in range(0,len(unique),8):
                if listener()!=[pid] or time.monotonic()>budget.deadline:raise RuntimeError('Warm service changed or budget elapsed')
                batch=unique[offset:offset+8]
                values=kg._local_dense_embeddings(batch,input_type=role,model_id=MODEL,timeout_seconds=15,_budget=budget,state_root=state)
                if any(len(v)!=384 or any(not math.isfinite(x) for x in v) for v in values):raise RuntimeError('Actual embedding shape invalid')
                for text,value in zip(batch,values):cache[(role,text)]=value
                requests.append({'role':role,'inputs':[sha(t.encode()) for t in batch],'vectors':len(values)})
        dump(vector_file,{'model':MODEL,'endpoint':URL,'pid':pid,'requests':requests,
          'vectors':[{'role':role,'text_sha256':sha(text.encode()),'vector':v} for (role,text),v in cache.items()]})
    pool={};chunks={};texts={}
    for doc in data['documents']:
        id=doc['id'];text=doc['label']+'\n'+doc['body'];pieces=windows(text);total=[0.]*384;parts=[]
        for piece in pieces:
            vector=kg._normalize_dense_vector(list(cache[('passage',piece)]));parts.append(vector)
            for i,x in enumerate(vector):total[i]+=len(piece)*x
        pool[id]=kg._normalize_dense_vector(total);chunks[id]=parts;texts[id]=text
    # Installed product producer, isolated synthetic store, exact real cached responses.
    store=CollectionStore(state);target=store.register(platform='graph',original_id='embedding-shadow',kind='source',
       label='Isolated synthetic embedding audit',projects=['shadow'],enabled=True)
    store.ingest(target,[{'platform':'graph','original_id':d['id'],'label':d['label'],'text':d['body']} for d in data['documents']])
    used=[]
    def replay(pieces):
        used.extend(sha(p.encode()) for p in pieces)
        return [cache[('passage',p)] for p in pieces]
    if kg._active_dense_embedding_model()!=MODEL:raise RuntimeError('Producer model changed; stop')
    producer=collection.index_dense(state,['shadow'],embed=replay)
    with sqlite3.connect(collection._path(store)) as db:
        rows={r[0]:r[1] for r in db.execute('SELECT document_id,vector FROM vectors')}
    if len(rows)!=len(data['documents']):raise RuntimeError('Isolated producer document count mismatch')
    for doc in data['documents']:
        expected=struct.pack('<384f',*pool[doc['id']])
        if rows[identity('graph',doc['id'])]!=expected:raise RuntimeError('Actual producer pooling differs from shadow baseline')
    results=[]
    for case in data['cases']:
        q=cache[('query',data['queries'][case['query']])];scores={}
        for id in (case['positive'],case['negative']):
            ds=sorted((dot(q,v) for v in chunks[id]),reverse=True);centroid=dot(q,pool[id]);maximum=ds[0]
            scores[id]={'pooled':centroid,'max_chunk':maximum,'top2_mean':sum(ds[:2])/len(ds[:2]),'half_pool_half_max':.5*centroid+.5*maximum}
        margins={method:scores[case['positive']][method]-scores[case['negative']][method] for method in METHODS}
        results.append({**case,'scores':scores,'margins':margins,'preference':{m:'positive' if v>1e-9 else 'negative' if v< -1e-9 else 'tie' for m,v in margins.items()}})
    summary={kind:{m:{choice:sum(r['preference'][m]==choice for r in results if r['kind']==kind) for choice in ['positive','negative','tie']} for m in METHODS} for kind in ['topic','contrast']}
    if listener()!=[pid]:raise RuntimeError('Service identity changed before final receipt')
    store.register(platform='graph',original_id='embedding-shadow',kind='source',
       label='Isolated synthetic embedding audit',projects=['shadow'],enabled=False)
    dump(out/'shadow-results.json',{'scope':data['scope'],'model':MODEL,'endpoint':URL,'pid_unchanged':pid,
      'model_assets_revalidated':False,'warm_service_functional':True,'source_code_hashes':{name:sha((SCRIPTS/name).read_bytes()) for name in ['auto_reply_knowledge_graph.py','alden_collection_retrieval.py','alden_collection.py']},
      'protocol':protocol,'requests':len(requests),'new_requests_this_run':budget.requests,'actual_cache_replayed':cache_replayed,
      'unique_real_embeddings':len(cache),'elapsed_seconds':time.monotonic()-started,'isolated_target_paused_after_audit':True,
      'producer':producer,'producer_input_windows':len(used),'producer_float32_vectors_exact_match':len(rows),
      'producer_scope':'Actual installed producer and SQLite in isolated synthetic store; embed callback replays exact real E5 responses, not transport retry/live producer inference',
      'summary':summary,'results':results,'limits':protocol['limits']})
    print(json.dumps({'requests':len(requests),'unique_real_embeddings':len(cache),'producer_vectors_exact':len(rows),'summary':summary},ensure_ascii=False))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--phase',choices=['prepare','run'],required=True);p.add_argument('--output-dir',type=Path,required=True)
    args=p.parse_args();out=args.output_dir.resolve()
    if not out.is_relative_to(ROOT/'outputs'):raise RuntimeError('Audit output must stay in repository outputs')
    out.mkdir(parents=True,exist_ok=True)
    (prepare if args.phase=='prepare' else run)(out)

if __name__=='__main__':main()

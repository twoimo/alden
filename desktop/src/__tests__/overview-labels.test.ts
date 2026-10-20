// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { KnowledgeHologram } from '../knowledge/hologram';
import { communityCaptionNodes, internalGraphLabel, selectOverviewLabels } from '../knowledge/overview-labels';

const graph = (nodes: Array<Record<string, unknown>>) => parseKnowledgeGraph({nodes,edges:[]});
describe('overview reading entry labels', () => {
  it.each(['topic', 'source_claim'])('reserves overview slots for another eligible %s source before repeating a source', category => {
    const nodes = graph([
      ...Array.from({ length: 9 }, (_, i) => ({ id: `topic-${i}`, label: `저장된 주제 ${i}`, category: 'topic', degree: 100 - i, source_target: 'dominant' })),
      { id: 'other', label: '다른 출처의 검증된 기록', category, source_target: 'other' },
    ]).nodes;
    const before = JSON.stringify(nodes), selected = selectOverviewLabels(nodes);
    expect(selected).toHaveLength(8);
    expect(selected.map(node => node.id)).toEqual(['topic-0', 'topic-1', 'topic-2', 'topic-3', 'topic-4', 'topic-5', 'topic-6', 'other']);
    expect(selectOverviewLabels([...nodes].reverse())).toEqual(selected);
    expect(selectOverviewLabels(nodes, 1).map(node => node.id)).toEqual(['topic-0']);
    expect(selectOverviewLabels(nodes, 2).map(node => node.id)).toEqual(['topic-0', 'other']);
    expect(selected.every(node => nodes.includes(node))).toBe(true);
    expect(JSON.stringify(nodes)).toBe(before);
    const shell = document.createElement('div'), canvas = document.createElement('canvas'), layer = document.createElement('div');
    layer.className = 'knowledge-node-labels'; shell.append(canvas, layer); document.body.append(shell);
    const state = { canvas, view: { nodes, edges: [], focusId: null, hops: 0 }, hoveredId: null,
      globalLabels: new Set(selected.map(node => node.id)), globalLabelRanks: new Map(selected.map((node, index) => [node.id, index])),
      labels: new Map<string, HTMLSpanElement>(), labelBounds: [] };
    const hologram = Object.assign(Object.create(KnowledgeHologram.prototype), state) as typeof state & { refreshOverviewLabels(): void };
    try {
      hologram.refreshOverviewLabels();
      expect(hologram.labels.size).toBe(8);
      expect(hologram.labels.get('other')?.textContent).toBe(nodes.find(node => node.id === 'other')!.label);
    } finally { shell.remove(); }
  });

  it('keeps link references navigable while prioritizing substantive labels', () => {
    const nodes=graph([
      {id:'reference',label:'채용 절차 안내 링크',category:'document',degree:9999,source_metadata:{content_evidence:'reference_only'}},
      {id:'body',label:'채용 절차의 실제 평가 기준',category:'document',degree:1,source_metadata:{content_evidence:'content_candidate'}},
    ]).nodes;
    expect(nodes).toHaveLength(2);expect(nodes[0].contentEvidence).toBe('reference_only');
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(['body']);
  });
  it('names a large unlabeled community with one short saved title', () => {
    const nodes = graph([
      ...Array.from({length: 8}, (_, i) => ({id: 'v' + i, label: i === 0 ? '생산·제조 기록' : '생산 현장의 아주 긴 작업 기록 입니다 ' + i, category: 'document', degree: 8 - i, source_metadata: {content_evidence: 'content_candidate'}})),
      {id: 'named', label: '자소서·면접', category: 'topic', degree: 4, source_metadata: {content_evidence: 'content_candidate'}},
    ]).nodes;
    const rows = nodes.map(node => [node.id, node.id === 'named' ? 'c:aaa:bbb' : 'c:ccc:ddd'] as [string, string]);
    expect(communityCaptionNodes(nodes, rows, new Set(['named'])).map(node => node.label)).toEqual(['생산·제조 기록']);
    expect(communityCaptionNodes(nodes, rows, new Set(['v0']))).toEqual([]);
    expect(communityCaptionNodes(nodes, undefined, new Set())).toEqual([]);
  });
  it('shows saved subjects rather than high-degree authors and broad buckets', () => {
    const input=graph([
      {id:'a',label:'렉카박멸',category:'source_group',degree:9999,source_target:'one'},
      {id:'b',label:'홍민태',category:'author',degree:9999,source_target:'two'},
      {id:'c',label:'일반 동영상',category:'source_group',degree:9999},
      {id:'d',label:'기타·미분류',category:'topic',degree:9999},
      {id:'e',label:'연봉·복리후생',category:'topic',degree:30,source_target:'one'},
      {id:'f',label:'이력서·서류',category:'topic',degree:20,source_target:'one'},
      {id:'g',label:'면접 질문을 준비하는 방법',category:'video',source_target:'two'},
      {id:'h',label:'snippets',category:'hub',degree:9999},
      {id:'i',label:'돈 벌어주는 스파미 일반 영상 133개',category:'index',degree:9999},
    ]);
    const before=JSON.stringify(input);
    expect(selectOverviewLabels(input.nodes).map(n=>n.label)).toEqual(['연봉·복리후생','이력서·서류','면접 질문을 준비하는 방법']);
    expect(JSON.stringify(input)).toBe(before);expect(input.nodes).toHaveLength(9);
  });
  it.each(['topic', 'claim', 'source_claim'])('excludes high-degree ambiguous %s labels when multi-term titles exist', category => {
    const nodes=graph([
      {id:'slogan',label:'렉카박멸',category,degree:99999,source_target:'channel'},
      {id:'person',label:'홍민태',category,degree:99998,source_target:'person'},
      {id:'channel',label:'홍민태 채널',category,degree:99997,source_target:'channel'},
      {id:'tv',label:'렉카박멸 TV',category,degree:99996,source_target:'channel'},
      {id:'media',label:'일반 동영상',category,degree:99995},
      {id:'snippets',label:'snippets',category,degree:99994},
      {id:'media-count',label:'돈 벌어주는 스파미 일반 영상 133개',category,degree:99993,source_target:'media'},
      {id:'snippets-count',label:'검색 snippets 133',category,degree:99992,source_target:'snippets'},
      {id:'production',label:'생산·제조',category,degree:2,source_target:'project'},
      {id:'career',label:'자소서·면접',category,degree:1,source_target:'project'},
    ]).nodes;
    const before=JSON.stringify(nodes);
    const selected=selectOverviewLabels(nodes,2);
    expect(selected.map(n=>n.label)).toEqual(['생산·제조','자소서·면접']);
    expect(selectOverviewLabels(nodes)).toEqual(selected);
    expect(selectOverviewLabels([...nodes].reverse())).toEqual(selected);
    expect(selected[0]).toBe(nodes[8]);expect(selected[1]).toBe(nodes[9]);
    expect(JSON.stringify(nodes)).toBe(before);expect(nodes).toHaveLength(10);
  });
  it('keeps spare slots empty instead of adding ambiguous topics or buckets for source coverage', () => {
    const nodes=graph([
      {id:'person',label:'홍민태',category:'topic',degree:9999,source_target:'person'},
      {id:'channel',label:'홍민태 채널',category:'source_claim',degree:9999,source_target:'channel'},
      {id:'media',label:'일반 동영상 133개',category:'document',degree:9999,source_target:'media'},
      {id:'topic',label:'생산·제조',category:'topic',degree:2,source_target:'project'},
      {id:'claim',label:'제조 공정의 오류를 줄이는 조건',category:'source_claim',degree:1,source_target:'project'},
    ]).nodes;
    expect(selectOverviewLabels(nodes,2).map(n=>n.id)).toEqual(['topic','claim']);
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(['topic','claim']);
  });
  it.each(['topic', 'claim', 'source_claim'])('leaves overview slots empty when only ambiguous or generic %s labels are saved', category => {
    const nodes=graph([
      {id:'slogan',label:'렉카박멸',category,degree:99999},
      {id:'person',label:'홍민태',category,degree:99998},
      {id:'subject',label:'홍길동',category,degree:1},
      {id:'generic',label:'기타·미분류',category,degree:999999},
      {id:'channel',label:'홍민태 채널',category,degree:999997},
      {id:'tv',label:'렉카박멸 TV',category,degree:999996},
      {id:'media',label:'일반 동영상',category,degree:999995},
      {id:'snippets',label:'snippets',category,degree:999994},
      {id:'media-count',label:'돈 벌어주는 스파미 일반 영상 133개',category,degree:999993},
      {id:'snippets-count',label:'검색 snippets 133',category,degree:999992},
      {id:'unread',label:'읽지 않은 문서 제목',category,source_metadata:{content_evidence:'reference_only'}},
    ]).nodes;
    const before=JSON.stringify(nodes);
    const selected=selectOverviewLabels(nodes);
    expect(selected).toEqual([]);
    expect(selectOverviewLabels([...nodes].reverse())).toEqual(selected);
    expect(selectOverviewLabels(nodes,2)).toEqual([]);
    expect(JSON.stringify(nodes)).toBe(before);expect(nodes).toHaveLength(11);
  });
  it('keeps substantive titles about channels and media in the preferred group', () => {
    const nodes=graph([
      {id:'fallback',label:'렉카박멸',category:'topic',degree:9999},
      {id:'channel',label:'채널 운영 전략',category:'topic',degree:3},
      {id:'video',label:'일반 동영상 분류 기준',category:'topic',degree:2},
      {id:'snippets',label:'Snippets improve retrieval precision',category:'source_claim',degree:1},
    ]).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(['channel','video','snippets']);
  });
  it('ranks connected subjects first while reserving a small cap for distinct sources', () => {
    const nodes=graph([
      {id:'low',label:'경력·이직',category:'topic',degree:10,source_target:'career'},
      {id:'high',label:'채용공고·지원시기',category:'topic',degree:3000,source_target:'career'},
      {id:'bucket',label:'홍보·안내',category:'topic',degree:9000,source_target:'career'},
      {id:'link',label:'외부 자료·링크',category:'topic',degree:8000,source_target:'career'},
      {id:'claim',label:'115,700원은 편집자막 금액',category:'source_claim',degree:1,source_target:'clips'},
      {id:'title',label:'다 먹으면 평생 공짜기 사장님이 남긴 말',category:'video',degree:50,source_target:'channel'},
    ]).nodes;
    expect(selectOverviewLabels(nodes,2).map(n=>n.id)).toEqual(['high','claim']);
    expect(selectOverviewLabels(nodes).map(n=>n.id).slice(0,2)).toEqual(['high','low']);
    expect(nodes).toHaveLength(6);
  });
  it('does not spend an overview slot on an internal node id', () => {
    const nodes=graph([
      {id:'topic',label:'자소서·면접',category:'topic',degree:10,source_target:'career'},
      {id:'raw',label:'career-public-graph:node:source',category:'document',degree:9999,source_target:'files'},
    ]).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(['topic']);
    expect(internalGraphLabel('career-public-graph:node:source')).toBe(true);
    expect(internalGraphLabel('graph:000f30c5b24a8f33fe528ad55586d77a3f0f53e32a1ded9d1b6bfb7baec5b349')).toBe(true);
    expect(internalGraphLabel('자소서')).toBe(false);
  });
  it('deduplicates display text without merging nodes, and preserves project coverage', () => {
    const nodes=graph([
      {id:'a',label:'서류 준비',category:'topic',source_target:'a'},
      {id:'b',label:'서류   준비',category:'topic',source_target:'b'},
      {id:'c',label:'채용 일정',category:'topic',source_target:'b'},
      {id:'d',label:'수집 성능을 점검한 실제 자료',category:'document',source_target:'c'},
    ]).nodes;
    const result=selectOverviewLabels(nodes,3);
    expect(result.map(n=>n.sourceTarget)).toEqual(['a','b','c']); expect(nodes).toHaveLength(4);
  });
  it('deduplicates canonical Unicode and whitespace while keeping the chosen saved node and provenance', () => {
    const input=graph([
      {id:'saved',label:'  면접\t준비  ',category:'topic',degree:30,source_target:'career',source_version:'version-a',
        source_platform:'files',source_url:'https://example.test/career',osk_id:'osk-a',canonical_id:'canonical-a',
        description:'저장된 면접 준비 자료',facts:['원문에 기록한 조건'],
        evidence:{kind:'ledger',source_event_ids:['event-a'],chat_id:'room-a',confirmed_at:'2026-10-18T00:00:00Z'}},
      {id:'duplicate',label:'면접 준비'.normalize('NFD'),category:'topic',degree:20,source_target:'other',source_version:'version-b',
        evidence:{kind:'snapshot',source_event_ids:['event-b'],chat_id:'room-b'}},
      {id:'other',label:'채용 일정',category:'topic',degree:10,source_target:'other'},
    ]);
    const before=JSON.stringify(input);
    const selected=selectOverviewLabels(input.nodes);
    expect(selected.map(node=>node.id)).toEqual(['saved','other']);
    expect(selectOverviewLabels([...input.nodes].reverse())).toEqual(selected);
    expect(selected[0]).toBe(input.nodes[0]);
    expect(selected[0].label).toBe('  면접\t준비  ');
    expect(selected[0].evidence).toBe(input.nodes[0].evidence);
    expect(selected[0]).toMatchObject({sourceTarget:'career',sourceVersion:'version-a',sourcePlatform:'files',
      sourceUrl:'https://example.test/career',oskId:'osk-a',canonicalId:'canonical-a'});
    expect(input.nodes[1].sourceTarget).toBe('other');
    expect(input.nodes[1].evidence.sourceEventIds).toEqual(['event-b']);
    expect(JSON.stringify(input)).toBe(before);expect(input.nodes).toHaveLength(3);
  });
  it('keeps case, punctuation, numbers and compatibility characters distinct in display text', () => {
    const labels=['US 채용','us 채용','Rust: 설치','Rust 설치','채용 일정 2026','채용 일정 2027','GPU ① 구성','GPU 1 구성'];
    const nodes=graph(labels.map((label,index)=>({id:String(index),label,category:'topic',degree:labels.length-index}))).nodes;
    expect(selectOverviewLabels(nodes).map(node=>node.label)).toEqual(labels);
    expect(selectOverviewLabels([...nodes].reverse()).map(node=>node.label)).toEqual(labels);
  });
  it.each(['slogan','person','duplicate'])('keeps suppressed %s labels reachable through the production hover and focus label path', id => {
    const input=parseKnowledgeGraph({nodes:[
      {id:'slogan',label:'렉카박멸',category:'topic',degree:9999,source_target:'channel'},
      {id:'person',label:'홍민태',category:'source_claim',degree:9998,source_target:'person'},
      {id:'subject',label:'생산·제조',category:'topic',degree:2,source_target:'project'},
      {id:'duplicate',label:'생산·제조',category:'topic',degree:1,source_target:'another-project',source_version:'saved-version',
        evidence:{kind:'ledger',source_event_ids:['saved-event']}},
    ],edges:[
      {source:'slogan',target:'subject',relation:'관련 자료'},
      {source:'person',target:'subject',relation:'관련 자료'},
      {source:'duplicate',target:'subject',relation:'관련 자료'},
    ]});
    const before=JSON.stringify(input);
    const selected=selectOverviewLabels(input.nodes);
    expect(selected.map(node=>node.id)).toEqual(['subject']);
    const saved=input.nodes.find(node=>node.id===id)!;
    expect(internalGraphLabel(saved.label)).toBe(false);
    const shell=document.createElement('div'),canvas=document.createElement('canvas'),layer=document.createElement('div');
    layer.className='knowledge-node-labels';shell.append(canvas,layer);document.body.append(shell);
    const state={canvas,view:{...input,hops:0,focusId:null as string|null},hoveredId:null as string|null,
      globalLabels:new Set(selected.map(node=>node.id)),globalLabelRanks:new Map(selected.map((node,index)=>[node.id,index])),
      labels:new Map<string,HTMLSpanElement>(),labelBounds:[]};
    const hologram=Object.assign(Object.create(KnowledgeHologram.prototype),state) as typeof state & {refreshOverviewLabels():void};
    try {
      hologram.refreshOverviewLabels();
      expect([...hologram.labels.keys()]).toEqual(['subject']);
      hologram.hoveredId=id;hologram.refreshOverviewLabels();
      expect(hologram.labels.get(id)?.textContent).toBe(saved.label);
      expect(hologram.labels.get(id)?.title).toBe(saved.label);
      hologram.hoveredId=null;hologram.refreshOverviewLabels();
      expect(hologram.labels.has(id)).toBe(false);
      hologram.view.focusId=id;hologram.refreshOverviewLabels();
      expect(hologram.labels.get(id)?.textContent).toBe(saved.label);
      expect(hologram.labels.get(id)?.dataset.focused).toBe('true');
      expect(hologram.view.nodes.find(node=>node.id===id)).toBe(saved);
      hologram.view.focusId=null;hologram.refreshOverviewLabels();
      expect([...hologram.labels.keys()]).toEqual(['subject']);
      expect(JSON.stringify(input)).toBe(before);expect(input.nodes).toHaveLength(4);expect(input.edges).toHaveLength(3);
    } finally { shell.remove(); }
  });
  it('keeps declared unread metadata out of representatives without fabricating content', () => {
    const nodes=graph([
      {id:'a',label:'돈 벌어주는 스파미 일반 영상 133개',category:'document',source_metadata:{content_evidence:'metadata_description'}},
      {id:'b',label:'채용 자료 파일 모음',category:'document',source_metadata:{content_evidence:'unread_body_marker'}},
      {id:'c',label:'서류 준비의 실제 조건',category:'source_claim',source_metadata:{content_evidence:'content_candidate'}},
      {id:'d',label:'형식이 아직 알려지지 않은 자료',category:'document',source_metadata:{content_evidence:'unexpected'}},
    ]).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(['c','d']);expect(nodes).toHaveLength(4);
    expect(nodes[0].contentEvidence).toBe('metadata_description');expect(nodes[3].contentEvidence).toBeUndefined();
  });
  it('retains concise saved career subjects without admitting single-term names', () => {
    const nodes=graph([{id:'a',label:'면접',category:'topic'},{id:'b',label:'미확인 조건에서 발생할 수 있는 실패',category:'source_claim'}]).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.label)).toEqual(['면접','미확인 조건에서 발생할 수 있는 실패']);
    expect(selectOverviewLabels(nodes.slice(0,1)).map(n=>n.label)).toEqual(['면접']);
    const subjects=graph([{id:'resume',label:'자소서',category:'topic'},
      {id:'name',label:'홍민태',category:'topic'},{id:'slogan',label:'렉카박멸',category:'topic'},
      {id:'author',label:'면접',category:'author'}]).nodes;
    expect(selectOverviewLabels(subjects).map(n=>n.id)).toEqual(['resume']);
  });
  it('ignores retracted and identifier labels while retaining all stored targets', () => {
    const nodes=graph([{id:'a',label:'source:author:123',category:'source'},
      {id:'b',label:'1'.repeat(64),category:'source'}, {id:'c',label:'@name',category:'post'},
      {id:'d',label:'README.md',category:'document'}, {id:'e',label:'검증한 검색 과정',category:'document'}]).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(['e']); expect(nodes).toHaveLength(5);
  });
  it('is deterministic under input reorder and bounds unknown or excessive caps', () => {
    const nodes=graph(Array.from({length:20},(_,i)=>({id:String(i),label:'면접 준비 '+i,category:'topic',source_target:'one'}))).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.id)).toEqual(selectOverviewLabels([...nodes].reverse()).map(n=>n.id));
    expect(selectOverviewLabels(nodes,999)).toHaveLength(8);expect(selectOverviewLabels(nodes,NaN)).toEqual([]);
  });
});

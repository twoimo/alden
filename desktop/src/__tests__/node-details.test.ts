// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { renderNodeDetails, nodeSummary } from '../knowledge/node-details';
const account='1'.repeat(64),other='2'.repeat(64);
const id=`person:kakao:${account}:actor:7`;
const graph=parseKnowledgeGraph({nodes:[{id,label:'같은 이름',category:'대화 상대',description:'기록에 남은 대화 상대'}],edges:[]});
beforeEach(()=>{document.body.innerHTML='<p id="knowledge-node-summary"></p><div id="knowledge-node-body" hidden></div><p id="knowledge-node-kind"></p><p id="knowledge-node-basis"></p><ul id="knowledge-node-facts"></ul><h2 id="knowledge-evidence-heading"></h2><div id="knowledge-node-evidence"></div>';});
const source={source_id:`kakao:${account}:room:42:log:1`,source_kind:'local_db_snapshot',author_id:'7',room_title:'회의',sender:'같은 이름',date:'2026-10-01T15:00:00+09:00',content:'금요일 오후 3시에 회의합니다.',source_role:'peer_history'};
describe('selected-node explanations',()=>{
  it('keeps a separated numeric tail collapsed, inert and tied to the selected source version',()=>{
    const notes=parseKnowledgeGraph({nodes:[{id:'post',label:'게시물',category:'source',source_version:'v1',source_target:'target-a'}],edges:[]});
    const tail='\n\u00a0\n1\n/\n2\n6\n1\n3';
    const details={node_id:'post',basis:'source_record',version:'v1',target_id:'target-a',body:'1. 정상 항목\n5. 마지막 항목',raw_sha256:'1'.repeat(64),
      text_projection:{policy:'threads-separated-numeric-tail-v1',status:'structural_tail_candidate',original_preserved:true,
        semantic_validity:'unverified',separated_tail:tail,removed_characters:tail.length,
        original_body_sha256:'2'.repeat(64),body_sha256:'3'.repeat(64),separated_tail_sha256:'4'.repeat(64)}};
    renderNodeDetails(notes,notes.nodes[0],{details});
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(details.body);
    const disclosure=document.querySelector<HTMLDetailsElement>('.knowledge-separated-tail')!;
    expect(disclosure.open).toBe(false); expect(disclosure.querySelector('pre')!.textContent).toBe(tail);
    expect(disclosure.parentElement?.id).toBe('knowledge-node-evidence');
    expect(document.getElementById('knowledge-node-body')!.nextElementSibling?.className).not.toBe('knowledge-separated-tail');
    disclosure.open=true;
    renderNodeDetails(notes,notes.nodes[0],{details:{...details,version:'old'}});
    expect(document.querySelector('.knowledge-separated-tail')).toBeNull();
    renderNodeDetails(notes,notes.nodes[0],{details:{...details,text_projection:{...details.text_projection,separated_tail:'<img src=x>',removed_characters:11}}});
    expect(document.querySelector('img')).toBeNull();
    renderNodeDetails(notes,notes.nodes[0],{details:{...details,text_projection:{...details.text_projection,removed_characters:999}}});
    expect(document.querySelector('.knowledge-separated-tail')).toBeNull();
  });
  it('preserves unclassified numeric source content and facts without a projection receipt', () => {
    const notes = parseKnowledgeGraph({ nodes: [{ id: 'post', label: '서류 특징', category: 'document', source_version: 'v1', source_target: 'target-a' }], edges: [] });
    const saved = '서류에서 높은 점수를 받는 지원자 특징.\n1. 채용공고의 JD를 그대로 옮겨 쓰지 않는다.\n1\n2\n6\n1\n3';
    renderNodeDetails(notes, notes.nodes[0], { details: { node_id: 'post', basis: 'source_record', version: 'v1', target_id: 'target-a', body: saved, raw_sha256: 'a'.repeat(64), key_facts: ['핵심 문장', '1'] } });
    const shown = document.getElementById('knowledge-node-body')!.textContent || '';
    expect(shown).toContain('1. 채용공고의 JD를 그대로 옮겨 쓰지 않는다.');
    expect(shown).toBe(saved);
    expect([...document.getElementById('knowledge-node-facts')!.children].map(item => item.textContent)).toEqual(['핵심 문장','1']);
    expect(saved.endsWith('3')).toBe(true);
  });
  it('reads saved body with stable node links and keeps hostile markup inert',()=>{
    const notes=parseKnowledgeGraph({nodes:[{id:'osk:261005-0001-abcdefgh',label:'설계 가설',category:'memory'},
      {id:'person:actor',osk_id:'261005-0002-abcdefgh',label:'전제',category:'memory'}],edges:[]});
    const body='결론과 한계\n\n[[261005-0002-abcdefgh|확인한 전제]]\n[논문](https://arxiv.org/abs/2608.11994)\n<script>unsafe()</script> [실행](javascript:unsafe())';
    renderNodeDetails(notes,notes.nodes[0],{details:{node_id:notes.nodes[0].id,basis:'note',summary:'요약',body}});
    expect(document.getElementById('knowledge-node-body')!.hidden).toBe(false);
    expect(document.getElementById('knowledge-node-summary')!.hidden).toBe(true);
    expect(document.querySelector<HTMLButtonElement>('button.knowledge-note-link')!.dataset.knowledgeNode).toBe('person:actor');
    expect(document.querySelector<HTMLAnchorElement>('#knowledge-node-body a')!.href).toBe('https://arxiv.org/abs/2608.11994');
    expect(document.querySelector('script,img')).toBeNull();
    expect(document.querySelectorAll('#knowledge-node-body a')).toHaveLength(1);
    expect(document.getElementById('knowledge-node-body')!.textContent).toContain('javascript:unsafe()');
    renderNodeDetails(notes,notes.nodes[0],{details:{node_id:'another',basis:'note',body:'이전 선택 결과'}});
    expect(document.getElementById('knowledge-node-body')!.hidden).toBe(true);
    expect(document.getElementById('knowledge-node-summary')!.hidden).toBe(false);
  });
  it('does not choose one of equal labels as a body dependency',()=>{
    const notes=parseKnowledgeGraph({nodes:[{id:'a',label:'가설',category:'memory'},{id:'b',label:'같은 이름',category:'memory'},{id:'c',label:'같은 이름',category:'memory'}],edges:[]});
    renderNodeDetails(notes,notes.nodes[0],{details:{node_id:'a',basis:'note',body:'[[같은 이름]]'}});
    expect(document.querySelector('button.knowledge-note-link')).toBeNull();
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe('[[같은 이름]]');
  });
  it('requires a stable ID for collected source body links even when an institution label is unique',()=>{
    const notes=parseKnowledgeGraph({nodes:[{id:'post',label:'채용 공고',category:'post',source_version:'v1',source_target:'target-a'},
      {id:'institution:a',label:'하나저축은행',category:'organization'}],edges:[]});
    renderNodeDetails(notes,notes.nodes[0],{details:{node_id:'post',basis:'source_record',version:'v1',target_id:'target-a',
      body:'[[하나저축은행]] · [[institution:a|저장된 기관 ID]]'}});
    expect(document.querySelectorAll('button.knowledge-note-link')).toHaveLength(1);
    expect(document.querySelector<HTMLButtonElement>('button.knowledge-note-link')!.dataset.knowledgeNode).toBe('institution:a');
    expect(document.getElementById('knowledge-node-body')!.textContent).toContain('[[하나저축은행]]');
    expect(notes.edges).toEqual([]);
    const savedNote = { ...notes, nodes: notes.nodes.map(node => ({ ...node, sourceVersion: '', sourceTarget: '' })) };
    renderNodeDetails(savedNote,savedNote.nodes[0],{details:{node_id:'post',basis:'note',body:'[[하나저축은행]]'}});
    expect(document.querySelector<HTMLButtonElement>('button.knowledge-note-link')!.dataset.knowledgeNode).toBe('institution:a');
  });
  it('displays traceable original text and excludes another room, account and same-name actor',()=>{
    const payload={details:{node_id:id,scope_room_id:'42',summary:'선택한 방의 참여자',basis:'snapshot'},sources:[source,source,{...source,source_id:`kakao:${account}:room:84:log:2`,content:'다른 방'},{...source,source_id:`kakao:${other}:room:42:log:3`,content:'다른 계정'},{...source,author_id:'8',source_id:`kakao:${account}:room:42:log:4`,content:'동명이인'}]};
    renderNodeDetails(graph,graph.nodes[0],payload);
    expect(document.querySelectorAll('.knowledge-source')).toHaveLength(1);
    expect(document.body.textContent).toContain('금요일 오후 3시');
    expect(document.body.textContent).not.toContain('다른 방');expect(document.body.textContent).not.toContain('동명이인');
    expect(document.getElementById('knowledge-node-basis')?.textContent).toContain('최근 기록의 일부');
  });
  it('keeps quoted markup inert and does not classify outgoing history as Alden speech',()=>{
    renderNodeDetails(graph,graph.nodes[0],{details:{node_id:id},sources:[{...source,content:'<img src=x onerror="alert(1)">',source_role:'outgoing_unclassified'}]});
    expect(document.querySelector('img')).toBeNull();expect(document.body.textContent).toContain('<img');
    expect(document.body.textContent).toContain('발신자 구분 미확인');
  });
  it('rejects mismatched details and labels missing proof explicitly',()=>{
    renderNodeDetails(graph,graph.nodes[0],{details:{node_id:'another',summary:'잘못된 설명'},sources:[source]});
    expect(document.body.textContent).not.toContain('잘못된 설명');expect(document.querySelector('.knowledge-source')).toBeNull();
    expect(document.body.textContent).toContain('원문을 확인하지 못했습니다');
    const collection=parseKnowledgeGraph({nodes:[{id:'group',label:'대화 맥락',category:'collection',is_hub:true},...graph.nodes],edges:[{source:'group',target:id,relation:'linked',purpose:'navigation'}]});
    expect(nodeSummary(collection,collection.nodes[0])).toContain('입구 1개');
  });
  it('keeps a room node scoped to its own account and room even without an explicit detail scope',()=>{
    const roomId=`chat:kakao:${account}:room:42`;
    const roomGraph=parseKnowledgeGraph({nodes:[{id:roomId,label:'회의',category:'채팅방'}],edges:[]});
    renderNodeDetails(roomGraph,roomGraph.nodes[0],{details:{node_id:roomId},sources:[
      source,
      {...source,source_id:`kakao:${account}:room:84:log:2`,content:'다른 방'},
      {...source,source_id:`kakao:${other}:room:42:log:3`,content:'다른 계정'},
      {...source,source_id:`kakao:${account}:room:42:log:4`,source_kind:'other',content:'다른 자료'},
      {...source,source_id:'invalid',content:'잘못된 출처'},
    ]});
    expect([...document.querySelectorAll('.knowledge-source')].map(entry=>(entry as HTMLElement).dataset.sourceId)).toEqual([source.source_id]);
    expect(document.getElementById('knowledge-evidence-heading')!.textContent).toBe('원문 근거 · 1건');
  });
  it('renders stored multiline notes and facts as inert text, preserving the distinction from original evidence',()=>{
    const summary='저장된 문단\n\n<img src="https://invalid.test/note" onerror="alert(1)">';
    renderNodeDetails(graph,graph.nodes[0],{details:{node_id:id,summary,basis:'note',key_facts:['첫 번째 사실','첫 번째 사실','<script>unsafe()</script>',9,null]},sources:[]});
    expect(document.getElementById('knowledge-node-summary')!.textContent).toBe(summary);
    const facts=document.getElementById('knowledge-node-facts')!;
    expect([...facts.children].map(item=>item.textContent)).toEqual(['첫 번째 사실','<script>unsafe()</script>']);
    expect(facts.hidden).toBe(false);
    expect(document.querySelector('script, img')).toBeNull();
    expect(document.getElementById('knowledge-node-basis')!.textContent).toContain('저장된 노트');
    expect(document.querySelector('.knowledge-source')).toBeNull();
    expect(document.getElementById('knowledge-evidence-heading')!.hidden).toBe(false);
    renderNodeDetails(graph,graph.nodes[0],{details:{node_id:id},sources:[]});
    expect(facts.children).toHaveLength(0);
    expect(facts.hidden).toBe(true);
  });
});

// @vitest-environment happy-dom
import {beforeEach,describe,expect,it} from 'vitest';
import {parseKnowledgeGraph} from '../knowledge/graph-model';
import {renderNodeDetails} from '../knowledge/node-details';

const graph=parseKnowledgeGraph({nodes:[{id:'record',label:'보존 자료',category:'document',source_version:'v1'}],edges:[]});
beforeEach(()=>{
  document.body.innerHTML='<p id="knowledge-node-summary"></p><div id="knowledge-node-body" hidden></div><p id="knowledge-node-kind"></p><p id="knowledge-node-basis"></p><ul id="knowledge-node-facts"></ul><h2 id="knowledge-evidence-heading"></h2><div id="knowledge-node-evidence"></div>';
});
function render(body:string, extras:Record<string,unknown>={}) {
  renderNodeDetails(graph,graph.nodes[0],{details:{node_id:'record',basis:'source_record',version:'v1',body,raw_sha256:'a'.repeat(64),...extras}});
}
describe('source-backed reading and numeric facts',()=>{
  for (const newline of ['\n', '\r\n', '\r']) it(`keeps exact source boundaries when folding ${JSON.stringify(newline)} with outer whitespace`, () => {
    const reading = '\t  원문의 들여쓰기와 줄바꿈을 보존합니다.' + newline + '준비 과정과 확인 가능한 근거입니다.  ';
    const tail = newline + ['1', '2', '6', '1', '3'].join(newline) + newline;
    const projection = {policy:'graph-reading-numeric-lines-v1', status:'unverified_presentation_candidate',
      basis:'retained_graph_original_text', original_preserved:true, semantic_validity:'unverified',
      reading, tail, body_sha256:'b'.repeat(64), reading_sha256:'c'.repeat(64), tail_sha256:'d'.repeat(64)};
    render(reading + tail, {body_format:'retained_original_text', reading_projection:projection});
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(reading);
    const note = document.querySelector<HTMLDetailsElement>('.knowledge-reading-tail')!;
    expect(note).not.toBeNull();
    expect(note.open).toBe(false);
    expect(note.querySelector('pre')!.textContent).toBe(tail);
    note.open = true;
    expect(document.getElementById('knowledge-node-body')!.textContent! + note.querySelector('pre')!.textContent!).toBe(reading + tail);
  });

  it('does not fold a projection whose source needs control removal or truncation', () => {
    for (const reading of ['\u0000보존된 원문입니다.', '보존'.repeat(6001)]) {
      const tail = '\n1\n2\n3';
      const projection = {policy:'graph-reading-numeric-lines-v1', status:'unverified_presentation_candidate',
        basis:'retained_graph_original_text', original_preserved:true, semantic_validity:'unverified',
        reading, tail, body_sha256:'b'.repeat(64), reading_sha256:'c'.repeat(64), tail_sha256:'d'.repeat(64)};
      render(reading + tail, {body_format:'retained_original_text', reading_projection:projection});
      expect(document.querySelector('.knowledge-reading-tail')).toBeNull();
      expect(document.getElementById('knowledge-node-body')!.textContent).not.toContain('\u0000');
      expect(document.getElementById('knowledge-node-body')!.textContent!.length).toBeLessThanOrEqual(12000);
    }
  });

  it('folds a retained graph numeric line tail and keeps three-digit measurements', () => {
    const reading = '서류에서 높은 점수를 받는 지원자 특징과 준비 방법입니다. 원문은 그대로 보존됩니다.';
    const tail = '\n1\n2\n6\n1\n3';
    const projection = {policy:'graph-reading-numeric-lines-v1', status:'unverified_presentation_candidate',
      basis:'retained_graph_original_text', original_preserved:true, semantic_validity:'unverified',
      reading, tail, body_sha256:'b'.repeat(64), reading_sha256:'c'.repeat(64), tail_sha256:'d'.repeat(64)};
    render(reading + tail, {body_format:'retained_original_text', reading_projection:projection});
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(reading);
    expect(document.querySelector('.knowledge-reading-tail pre')!.textContent).toBe(tail);
    render('분기별 관측값\n125\n180\n240');
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe('분기별 관측값\n125\n180\n240');
  });
  it('folds only an explicit source-bound candidate and retains its exact expandable tail',()=>{
    const reading='서류에서 높은 점수를 받는 지원자 특징과 준비 방법입니다.',tail='\n11\n2',body=reading+tail;
    const projection={policy:'legacy-threads-reading-tail-v1',status:'unverified_presentation_candidate',
      basis:'verified_retained_legacy_threads_record',original_preserved:true,semantic_validity:'unverified',reading,tail,
      body_sha256:'b'.repeat(64),reading_sha256:'c'.repeat(64),tail_sha256:'d'.repeat(64)};
    render(body,{body_format:'retained_original_text',reading_projection:projection});
    const note=document.querySelector<HTMLDetailsElement>('.knowledge-reading-tail')!;
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(reading);
    expect(note.open).toBe(false);expect(note.querySelector('pre')!.textContent).toBe(tail);
    expect(note.textContent).toContain('의미 미확인');
    note.open=true;expect(note.querySelector('pre')!.textContent).toBe(tail);
    render(body,{basis:'note',body_format:'retained_original_text',reading_projection:projection});
    expect(document.querySelector('.knowledge-reading-tail')).toBeNull();
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(body);
    render(body,{body_format:'retained_original_text',reading_projection:{...projection,tail:'\n33\n4'}});
    expect(document.querySelector('.knowledge-reading-tail')).toBeNull();
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(body);
  });
  for(const [name,body] of [
    ['year','기준 연도\n2026'],
    ['measurements','분기별 관측값\n125\n180\n240'],
    ['identifiers','원문에 적힌 식별 번호\n0021\n0037'],
    ['inline numeric series','비교표의 실제 수치 1 / 2 6 1 3'],
  ]) it(`preserves a legitimate ${name} without a source projection receipt`,()=>{
    render(body);
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(body);
  });
  it('preserves a saved note numeric suffix as well as source records',()=>{
    const body='실험 단계별 입력\n1\n2\n4';render(body,{basis:'note'});
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe(body);
  });
  it('keeps numeric facts and deduplicates only NFC/whitespace-equivalent display strings',()=>{
    render('본문',{key_facts:['2026','10','25','필기 인적성',' 필기  인적성 ', '\u1111\u1175\u11af\u1100\u1175 인적성','1 MB','1 mb']});
    expect([...document.querySelectorAll('#knowledge-node-facts li')].map(e=>e.textContent))
      .toEqual(['2026','10','25','필기 인적성','1 MB','1 mb']);
  });
  it('applies the six-fact cap after skipping empty and duplicate display values',()=>{
    render('본문',{key_facts:['',' ','\n','\t','\r','  ','첫 근거',' 첫 근거 ','둘째 근거','셋째 근거','넷째 근거','다섯째 근거','여섯째 근거','일곱째 근거']});
    expect([...document.querySelectorAll('#knowledge-node-facts li')].map(e=>e.textContent))
      .toEqual(['첫 근거','둘째 근거','셋째 근거','넷째 근거','다섯째 근거','여섯째 근거']);
  });
});

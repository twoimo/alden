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

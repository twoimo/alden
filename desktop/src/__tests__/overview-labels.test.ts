import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { selectOverviewLabels } from '../knowledge/overview-labels';

const graph = (nodes: Array<Record<string, unknown>>) => parseKnowledgeGraph({nodes,edges:[]});
describe('overview reading entry labels', () => {
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
    expect(selectOverviewLabels(input.nodes).map(n=>n.label)).toEqual(['연봉·복리후생','면접 질문을 준비하는 방법','이력서·서류']);
    expect(JSON.stringify(input)).toBe(before);expect(input.nodes).toHaveLength(9);
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
  it('keeps meaningful short subjects and uncertain claims, with no generated text', () => {
    const nodes=graph([{id:'a',label:'면접',category:'topic'},{id:'b',label:'미확인 조건에서 발생할 수 있는 실패',category:'source_claim'}]).nodes;
    expect(selectOverviewLabels(nodes).map(n=>n.label)).toEqual(['면접','미확인 조건에서 발생할 수 있는 실패']);
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

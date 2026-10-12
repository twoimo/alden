// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { knowledgeChangeSummary, knowledgeSignature } from '../knowledge/changes';
import { renderNodeDetails } from '../knowledge/node-details';

const member = (id: string) => ({ id, label: id === 'b' ? '<img src=x onerror=alert(1)>' : '대표 자료', source_version: id + '-v', source_target: 'target' });
const raw = () => ({ nodes: [{ id: 'a', label: '대표', source_version: 'a-v', source_target: 'target', category: 'document',
  evidence: { kind: 'snapshot', source_event_ids: ['source-a'] },
  resolution: { resolution_id: 'decision', project: 'one', kind: 'explicit_user_decision', reason: '수집 원문을 검토한 판단', members: [member('a'), member('b')] } }],
  edges: [{ source: 'a', target: 'a', original_source: 'a', original_target: 'b', relation: 'quotes', evidence_message_id: 'original-edge',
    evidence: { kind: 'snapshot', source_event_ids: ['original-edge'] } }] });

const context = (project: string, consolidated = true, displayMatches = true) => ({
  ...raw().nodes[0].resolution, resolution_id: 'decision-' + project, project, consolidated, display_matches: displayMatches,
});
const withContexts = (contexts: unknown) => {
  const payload = raw();
  return { ...payload, nodes: [{ ...payload.nodes[0], resolution_contexts: contexts }] };
};
const prepareDetails = () => {
  document.body.innerHTML = '<p id="knowledge-node-summary"></p><div id="knowledge-node-body"></div><ul id="knowledge-node-facts"></ul>';
};

describe('review decisions remain distinct from source facts', () => {
  it('retains original directed endpoints and source evidence beside the display grouping', () => {
    const graph = parseKnowledgeGraph(raw());
    expect(graph.nodes[0].resolution?.members.map(member => member.id)).toEqual(['a', 'b']);
    expect(graph.nodes[0].evidence.kind).toBe('snapshot');
    expect(graph.nodes[0].evidence.sourceEventIds).toEqual(['source-a']);
    expect(graph.edges[0]).toMatchObject({ source: 'a', target: 'a', originalSource: 'a', originalTarget: 'b', relation: 'quotes', evidenceMessageId: 'original-edge' });
    const other = raw(); other.nodes[0].resolution.reason = '수정된 판단 근거';
    expect(knowledgeSignature(graph)).not.toBe(knowledgeSignature(parseKnowledgeGraph(other)));
  });
  it('does not present a similarity or inferred grouping as an explicit user decision', () => {
    const payload = raw(); payload.nodes[0].resolution.kind = 'inferred_similarity';
    expect(parseKnowledgeGraph(payload).nodes[0].resolution).toBeUndefined();
  });
  it('shows review basis and member labels as text, and clears it on an ungrouped selection', () => {
    document.body.innerHTML = '<p id="knowledge-node-summary"></p><div id="knowledge-node-body"></div>';
    const graph = parseKnowledgeGraph(raw());
    renderNodeDetails(graph, graph.nodes[0]);
    const note = document.querySelector('.knowledge-resolution-note')!;
    expect(note.textContent).toContain('검토로 묶은 자료 2개');
    expect(note.textContent).toContain('현재 화면의 검토 결정');
    expect(note.querySelector('img')).toBeNull();
    expect(note.textContent).toContain('<img');
    renderNodeDetails(graph, { ...graph.nodes[0], resolution: undefined });
    expect(document.querySelector('.knowledge-resolution-note')).toBeNull();
  });
});

describe('project review contexts', () => {
  it('preserves each explicit project decision and both independent display flags', () => {
    const graph = parseKnowledgeGraph(withContexts([context('one'), context('two', false, false)]));
    expect(graph.nodes[0].resolutionContexts).toEqual([
      { id: 'decision-one', project: 'one', reason: '수집 원문을 검토한 판단', consolidated: true, displayMatches: true,
        members: [{ id: 'a', label: '대표 자료', version: 'a-v', target: 'target' },
          { id: 'b', label: '<img src=x onerror=alert(1)>', version: 'b-v', target: 'target' }] },
      { id: 'decision-two', project: 'two', reason: '수집 원문을 검토한 판단', consolidated: false, displayMatches: false,
        members: [{ id: 'a', label: '대표 자료', version: 'a-v', target: 'target' },
          { id: 'b', label: '<img src=x onerror=alert(1)>', version: 'b-v', target: 'target' }] },
    ]);
    const before = parseKnowledgeGraph(raw());
    expect(graph.nodes[0].resolution).toEqual(before.nodes[0].resolution);
    expect(graph.nodes[0].evidence).toEqual(before.nodes[0].evidence);
    expect(graph.nodes[0].facts).toEqual([]);
    expect(graph.edges).toEqual(before.edges);
  });

  it.each([2, 16])('accepts a complete explicit decision with %i distinct members', count => {
    const decision = context('one');
    decision.members = Array.from({ length: count }, (_, index) => member('member-' + index));
    expect(parseKnowledgeGraph(withContexts([decision])).nodes[0].resolutionContexts?.[0].members).toHaveLength(count);
  });

  it.each([
    ['inferred similarity', { kind: 'inferred_similarity' }],
    ['space grouping', { kind: 'space_grouping' }],
    ['missing decision ID', { resolution_id: '' }],
    ['missing project', { project: '   ' }],
    ['too few members', { members: [member('a')] }],
    ['too many members', { members: Array.from({ length: 17 }, (_, index) => member(String(index))) }],
    ['duplicate member IDs', { members: [member('a'), member('a')] }],
    ['missing member ID', { members: [member('a'), { ...member('b'), id: '' }] }],
    ['missing member version', { members: [member('a'), { ...member('b'), source_version: '' }] }],
    ['missing member target', { members: [member('a'), { ...member('b'), source_target: '   ' }] }],
    ['non-object member', { members: [member('a'), null] }],
    ['missing consolidated flag', { consolidated: undefined }],
    ['truthy consolidated flag', { consolidated: 'true' }],
    ['missing display match flag', { display_matches: undefined }],
    ['truthy display match flag', { display_matches: 1 }],
  ])('rejects %s without discarding valid contexts beside it', (_name, invalid) => {
    const payload = withContexts([{ ...context('invalid'), ...invalid }, null, context('valid')]);
    expect(parseKnowledgeGraph(payload).nodes[0].resolutionContexts?.map(item => item.project)).toEqual(['valid']);
  });

  it.each([undefined, null, {}, [], [null], [{ kind: 'inferred_similarity' }]])(
    'does not create context notes from malformed or absent metadata %#', value => {
      const payload = withContexts(value);
      const graph = parseKnowledgeGraph({ ...payload, nodes: payload.nodes.map(node => ({ ...node, resolution: undefined })) });
      expect(graph.nodes[0].resolutionContexts).toBeUndefined();
      prepareDetails();
      renderNodeDetails(graph, graph.nodes[0]);
      expect(document.querySelector('.knowledge-resolution-note')).toBeNull();
    });

  it('shows project decisions alongside their current display state without duplicating the legacy decision', () => {
    prepareDetails();
    const graph = parseKnowledgeGraph(withContexts([context('one'), context('two', false)]));
    renderNodeDetails(graph, graph.nodes[0]);
    const notes = [...document.querySelectorAll<HTMLElement>('.knowledge-resolution-note')];
    expect(notes.map(note => note.dataset.project)).toEqual(['one', 'two']);
    expect(notes.map(note => note.dataset.resolutionId)).toEqual(['decision-one', 'decision-two']);
    expect(notes[0].textContent).toContain('one · 검토 결정 · 자료 2개');
    expect(notes[0].textContent).toContain('이 프로젝트의 검토 결정으로 묶어 표시');
    expect(notes[1].textContent).toContain('two · 검토 결정 · 자료 2개');
    expect(notes[1].textContent).toContain('이 프로젝트의 묶음을 적용하지 않고 원문을 유지');
    for (const note of notes) {
      expect(note.textContent).toContain('명시적인 사용자 검토 결정');
      expect(note.textContent).toContain('각 원문과 관계는 보존');
      expect(note.querySelectorAll('li')).toHaveLength(2);
      expect(note.textContent).toContain('<img');
      expect(note.querySelector('img')).toBeNull();
    }
    expect(document.getElementById('knowledge-node-facts')!.children).toHaveLength(0);
  });

  it.each([true, false])('marks a different displayed version with consolidated=%s', consolidated => {
    prepareDetails();
    const payload = withContexts([context('one', consolidated, false)]);
    payload.nodes[0].source_version = 'a-new-v';
    const graph = parseKnowledgeGraph(payload);
    renderNodeDetails(graph, graph.nodes[0], { details: { node_id: 'a', basis: 'source_record', version: 'a-new-v', body: '현재 버전 본문' } });
    const note = document.querySelector('.knowledge-resolution-note')!;
    expect(note.textContent).toContain('현재 표시 버전이 이 결정의 검토 버전과 다릅니다');
    expect(note.textContent).toContain('현재 본문을 검토된 내용으로 간주할 수 없습니다');
    expect(note.textContent).not.toContain('검토 버전과 일치');
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe('현재 버전 본문');
  });

  it('renders all project names, reasons and member labels as text', () => {
    prepareDetails();
    const decision = context('<script>project</script>');
    decision.reason = '<img src=x onerror=alert(1)>검토 이유';
    const graph = parseKnowledgeGraph(withContexts([decision]));
    renderNodeDetails(graph, graph.nodes[0]);
    const note = document.querySelector('.knowledge-resolution-note')!;
    expect(note.textContent).toContain('<script>project</script>');
    expect(note.textContent).toContain('<img src=x onerror=alert(1)>검토 이유');
    expect(note.querySelector('script, img, a')).toBeNull();
  });

  it('clears every previous project note on refresh and on an unreviewed selection', () => {
    prepareDetails();
    const graph = parseKnowledgeGraph(withContexts([context('one'), context('two', false)]));
    renderNodeDetails(graph, graph.nodes[0]);
    renderNodeDetails(graph, graph.nodes[0], { details: { node_id: 'a' } });
    expect(document.querySelectorAll('.knowledge-resolution-note')).toHaveLength(2);
    const next = parseKnowledgeGraph(withContexts([context('three', false, false)]));
    renderNodeDetails(next, next.nodes[0]);
    expect(document.querySelectorAll('.knowledge-resolution-note')).toHaveLength(1);
    expect(document.querySelector<HTMLElement>('.knowledge-resolution-note')!.dataset.project).toBe('three');
    renderNodeDetails(next, { ...next.nodes[0], resolution: undefined, resolutionContexts: undefined });
    expect(document.querySelector('.knowledge-resolution-note')).toBeNull();
  });

  it.each([
    ['decision ID', { resolution_id: 'decision-updated' }],
    ['project', { project: 'updated' }],
    ['reason', { reason: '갱신된 검토 근거' }],
    ['grouping state', { consolidated: true }],
    ['displayed version match', { display_matches: true }],
    ['member identity', { members: [member('a'), member('c')] }],
    ['member label', { members: [member('a'), { ...member('b'), label: '갱신된 자료' }] }],
    ['member version', { members: [member('a'), { ...member('b'), source_version: 'b-new-v' }] }],
    ['member target', { members: [member('a'), { ...member('b'), source_target: 'new-target' }] }],
  ])('invalidates the signature for a change to the second context %s', (_name, change) => {
    const before = parseKnowledgeGraph(withContexts([context('one'), context('two', false, false)]));
    const after = parseKnowledgeGraph(withContexts([context('one'), { ...context('two', false, false), ...change }]));
    expect(knowledgeSignature(after)).not.toBe(knowledgeSignature(before));
    expect(knowledgeChangeSummary(before, after)).toBe('기억 갱신 1');
    expect(after.edges).toEqual(before.edges);
  });

  it('invalidates the signature when a project decision is added or removed', () => {
    const before = parseKnowledgeGraph(withContexts([context('one')]));
    const after = parseKnowledgeGraph(withContexts([context('one'), context('two', false, false)]));
    expect(knowledgeSignature(before)).not.toBe(knowledgeSignature(after));
    expect(knowledgeChangeSummary(before, after)).toBe('기억 갱신 1');
    expect(knowledgeChangeSummary(after, before)).toBe('기억 갱신 1');
  });
});

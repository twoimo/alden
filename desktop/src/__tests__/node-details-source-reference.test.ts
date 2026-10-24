// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { renderNodeDetails } from '../knowledge/node-details';

const graph = () => parseKnowledgeGraph({ nodes: [{ id: 'record-a', label: '선택 자료', category: 'document',
  source_version: 'v1', source_target: 'target-a', description: '이전 원문의 캐시 설명' }], edges: [] });
const detail = () => ({ node_id: 'record-a', basis: 'source_record', version: 'v1', target_id: 'target-a',
  body: '확인한 원문', summary: '확인한 설명', key_facts: ['확인한 사실'], raw_sha256: 'a'.repeat(64),
  source_url: 'https://example.com/source', platform: 'graph' });
beforeEach(() => {
  document.body.innerHTML = '<p id="knowledge-node-summary"></p><div id="knowledge-node-body" hidden></div><p id="knowledge-node-kind"></p><p id="knowledge-node-basis"></p><ul id="knowledge-node-facts"></ul><h2 id="knowledge-evidence-heading"></h2><div id="knowledge-node-evidence"></div>';
});

describe('source-record popup identity boundary', () => {
  it.each([
    ['another target', { target_id: 'target-b' }], ['another version', { version: 'v2' }],
    ['another node', { node_id: 'record-b' }], ['missing target', { target_id: undefined }],
    ['missing version', { version: undefined }], ['note downgrade', { basis: 'note' }],
  ])('does not display a %s response or restore cached source description', (_name, delta) => {
    const data = graph();
    renderNodeDetails(data, data.nodes[0], { ok: true, details: { ...detail(), ...delta } });
    expect(document.getElementById('knowledge-node-body')!.hidden).toBe(true);
    for (const value of ['확인한 원문', '확인한 설명', '확인한 사실', '이전 원문의 캐시 설명', 'example.com']) {
      expect(document.body.textContent).not.toContain(value);
    }
    expect(document.querySelector('.knowledge-source')).toBeNull();
    expect(document.getElementById('knowledge-node-basis')!.textContent).toContain('확인하지 못했습니다');
  });

  it.each([{ ok: false }, { ok: true, discarded: true }])('clears previously displayed source fields on a failed/discarded envelope %j', envelope => {
    const data = graph();
    renderNodeDetails(data, data.nodes[0], { ok: true, details: detail() });
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe('확인한 원문');
    renderNodeDetails(data, data.nodes[0], { ...envelope, details: detail() });
    expect(document.getElementById('knowledge-node-body')!.hidden).toBe(true);
    expect(document.querySelectorAll('#knowledge-node-facts li, .knowledge-source')).toHaveLength(0);
    expect(document.body.textContent).not.toContain('이전 원문의 캐시 설명');
  });

  it('retains the exact matching source and blocks source details without a graph source reference', () => {
    const data = graph();
    renderNodeDetails(data, data.nodes[0], { ok: true, details: detail() });
    expect(document.getElementById('knowledge-node-body')!.textContent).toBe('확인한 원문');
    expect(document.querySelectorAll('#knowledge-node-facts li')).toHaveLength(1);
    expect(document.querySelector('.knowledge-source')).not.toBeNull();
    const unbound = parseKnowledgeGraph({ nodes: [{ id: 'record-a', label: '출처 미확인' }], edges: [] });
    renderNodeDetails(unbound, unbound.nodes[0], { ok: true, details: detail() });
    expect(document.getElementById('knowledge-node-body')!.hidden).toBe(true);
    expect(document.querySelector('.knowledge-source')).toBeNull();
  });

  it('discloses frontend clipping even when the payload says untruncated, then removes the stale notice', () => {
    const data = graph();
    renderNodeDetails(data, data.nodes[0], { ok: true, details: { ...detail(), body: '원'.repeat(12001), truncated: false } });
    expect(document.getElementById('knowledge-node-body')!.textContent).toHaveLength(12000);
    expect(document.querySelector('.knowledge-body-limit')!.textContent).toContain('일부 발췌');
    renderNodeDetails(data, data.nodes[0], { ok: true, details: { ...detail(), body: '원'.repeat(12000), truncated: false } });
    expect(document.querySelector('.knowledge-body-limit')).toBeNull();
  });
});

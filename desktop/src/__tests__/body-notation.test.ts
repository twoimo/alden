// @vitest-environment happy-dom
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import fixture from './fixtures/body-notation.json';
import { verifiedBodyNotation, renderBodyNotation } from '../knowledge/body-notation';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { renderNodeDetails, setNodeDetailsVisible } from '../knowledge/node-details';

const data = () => structuredClone(fixture);
const graph = () => parseKnowledgeGraph({ nodes: [{ id: 'record-a', label: '자료', category: 'document', source_version: 'v1', source_target: 'target-a' }], edges: [] });
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
  document.body.innerHTML = '<dialog id="knowledge-note-pane" open><div id="knowledge-node-body"></div></dialog>';
  const pane = document.querySelector('dialog')!;
  pane.close = () => { pane.removeAttribute('open'); };
});
afterEach(() => vi.unstubAllGlobals());

describe('exact source body notation boundary', () => {
  it('accepts the actual Python parser fixture, including emoji, exact integers and percent points', async () => {
    const result = await verifiedBodyNotation(graph().nodes[0], data());
    expect(result?.items).toHaveLength(8);
    expect(result?.items.map(item => item.value)).toEqual(expect.arrayContaining(['2026-02-23', '50000000 원', '-5 명', '5 %p', '8000 g ~ 3000 g · 원문 순서', '의미 미확정', '9007199254740993 명']));
  });
  it.each([
    ['body bytes', (v: any) => { v.body = v.body.replace('기한', '수정'); }],
    ['document', (v: any) => { v.body_notation.binding.document_id = 'other'; }],
    ['version', (v: any) => { v.body_notation.binding.version = 'v2'; }],
    ['target', (v: any) => { v.body_notation.binding.target_id = 'other'; }],
    ['archive', (v: any) => { v.body_notation.binding.raw_sha256 = 'b'.repeat(64); }],
    ['body digest', (v: any) => { v.body_notation.binding.body_sha256 = v.body_notation.analysis.body_sha256 = 'b'.repeat(64); }],
    ['excerpt scope', (v: any) => { v.body_notation.binding.body_scope = 'detail_body_excerpt'; }],
    ['raw quote', (v: any) => { v.body_notation.analysis.candidates[0].raw = '<img src=x>'; }],
    ['UTF16 offset', (v: any) => { v.body_notation.analysis.candidates[0].utf16_start -= 1; }],
    ['codepoint offset', (v: any) => { v.body_notation.analysis.candidates[0].start -= 1; }],
    ['invalid offset', (v: any) => { v.body_notation.analysis.candidates[0].start = -1; }],
    ['overlapping spans', (v: any) => { v.body_notation.analysis.candidates.push(v.body_notation.analysis.candidates[0]); }],
    ['unknown unit', (v: any) => { v.body_notation.analysis.candidates[1].canonical_unit = 'KRW'; }],
    ['invented dimension', (v: any) => { v.body_notation.analysis.candidates[1].dimension = '__proto__'; }],
    ['fact claim', (v: any) => { v.body_notation.analysis.semantic_validity = 'verified'; }],
    ['incomplete state', (v: any) => { v.body_notation.analysis.state = 'supported_notation_scan_complete'; v.body_notation.analysis.truncated = true; }],
    ['excess candidates', (v: any) => { v.body_notation.analysis.candidates = Array(65).fill(v.body_notation.analysis.candidates[0]); }],
  ])('rejects a mismatched %s without altering the body', async (_name, mutate) => {
    const value = data(); mutate(value);
    expect(await verifiedBodyNotation(graph().nodes[0], value)).toBeNull();
  });
  it('supports canonical IDs after unified graph ID rewriting', async () => {
    const node = { ...graph().nodes[0], id: 'scoped:record-a', canonicalId: 'record-a' };
    expect(await verifiedBodyNotation(node, { ...data(), node_id: node.id })).not.toBeNull();
  });
  it('leaves the source readable if WebCrypto is absent or rejects', async () => {
    vi.stubGlobal('crypto', {});
    renderNodeDetails(graph(), graph().nodes[0], { ok: true, details: data() });
    await vi.waitFor(() => expect(document.querySelector('.knowledge-body-notation')).toBeNull());
    expect(document.getElementById('knowledge-node-body')?.textContent).toBe(fixture.body);
    vi.stubGlobal('crypto', { subtle: { digest: () => Promise.reject(new Error('unavailable')) } });
    renderBodyNotation(document.getElementById('knowledge-node-body')!, graph().nodes[0], data());
    await vi.waitFor(() => expect(document.querySelector('.knowledge-body-notation')).toBeNull());
  });
  it('uses a collapsed disclosure and literal source quotes without HTML execution', async () => {
    renderNodeDetails(graph(), graph().nodes[0], { ok: true, details: data() });
    await vi.waitFor(() => expect(document.querySelector<HTMLDetailsElement>('.knowledge-body-notation')?.hidden).toBe(false));
    const section = document.querySelector<HTMLDetailsElement>('.knowledge-body-notation')!;
    expect(section.open).toBe(false);
    expect(section.querySelectorAll('mark')).toHaveLength(8);
    expect(document.querySelector('img')).toBeNull();
    expect(section.textContent).toContain('현재 유효성');
    expect(document.querySelector('#knowledge-node-facts')).toBeNull();
  });
  it('discloses excerpt and candidate budgets', async () => {
    const value = data(); value.truncated = true; value.body_notation.binding.body_scope = 'detail_body_excerpt';
    value.body_notation.analysis.truncated = true; value.body_notation.analysis.state = 'bounded_candidates';
    renderBodyNotation(document.getElementById('knowledge-node-body')!, graph().nodes[0], value);
    await vi.waitFor(() => expect(document.querySelector('.knowledge-body-notation')?.textContent).toContain('처리 한도'));
    expect(document.querySelector('.knowledge-body-notation')?.textContent).toContain('발췌 범위');
  });
  it.each(['new render', 'close'])('discards a delayed digest after %s', async action => {
    let finish!: (buffer: ArrayBuffer) => void;
    vi.stubGlobal('crypto', { subtle: { digest: () => new Promise<ArrayBuffer>(resolve => { finish = resolve; }) } });
    renderNodeDetails(graph(), graph().nodes[0], { ok: true, details: data() });
    if (action === 'new render') renderNodeDetails(graph(), graph().nodes[0], { ok: false });
    else setNodeDetailsVisible(false);
    finish(await webcrypto.subtle.digest('SHA-256', new TextEncoder().encode(fixture.body)));
    await new Promise(resolve => setTimeout(resolve, 0));
    expect(document.querySelector('.knowledge-body-notation')).toBeNull();
  });
});

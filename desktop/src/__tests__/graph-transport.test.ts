import { describe, it, expect } from 'vitest';
import { expandGraphTransport } from '../graph-transport';

describe('lossless bounded source context transport', () => {
  const packet = () => ({ source_context_encoding: 'display-source-context-v1', ok: true, nodes: [{
    id: 'a', label: 'Title', source_target: 't', source_version: 'v',
    source_contexts: [{ project: 'one', same_as_display: true },
      { project: 'two', target_id: 'other', version: 'older', label: 'Old title' }],
  }], edges: [{ id: 'original-edge', source: 'a', target: 'b' }] });
  it('restores exact current fields and preserves independently scoped versions and edges', () => {
    const value = packet(), decoded: any = expandGraphTransport(value);
    expect(decoded.nodes[0].source_contexts).toEqual([
      { project: 'one', target_id: 't', version: 'v', label: 'Title' },
      { project: 'two', target_id: 'other', version: 'older', label: 'Old title' },
    ]);
    expect(decoded.edges).toEqual(value.edges);
    expect(decoded.source_context_encoding).toBeUndefined();
    expect(value.source_context_encoding).toBe('display-source-context-v1');
  });
  it('never invents reference fields from missing, conflicting or unknown packets', () => {
    for (const change of [
      (v: any) => { delete v.nodes[0].source_version; },
      (v: any) => { v.nodes[0].source_contexts[0].same_as_display = 'true'; },
      (v: any) => { v.nodes[0].source_contexts[0].target_id = 'foreign'; },
      (v: any) => { v.source_context_encoding = 'unknown'; },
    ]) { const value = packet(); change(value); expect(() => expandGraphTransport(value)).toThrow(); }
  });
});

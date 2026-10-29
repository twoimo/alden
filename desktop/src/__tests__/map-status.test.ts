// @vitest-environment happy-dom
import { beforeEach, describe, expect, it } from 'vitest';
import { knowledgeMapStatus, renderKnowledgeMapStatus } from '../knowledge/map-status';

beforeEach(() => {
 document.body.innerHTML = '<section id="knowledge-map-status"><h2 id="knowledge-map-status-title"></h2><p id="knowledge-map-status-message"></p></section><canvas id="knowledge-graph-canvas"></canvas><button id="knowledge-back">이전</button>';
});

describe('knowledge map source state', () => {
 it('requires an accepted, well formed response before claiming an empty collection', () => {
  expect(knowledgeMapStatus({ ok:true, nodes:[] }, 0)).toBe('empty');
  for (const response of [null, {}, { ok:false, nodes:[] }, { nodes:[] }, { ok:true }, { ok:true, nodes:{} }, { ok:true, nodes:[], partial:'false' }, { ok:true, nodes:[], stale:1 }]) {
   expect(knowledgeMapStatus(response, 0)).toBe('unavailable');
  }
  expect(knowledgeMapStatus({ ok:true, nodes:[{ id:'a' }] }, 0)).toBe('unavailable');
 });
 it('does not turn incomplete or stale sources into a clean empty state', () => {
  expect(knowledgeMapStatus({ ok:true, nodes:[], partial:true }, 0)).toBe('partial');
  expect(knowledgeMapStatus({ ok:true, nodes:[], stale:true }, 0)).toBe('stale');
  expect(knowledgeMapStatus({ ok:true, nodes:[{}], stale:true }, 1)).toBe('stale');
  expect(knowledgeMapStatus({ ok:true, nodes:[{}], partial:true, stale:true }, 1)).toBe('partial');
 });
 it.each([-1, 0.5, NaN, Infinity])('rejects an invalid visible count: %s', count => {
  expect(knowledgeMapStatus({ ok:true, nodes:[{}] }, count)).toBe('unavailable');
 });
 it('keeps the existing map and navigation intact when an update fails', () => {
  const canvas=document.getElementById('knowledge-graph-canvas');
  const button=document.getElementById('knowledge-back');
  renderKnowledgeMapStatus(document, { ok:false }, 1);
  const panel=document.getElementById('knowledge-map-status')!;
  expect(panel.hidden).toBe(false);
  expect(panel.dataset.state).toBe('unavailable');
  expect(panel.dataset.presentation).toBe('notice');
  expect(panel.textContent).toContain('자료가 없는 상태와는 다릅니다');
  expect(document.getElementById('knowledge-graph-canvas')).toBe(canvas);
  expect(document.getElementById('knowledge-back')).toBe(button);
 });
 it('reconciles a missing source, an empty result and a recovered graph without source authority changes', () => {
  renderKnowledgeMapStatus(document, null, 0);
  const panel=document.getElementById('knowledge-map-status')!;
  expect(panel.dataset.state).toBe('unavailable');
  expect(panel.dataset.presentation).toBe('empty');
  renderKnowledgeMapStatus(document, { ok:true, nodes:[] }, 0);
  expect(panel.dataset.state).toBe('empty');
  expect(panel.textContent).toContain('필터');
  const response={ ok:true, nodes:[{ id:'a' }], stale:false };
  renderKnowledgeMapStatus(document, response, 1);
  expect(panel.hidden).toBe(true);
  expect(panel.dataset.state).toBe('ready');
  expect(response).toEqual({ ok:true, nodes:[{ id:'a' }], stale:false });
 });
 it('is safe on a screen that does not contain the graph status region', () => {
  document.body.innerHTML='<p>다른 화면</p>';
  expect(() => renderKnowledgeMapStatus(document, null, 0)).not.toThrow();
 });
});

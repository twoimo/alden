// @vitest-environment happy-dom
import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { settingsMarkup } from '../ui';
import { parseKnowledgeGraph } from '../knowledge/graph-model';
import { renderNodeDetails } from '../knowledge/node-details';

const graph = parseKnowledgeGraph({ nodes: [
  { id: 'a', label: '회의 기록', description: '저장된 첫 문단\n다음 문단' },
  { id: 'b', label: '연결된 기록', description: '다른 노트의 저장 내용' },
], edges: [{ source: 'a', target: 'b', relation: 'linked' }] });

beforeEach(() => {
  document.body.innerHTML = settingsMarkup();
});

describe('adjacent note reader', () => {
  it('keeps the graph and all existing detail targets inside separate workspace panes', () => {
    const pane = document.getElementById('knowledge-note-pane')!;
    const workspace = document.querySelector('.knowledge-workspace')!;
    expect(pane.parentElement).toBe(workspace);
    expect(document.getElementById('knowledge-graph-canvas')!.closest('.knowledge-graph-pane')?.parentElement).toBe(workspace);
    expect(pane.hidden).toBe(true);
    expect(pane.getAttribute('aria-labelledby')).toBe('knowledge-focus-title');
    for (const id of ['knowledge-focus-title', 'knowledge-focus-close', 'knowledge-node-kind', 'knowledge-node-summary',
      'knowledge-node-basis', 'knowledge-node-facts', 'knowledge-relations', 'knowledge-evidence-heading',
      'knowledge-node-evidence', 'knowledge-retrieve']) {
      expect(document.querySelectorAll(`#${id}`)).toHaveLength(1);
      expect(pane.contains(document.getElementById(id))).toBe(true);
    }
    expect(document.getElementById('knowledge-node-kind')!.hidden).toBe(true);
    expect(pane.querySelector('details')!.open).toBe(false);
    expect(pane.querySelector('iframe, img, video')).toBeNull();
  });

  it('keeps existing connected-note buttons and their handlers through asynchronous detail updates', () => {
    const container = document.getElementById('knowledge-relations')!;
    const linkedNote = document.createElement('button');
    const openNote = vi.fn();
    linkedNote.textContent = graph.nodes[1].label;
    linkedNote.addEventListener('click', openNote);
    container.append(linkedNote);
    renderNodeDetails(graph, graph.nodes[0]);
    renderNodeDetails(graph, graph.nodes[0], { details: { node_id: 'a', summary: '저장된 설명', key_facts: ['저장된 사실'] } });
    expect(container.firstElementChild).toBe(linkedNote);
    linkedNote.click();
    expect(openNote).toHaveBeenCalledTimes(1);
    expect(document.getElementById('knowledge-node-summary')!.textContent).toBe('저장된 설명');
    expect(document.getElementById('knowledge-node-facts')!.textContent).toBe('저장된 사실');
  });

  it('preserves reading position and disclosure choice on retrieval, resetting them only on a new selection', () => {
    const pane = document.getElementById('knowledge-note-pane')!;
    const disclosure = pane.querySelector('details')!;
    pane.hidden = false;
    renderNodeDetails(graph, graph.nodes[0]);
    expect(document.activeElement).toBe(pane);
    disclosure.open = true;
    pane.scrollTop = 160;
    const close = document.getElementById('knowledge-focus-close')!;
    close.focus();
    renderNodeDetails(graph, graph.nodes[0], { details: { node_id: 'a', summary: '확인된 저장 설명' } });
    expect(disclosure.open).toBe(true);
    expect(pane.scrollTop).toBe(160);
    expect(document.activeElement).toBe(close);
    renderNodeDetails(graph, graph.nodes[1]);
    expect(disclosure.open).toBe(false);
    expect(pane.scrollTop).toBe(0);
    expect(document.activeElement).toBe(pane);
  });

  it('uses the existing close action once for Escape, returns focus to the graph, and never opens a hidden pane', () => {
    const pane = document.getElementById('knowledge-note-pane')!;
    const close = document.getElementById('knowledge-focus-close')!;
    const onClose = vi.fn(() => { pane.hidden = true; });
    close.addEventListener('click', onClose);
    renderNodeDetails(graph, graph.nodes[0]);
    expect(pane.hidden).toBe(true);
    expect(document.activeElement).not.toBe(pane);
    pane.hidden = false;
    renderNodeDetails(graph, graph.nodes[0]);
    renderNodeDetails(graph, graph.nodes[0], { details: { node_id: 'a' } });
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    pane.dispatchEvent(escape);
    expect(escape.defaultPrevented).toBe(true);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(pane.hidden).toBe(true);
    expect(document.activeElement).toBe(document.getElementById('knowledge-graph-canvas'));
    renderNodeDetails(graph, graph.nodes[0], { details: { node_id: 'a' } });
    expect(pane.hidden).toBe(true);
    expect(document.activeElement).toBe(document.getElementById('knowledge-graph-canvas'));
  });

  it('does not close while Escape is being used for text composition', () => {
    const pane = document.getElementById('knowledge-note-pane')!;
    const onClose = vi.fn();
    document.getElementById('knowledge-focus-close')!.addEventListener('click', onClose);
    renderNodeDetails(graph, graph.nodes[0]);
    pane.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', isComposing: true, bubbles: true }));
    expect(onClose).not.toHaveBeenCalled();
  });
});

// These check the actual stylesheet cascade and media queries, not pixel layout.
describe('note reader responsive CSS', () => {
  const styles = ['../styles.css', '../settings.css'].map(path => readFileSync(new URL(path, import.meta.url), 'utf8')).join('\n');
  it.each([{ width: 1200, height: 760, stacked: false }, { width: 640, height: 680, stacked: true }])(
    'keeps an independent reading surface at $width × $height', ({ width, height, stacked }) => {
      const browser = new Window({ width, height });
      try {
        const doc = browser.document;
        const style = doc.createElement('style');
        style.textContent = styles;
        doc.head.append(style);
        doc.body.className = 'settings-view';
        doc.body.innerHTML = settingsMarkup();
        const pane = doc.getElementById('knowledge-note-pane')!;
        const section = doc.getElementById('settings-knowledge-card')!;
        const workspace = doc.querySelector('.knowledge-workspace')!;
        expect(browser.getComputedStyle(pane).display).toBe('none');
        expect(browser.getComputedStyle(workspace).gridTemplateColumns).toBe('minmax(0,1fr)');
        pane.removeAttribute('hidden');
        section.setAttribute('data-note-open', 'true');
        const layout = browser.getComputedStyle(workspace);
        expect(layout.display).toBe('grid');
        expect(layout.gridTemplateColumns).toBe(stacked ? 'minmax(0,1fr)' : 'minmax(0,1fr) minmax(300px,38%)');
        expect(layout.gridTemplateRows).toBe(stacked ? 'minmax(220px,.85fr) minmax(0,1fr)' : 'minmax(0,1fr)');
        const note = browser.getComputedStyle(pane);
        expect(note.position).toBe('relative');
        expect(note.overflowY).toBe('auto');
        expect(note.overflowX).toBe('hidden');
        expect(note.minWidth).toBe('0');
        expect(note.minHeight).toBe('0');
        expect(note.borderRadius).toBe('0px');
        expect(note.boxShadow).toBe('none');
        expect(browser.getComputedStyle(doc.querySelector('.knowledge-heading')!).position).toBe('static');
        expect(browser.getComputedStyle(doc.querySelector('.knowledge-hologram-toolbar')!).position).toBe('static');
        expect(browser.getComputedStyle(doc.getElementById('knowledge-node-summary')!).whiteSpace).toBe('pre-wrap');
        expect(browser.getComputedStyle(doc.getElementById('knowledge-focus-title')!).overflowWrap).toBe('anywhere');
        pane.setAttribute('hidden', '');
        section.setAttribute('data-note-open', 'false');
        expect(browser.getComputedStyle(pane).display).toBe('none');
        expect(browser.getComputedStyle(workspace).gridTemplateColumns).toBe('minmax(0,1fr)');
        expect(browser.getComputedStyle(workspace).gridTemplateRows).toBe('minmax(0,1fr)');
      } finally {
        browser.happyDOM.abort();
        browser.close();
      }
    },
  );
});

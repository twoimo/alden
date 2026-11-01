import {readFileSync} from 'node:fs';
import {Window} from 'happy-dom';
import {describe, expect, it} from 'vitest';

const source = readFileSync(new URL('../../src-tauri/src/render_audit.rs', import.meta.url), 'utf8');
const template = /const GRAPH_ACTION_ORIGIN: &str = r#"([\s\S]*?)"#;/.exec(source)?.[1];
if (!template) throw new Error('native graph action origin script missing');

function navigation() {
  return {focused: true, focusId: 'anonymous-source', source: 'collection',
    focusedSourceVersion: 'anonymous-version', focusSlot: 0, hops: 0, canGoBack: true,
    nodeCount: 2048, edgeCount: 3789, overviewLimit: 2048, overviewOffset: 0, hasMoreContexts: true,
    targets: {camera: [.006948103582295584, .007376869018654864, 4.842554731070363],
      lookAt: [.006948103582295584, .007376869018654864, .00293568722151788]}};
}

function script() {
  return template!.replaceAll('__AUDIT_GRAPH_COLLECT__', 'JSON.stringify({navigation:window.navigation})')
    .replace('__AUDIT_GRAPH_ACTION__', "document.querySelector('#expand')?.click()");
}

describe('actual native click-origin script', () => {
  it('captures the changed screenshot-layout pose in the same task before dispatch and keeps it immutable', async () => {
    const window = new Window();
    try {
      const early = navigation();
      const current = structuredClone(early);
      current.targets = {camera: [.03584005775593069, -.009356112800393055, 4.788133435594943],
        lookAt: [.03584005775593069, -.009356112800393055, .05036227746934685]};
      (window as any).navigation = current;
      const saved: ReturnType<typeof navigation>[] = [];
      const button = window.document.createElement('button'); button.id = 'expand';
      button.addEventListener('click', () => {
        saved.push(JSON.parse(JSON.stringify((window as any).navigation)));
        (window as any).navigation = {...current, hops: 1, nodeCount: 5,
          targets: {camera: [0, 0, 3.6], lookAt: [0, 0, 0]}};
      });
      window.document.body.append(button);
      const result = JSON.parse(window.eval(script()));
      expect(result.before).toEqual(saved[0]);
      expect(result.before).not.toEqual(early);
      expect(result.after.hops).toBe(1);
      (window as any).navigation.targets.camera[0] = 99;
      expect(result.before).toEqual(current);
      expect(result.before.targets.camera[0]).not.toBe(99);
    } finally {await window.happyDOM.close();}
  });

  it('exposes a history save deferred to a different pose instead of silently accepting that restored pose', async () => {
    const window = new Window();
    try {
      (window as any).navigation = navigation();
      let saved: ReturnType<typeof navigation> | null = null;
      const button = window.document.createElement('button'); button.id = 'expand';
      button.addEventListener('click', () => queueMicrotask(() => {
        (window as any).navigation.targets.camera[0] = .25;
        saved = JSON.parse(JSON.stringify((window as any).navigation));
      }));
      window.document.body.append(button);
      const result = JSON.parse(window.eval(script()));
      await Promise.resolve();
      expect(saved).not.toBeNull();
      expect(result.before).not.toEqual(saved);
      expect(result.before.targets.camera[0]).toBe(navigation().targets.camera[0]);
    } finally {await window.happyDOM.close();}
  });
});

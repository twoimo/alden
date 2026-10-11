import { readFileSync } from 'node:fs';
import { Window } from 'happy-dom';
import { describe, expect, it, vi } from 'vitest';

const source = readFileSync(new URL('../../src-tauri/src/render_audit.rs', import.meta.url), 'utf8');
const script = /const ASSISTANT_READY_SCRIPT: &str = r#"([\s\S]*?)"#;/.exec(source)?.[1];
if (!script) throw new Error('assistant readiness initialization script missing');
const tick = () => new Promise<void>(resolve => setTimeout(resolve, 0));

function fixture() {
  const window = new Window({ url: 'http://tauri.localhost/index.html?view=settings' });
  Object.defineProperty(window.document, 'readyState', { configurable: true, value: 'loading' });
  const internal: { plugins: Record<string, never>; invoke?: ReturnType<typeof vi.fn> } = { plugins: {} };
  Object.defineProperty(window, '__TAURI_INTERNALS__', { value: internal });
  return { window, internal };
}

describe('actual native assistant readiness script', () => {
  it('waits for framework initialization before calling IPC', async () => {
    const { window, internal } = fixture();
    try {
      expect(() => window.eval(script)).not.toThrow();
      const invoke = vi.fn().mockResolvedValue(null);
      internal.invoke = invoke;
      const input = window.document.createElement('textarea');
      input.id = 'assistant-composer-text';
      window.document.body.append(input);
      window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
      await tick();
      expect(invoke.mock.calls).toEqual([
        ['audit_assistant_ready', { phase: 'dom' }],
        ['audit_assistant_ready', { phase: 'composer' }],
      ]);
    } finally { await window.happyDOM.close(); }
  });

  it('observes a late composer once without repeated readiness signals', async () => {
    const { window, internal } = fixture();
    try {
      window.eval(script);
      const invoke = vi.fn().mockResolvedValue(null);
      internal.invoke = invoke;
      window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
      await tick();
      expect(invoke.mock.calls).toEqual([['audit_assistant_ready', { phase: 'dom' }]]);
      const input = window.document.createElement('textarea');
      input.id = 'assistant-composer-text';
      window.document.body.append(input);
      await tick();
      input.remove(); window.document.body.append(input);
      await tick();
      expect(invoke.mock.calls).toEqual([
        ['audit_assistant_ready', { phase: 'dom' }],
        ['audit_assistant_ready', { phase: 'composer' }],
      ]);
    } finally { await window.happyDOM.close(); }
  });
});

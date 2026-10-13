// @vitest-environment happy-dom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { VirtualList } from '../virtual-list';

describe('message list viewport changes', () => {
  afterEach(() => { vi.unstubAllGlobals(); document.body.replaceChildren(); });

  function setup() {
    let observer: ResizeObserverCallback;
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: ResizeObserverCallback) { observer = callback; }
      observe() {} disconnect() {}
    });
    const host = document.createElement('div');
    let width = 600, height = 300;
    Object.defineProperties(host, {
      clientWidth: { get: () => width }, clientHeight: { get: () => height },
    });
    document.body.append(host);
    const list = new VirtualList(host, x => String(x), x => {
      const row = document.createElement('div'); row.textContent = String(x); return row;
    }, 100);
    const measured = () => observer!([{
      target: host, contentRect: new DOMRect(0, 0, width, height),
      borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [],
    }], {} as ResizeObserver);
    const resize = (nextWidth: number, nextHeight: number) => {
      width = nextWidth; height = nextHeight;
      // A browser may deliver the clamped scroll event before ResizeObserver.
      host.dispatchEvent(new Event('scroll'));
      measured();
    };
    list.set([1, 2, 3, 4, 5, 6], { end: true }); measured();
    host.scrollTop = 300; host.dispatchEvent(new Event('scroll'));
    return { host, list, resize };
  }

  it('keeps following new replies when compact resize changes the scroll range', () => {
    const { host, list, resize } = setup();
    expect(list.followsLatest).toBe(true);
    resize(240, 120);
    expect(list.followsLatest).toBe(true);
    list.set([1, 2, 3, 4, 5, 6, 7], { end: list.followsLatest, preserve: true });
    expect(host.scrollTop).toBeGreaterThanOrEqual(580);
    list.dispose();
  });

  it('preserves a deliberate older-message position through resize and new replies', () => {
    const { host, list, resize } = setup();
    host.scrollTop = 110; host.dispatchEvent(new Event('scroll'));
    expect(list.followsLatest).toBe(false);
    resize(240, 120);
    list.set([1, 2, 3, 4, 5, 6, 7], { end: list.followsLatest, preserve: true });
    expect(list.followsLatest).toBe(false);
    expect(host.scrollTop).toBe(110);
    list.dispose();
  });
});

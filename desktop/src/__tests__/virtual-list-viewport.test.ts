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
    const resize = (nextWidth: number, nextHeight: number, beforeMeasured?: () => void) => {
      width = nextWidth; height = nextHeight;
      beforeMeasured?.();
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

  it('honors an upward wheel while the resize observer is still pending', () => {
    const { host, list, resize } = setup();
    resize(240, 120, () => {
      host.dispatchEvent(new WheelEvent('wheel', { deltaY: -100 }));
      host.scrollTop = 110;
    });
    expect(list.followsLatest).toBe(false);
    expect(host.scrollTop).toBe(110);
    list.set([1, 2, 3, 4, 5, 6, 7], { end: list.followsLatest, preserve: true });
    expect(host.scrollTop).toBe(110);
    list.dispose();
  });

  it('keeps latest reading intent through a hidden scroll reset and same-size restore', () => {
    const { host, list, resize } = setup();
    list.setVisible(false); resize(0, 0);
    host.scrollTop = 0; host.dispatchEvent(new Event('scroll'));
    list.setVisible(true); resize(600, 300);
    expect(list.followsLatest).toBe(true);
    list.set([1, 2, 3, 4, 5, 6, 7], { end: list.followsLatest, preserve: true });
    expect(host.scrollTop).toBeGreaterThanOrEqual(400);
    list.dispose();
  });

  it('restores an older-message anchor when hidden layout resets scrolling', () => {
    const { host, list, resize } = setup();
    host.scrollTop = 110; host.dispatchEvent(new Event('scroll'));
    list.setVisible(false); resize(0, 0); host.scrollTop = 0;
    host.dispatchEvent(new Event('scroll'));
    list.setVisible(true); resize(600, 300);
    expect(list.followsLatest).toBe(false); expect(host.scrollTop).toBe(110);
    list.dispose();
  });

  it('honors keyboard navigation during resize without treating text editing as scroll intent', () => {
    const { host, list, resize } = setup();
    const input = document.createElement('input'); host.append(input);
    resize(240, 120, () => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Home', bubbles: true })));
    expect(list.followsLatest).toBe(true);
    resize(320, 120, () => {
      host.dispatchEvent(new KeyboardEvent('keydown', { key: 'PageUp', bubbles: true }));
      host.scrollTop = 110;
    });
    expect(list.followsLatest).toBe(false); expect(host.scrollTop).toBe(110);
    list.dispose();
  });
});

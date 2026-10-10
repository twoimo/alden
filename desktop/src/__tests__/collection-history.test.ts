// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { wireCollectionHistory } from '../collection-history';
import type { fetchSettingsAction } from '../runtime';

type Row = Record<string, unknown>;
const event = (sequence: number, extra: Row = {}): Row => ({
  sequence, at: 100, stage: 'stored', target_label: '대상', document_label: '자료 ' + sequence,
  projects: ['one'], details: { change: 'added' }, ...extra,
});
const response = (items: Row[], next: number | null = null) => ({
  ok: true, items, next, projects: [{ project: 'one' }, { project: 'two' }],
  targets: [{ id: 'a', label: '대상 A' }, { id: 'b', label: '대상 B' }],
});
const summary = (added = 0, unchanged = 0) => ({ total: added + unchanged,
  stages: { discovered: 0, parsed: 0, validated: 0, stored: added + unchanged, indexed: 0, failed: 0, paused: 0 },
  changes: { added, revised: 0, unchanged, removed: 0, relations_changed: 0 },
});
const active: ReturnType<typeof wireCollectionHistory>[] = [];
const query = (load: ReturnType<typeof vi.fn<typeof fetchSettingsAction>>, index = -1) =>
  JSON.parse(load.mock.calls.at(index)![1]!.query!);
const setup = (load: ReturnType<typeof vi.fn<typeof fetchSettingsAction>>) => {
  const history = wireCollectionHistory(load); active.push(history); history.select('history'); return history;
};
beforeEach(() => { vi.useFakeTimers(); document.body.innerHTML = '<div id="settings-page-history"></div>'; });
afterEach(() => { for (const item of active.splice(0)) item.dispose(); document.body.replaceChildren(); vi.useRealTimers(); });

describe('durable collection history', () => {
  it('starts forward consumption at an empty checkpoint and counts changes without counting stages as knowledge', async () => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce({ ...response([]), stream_id: 'one', cursor: 0, summary: summary() })
      .mockResolvedValueOnce({ ...response([event(1), event(2, { stage: 'indexed' }), event(3, { details: { change: 'unchanged' } })]),
        stream_id: 'one', cursor: 3 });
    setup(load); await vi.advanceTimersByTimeAsync(2500);
    expect(query(load)).toMatchObject({ after: 0, stream_id: 'one' });
    expect(document.querySelector('.collection-summary')!.textContent).toContain('3개 단계 기록 · 추가 1 · 수정 0 · 유지 1');
  });

  it('recovers a replaced journal and clears the old rows, expanded evidence and summary', async () => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce({ ...response([event(100, { document_label: '이전 저장소' })], 100),
        stream_id: 'old', cursor: 100, summary: summary(10) })
      .mockResolvedValueOnce({ ...response([event(1, { document_label: '복구한 저장소', origin: 'native-source' })]),
        stream_id: 'new', cursor: 1, reset: true, summary: summary(1) });
    setup(load); await vi.advanceTimersByTimeAsync(2500);
    expect(document.body.textContent).not.toContain('이전 저장소');
    expect(document.body.textContent).toContain('복구한 저장소');
    expect(document.body.textContent).toContain('수집 경로: native-source');
    expect(document.querySelector('.collection-summary')!.textContent).toContain('추가 1');
    expect(document.querySelector<HTMLButtonElement>('.history-older')!.hidden).toBe(true);
    expect(vi.getTimerCount()).toBe(1);
  });
  it('describes validation and unchanged records truthfully and never links executable URLs', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue(response([
      event(3, { stage: 'validated', source_url: 'javascript:alert(1)' }),
      event(2, { details: { change: 'unchanged' }, source_url: 'https://user:secret@example.com/' }),
      event(1, { source_url: 'https://www.youtube.com/watch?v=AbC', run_id: 'run', version: 'v1', document_id: 'doc' }),
    ]));
    expect(load).not.toHaveBeenCalled(); setup(load); await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).toContain('형식 확인');
    expect(document.body.textContent).toContain('내용의 사실 여부를 검증한 단계는 아닙니다');
    expect(document.body.textContent).toContain('기존 자료 유지');
    expect(document.querySelectorAll('a')).toHaveLength(1);
    expect(document.querySelector('a')!.getAttribute('href')).toContain('watch?v=AbC');
    expect(document.body.textContent).toContain('저장 버전');
  });

  it('discards hidden pending responses and resumes with one timer and one request', async () => {
    let resolve!: (value: Row) => void;
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockImplementationOnce(() => new Promise(done => { resolve = done; }))
      .mockResolvedValue(response([event(2)]));
    const history = setup(load); history.visible(false);
    resolve(response([event(1, { document_label: '늦은 응답' })])); await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).not.toContain('늦은 응답');
    expect(vi.getTimerCount()).toBe(0);
    history.visible(true); await vi.advanceTimersByTimeAsync(0);
    expect(load).toHaveBeenCalledTimes(2); expect(vi.getTimerCount()).toBe(1);
    history.select('memory'); await vi.advanceTimersByTimeAsync(10000);
    expect(load).toHaveBeenCalledTimes(2); expect(vi.getTimerCount()).toBe(0);
  });

  it('fences a filter changed while its previous request is pending', async () => {
    let resolve!: (value: Row) => void;
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockImplementationOnce(() => new Promise(done => { resolve = done; }))
      .mockResolvedValue(response([event(2, { document_label: '새 범위' })]));
    setup(load);
    const input = document.querySelector<HTMLInputElement>('input[type=search]')!;
    input.value = '새 범위'; input.dispatchEvent(new Event('change'));
    resolve(response([event(1, { document_label: '이전 범위' })])); await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).not.toContain('이전 범위');
    expect(document.body.textContent).toContain('새 범위');
    expect(query(load).search).toBe('새 범위'); expect(vi.getTimerCount()).toBe(1);
  });

  it('recovers multiple forward pages after hiding without skipping their middle or duplicating rows', async () => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(response([event(5)], 5))
      .mockResolvedValueOnce(response(Array.from({ length: 200 }, (_, i) => event(i + 6)), 205))
      .mockResolvedValueOnce(response([event(205), event(206), event(207), event(208), event(209)]));
    const history = setup(load); await vi.advanceTimersByTimeAsync(0);
    history.visible(false); history.visible(true); await vi.advanceTimersByTimeAsync(0);
    expect(query(load).after).toBe(5);
    await vi.advanceTimersByTimeAsync(500);
    expect(query(load).after).toBe(205);
    expect(document.querySelector('.collection-status')!.textContent).toContain('205개');
    expect(document.querySelectorAll('.collection-event-row').length).toBeLessThanOrEqual(80);
    expect(vi.getTimerCount()).toBe(1);
  });

  it('keeps the older view stable while announcing new durable stages and allows returning to latest', async () => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(response([event(20)], 20))
      .mockResolvedValueOnce(response([event(19)], 19))
      .mockResolvedValueOnce(response([event(21)]))
      .mockResolvedValueOnce(response([event(21)]));
    setup(load); await vi.advanceTimersByTimeAsync(0);
    document.querySelector<HTMLButtonElement>('.history-older')!.click(); await vi.advanceTimersByTimeAsync(0);
    expect(query(load).before).toBe(20); expect(query(load).after).toBeUndefined();
    await vi.advanceTimersByTimeAsync(2500);
    expect(query(load).after).toBe(20);
    expect(document.querySelector('.collection-status')!.textContent).toContain('새 처리 이력 1개');
    expect(document.querySelector('.collection-event-list')!.textContent).not.toContain('자료 21');
    document.querySelector<HTMLButtonElement>('.history-latest')!.click(); await vi.advanceTimersByTimeAsync(0);
    expect(query(load).after).toBeUndefined();
    expect(document.querySelector('.collection-event-list')!.textContent).toContain('자료 21');
  });

  it('passes project, target, source, stage and local date boundaries to the read-only API', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue(response([]));
    setup(load); await vi.advanceTimersByTimeAsync(0);
    const choose = async (label: string, value: string) => {
      const input = document.querySelector<HTMLSelectElement | HTMLInputElement>('[aria-label="' + label + '"]')!;
      input.value = value; input.dispatchEvent(new Event('change')); await vi.advanceTimersByTimeAsync(0);
    };
    await choose('프로젝트', 'one'); await choose('수집 대상', 'a'); await choose('자료 출처', 'youtube');
    await choose('처리 단계', 'stored'); await choose('시작일', '2026-10-01'); await choose('종료일', '2026-10-02');
    expect(query(load)).toMatchObject({ projects: ['one'], target_id: 'a', platform: 'youtube', stage: 'stored',
      since: new Date('2026-10-01T00:00:00').getTime() / 1000, until: new Date('2026-10-03T00:00:00').getTime() / 1000 });
  });

  it('keeps open evidence through unchanged polling and insertion of newer records', async () => {
    const load = vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(response([event(1)]))
      .mockResolvedValueOnce(response([]))
      .mockResolvedValueOnce(response([event(2)]));
    setup(load); await vi.advanceTimersByTimeAsync(0);
    const detail = document.querySelector<HTMLDetailsElement>('.collection-event-detail')!;
    detail.open = true; detail.dispatchEvent(new Event('toggle')); await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(2500);
    expect(document.querySelector('.collection-event-detail')).toBe(detail);
    await vi.advanceTimersByTimeAsync(2500);
    expect(document.querySelector<HTMLDetailsElement>('[data-sequence="1"]')!.open).toBe(true);
  });

  it('backs off unavailable and unconfigured stores and stops all reads after disposal', async () => {
    const load = vi.fn<typeof fetchSettingsAction>().mockResolvedValue({ ok: true, state: 'not_configured', items: [] });
    const history = setup(load); await vi.advanceTimersByTimeAsync(14999);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1); expect(load).toHaveBeenCalledTimes(2);
    history.dispose(); await vi.advanceTimersByTimeAsync(30000);
    expect(load).toHaveBeenCalledTimes(2); expect(vi.getTimerCount()).toBe(0);
    expect(document.querySelector('.collection-history')).toBeNull();
  });
});

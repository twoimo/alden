// @vitest-environment happy-dom
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {wireCollectionSchedule} from '../collection-schedule';
import type {fetchSettingsAction} from '../runtime';

const result=(extra:Record<string,unknown>={})=>({ok:true,state:'enabled',global_paused:false,abort_latched:false,
  time_zone:'KST',schedule:{state:'waiting'},targets:[{id:'target-a',label:'공개 영상',enabled:1,permitted:1,interval_seconds:21600,
    next_run:1791511200,last_state:'complete',last_finished:1791489600,paused:false}],...extra});
const owned:ReturnType<typeof wireCollectionSchedule>[]=[];
beforeEach(()=>{vi.useFakeTimers();document.body.innerHTML='<section id="settings-page-history"><header class="settings-page-heading"></header></section>';});
afterEach(()=>{for(const component of owned.splice(0))component.dispose();vi.useRealTimers();document.body.replaceChildren();});
const setup=(load:ReturnType<typeof vi.fn<typeof fetchSettingsAction>>)=>{const component=wireCollectionSchedule(load);owned.push(component);component.select('history');return component;};
describe('native collection schedule',()=>{
  it('shows room-scoped waiting after successful indexing and clears it after recovery',async()=>{
    const partial=result({targets:[{...result().targets[0],collection_status:'partial',collection_blocked_rooms:3}]});
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(partial).mockResolvedValue(result());
    setup(load);await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).toContain('일부 방 대기 3개');
    expect(document.querySelector<HTMLButtonElement>('.collection-run')!.disabled).toBe(false);
    await vi.advanceTimersByTimeAsync(15000);
    expect(document.body.textContent).not.toContain('일부 방 대기');
    expect(document.body.textContent).toContain('최근 완료');
  });
  it.each([0,-1,1.5,100001,'3',NaN])('does not treat malformed room counts as successful partial collection (%s)',async(count)=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue(result({targets:[{...result().targets[0],
      collection_status:'partial',collection_blocked_rooms:count,last_state:'failed'}]}));
    setup(load);await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).not.toContain('일부 방 대기');
    expect(document.body.textContent).toContain('최근 실패');
  });
  it('enables live input detection with the observed CAS revision and reads back actual monitor state',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(result({realtime:{enabled:false,revision:3,state:'disabled',inputs:[]}}))
      .mockResolvedValue(result({realtime:{enabled:true,revision:4,state:'watching',inputs:[]}}));
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const toggle=document.querySelector<HTMLButtonElement>('.collection-realtime-toggle')!;
    toggle.click();toggle.click();await vi.advanceTimersByTimeAsync(0);
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toEqual({operation:'realtime',target_id:null,enabled:true,expected_revision:3});
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(document.querySelector('.collection-realtime-status')?.textContent).toContain('변경 감지 중');
    expect(load.mock.calls.filter(([action])=>action==='collection-scheduler-control')).toHaveLength(1);
  });
  it('does not label a saved live policy as running without a fresh owned monitor',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue(result({realtime:{enabled:true,revision:1,state:'reconnecting',inputs:[{pending:1,error:'missing'}]}}));
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const status=document.querySelector('.collection-realtime-status')!;
    expect(status.textContent).toContain('감시기 연결 확인 필요');
    expect(status.textContent).toContain('원본 확인 필요 1');
    expect(status.textContent).not.toContain('변경 감지 중');
  });
  it('uses the observed source policy revision and changes no interval when toggled',async()=>{
    const first=result({targets:[{...result().targets[0],acquisition_supported:true,acquisition:{enabled:false,revision:4}}]});
    const changed=result({targets:[{...result().targets[0],acquisition_supported:true,acquisition:{enabled:true,revision:5},last_acquisition:'2026-10-09T08:00:00Z'}]});
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(first).mockResolvedValue(changed);
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const source=document.querySelector<HTMLButtonElement>('.collection-source-toggle')!;
    expect(source.hidden).toBe(false);source.click();source.click();await vi.advanceTimersByTimeAsync(0);
    expect(load.mock.calls.filter(call=>call[0]==='collection-scheduler-control')).toHaveLength(1);
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toEqual({operation:'acquisition',target_id:'target-a',enabled:true,expected_revision:4});
    expect(source.textContent).toContain('끄기');expect(document.body.textContent).toContain('원본 확인됨');
  });
  it('keeps source controls absent for unsupported targets and requires verified readback',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(result()).mockResolvedValue({ok:false});
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const source=document.querySelector<HTMLButtonElement>('.collection-source-toggle')!;
    expect(source.hidden).toBe(true);expect(source.disabled).toBe(true);
    source.click();expect(load.mock.calls).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(15000);expect(source.disabled).toBe(true);
  });
  it('queues one explicit manual request and disables duplicate clicks while waiting',async()=>{
    const pending=result({targets:[{...result().targets[0],manual_pending:true}]});
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(result()).mockResolvedValue(pending);
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const button=document.querySelector<HTMLButtonElement>('.collection-run')!;
    button.click();button.click();await vi.advanceTimersByTimeAsync(0);
    const calls=load.mock.calls.filter(call=>call[0]==='collection-scheduler-control');expect(calls).toHaveLength(1);
    expect(calls[0][1]?.explicitOptIn).toBe(true);
    expect(JSON.parse(calls[0][1]!.query!)).toMatchObject({operation:'run',target_id:'target-a',request_id:expect.stringMatching(/^[\da-f-]{36}$/)});
    expect(button.disabled).toBe(true);expect(button.textContent).toBe('대기 중');
    await vi.advanceTimersByTimeAsync(15000);expect(load.mock.calls.filter(call=>call[0]==='collection-scheduler-control')).toHaveLength(1);
  });

  it('retains a draft across polling and sends the original observed interval only when saved',async()=>{
    const changed=result({targets:[{...result().targets[0],interval_seconds:43200}]});
    const saved=result({targets:[{...result().targets[0],interval_seconds:10800}]});
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(result()).mockResolvedValueOnce(changed).mockResolvedValue(saved);
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const select=document.querySelector<HTMLSelectElement>('.collection-interval select')!;
    select.value='10800';select.dispatchEvent(new Event('change'));
    await vi.advanceTimersByTimeAsync(15000);expect(select.value).toBe('10800');
    expect(load.mock.calls.filter(call=>call[0]==='collection-scheduler-control')).toHaveLength(0);
    document.querySelector<HTMLButtonElement>('.collection-interval-save')!.click();await vi.advanceTimersByTimeAsync(0);
    expect(JSON.parse(load.mock.calls.at(-1)![1]!.query!)).toEqual({operation:'interval',target_id:'target-a',interval_seconds:10800,expected_interval:21600});
    expect(select.dataset.expected).toBe('10800');expect(document.querySelector<HTMLButtonElement>('.collection-interval-save')!.disabled).toBe(true);
  });

  it.each([{global_paused:true},{abort_latched:true},
    {targets:[{...result().targets[0],paused:true}]},{targets:[{...result().targets[0],permitted:0}]}])
  ('keeps manual execution disabled while its execution gates are closed',async(extra)=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue(result(extra));
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const button=document.querySelector<HTMLButtonElement>('.collection-run')!;expect(button.disabled).toBe(true);
    button.click();expect(load.mock.calls).toHaveLength(1);
  });
  it('reads on history visibility and sends exactly one deliberate target control without starting work',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue(result());
    setup(load);await vi.advanceTimersByTimeAsync(0);
    expect(load.mock.calls[0][0]).toBe('collection-scheduler-status');expect(document.body.textContent).toContain('6시간 간격');
    document.querySelector<HTMLButtonElement>('.collection-schedule-targets button')!.click();await vi.advanceTimersByTimeAsync(0);
    expect(load.mock.calls[1]).toEqual(['collection-scheduler-control',{query:JSON.stringify({operation:'pause',target_id:'target-a'}),explicitOptIn:true}]);
    await vi.advanceTimersByTimeAsync(15000);
    expect(load.mock.calls.filter(call=>call[0]==='collection-scheduler-control')).toHaveLength(1);
  });
  it('drops a hidden in-flight response and resumes one reader with no hidden timer',async()=>{
    let resolve!:(value:Record<string,unknown>)=>void;
    const load=vi.fn<typeof fetchSettingsAction>().mockImplementationOnce(()=>new Promise(done=>{resolve=done;})).mockResolvedValue(result());
    const component=setup(load);component.visible(false);resolve(result());await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).not.toContain('공개 영상');expect(vi.getTimerCount()).toBe(0);
    component.visible(true);await vi.advanceTimersByTimeAsync(0);expect(load).toHaveBeenCalledTimes(2);expect(vi.getTimerCount()).toBe(1);
    component.select('memory');await vi.advanceTimersByTimeAsync(30000);expect(load).toHaveBeenCalledTimes(2);
  });
  it('never allows schedule resume to stand in for emergency resume',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue(result({abort_latched:true,global_paused:true}));
    setup(load);await vi.advanceTimersByTimeAsync(0);
    expect(document.body.textContent).toContain('비상 중단');
    expect(document.querySelector<HTMLButtonElement>('.collection-schedule-global')!.disabled).toBe(true);
    expect(document.querySelector<HTMLButtonElement>('.collection-schedule-targets button')!.disabled).toBe(true);
    expect(load).toHaveBeenCalledTimes(1);
  });
  it('reads back an uncertain write before allowing another control, without resending it',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValueOnce(result()).mockResolvedValueOnce(null).mockResolvedValue(result({global_paused:true}));
    setup(load);await vi.advanceTimersByTimeAsync(0);
    const global=document.querySelector<HTMLButtonElement>('.collection-schedule-global')!;global.click();await vi.advanceTimersByTimeAsync(0);
    expect(global.disabled).toBe(true);expect(document.body.textContent).toContain('변경 결과를 확인하지 못');
    document.querySelector<HTMLButtonElement>('.collection-schedule-heading button:last-child')!.click();await vi.advanceTimersByTimeAsync(0);
    expect(global.textContent).toBe('정기 확인 재개');expect(global.disabled).toBe(false);
    expect(load.mock.calls.filter(call=>call[0]==='collection-scheduler-control')).toHaveLength(1);
  });
  it('retains a focused target control across unchanged status polling and cancels after disposal',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue(result());const component=setup(load);await vi.advanceTimersByTimeAsync(0);
    const button=document.querySelector<HTMLButtonElement>('.collection-schedule-targets button')!;button.focus();
    await vi.advanceTimersByTimeAsync(15000);expect(document.activeElement).toBe(button);
    component.dispose();const calls=load.mock.calls.length;await vi.advanceTimersByTimeAsync(30000);
    expect(load).toHaveBeenCalledTimes(calls);expect(vi.getTimerCount()).toBe(0);
  });
});

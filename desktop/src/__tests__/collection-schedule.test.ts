// @vitest-environment happy-dom
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {wireCollectionSchedule} from '../collection-schedule';
import type {fetchSettingsAction} from '../runtime';

const result=(extra:Record<string,unknown>={})=>({ok:true,state:'enabled',global_paused:false,abort_latched:false,
  time_zone:'KST',schedule:{state:'waiting'},targets:[{id:'target-a',label:'공개 영상',enabled:1,interval_seconds:21600,
    next_run:1791511200,last_state:'complete',last_finished:1791489600,paused:false}],...extra});
const owned:ReturnType<typeof wireCollectionSchedule>[]=[];
beforeEach(()=>{vi.useFakeTimers();document.body.innerHTML='<section id="settings-page-history"><header class="settings-page-heading"></header></section>';});
afterEach(()=>{for(const component of owned.splice(0))component.dispose();vi.useRealTimers();document.body.replaceChildren();});
const setup=(load:ReturnType<typeof vi.fn<typeof fetchSettingsAction>>)=>{const component=wireCollectionSchedule(load);owned.push(component);component.select('history');return component;};
describe('native collection schedule',()=>{
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

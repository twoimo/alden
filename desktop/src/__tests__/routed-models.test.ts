// @vitest-environment happy-dom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RoutedModels } from '../routed-models';
import { settingsMarkup } from '../ui';
import type { fetchSettingsAction } from '../runtime';

const gemini='google-antigravity/gemini-3.8-flash';
const payload={ok:true,model:gemini,reasoning_effort:'high',mode:'manual',models:[
  {id:gemini,label:'agy/gemini-3.8-flash',provider:'google-antigravity',local:false,efforts:['low','medium','high'],default_effort:'medium',status:'available',selectable:true},
  {id:'gpt-6-astra',label:'GPT-6 Astra',provider:'openai',local:false,efforts:['high'],default_effort:'high',status:'connected',selectable:true},
  {id:'mlx/Qwen',label:'Qwen',provider:'mlx',local:true,efforts:['high'],default_effort:'high',status:'memory',selectable:false},
]};
let controller:RoutedModels;
beforeEach(()=>{document.body.innerHTML=settingsMarkup();});
afterEach(()=>{controller?.dispose();vi.restoreAllMocks();});
function mount(fail=false){
  const load=vi.fn<typeof fetchSettingsAction>(async(action,input)=>action==='routed-models'?payload:
    fail?{ok:false,reason:'local_model_not_ready'}:{ok:true,stored:true,...JSON.parse(input!.query!),action});
  controller=new RoutedModels(load);return load;
}
describe('live model choices and distinct availability',()=>{
  it('discloses a partial catalog, withdraws stale remote health and preserves the saved choice and effort',async()=>{
    const native={id:'mlx/ddalcu/Qwen3.8-27B-MLX-Serve-4bit',label:'Native Qwen',provider:'mlx',
      local:true,efforts:[],default_effort:null,status:'running',selectable:true};
    const load=vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(payload)
      .mockResolvedValueOnce({...payload,partial:true,catalog_error:'routed_llm_authentication',models:[native]})
      .mockResolvedValueOnce(payload);
    controller=new RoutedModels(load);await controller.refresh();
    const search=document.getElementById('routed-model-search') as HTMLInputElement;
    search.value='Native';
    await controller.refresh();
    expect(document.getElementById('routed-model-name')!.textContent).toBe(gemini);
    const effort=document.getElementById('routed-model-effort') as HTMLSelectElement;
    expect(effort.value).toBe('high');expect(effort.disabled).toBe(true);
    expect(search.value).toBe('Native');
    expect(document.getElementById('routed-model-indicator')!.getAttribute('data-state')).toBe('unavailable');
    expect(document.getElementById('routed-local-status')!.textContent).toBe('로컬 · 1개 실행 중');
    expect(document.getElementById('routed-model-status')!.textContent).toContain('확인된 로컬 모델만 표시');
    expect(document.getElementById('routed-model-status')!.textContent).toContain('현재 선택은 유지');
    expect(document.querySelectorAll('button[data-route-model]')).toHaveLength(1);
    expect(document.querySelector<HTMLButtonElement>('[data-route-model="'+native.id+'"]')!.disabled).toBe(false);
    expect(document.querySelector('#routed-model-list .model-availability')!.getAttribute('data-state')).toBe('running');
    await controller.refresh();
    expect(effort.value).toBe('high');expect(effort.disabled).toBe(false);
    expect(document.getElementById('routed-model-indicator')!.getAttribute('data-state')).toBe('available');
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
  });
  it('labels unsupported chat capability without assuming the model is an audio model',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue({...payload,models:[
      {...payload.models[2],status:'not_chat'},
    ]});
    controller=new RoutedModels(load);await controller.refresh();
    expect(document.querySelector('#routed-model-list .model-availability')!.textContent).toBe('대화 지원 안 함');
    expect(document.querySelector<HTMLButtonElement>('button[data-route-model]')!.disabled).toBe(true);
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
  });
  it('keeps automatic mode visibly unconfirmed in a partial catalog despite an available local candidate',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>().mockResolvedValue({...payload,mode:'automatic',
      partial:true,automatic_model:null,catalog_error:'routed_llm_unavailable',models:[
        {...payload.models[2],id:'mlx/Qwen3.8-27B',status:'available',selectable:true},
      ]});
    controller=new RoutedModels(load);await controller.refresh();
    expect(document.getElementById('routed-model-name')!.textContent).toBe('자동 · '+gemini);
    expect(document.getElementById('routed-model-auto')!.getAttribute('aria-pressed')).toBe('true');
    expect(document.getElementById('routed-model-indicator')!.getAttribute('data-state')).toBe('unavailable');
    expect(document.getElementById('routed-local-status')!.textContent).toBe('로컬 · 1개 실행 중');
    expect(document.getElementById('routed-model-status')!.textContent).toContain('확인된 로컬 모델만 표시');
    expect((document.getElementById('routed-model-effort') as HTMLSelectElement).value).toBe('high');
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
  });
  it('ends the initial loading labels on catalog failure without changing a saved model',async()=>{
    const load=vi.fn<typeof fetchSettingsAction>(async()=>({ok:false}));
    controller=new RoutedModels(load);await controller.refresh();
    expect(document.getElementById('routed-model-name')!.textContent).toBe('모델 확인 불가');
    expect(document.getElementById('routed-model-indicator')!.textContent).toBe('연결 확인 필요');
    expect(document.getElementById('routed-local-status')!.textContent).toBe('로컬 · 상태 확인 불가');
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
  });
  it.each(['response','rejection'] as const)('withdraws open and searched menu health after refresh failure and restores it only on fresh success (%s)',async failure=>{
    let available=true;
    const catalog={...payload,models:[...payload.models,
      {id:'mlx/Ready',label:'Ready local',provider:'mlx',local:true,efforts:['high'],default_effort:'high',status:'running',selectable:true},
    ]};
    const load=vi.fn<typeof fetchSettingsAction>(async()=>{
      if(available)return catalog;
      if(failure==='rejection')throw Error('offline');
      return {ok:false};
    });
    controller=new RoutedModels(load);
    const picker=document.getElementById('routed-model-picker') as HTMLDetailsElement;
    picker.open=true;picker.dispatchEvent(new Event('toggle'));
    const buttons=()=>Array.from(document.querySelectorAll<HTMLButtonElement>('button[data-route-model]'));
    const statuses=()=>Array.from(document.querySelectorAll<HTMLElement>('#routed-model-list .model-availability'));
    const choices=()=>buttons().map(button=>({id:button.dataset.routeModel,disabled:button.disabled,pressed:button.getAttribute('aria-pressed')}));
    await vi.waitFor(()=>expect(buttons()).toHaveLength(4));
    const name=document.getElementById('routed-model-name')!.textContent;
    const effort=(document.getElementById('routed-model-effort') as HTMLSelectElement).value;
    const beforeChoices=choices();
    const beforeHealth=statuses().map(status=>({state:status.dataset.state,text:status.textContent,title:status.title}));
    expect(beforeHealth.map(status=>status.state)).toEqual(['available','connected','memory','running']);
    expect(beforeHealth[0].title).toBe('최근 5분 안에 실제 응답을 확인했습니다.');
    expect(document.getElementById('routed-local-status')!.textContent).toBe('로컬 · 1개 실행 중');
    available=false;await controller.refresh();
    const expectUnconfirmed=()=>{
      expect(picker.open).toBe(true);
      expect(document.getElementById('routed-model-name')!.textContent).toBe(name);
      expect((document.getElementById('routed-model-effort') as HTMLSelectElement).value).toBe(effort);
      expect(document.getElementById('routed-model-indicator')!.getAttribute('data-state')).toBe('unavailable');
      expect(document.getElementById('routed-local-status')!.getAttribute('data-state')).toBe('unavailable');
      for(const status of statuses()){
        expect(status.dataset.state).toBe('unavailable');
        expect(status.textContent).toBe('현재 상태 확인 불가');
        expect(status.title).toBe('현재 상태 확인 불가');
      }
    };
    expect(buttons()).toHaveLength(4);expectUnconfirmed();expect(choices()).toEqual(beforeChoices);
    const reads=load.mock.calls.length;
    const search=document.getElementById('routed-model-search') as HTMLInputElement;
    search.value='Astra';search.dispatchEvent(new Event('input'));
    expect(buttons()).toHaveLength(1);expectUnconfirmed();
    expect(choices()).toEqual([beforeChoices[1]]);
    search.value='';search.dispatchEvent(new Event('input'));
    expect(buttons()).toHaveLength(4);expectUnconfirmed();expect(choices()).toEqual(beforeChoices);
    expect(load.mock.calls).toHaveLength(reads);
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
    available=true;await controller.refresh();
    expect(document.getElementById('routed-model-indicator')!.textContent).toBe('사용 가능');
    expect(document.getElementById('routed-local-status')!.textContent).toBe('로컬 · 1개 실행 중');
    expect(statuses().map(status=>({state:status.dataset.state,text:status.textContent,title:status.title}))).toEqual(beforeHealth);
    expect(choices()).toEqual(beforeChoices);
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
  });

  it('keeps explicit selection working after catalog failure without reconfirming cached health',async()=>{
    let available=true;
    const load=vi.fn<typeof fetchSettingsAction>(async(action,input)=>action==='routed-models'?
      available?payload:{ok:false}:{ok:true,stored:true,...JSON.parse(input!.query!)});
    controller=new RoutedModels(load);await controller.refresh();
    available=false;await controller.refresh();
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
    document.querySelector<HTMLButtonElement>('[data-route-model="gpt-6-astra"]')!.click();
    await vi.waitFor(()=>expect(document.getElementById('routed-model-name')!.textContent).toBe('GPT-6 Astra'));
    expect(load.mock.calls.filter(([action])=>action==='routed-model-set')).toEqual([
      ['routed-model-set',{query:JSON.stringify({model:'gpt-6-astra',reasoning_effort:'high',mode:'manual'})}],
    ]);
    expect((document.getElementById('routed-model-effort') as HTMLSelectElement).value).toBe('high');
    expect(document.getElementById('routed-model-indicator')!.getAttribute('data-state')).toBe('unavailable');
    expect(document.getElementById('routed-local-status')!.getAttribute('data-state')).toBe('unavailable');
    for(const status of document.querySelectorAll<HTMLElement>('#routed-model-list .model-availability')){
      expect(status.dataset.state).toBe('unavailable');
      expect(status.textContent).toBe('현재 상태 확인 불가');
      expect(status.title).toBe('현재 상태 확인 불가');
    }
  });
  it.each(['disposal','newer refresh'] as const)('ignores a pending refresh rejection after %s',async next=>{
    let rejectRead!:(reason:Error)=>void;
    const load=vi.fn<typeof fetchSettingsAction>()
      .mockResolvedValueOnce(payload)
      .mockImplementationOnce(()=>new Promise((_,reject)=>{rejectRead=reject;}))
      .mockResolvedValueOnce({...payload,model:'gpt-6-astra'});
    controller=new RoutedModels(load);await controller.refresh();
    const pending=controller.refresh();
    if(next==='disposal')controller.dispose();else await controller.refresh();
    const before=document.getElementById('routed-model-picker')!.outerHTML;
    rejectRead(Error('offline'));await pending;
    expect(document.getElementById('routed-model-picker')!.outerHTML).toBe(before);
    expect(load.mock.calls.every(([action])=>action==='routed-models')).toBe(true);
  });
  it('lists the actual catalog, supports advertised effort and disables unavailable local weights',async()=>{
    mount();await controller.refresh();
    expect(document.querySelectorAll('button[data-route-model]')).toHaveLength(3);
    expect(document.querySelector<HTMLButtonElement>('[data-route-model="mlx/Qwen"]')!.disabled).toBe(true);
    expect(document.getElementById('routed-model-indicator')!.textContent).toBe('사용 가능');
    expect((document.getElementById('routed-model-effort') as HTMLSelectElement).value).toBe('high');
    expect(document.getElementById('routed-model-list')!.textContent).toContain('메모리 부족');
  });
  it('makes Automatic an explicit save and keeps Astra as a manual choice',async()=>{
    const load=mount();await controller.refresh();document.getElementById('routed-model-auto')!.click();
    await vi.waitFor(()=>expect(load).toHaveBeenCalledWith('routed-model-set',{query:JSON.stringify({model:gemini,reasoning_effort:'high',mode:'automatic'})}));
    expect(load.mock.calls.filter(([action])=>action==='routed-model-set')).toHaveLength(1);
    await vi.waitFor(()=>expect(document.getElementById('routed-model-name')!.textContent).toContain('자동'));
  });
  it('preserves the displayed choice when a selection is rejected',async()=>{
    mount(true);await controller.refresh();const before=document.getElementById('routed-model-name')!.textContent;
    document.querySelector<HTMLButtonElement>('[data-route-model="gpt-6-astra"]')!.click();
    await vi.waitFor(()=>expect(document.getElementById('routed-model-status')!.textContent).toContain('현재 선택은 유지'));
    expect(document.getElementById('routed-model-name')!.textContent).toBe(before);
  });
  it('keeps an open menu bounded after resize and scroll without extra catalog reads',async()=>{
    const load=mount();await controller.refresh();
    const picker=document.getElementById('routed-model-picker') as HTMLDetailsElement;
    const menu=picker.querySelector<HTMLElement>('.routed-model-menu')!;
    let bounds=new DOMRect(200,582,400,40),height=728;
    vi.spyOn(picker,'getBoundingClientRect').mockImplementation(()=>bounds);
    vi.spyOn(window,'innerHeight','get').mockImplementation(()=>height);
    picker.open=true;picker.dispatchEvent(new Event('toggle'));
    await vi.waitFor(()=>expect(menu.style.maxHeight).toBe('430px'));
    const reads=load.mock.calls.length;
    bounds=new DOMRect(161,350,350,44);height=648;
    window.dispatchEvent(new Event('resize'));
    expect(picker.dataset.placement).toBe('above');expect(menu.style.maxHeight).toBe('338px');
    bounds=new DOMRect(161,100,350,44);
    document.dispatchEvent(new Event('scroll'));
    expect(picker.dataset.placement).toBe('below');expect(menu.style.maxHeight).toBe('430px');
    expect(load.mock.calls.length).toBe(reads);
  });
  it('does not reflow the menu after disposal',async()=>{
    mount();await controller.refresh();
    const picker=document.getElementById('routed-model-picker') as HTMLDetailsElement;
    const menu=picker.querySelector<HTMLElement>('.routed-model-menu')!;
    let bounds=new DOMRect(200,582,400,40);
    vi.spyOn(picker,'getBoundingClientRect').mockImplementation(()=>bounds);
    vi.spyOn(window,'innerHeight','get').mockReturnValue(728);
    picker.open=true;picker.dispatchEvent(new Event('toggle'));
    await vi.waitFor(()=>expect(menu.style.maxHeight).toBe('430px'));
    controller.dispose();bounds=new DOMRect(161,100,350,44);
    window.dispatchEvent(new Event('resize'));document.dispatchEvent(new Event('scroll'));
    expect(picker.dataset.placement).toBe('above');expect(menu.style.maxHeight).toBe('430px');
  });
});

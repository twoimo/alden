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

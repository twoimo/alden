// @vitest-environment happy-dom
import { beforeAll, expect, it, vi } from 'vitest';
import type { VisibilityHandler } from '../core/lifecycle-wiring';
import { parseRuntimeSnapshot } from '../contracts';

vi.mock('@tauri-apps/api/core',()=>({invoke:vi.fn(async()=>null)}));
vi.mock('@tauri-apps/api/app',()=>({getVersion:async()=> '0.3.91'}));
vi.mock('../knowledge/hologram',()=>({KnowledgeHologram:class {
  requested=false;disposed=false;renderCount=0;
  currentGraph:any;currentView:any;canGoBack=false;
  constructor(_canvas:unknown,graph:any){this.currentGraph=graph;this.currentView={...graph,hops:0,focusId:null};}
  get lifecycleDiagnostics(){return{animationRequested:this.requested,emergencyPaused:false,disposed:this.disposed};}
  get navigationTargets(){return{camera:[0,0,5],lookAt:[0,0,0]};}
  get semanticDiagnostics(){return{};}
  get nodeActivityDiagnostics(){return{};}
  get reducedMotionEnabled(){return false;}
  start(){this.requested=true;}
  stop(){this.requested=false;}
  diagnostics(){return{running:this.requested,pendingFrame:this.requested,renderCount:0,lastRenderAtMs:null};}
  dispose(){this.stop();this.disposed=true;}
  setSignals(){}setActivity(){}setEmergency(){}setReducedMotion(){}setLayoutAffinity(){}
  showNodeActivity(){}clearNodeActivity(){}
  replaceGraph(graph:any){this.currentGraph=graph;this.currentView={...graph,hops:0,focusId:null};}
}}));

let bootSettings:typeof import('../main').bootSettings;
let setupKnowledgeGraph:typeof import('../main').setupKnowledgeGraph;
beforeAll(async()=>{
  document.body.innerHTML='<div id="app"></div>';
  window.history.replaceState({},'', '/?view=settings');
  const main=await import('../main');bootSettings=main.bootSettings;setupKnowledgeGraph=main.setupKnowledgeGraph;
  await vi.waitFor(()=>expect(document.querySelector<HTMLElement>('#app')?.dataset.state).not.toBe('loading'));
  window.dispatchEvent(new Event('pagehide'));
});

it('keeps a shown graph updating after late blur, while native hide, inactive page and close stop it',async()=>{
  const handlers:VisibilityHandler[]=[];
  const snapshot=parseRuntimeSnapshot({available:true,rooms:[],jobs:[],context_sync:{mode:'async',waited:false}});
  const graph={ok:true,nodes:[{id:'n1',label:'Saved note'}],edges:[]};
  const original=Object.getOwnPropertyDescriptor(document,'visibilityState');
  Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});
  const state=()=> (window as any).__knowledgeRenderDiagnostics.lifecycle;
  const signal=(visible:boolean)=>handlers.forEach(handler=>handler(visible));
  try {
    await bootSettings({loadSnapshot:async()=>snapshot,loadAction:async action=>action==='knowledge-graph'?graph:null,
      loadEmergency:async()=>null,subscribeEmergency:null,wireVoice:()=>undefined,
      invokeCommand:async<T>()=>null as T,subscribeVisibility:async handler=>{handlers.push(handler);return()=>undefined;},
      readVisibility:async()=>false});
    await vi.waitFor(()=>expect(handlers.length).toBe(3));
    await new Promise(resolve=>setTimeout(resolve,0));
    expect(state().animationRequested).toBe(false);
    signal(true);expect(state().animationRequested).toBe(true);
    window.dispatchEvent(new Event('blur'));
    expect(state().animationRequested).toBe(true);
    signal(false);expect(state().animationRequested).toBe(false);
    window.dispatchEvent(new Event('focus'));expect(state().animationRequested).toBe(false);
    signal(true);expect(state().animationRequested).toBe(true);
    document.querySelector<HTMLButtonElement>('[data-settings-view="settings"]')!.click();
    expect(state().animationRequested).toBe(false);
    signal(true);expect(state().animationRequested).toBe(false);
    document.querySelector<HTMLButtonElement>('[data-settings-view="memory"]')!.click();
    expect(state().animationRequested).toBe(true);
    window.dispatchEvent(new Event('pagehide'));signal(true);
    expect(state()).toMatchObject({animationRequested:false,disposed:true});
  }finally{
    window.dispatchEvent(new Event('pagehide'));
    if(original)Object.defineProperty(document,'visibilityState',original);
    else delete (document as any).visibilityState;
  }
});

it.each([
  [{ok:true,nodes:[]},'empty','이 범위에 표시할 지식이 없습니다.'],
  [{ok:true,nodes:[],partial:true},'partial','일부 출처를 확인하지 못했습니다.'],
  [{ok:true,nodes:[],stale:true},'stale','현재 원본과의 일치를 확인하지 못했습니다.'],
  [{nodes:[]},'unavailable','대화의 연결 정보를 확인할 수 없습니다.'],
] as const)('keeps initial header classification consistent with the map: %j',async(payload,state,summary)=>{
  const {settingsMarkup}=await import('../ui');document.body.innerHTML=settingsMarkup();
  const snapshot=parseRuntimeSnapshot({available:false,rooms:[],jobs:[]});
  expect(await setupKnowledgeGraph(payload,snapshot)).toBeNull();
  expect(document.getElementById('knowledge-map-status')!.dataset.state).toBe(state);
  expect(document.getElementById('knowledge-summary')!.textContent).toBe(summary);
  expect(document.getElementById('knowledge-mode')!.textContent).not.toBe('준비 중');
});

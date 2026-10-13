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
beforeAll(async()=>{
  document.body.innerHTML='<div id="app"></div>';
  window.history.replaceState({},'', '/?view=settings');
  const main=await import('../main');bootSettings=main.bootSettings;
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

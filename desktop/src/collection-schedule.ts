import { fetchSettingsAction } from './runtime';
import type { SettingsPage } from './settings-navigation';

type Row = Record<string, unknown>;
type Operation = 'pause' | 'resume' | 'interval' | 'run' | 'acquisition';
type Entry = {row:HTMLElement;label:HTMLElement;next:HTMLElement;last:HTMLElement;button:HTMLButtonElement;
  run:HTMLButtonElement;source:HTMLButtonElement;interval:HTMLSelectElement;save:HTMLButtonElement;dirty:boolean};
const records = (value: unknown): Row[] => Array.isArray(value)
  ? value.filter(item => item && typeof item === 'object' && !Array.isArray(item)) : [];
const time = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0
  ? new Date(value * 1000).toLocaleString('ko-KR', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '미확인';
const lastStates: Record<string,string> = {complete:'완료',failed:'실패',pending:'검색 반영 대기',paused:'중단',
  blocked:'확인 필요',busy:'다른 작업 대기',interrupted:'중단 후 복구 대기',running:'처리 중 기록'};

export function wireCollectionSchedule(load: typeof fetchSettingsAction = fetchSettingsAction) {
  const host = document.getElementById('settings-page-history');
  if (!host) return {select(_page:SettingsPage){},visible(_flag:boolean){},dispose(){}};
  const create = (tag:string, text='') => {const node=document.createElement(tag);node.textContent=text;return node;};
  const section=create('section');section.className='collection-schedule';section.setAttribute('aria-label','정기 수집 일정');
  const header=create('div');header.className='collection-schedule-heading';
  const title=create('h2','정기 수집'), global=document.createElement('button'), reload=document.createElement('button');
  global.type=reload.type='button';global.className='collection-schedule-global';reload.textContent='새로고침';
  const status=create('p','일정을 확인합니다.');status.className='collection-schedule-status';status.setAttribute('aria-live','polite');
  const note=create('p');note.className='collection-schedule-note';
  const list=create('ul');list.className='collection-schedule-targets';
  header.append(title,global,reload);section.append(header,status,list,note);
  (host.querySelector('.collection-history') ?? host.querySelector('.settings-page-heading'))?.after(section);
  if(!section.parentElement)host.prepend(section);
  const entries=new Map<string,Entry>();
  const listeners=new AbortController();let page:SettingsPage='memory', visible=true, dead=false, busy=false, epoch=0;
  let timer:ReturnType<typeof setTimeout>|null=null, verified=false, aborted=false, globalPaused=false;
  const stop=()=>{if(timer!==null)clearTimeout(timer);timer=null;};
  const active=()=>!dead&&visible&&page==='history';
  const controls=()=>{
    global.disabled=busy||!verified||aborted||entries.size===0;
    reload.disabled=busy;
    for(const entry of entries.values()){
      const disabled=busy||!verified||aborted||entry.button.dataset.excluded==='true';
      entry.button.disabled=disabled;entry.interval.disabled=disabled;entry.save.disabled=disabled||!entry.dirty;
      entry.source.disabled=disabled||entry.source.hidden;
      entry.run.disabled=disabled||globalPaused||entry.run.dataset.blocked==='true';
    }
  };
  function render(data:Row) {
    verified=true;aborted=data.abort_latched===true;globalPaused=data.global_paused===true;
    const schedule=data.schedule&&typeof data.schedule==='object'?data.schedule as Row:{};
    const targets=records(data.targets).filter(row=>typeof row.id==='string').slice(0,200);
    const total=Number.isSafeInteger(data.total_targets)&&Number(data.total_targets)>=targets.length?Number(data.total_targets):targets.length;
    const count=total>targets.length?`${targets.length}/${total}개 대상 표시`:`${total}개 대상`;
    section.dataset.state=String(data.state??'unverified');section.dataset.targetCount=String(targets.length);
    const live=['waiting','running'].includes(String(schedule.state));
    status.textContent=aborted?'비상 중단 상태입니다. 수집 재개 전 비상 중단을 해제하세요.'
      :globalPaused?'전체 수집이 중지돼 있습니다.'
      :!targets.length?'등록된 수집 대상이 없습니다.'
      :live?`자동 확인 켜짐 · ${count} · ${String(data.time_zone??'현지 시간')}`
      :`일정 실행 확인 필요 · ${count}`;
    note.textContent=live?'로그인한 macOS 세션에서 1분마다 예정된 대상을 확인합니다. 대화·음성이 진행 중이면 수집을 미룹니다.'
      :'저장된 예정 시각입니다. 자동 실행 작업의 설치·연결 상태를 확인해야 합니다.';
    global.textContent=globalPaused?'정기 확인 재개':'정기 확인 중지';
    const ids=new Set(targets.map(row=>String(row.id)));
    for(const [id,entry] of entries)if(!ids.has(id)){entry.row.remove();entries.delete(id);}
    for(const target of targets){
      const id=String(target.id);let entry=entries.get(id);
      if(!entry){
        const row=create('li'),label=create('strong'),next=create('span'),last=create('span'),button=document.createElement('button');
        const actions=create('div');actions.className='collection-target-actions';
        const run=document.createElement('button');run.type='button';run.className='collection-run';
        const editor=document.createElement('details');editor.className='collection-interval';
        editor.append(create('summary','주기'));
        const fields=create('div'),interval=document.createElement('select'),save=document.createElement('button');
        save.type='button';save.textContent='저장';save.className='collection-interval-save';
        for(const [seconds,text] of [[900,'15분'],[3600,'1시간'],[10800,'3시간'],[21600,'6시간'],[43200,'12시간'],[86400,'1일'],[259200,'3일'],[604800,'7일'],[2592000,'30일']] as const){
          const option=document.createElement('option');option.value=String(seconds);option.textContent=text;interval.append(option);
        }
        const source=document.createElement('button');source.type='button';source.className='collection-source-toggle';source.hidden=true;
        fields.append(interval,save);editor.append(fields,source);button.type='button';actions.append(button,run,editor);
        row.append(label,next,last,actions);list.append(row);entry={row,label,next,last,button,run,interval,save,source,dirty:false};entries.set(id,entry);
        button.addEventListener('click',()=>void change(button.dataset.operation==='resume'?'resume':'pause',id),{signal:listeners.signal});
        run.addEventListener('click',()=>{if(!run.disabled)void change('run',id,{request_id:crypto.randomUUID()});},{signal:listeners.signal});
        interval.addEventListener('change',()=>{const current=entries.get(id);if(current){current.dirty=true;controls();}},{signal:listeners.signal});
        save.addEventListener('click',()=>{if(!save.disabled)void change('interval',id,{interval_seconds:Number(interval.value),expected_interval:Number(interval.dataset.expected)});},{signal:listeners.signal});
        source.addEventListener('click',()=>{if(!source.disabled)void change('acquisition',id,
          {enabled:source.dataset.enabled!=='true',expected_revision:Number(source.dataset.revision)});},{signal:listeners.signal});
      }
      entry.label.textContent=String(target.label??'수집 대상');
      const hours=Number(target.interval_seconds)/3600;
      const interval=Number.isFinite(hours)&&hours>0?`${hours<1?Math.round(hours*60)+'분':hours+'시간'} 간격`:'간격 미확인';
      const paused=target.paused===true, blocked=target.blocked===true;
      const retry=Number(target.retry_at)||0;
      const nextAt=target.last_state&&target.last_state!=='complete'&&retry>0?retry:Number(target.next_run)||0;
      entry.next.textContent=`${interval} · ${target.permitted===0?'접근 확인 필요':target.enabled===0?'대상 제외':aborted||globalPaused||paused?'중지됨':blocked?'확인 후 재개':target.manual_pending===true?'재실행 대기':nextAt<=Date.now()/1000?'실행 대기':'다음 '+time(nextAt)}`;
      entry.last.textContent=`최근 ${lastStates[String(target.last_state)]??'결과 미확인'} · ${time(target.last_finished??target.last_success)}`;
      const acquisition=target.acquisition&&typeof target.acquisition==='object'?target.acquisition as Row:{};
      entry.source.hidden=target.acquisition_supported!==true&&acquisition.enabled!==true;
      entry.source.dataset.enabled=String(acquisition.enabled===true);entry.source.dataset.revision=String(acquisition.revision??0);
      entry.source.textContent=acquisition.enabled===true?'원본 자동 확인 끄기':'원본 자동 확인 켜기';
      entry.source.setAttribute('aria-label',String(target.label??'수집 대상')+' '+entry.source.textContent);
      entry.source.title='Aside가 연결된 동안 설정한 주기에 영상 정보와 자막을 새로 확인합니다.';
      if(acquisition.enabled===true)entry.last.textContent+=' · '+(target.last_stage==='acquisition'&&target.acquisition_error
        ?'원본 접근 확인 필요':target.last_acquisition?'원본 확인됨':'원본 확인 대기');
      entry.button.dataset.operation=paused||blocked?'resume':'pause';entry.button.textContent=paused||blocked?'재개':'중지';
      entry.button.dataset.excluded=String(target.enabled===0||target.permitted===0);
      entry.button.setAttribute('aria-label',String(target.label??'수집 대상')+' 수집 '+entry.button.textContent);
      entry.run.textContent=target.manual_pending===true?'대기 중':'지금 수집';
      entry.run.setAttribute('aria-label',String(target.label??'수집 대상')+' 지금 수집');
      entry.run.dataset.blocked=String(paused||blocked||target.manual_pending===true||target.permitted!==1);
      entry.interval.setAttribute('aria-label',String(target.label??'수집 대상')+' 수집 주기');
      entry.save.setAttribute('aria-label',String(target.label??'수집 대상')+' 수집 주기 저장');
      if(!entry.dirty){
        const value=String(target.interval_seconds);
        if(!Array.from(entry.interval.options).some(option=>option.value===value)){
          const option=document.createElement('option');option.value=value;option.textContent=interval.replace(' 간격','');entry.interval.append(option);
        }
        entry.interval.value=value;entry.interval.dataset.expected=value;
      }
    }
    controls();
  }
  async function request(operation?:Operation,targetId?:string,extra:Row={}) {
    if(!active()||busy)return;stop();busy=true;controls();const ticket=epoch;
    section.setAttribute('aria-busy','true');
    try{
      const data=await load(operation?'collection-scheduler-control':'collection-scheduler-status',operation?
        {query:JSON.stringify({operation,target_id:targetId??null,...extra}),explicitOptIn:true}:{});
      if(!active()||ticket!==epoch)return;
      if(data?.ok===true){if(operation==='interval'&&targetId){const entry=entries.get(targetId);if(entry)entry.dirty=false;}render(data);}
      else {verified=false;section.dataset.state='unavailable';status.textContent=operation?
        '변경 결과를 확인하지 못했습니다. 새로고침으로 확인하세요.':'일정을 확인하지 못했습니다. 잠시 후 다시 확인합니다.';}
    }catch{if(active()&&ticket===epoch){verified=false;section.dataset.state='unavailable';status.textContent='일정을 확인하지 못했습니다.';}}
    finally{
      busy=false;section.setAttribute('aria-busy','false');controls();
      if(active()){if(ticket!==epoch)void request();else timer=setTimeout(()=>void request(),15000);}
    }
  }
  async function change(operation:Operation,targetId?:string,extra:Row={}){if(verified&&!aborted)await request(operation,targetId,extra);}
  global.addEventListener('click',()=>void change(globalPaused?'resume':'pause'),{signal:listeners.signal});
  reload.addEventListener('click',()=>{for(const entry of entries.values())entry.dirty=false;void request();},{signal:listeners.signal});controls();
  return {
    select(next:SettingsPage){if(page===next)return;page=next;epoch++;stop();if(active())void request();},
    visible(flag:boolean){if(visible===flag)return;visible=flag;epoch++;stop();if(active())void request();},
    dispose(){dead=true;epoch++;stop();listeners.abort();section.remove();},
  };
}

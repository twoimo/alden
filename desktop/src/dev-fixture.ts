/** Explicit development-only synthetic data. Never included in native boot. */
import { unavailableSnapshot, type EmergencyState } from './contracts';
import type { fetchSettingsAction } from './runtime';
const at=Date.now()/1000;
const rooms=[{chat_id:'101',chat_name:'예시 · 디자인 이야기',last_updated_at:at},{chat_id:'202',chat_name:'예시 · 책을 읽는 사람들',last_updated_at:at-3600}];
let catalog=[{chat_id:101,title:rooms[0].chat_name,auto_reply:true,geeknews:false}];
export let emergency:EmergencyState={schemaVersion:1,epoch:0,latched:false,reason:'initial'};
const nodes=[{id:'root',label:'카카오톡',category:'collection',importance:100},{id:'people',label:'인물',category:'collection',importance:98},{id:'rooms',label:'대화방',category:'collection',importance:98},{id:'topics',label:'주제',category:'collection',importance:98},...Array.from({length:14},(_,i)=>({id:'person-'+i,label:['예시 민준','예시 서연','예시 윤서','예시 지우'][i%4]+(i>3?' '+i:''),category:'대화 상대',importance:70-i})),{id:'design',label:'디자인 이야기',category:'대화방',importance:88},{id:'books',label:'책을 읽는 사람들',category:'대화방',importance:83},{id:'writing',label:'글쓰기',category:'대화 주제',importance:81},{id:'ai',label:'AI와 일상',category:'대화 주제',importance:76}];
const edges=[...['people','rooms','topics'].map(target=>({source:'root',target,relation:'contains',weight:2})),...nodes.filter(n=>n.category!=='collection').map(n=>({source:n.category==='대화 상대'?'people':n.category==='대화방'?'rooms':'topics',target:n.id,relation:'contains',weight:1})),{source:'design',target:'person-0',relation:'participates_in',weight:1}];
export const action:typeof fetchSettingsAction=async(kind,input={})=>{
  const q=input.query?JSON.parse(input.query):{};
  if(kind==='knowledge-graph')return {ok:true,nodes,edges,stale:false,node_count:nodes.length,osk:{state:'ready',engine:'example',synced_at:at,pending:0,conflicts:0}};
  if(kind==='knowledge-graph-status')return {ok:true,stale:false};
  if(kind==='knowledge-graph-focus')return {ok:true,facts:['개발 화면의 예시 지식입니다. 실제 대화가 아닙니다.']};
  if(kind==='history-rooms')return {ok:true,account:'synthetic-preview',rooms};
  if(kind==='history-messages'){const high=q.before?Number(q.before)-1:1000;const low=Math.max(1,high-99);return {ok:true,anchor_log_id:'1000',total:1000,next_before:low>1?String(low):null,messages:Array.from({length:high-low+1},(_,i)=>({id:String(low+i),chat_id:input.chatId,author_id:'fixture',is_self:(low+i)%3===0,sender:(low+i)%3===0?'나':'예시 서연',text:(low+i)%4===0?'말씀하신 방향으로 자료를 정리했습니다.\n필요한 맥락부터 이어서 살펴보겠습니다.':'지난 대화의 내용도 날짜순으로 읽을 수 있으면 좋겠습니다. · 예시 '+(low+i),type:1,sent_at:at-(1000-low-i)*600}))};}
  if(kind==='voice-history-sessions')return {ok:true,items:[{id:'voice-demo',title:'예시 · 오늘 나눈 이야기',started_at:at,messages:2}]};
  if(kind==='voice-history-messages')return {ok:true,next:null,items:[{turn_id:1,role:'user',content:'오늘 이야기한 내용을 기억해 줘.',created_at:at-60},{turn_id:1,role:'assistant',content:'핵심 내용을 정리해 두었습니다.',created_at:at-50}]};
  if(kind==='db-sync-history')return {ok:true,current:{phase:'complete',updated_at:at},next:null,items:[{id:3,at,phase:'complete',details:{nodes:50}},{id:2,at:at-10,phase:'graphing',details:{nodes:50}},{id:1,at:at-14,phase:'collecting',details:{rooms:2}}]};
  if(kind==='room-catalog')return {ok:true,rooms:catalog};
  if(kind==='room-upsert'){const row={chat_id:Number(input.chatId),...q};catalog=[...catalog.filter(r=>r.chat_id!==row.chat_id),row];return {ok:true};}
  if(kind==='room-delete'){catalog=catalog.filter(r=>r.chat_id!==Number(input.chatId));return {ok:true,rooms:catalog};}
  return {ok:true};
};
export const snapshot=async()=>({...unavailableSnapshot('example'),available:true});
export const pause=async()=>{emergency={schemaVersion:1,epoch:emergency.epoch+1,latched:true,reason:'example_pause'};return emergency;};
export const resume=async()=>{emergency={schemaVersion:1,epoch:emergency.epoch+1,latched:false,reason:'human_resume'};return emergency;};

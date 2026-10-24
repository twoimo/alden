// Read-only synthetic experiment. No product state, network, models or writes.
import { SemanticLayoutEngine } from '../../desktop/src/knowledge/semantic-layout';
import { parseKnowledgeGraph } from '../../desktop/src/knowledge/graph-model';

const choose2 = (n: number) => n * (n - 1) / 2;
const includeInputs = process.argv.includes('--include-inputs');
const argumentInt = (name: string, fallback: number, max: number) => {
  const at=process.argv.indexOf(name);const value=at<0?fallback:Number(process.argv[at+1]);
  if(!Number.isSafeInteger(value)||value<1||value>max)throw new Error('invalid '+name);return value;
};
const seedStart=argumentInt('--seed-start',1,1_000_000),seedCount=argumentInt('--seed-count',5,30);
function ari(truth: number[], found: string[]): number {
  const rows = new Map<number, number>(), cols = new Map<string, number>(), cells = new Map<string, number>();
  truth.forEach((v, i) => {
    rows.set(v, (rows.get(v) ?? 0) + 1); cols.set(found[i], (cols.get(found[i]) ?? 0) + 1);
    const key = JSON.stringify([v, found[i]]); cells.set(key, (cells.get(key) ?? 0) + 1);
  });
  const a = [...rows.values()].reduce((s,n) => s + choose2(n), 0);
  const b = [...cols.values()].reduce((s,n) => s + choose2(n), 0);
  const observed = [...cells.values()].reduce((s,n) => s + choose2(n), 0);
  const expected = a*b/choose2(truth.length), max = (a+b)/2;
  return max === expected ? 1 : (observed-expected)/(max-expected);
}
function random(seed: number) {
  let x = seed >>> 0;
  return () => {x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0)/4294967296;};
}
if (Math.abs(ari([0,0,1,1], ['a','a','b','b']) - 1) > 1e-12
  || Math.abs(ari([0,0,1,1], ['a','b','a','b']) + .5) > 1e-12) throw new Error('ARI calibration failed');
const cases = [
  {name:'clean-blocks', within:.55, between:0, hub:false},
  {name:'clean-plus-three-bridges', within:.55, between:0, hub:false},
  {name:'sparse-blocks', within:.15, between:0, hub:false},
  {name:'weak-bridges', within:.55, between:.02, hub:false},
  {name:'noisy-bridges', within:.35, between:.10, hub:false},
  {name:'structural-hub', within:.55, between:.02, hub:true},
];
const results: unknown[] = [];
for (const scenario of cases) for (let seed = seedStart; seed < seedStart+seedCount; seed++) {
  const rng = random(seed), n = 96, width = 24;
  const truth = Array.from({length:n}, (_,i) => Math.floor(i/width));
  const ids = truth.map((_,i) => 'audit:'+String(i).padStart(4,'0'));
  const edges: {source:string;target:string;weight:number}[] = [];
  for (let i=0; i<n; i++) for (let j=i+1; j<n; j++)
    if (rng() < (truth[i] === truth[j] ? scenario.within : scenario.between))
      edges.push({source:ids[i],target:ids[j],weight:1});
  if(scenario.name==='clean-plus-three-bridges') for(let group=0;group<3;group++)
    edges.push({source:ids[group*width],target:ids[(group+1)*width],weight:1});
  if (scenario.hub) for(let i=1;i<n;i++)
    if(!edges.some(e=>e.source===ids[0]&&e.target===ids[i])) edges.push({source:ids[0],target:ids[i],weight:1});
  const nodes = ids.map(id=>({id,label:'Synthetic audit node',source_target:'audit',source_version:'v1'}));
  const view = {...parseKnowledgeGraph({nodes,edges}),focusId:null,hops:0};
  const start = performance.now();
  const engine = new SemanticLayoutEngine();
  const result = engine.layout(view,new Map(),[],{nowMs:1000});
  const elapsed = performance.now()-start;
  const predicted = ids.map(id=>result.clusterIds.get(id)!);
  const reversed = {...parseKnowledgeGraph({nodes:nodes.slice().reverse(),edges:edges.slice().reverse()}),focusId:null,hops:0};
  const other = new SemanticLayoutEngine().layout(reversed,new Map(),[],{nowMs:1000});
  const permutationEqual = ids.every(id=>JSON.stringify(result.anchors.get(id))===JSON.stringify(other.anchors.get(id))&&result.clusterIds.get(id)===other.clusterIds.get(id));
  const renamed = ids.map((_,i)=>'renamed:'+String((i*37+seed*11)%n).padStart(4,'0'));
  const renamedId = new Map(ids.map((id,i)=>[id,renamed[i]]));
  const renamedView={...parseKnowledgeGraph({nodes:nodes.map((node,i)=>({...node,id:renamed[i]})),
    edges:edges.map(e=>({...e,source:renamedId.get(e.source),target:renamedId.get(e.target)}))}),focusId:null,hops:0};
  const renamedResult=new SemanticLayoutEngine().layout(renamedView,new Map(),[],{nowMs:1000});
  const canonicalFound=new Map<string,number>();
  const oldPartition=predicted.map(label=>{if(!canonicalFound.has(label))canonicalFound.set(label,canonicalFound.size);return canonicalFound.get(label)!;});
  const relabelPartitionARI=ari(oldPartition,renamed.map(id=>renamedResult.clusterIds.get(id)!));
  // A disconnected group in the raw input union is certainly disconnected;
  // a connected union does not establish connectivity in filtered solver input.
  const groups = new Map<string,string[]>();
  ids.forEach((id,i)=>groups.set(predicted[i],[...(groups.get(predicted[i])??[]),id]));
  let disconnectedInRawUnion = 0;
  for (const members of groups.values()) {
    const allowed = new Set(members), seen = new Set([members[0]]), pending=[members[0]];
    while(pending.length) {const at=pending.pop()!; for(const e of edges) {
      const next=e.source===at?e.target:e.target===at?e.source:null;
      if(next&&allowed.has(next)&&!seen.has(next)){seen.add(next);pending.push(next);}
    }}
    if(seen.size!==members.length)disconnectedInRawUnion++;
  }
  let referenceInput: unknown;
  if (includeInputs) {
    if (result.diagnostics.relationPairs !== edges.length || result.diagnostics.affinityPairs !== 0
      || result.diagnostics.nodes !== n) throw new Error('reference export requires complete structural-only fixture');
    const rawDegree=new Map(ids.map(id=>[id,0]));
    for(const e of edges) for(const id of [e.source,e.target]) rawDegree.set(id,rawDegree.get(id)!+e.weight/(1+e.weight));
    const communityEdges=engine.checkpoint().pairs.flatMap(([,p])=>{
      if(p.s!==0)throw new Error('reference export does not reconstruct semantic admission');
      const a=rawDegree.get(p.source)!,b=rawDegree.get(p.target)!;
      const similar=a+b===0?1:Math.min(a,b)/Math.max(a,b);
      return similar>=.5?[{source:p.source,target:p.target,weight:p.r}]:[];
    });
    referenceInput={ids,plantedMembership:truth,currentMembership:predicted,rawEdges:edges,communityEdges,
      scope:'Exact checkpoint-normalized weights with structural degree-imbalance gate reconstructed for complete positive uniform fixtures only'};
  }
  results.push({scenario:scenario.name,seed,nodes:n,edges:edges.length,plantedGroups:4,
    predictedGroups:groups.size,adjustedRand:ari(truth,predicted),permutationEqual,relabelPartitionARI,
    disconnectedInRawUnion,finite:[...result.anchors.values()].every(p=>[p.x,p.y,p.z].every(Number.isFinite)),
    elapsedMs:elapsed,diagnostics:result.diagnostics,referenceInput});
}
console.log(JSON.stringify({schema:1,scope:cases.length*seedCount+' synthetic planted structural graphs; no semantic encoder, real-source gold, UI or installed flow',
  seedPolicy:{generator:'xorshift32',seedStart,seedCount,nodes:96,blocks:4},
  metrics:{adjustedRand:'chance-adjusted partition agreement with planted generation labels',
    disconnectedInRawUnion:'one-sided counterexample check; zero is not filtered-community connectivity proof',
    elapsedMs:'single-process exploratory CPU observation, not performance comparison'},results},null,2));

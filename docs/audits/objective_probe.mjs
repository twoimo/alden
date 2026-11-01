// Compile read-only source variants into explicit audit outputs. Never edits
// application source, build receipts, indices, model/config or installed app.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const require=createRequire(path.resolve('desktop/package.json'));
const {build}=require('esbuild');
const root=process.cwd(),out=path.join(root,'outputs/algorithm-reference-20261019');
const sourcePath='desktop/src/knowledge/semantic-layout.ts';
const source=fs.readFileSync(sourcePath,'utf8');
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const anchor="const score = (label: string) => (weights.get(label) ?? 0) - own * (volumes.get(volumeKey(i, label)) ?? 0) / componentVolume.get(current.find(i))!;";
if(source.split(anchor).length!==2)throw new Error('Exact current objective anchor changed; stop');
const model=await build({entryPoints:['desktop/src/knowledge/graph-model.ts'],bundle:true,platform:'node',format:'esm',write:false});
fs.writeFileSync(path.join(out,'probe-graph-model.mjs'),model.outputFiles[0].contents);
const {parseKnowledgeGraph}=await import(pathToFileURL(path.join(out,'probe-graph-model.mjs')));
const variants=[['component-gamma1',anchor],
 ['global-gamma1',anchor.replace('componentVolume.get(current.find(i))!','[...componentVolume.values()].reduce((s, v) => s + v, 0)')],
 ['component-gamma025',anchor.replace(' - own *',' - .25 * own *')]];
const engines=[];
for(const [name,replacement]of variants){
 const code=source.replace(anchor,replacement);
 const result=await build({stdin:{contents:code,sourcefile:'semantic-layout.ts',resolveDir:path.join(root,'desktop/src/knowledge'),loader:'ts'},bundle:true,platform:'node',format:'esm',write:false});
 const target=path.join(out,'probe-'+name+'.mjs');fs.writeFileSync(target,result.outputFiles[0].contents);
 const {SemanticLayoutEngine}=await import(pathToFileURL(target));
 engines.push({name,Engine:SemanticLayoutEngine,sourceVariantSha256:sha(code),compiledSha256:sha(result.outputFiles[0].contents),replacement});
}
const id=i=>'n'+String(i).padStart(4,'0');
const A=[[0,1],[0,2],[0,3],[0,4],[1,3],[1,4],[2,4],[3,4]];
const B=[];for(let i=5;i<10;i++)for(let j=i+1;j<10;j++)B.push([i,j]);
function solve(Engine,n,edges){
 const view={...parseKnowledgeGraph({nodes:Array.from({length:n},(_,i)=>({id:id(i),label:'synthetic',source_version:'v1',source_target:'audit'})),
   edges:edges.map(([a,b])=>({source:id(a),target:id(b),weight:1}))}),focusId:null,hops:0};
 const result=new Engine().layout(view,new Map(),[],{nowMs:1000});
 const groups=new Map();for(let i=0;i<5;i++){const label=result.clusterIds.get(id(i));groups.set(label,[...(groups.get(label)??[]),i]);}
 return {partitionA:[...groups.values()].sort((a,b)=>a[0]-b[0]),passes:result.diagnostics.communityPasses};
}
const tradeoff=engines.map(({name,Engine})=>({name,alone:solve(Engine,5,A),unrelatedK5:solve(Engine,10,[...A,...B])}));
if(JSON.stringify(tradeoff[0].alone.partitionA)!==JSON.stringify(tradeoff[0].unrelatedK5.partitionA))throw new Error('Current component independence regressed');
if(tradeoff[1].alone.partitionA.length!==2||tradeoff[1].unrelatedK5.partitionA.length!==1)throw new Error('Global counterexample not reproduced');
const input=fs.readFileSync(path.join(out,'dev-inputs.json'));
const fixtures=JSON.parse(input).results.filter(r=>r.scenario==='clean-blocks'||r.scenario==='clean-plus-three-bridges');
const ablation=[];
for(const row of fixtures)for(const {name,Engine}of engines){
 const raw=row.referenceInput;const nodes=raw.ids.map(id=>({id,label:'synthetic',source_target:'audit',source_version:'v1'}));
 const view={...parseKnowledgeGraph({nodes,edges:raw.rawEdges}),focusId:null,hops:0};
 const result=new Engine().layout(view,new Map(),[],{nowMs:1000});
 ablation.push({scenario:row.scenario,seed:row.seed,variant:name,communities:new Set(result.clusterIds.values()).size,passes:result.diagnostics.communityPasses});
}
for(const r of ablation)if(r.variant==='component-gamma025'&&r.scenario==='clean-blocks'&&r.communities!==4)throw new Error('Gamma counterexample not reproduced');
console.log(JSON.stringify({scope:'Temporary native-source objective probes only; not Leiden, no production patch or recommended gamma',
 sourcePath,sourceSha256:sha(source),inputSha256:sha(input),variants:engines.map(({Engine,...rest})=>rest),tradeoff,ablation},null,2));

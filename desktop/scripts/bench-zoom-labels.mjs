import esbuild from 'esbuild';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { performance } from 'node:perf_hooks';
import { createHash } from 'node:crypto';

const desktop=path.dirname(path.dirname(fileURLToPath(import.meta.url))),repo=path.dirname(desktop);
const revision=process.argv[2]??'HEAD';
const cache=path.join(desktop,'node_modules/.cache/zoom-label-bench');mkdirSync(cache,{recursive:true});
const old=new Map(['hologram.ts','label-zoom.ts','semantic-layout.ts'].map(name=>[name,execFileSync('git',['show',`${revision}:desktop/src/knowledge/${name}`],{cwd:repo,encoding:'utf8'})]));
const implementations=[];
for(const mode of ['before','after']) {
  const outfile=path.join(cache,mode+'.mjs');
  await esbuild.build({entryPoints:[path.join(desktop,'src/perf/zoom-labels-node.ts')],outfile,bundle:true,format:'esm',platform:'node',packages:'external',logLevel:'silent',
    plugins:mode==='before'?[{name:'bound-baseline',setup(build){build.onLoad({filter:/\/knowledge\/(hologram|label-zoom|semantic-layout)\.ts$/},args=>({contents:old.get(path.basename(args.path)),loader:'ts',resolveDir:path.dirname(args.path)}));}}]:[]});
  implementations.push(await import(pathToFileURL(outfile).href));
}
const quantile=(values,q)=>[...values].sort((a,b)=>a-b)[Math.min(values.length-1,Math.floor(values.length*q))];
const rows=[];
let checksum=0;
for(const count of [128,512,2048])for(const moving of [false,true]) {
  const runs=implementations.map(module=>module.labelWorkload(count)),samples=[[],[]];
  for(let warm=0;warm<160;warm++)for(const run of runs)checksum+=run(moving);
  for(let round=0;round<11;round++)for(const index of round%2?[1,0]:[0,1]) {
    const started=performance.now();
    for(let frame=0;frame<80;frame++)checksum+=runs[index](moving);
    samples[index].push((performance.now()-started)/80);
  }
  rows.push({count,moving,frameBatches:11,framesPerBatch:80,before:{p50_ms:quantile(samples[0],.5),p95_ms:quantile(samples[0],.95)},after:{p50_ms:quantile(samples[1],.5),p95_ms:quantile(samples[1],.95)}});
}
const layout=[];
for(const count of [128,512,2048]) {
  for(const module of implementations)module.layoutWorkload(count);
  const samples=[[],[]];
  for(let round=0;round<9;round++)for(const index of round%2?[1,0]:[0,1])samples[index].push(implementations[index].layoutWorkload(count));
  const hashes=samples.map(rows=>new Set(rows.map(row=>createHash('sha256').update(row.snapshot).digest('hex'))));
  const identicalGeometry=hashes[0].size===1&&hashes[1].size===1&&[...hashes[0]][0]===[...hashes[1]][0];
  if(!identicalGeometry)throw new Error('Layout geometry changed; reject the performance comparison.');
  layout.push({count,topology:'chain',rounds:9,identicalGeometry,snapshot_sha256:[...hashes[0]][0],
    before:{p50_ms:quantile(samples[0].map(s=>s.ms),.5),p95_ms:quantile(samples[0].map(s=>s.ms),.95)},
    after:{p50_ms:quantile(samples[1].map(s=>s.ms),.5),p95_ms:quantile(samples[1].map(s=>s.ms),.95)},diagnostics:samples[1].at(-1).diagnostics});
}
const memory=[];
if(typeof global.gc==='function')for(const count of [128,512,2048]) {
  global.gc();const before=process.memoryUsage().heapUsed;
  const retained=Array.from({length:3},()=>implementations[1].labelWorkload(count));
  for(const run of retained)checksum+=run(false);
  global.gc();const after=process.memoryUsage().heapUsed;
  memory.push({count,copies:retained.length,retained_heap_bytes_per_fixture:(after-before)/retained.length});
  // Keep all fixtures alive through the heap measurement, then release them.
  for(const run of retained)checksum+=run(false);
}
console.log(JSON.stringify({baseline:revision,node:process.version,platform:process.platform,arch:process.arch,
  scope:'Synthetic actual CPU label selection and cold chain-layout solves. Excludes DOM, WebGL, app rendering, native source latency and battery.',
  memoryScope:'V8 retained heap delta for three live current label fixtures after explicit GC. Allocator/GC noise applies; excludes DOM, GPU and full app RSS.',rows,layout,memory,checksum},null,2));

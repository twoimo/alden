import esbuild from 'esbuild';
import { Window } from 'happy-dom';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath, pathToFileURL } from 'node:url';

const desktop = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const repo = path.dirname(desktop), revision = process.argv[2] ?? 'HEAD';
const target = 'desktop/src/knowledge/hologram.ts';
const before = execFileSync('git', ['show', `${revision}:${target}`], { cwd: repo, encoding: 'utf8' });
const after = readFileSync(path.join(repo, target), 'utf8');
const digest = value => createHash('sha256').update(value).digest('hex');
const cache = path.join(desktop, 'node_modules/.cache/label-refresh-bench');
mkdirSync(cache, { recursive: true });
const window = new Window(); globalThis.document = window.document;
const modules = [];
for (const [name, source] of [['before', before], ['after', after]]) {
  const outfile = path.join(cache, `${name}.mjs`);
  await esbuild.build({ entryPoints: [path.join(desktop, 'src/perf/label-refresh-node.ts')],
    outfile, bundle: true, format: 'esm', platform: 'node', packages: 'external', logLevel: 'silent',
    plugins: [{ name: 'bound-hologram-source', setup(build) {
      build.onLoad({ filter: /\/knowledge\/hologram\.ts$/ }, args => ({
        contents: source, loader: 'ts', resolveDir: path.dirname(args.path),
      }));
    } }],
  });
  modules.push(await import(pathToFileURL(outfile).href));
}
const quantile = (values, q) => [...values].sort((a, b) => a - b)[Math.floor((values.length - 1) * q)];
const rows = [];
let checksum = 0;
for (const count of [128, 512, 2048]) {
  const runs = modules.map(module => module.labelRefreshWorkload(count));
  const results = runs.map(run => run());
  if (JSON.stringify(results[0].labels) !== JSON.stringify(results[1].labels)) throw new Error('Label output changed');
  for (let warm = 0; warm < 4; warm++) for (const run of runs) run();
  const samples = [[], []];
  for (let round = 0; round < 11; round++) for (const index of round % 2 ? [1, 0] : [0, 1]) {
    const start = performance.now();
    for (let iteration = 0; iteration < 40; iteration++) checksum += runs[index]().labels.length;
    samples[index].push((performance.now() - start) / 40);
  }
  rows.push({ count, labels: results[0].labels.length, identicalOutput: true,
    labelOutputSha256: digest(JSON.stringify(results[0].labels)),
    before: { sourceIdReads: results[0].sourceIdReads, p50_ms: quantile(samples[0], .5), p95_ms: quantile(samples[0], .95), samples_ms: samples[0] },
    after: { sourceIdReads: results[1].sourceIdReads, p50_ms: quantile(samples[1], .5), p95_ms: quantile(samples[1], .95), samples_ms: samples[1] } });
}
console.log(JSON.stringify({ baseline: revision, source_sha256: { before: digest(before), after: digest(after) },
  node: process.version, platform: process.platform, arch: process.arch, rounds: 11, iterationsPerRound: 40,
  scope: 'Actual label refresh method; synthetic happy-dom and instrumented source IDs. No browser layout, GPU, FPS, installed app, collection latency, or battery claim.',
  rows, checksum }, null, 2));
await window.happyDOM.close();

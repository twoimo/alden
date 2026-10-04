import { readFileSync } from 'node:fs';
import { performance } from 'node:perf_hooks';
import { overviewGraph, parseKnowledgeGraph } from '../knowledge/graph-model';
import { relationAnchors } from '../knowledge/relation-layout';

const file = process.argv[2];
if (!file) throw new Error('Pass a private read_graph JSON snapshot; no data are sent or printed.');
const graph = parseKnowledgeGraph(JSON.parse(readFileSync(file, 'utf8')));
const overview = overviewGraph(graph);
const cases = [6, 12, 24].map(cap => {
  const nodes = overview.nodes.slice(0, cap), ids = new Set(nodes.map(n => n.id));
  const view = { ...overview, nodes, edges: overview.edges.filter(e => ids.has(e.source) && ids.has(e.target)) };
  for (let i = 0; i < 30; i++) relationAnchors(view, graph);
  const elapsed: number[] = [];
  for (let i = 0; i < 200; i++) {
    const start = performance.now(); relationAnchors(view, graph); elapsed.push(performance.now() - start);
  }
  elapsed.sort((a, b) => a - b);
  return { nodes: nodes.length, sourceEdges: view.edges.length, samples: elapsed.length,
    p50Ms: elapsed[Math.ceil(elapsed.length * .5) - 1], p95Ms: elapsed[Math.ceil(elapsed.length * .95) - 1], maxMs: elapsed.at(-1) };
});
console.log(JSON.stringify({ schemaVersion: 1, runtime: process.version, warmup: 30, iterations: 96,
  scope: 'Pure projection on a private real OSK snapshot; runs on graph changes, not every frame. Not GPU/frame/voice latency or a before-after speed claim.', cases }, null, 2));

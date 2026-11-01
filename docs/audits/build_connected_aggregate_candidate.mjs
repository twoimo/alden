// Bundle the actual engine with two explicitly reviewed audit-only deltas.
// This never writes product source, build receipts, installed apps or stores.
import { build } from '../../desktop/node_modules/esbuild/lib/main.js';
import { createHash } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const args = process.argv.slice(2);
const value = (flag) => { const at = args.indexOf(flag); if (at < 0 || !args[at + 1]) throw new Error('missing ' + flag); return args[at + 1]; };
const mode = value('--mode'), out = resolve(value('--out'));
if (!['baseline', 'candidate'].includes(mode)) throw new Error('invalid mode');
if (existsSync(out)) throw new Error('audit output already exists');
const originalPath = resolve(root, 'desktop/src/knowledge/semantic-layout.ts');
const candidatePath = resolve(root, 'docs/audits/connected_aggregate_candidate.ts');
const entry = resolve(root, 'docs/audits/audit_alden_graph_channels.ts');
const original = readFileSync(originalPath, 'utf8');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const once = (source, anchor, replacement) => {
  if (source.split(anchor).length !== 2) throw new Error('candidate source anchor drift: ' + anchor.slice(0, 80));
  return source.replace(anchor, replacement);
};
let candidate = once(original,
  "const labels = ids.map(id => options.reflow ? '@' + id : this.labels.get(id) ?? '@' + id);",
  "const labels = ids.map((id, i) => options.reflow || active[i] ? '@' + id : this.labels.get(id) ?? '@' + id);");
candidate = once(candidate, '    const groups = new Map<string, string[]>();', `    const { roots: refinedRoots, ...refinementDiagnostics } = refineConnectedCommunities(adjacency, labels, active, ids.map((_, i) => current.find(i)));
    ids.forEach((id, i) => { if (active[i]) labels[i] = '@aggregate:' + ids[refinedRoots[i]]; });
    Object.assign(d, { communityRefinement: refinementDiagnostics });
    const groups = new Map<string, string[]>();`);
candidate = `import { refineConnectedCommunities } from ${JSON.stringify(candidatePath)};\n` + candidate;
mkdirSync(out, { recursive: true, mode: 0o700 });
if (mode === 'candidate') writeFileSync(resolve(out, 'candidate-semantic-layout.ts'), candidate);
const bundle = resolve(out, 'channel-audit.mjs');
await build({ entryPoints: [entry], outfile: bundle, bundle: true, platform: 'node', format: 'esm', logLevel: 'warning',
  plugins: mode === 'candidate' ? [{ name: 'connected-aggregate-candidate', setup(api) {
    api.onLoad({ filter: /semantic-layout\.ts$/ }, args => {
      if (args.path !== originalPath) throw new Error('unexpected semantic engine path');
      return { contents: candidate, loader: 'ts', resolveDir: dirname(originalPath) };
    });
  } }] : [] });
const sourceFiles = [originalPath, candidatePath, entry, fileURLToPath(import.meta.url)];
const receipt = { schema: 1, phase: 'frozen-before-evaluation', mode, observedAt: new Date().toISOString(),
  sourceFiles: Object.fromEntries(sourceFiles.map(path => [path.slice(root.length + 1), sha(readFileSync(path))])),
  originalEngineSha256: sha(original), executedEngineSha256: sha(mode === 'candidate' ? candidate : original),
  bundleSha256: sha(readFileSync(bundle)),
  deltas: mode === 'candidate' ? ['Rerun affected components from singleton labels; preserve cached coordinates and overlap label reuse.',
    'Split disconnected community membership and merge connected blocks with strictly positive component-modularity gain, up to8 disjoint-merge passes.'] : [],
  limits: ['Audit build only; no product source/default/install or persisted data change.', 'Not Leiden, not a semantic identity merge, no objective/resolution/affinity retuning.'] };
writeFileSync(resolve(out, 'build.json'), JSON.stringify(receipt, null, 2) + '\n');
console.log(JSON.stringify({ mode, out, bundleSha256: receipt.bundleSha256 }));

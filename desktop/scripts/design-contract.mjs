#!/usr/bin/env node
/** Hallmark-inspired BEFORE / KEPT source inventory. Never loads application code. */
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { lstatSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import ts from 'typescript';

export const SCHEMA = 'alden-ui-wiring/v1';
export const SCOPE = Object.freeze({
  include: ['desktop/*.html', 'desktop/src/*.{ts,tsx,html}'],
  recursive: false,
  excludes: ['nested modules', 'tests', 'CSS', 'dependencies', 'runtime/data/provider state'],
  evidence: 'Source declarations only, including DEV/demo branches; no reachability or actual-state claim.',
});
export const LIMITS = Object.freeze({ files: 128, fileBytes: 2 * 1024 * 1024, totalBytes: 16 * 1024 * 1024 });
const KINDS = new Set(['id', 'control', 'field', 'form', 'data', 'link', 'resource', 'handler', 'selector', 'queried-class']);
const hash = value => createHash('sha256').update(value).digest('hex');
const order = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const json = value => JSON.stringify(value);
const scoped = path => /^desktop\/[^/]+\.html$/.test(path) || /^desktop\/src\/[^/]+\.(?:ts|tsx|html)$/.test(path);
const fieldTags = new Set(['input', 'select', 'textarea', 'button']);
const unwrap = node => {
  while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node) || ts.isTypeAssertionExpression(node) || ts.isNonNullExpression(node) || ts.isSatisfiesExpression(node))) node = node.expression;
  return node;
};
const property = node => ts.isPropertyAccessExpression(node) ? node.name.text :
  ts.isElementAccessExpression(node) && node.argumentExpression && ts.isStringLiteralLike(node.argumentExpression) ? node.argumentExpression.text : undefined;
const constant = node => ts.isVariableDeclaration(node) && ts.isVariableDeclarationList(node.parent) && Boolean(node.parent.flags & ts.NodeFlags.Const);
const scopeNode = node => ts.isSourceFile(node) || ts.isBlock(node) || ts.isFunctionLike(node) || ts.isClassLike(node);

// AST structure, not source text: quotes, comments and whitespace do not change identity.
// Text/copy and className assignments are presentation; calls and other literals stay pinned.
function structure(node) {
  if (!node) return null;
  node = unwrap(node);
  if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken &&
      ['textContent', 'innerText', 'className', 'title'].includes(property(node.left)) && ts.isStringLiteralLike(unwrap(node.right))) {
    return ['presentation-assignment', structure(node.left)];
  }
  if (ts.isIdentifier(node)) return ['identifier', node.text];
  if (ts.isStringLiteralLike(node)) return ['string', node.text];
  if (ts.isNumericLiteral(node)) return ['number', node.text];
  const children = [];
  ts.forEachChild(node, child => { children.push(structure(child)); });
  return [ts.SyntaxKind[node.kind], ...children];
}

function ownerOf(node) {
  const parts = [];
  for (let p = node.parent; p; p = p.parent) {
    if ((ts.isFunctionLike(p) || ts.isClassLike(p)) && p.name) parts.unshift(p.name.getText());
    else if ((ts.isArrowFunction(p) || ts.isFunctionExpression(p)) && ts.isVariableDeclaration(p.parent) && ts.isIdentifier(p.parent.name)) parts.unshift(p.parent.name.text);
    else if ((ts.isArrowFunction(p) || ts.isFunctionExpression(p)) && !p.name) parts.unshift('<callback>');
  }
  if (parts.length) return parts.join('/');
  for (let p = node.parent; p; p = p.parent) if (ts.isVariableDeclaration(p) && ts.isIdentifier(p.name)) return p.name.text;
  return '<module>';
}

function checkSources(sources) {
  if (!Array.isArray(sources) || !sources.length || sources.length > LIMITS.files) throw new Error('Expected 1..128 root source files');
  let total = 0;
  const paths = new Set();
  for (const source of sources) {
    if (!scoped(source.path) || paths.has(source.path) || typeof source.text !== 'string') throw new Error('Invalid, duplicate or out-of-scope source path');
    paths.add(source.path);
    const bytes = Buffer.byteLength(source.text);
    if (bytes > LIMITS.fileBytes) throw new Error(`Source exceeds byte limit: ${source.path}`);
    total += bytes;
  }
  if (total > LIMITS.totalBytes) throw new Error('Source inventory exceeds total byte limit');
}

/** Pure inventory of caller-supplied root source strings; no reads or execution. */
export function inventorySources(sources, source = { kind: 'memory' }) {
  checkSources(sources);
  const entries = [], unknowns = [], markup = [];
  function add(kind, file, owner, subject, value, line) {
    entries.push({ kind, file, owner, subject, value, line });
  }
  function unknown(code, file, owner, line, detail) {
    unknowns.push({ code, file, owner, line, detail });
  }
  for (const input of [...sources].sort((a, b) => order(a.path, b.path))) {
    const file = input.path;
    function parseMarkup(text, owner, line, expressions = new Map()) {
      // Quoted > characters, comments and raw script/style bodies must not create fake controls.
      const tags = /<!--[\s\S]*?-->|<(script|style)\b((?:"[^"]*"|'[^']*'|[^'">])*)>[\s\S]*?<\/\1\s*>|<([a-z][\w:-]*)\b((?:"[^"]*"|'[^']*'|[^'">])*)>/gi;
      let match;
      while ((match = tags.exec(text))) {
        if (match[0].startsWith('<!--')) continue;
        const tag = (match[1] ?? match[3]).toLowerCase(), attrs = new Map();
        const attributes = /([^\s=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
        let attribute;
        while ((attribute = attributes.exec(match[2] ?? match[4]))) {
          const name = attribute[1].toLowerCase();
          const value = attribute[2] ?? attribute[3] ?? attribute[4] ?? '';
          if (attrs.has(name)) unknown('duplicate-attribute', file, owner, line, `${tag}:${name}`);
          attrs.set(name, value);
          if (/__DC_EXPR_[a-f0-9]+__/.test(name)) unknown('dynamic-attributes', file, owner, line, tag);
        }
        const id = attrs.get('id'), name = attrs.get('name');
        const subject = id ? `#${id}` : name ? `${tag}[name=${name}]` : tag;
        const type = attrs.get('type') ?? (tag === 'input' ? 'text' : tag === 'button' ? 'submit' : '');
        const dynamic = value => /__DC_EXPR_[a-f0-9]+__/.test(value);
        for (const [key, value] of attrs) {
          if ((['id', 'name', 'href', 'src', 'type'].includes(key) || key.startsWith('data-') || /^on[a-z]+$/.test(key)) && dynamic(value)) {
            unknown('dynamic-attribute-value', file, owner, line, `${tag}:${key}:${value}`);
          }
          if (key.startsWith('data-')) add('data', file, owner, subject, `${key}=${value}`, line);
          if (/^on[a-z]+$/.test(key)) add('handler', file, owner, subject, `${key}:${hash(json(structure(ts.createSourceFile('handler.ts', value, ts.ScriptTarget.Latest, true))))}`, line);
        }
        if (id) add('id', file, owner, subject, tag, line);
        if (fieldTags.has(tag)) {
          add('control', file, owner, subject, `${tag}:${type}`, line);
          if (name) add('field', file, owner, subject, `${name}:${tag}:${type}`, line);
          if (!id && !name && ![...attrs.keys()].some(key => key.startsWith('data-'))) unknown('anonymous-control', file, owner, line, tag);
        }
        if (tag === 'form') add('form', file, owner, subject, `${attrs.get('method') ?? 'get'}:${attrs.get('action') ?? ''}`, line);
        if (['a', 'area'].includes(tag) && attrs.has('href')) add('link', file, owner, subject, attrs.get('href'), line);
        if ((tag === 'link' && attrs.has('href')) || (tag === 'script' && attrs.has('src'))) add('resource', file, owner, tag, attrs.get('href') ?? attrs.get('src'), line);
        if (tag === 'script' && !attrs.has('src') && match[1]) unknown('inline-html-script', file, owner, line, 'Not parsed as a TS/JS root module');
        markup.push({ file, owner, line, tag, attrs });
      }
      // Interpolation in content can insert arbitrary elements, even if adjacent copy is static.
      const outside = text.replace(tags, '');
      for (const token of new Set(outside.match(/__DC_EXPR_[a-f0-9]+__/g) ?? [])) {
        unknown('dynamic-markup-hole', file, owner, line, expressions.get(token) ?? token);
      }
      if (/<[a-z][\w:-]*(?:\s|$)/i.test(outside)) unknown('partial-markup', file, owner, line, 'Opening tag crosses a source fragment boundary');
    }
    if (file.endsWith('.html')) {
      parseMarkup(input.text, '<document>', 1);
      continue;
    }
    const tree = ts.createSourceFile(file, input.text, ts.ScriptTarget.Latest, true, file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const lineOf = node => tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1;
    for (const diagnostic of tree.parseDiagnostics) unknown('typescript-parse-error', file, '<module>', tree.getLineAndCharacterOfPosition(diagnostic.start ?? 0).line + 1, `TS${diagnostic.code}`);
    const bindings = new Map();
    function register(node) {
      if ((ts.isVariableDeclaration(node) || ts.isParameter(node) || ts.isFunctionDeclaration(node)) && node.name && ts.isIdentifier(node.name)) {
        let scope = node.parent;
        while (scope && !scopeNode(scope)) scope = scope.parent;
        if (scope) {
          const map = bindings.get(scope) ?? new Map();
          const list = map.get(node.name.text) ?? [];
          list.push(node); map.set(node.name.text, list); bindings.set(scope, map);
        }
      }
      ts.forEachChild(node, register);
    }
    register(tree);
    function binding(node) {
      for (let p = node.parent; p; p = p.parent) {
        const list = bindings.get(p)?.get(node.text);
        if (list) return list.length === 1 ? list[0] : undefined;
      }
      return undefined;
    }
    function valueOf(node, seen = new Set()) {
      node = unwrap(node);
      if (!node || seen.has(node)) return { text: '<unresolved>', known: false };
      seen = new Set(seen).add(node);
      if (ts.isStringLiteralLike(node) || ts.isNumericLiteral(node)) return { text: node.text, known: true };
      if (ts.isIdentifier(node)) {
        const declaration = binding(node);
        if (declaration?.initializer && constant(declaration)) return valueOf(declaration.initializer, seen);
      }
      if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
        const a = valueOf(node.left, seen), b = valueOf(node.right, seen);
        return { text: a.text + b.text, known: a.known && b.known };
      }
      if (ts.isTemplateExpression(node)) {
        let text = node.head.text, known = true;
        for (const span of node.templateSpans) {
          const value = valueOf(span.expression, seen);
          text += value.text + span.literal.text; known &&= value.known;
        }
        return { text, known };
      }
      return { text: `__DC_EXPR_${hash(json(structure(node))).slice(0, 20)}__`, known: false };
    }
    function receiver(node, seen = new Set()) {
      node = unwrap(node);
      if (!node || seen.has(node)) return { text: '<unresolved>', known: false };
      seen = new Set(seen).add(node);
      if (ts.isIdentifier(node)) {
        const declaration = binding(node);
        if (declaration?.initializer && constant(declaration)) return receiver(declaration.initializer, seen);
        if (!declaration && ['document', 'window'].includes(node.text)) return { text: node.text, known: true };
      }
      if (ts.isCallExpression(node) && ['getElementById', 'querySelector', 'querySelectorAll'].includes(property(node.expression))) {
        const value = valueOf(node.arguments[0]);
        const base = receiver(node.expression.expression, seen);
        return { text: `${base.text}/${property(node.expression)}:${value.text}`, known: value.known && base.known };
      }
      return { text: `expression:${hash(json(structure(node)))}`, known: false };
    }
    function callback(node) {
      node = unwrap(node);
      if (!node) return { identity: '<missing>', known: false };
      let declaration;
      if (ts.isIdentifier(node)) declaration = binding(node);
      const definition = declaration && (ts.isFunctionDeclaration(declaration) ? declaration : constant(declaration) ? declaration.initializer : undefined);
      const identity = `${ts.isIdentifier(node) ? `reference:${node.text}` : 'expression'}:${hash(json(structure(node)))}`;
      return {
        identity: definition ? `${identity}:definition:${hash(json(structure(definition)))}` : identity,
        known: ts.isArrowFunction(node) || ts.isFunctionExpression(node) || Boolean(definition && (ts.isFunctionDeclaration(definition) || ts.isArrowFunction(unwrap(definition)) || ts.isFunctionExpression(unwrap(definition)))),
      };
    }
    function handler(node, target, event, fn, options) {
      const owner = ownerOf(node), line = lineOf(node), action = callback(fn);
      let optionNode = unwrap(options);
      if (optionNode && ts.isIdentifier(optionNode)) {
        const declaration = binding(optionNode);
        if (declaration && constant(declaration) && declaration.initializer) optionNode = unwrap(declaration.initializer);
      }
      add('handler', file, owner, target.text, `${event.text}:${action.identity}:options:${hash(json(structure(optionNode)))}`, line);
      if (!target.known) unknown('unresolved-handler-target', file, owner, line, target.text);
      if (!event.known) unknown('dynamic-event-name', file, owner, line, event.text);
      if (!action.known) unknown('unresolved-handler-reference', file, owner, line, action.identity);
      if (optionNode && !(optionNode.kind === ts.SyntaxKind.TrueKeyword || optionNode.kind === ts.SyntaxKind.FalseKeyword ||
          (ts.isObjectLiteralExpression(optionNode) && !optionNode.properties.some(ts.isSpreadAssignment)))) {
        unknown('unresolved-listener-options', file, owner, line, hash(json(structure(optionNode))));
      }
    }
    function visit(node) {
      const owner = ownerOf(node), line = lineOf(node);
      if ((ts.isStringLiteralLike(node) || ts.isTemplateExpression(node)) && /<[a-z!/]/i.test(ts.isTemplateExpression(node) ? node.head.text + node.templateSpans.map(span => span.literal.text).join('') : node.text)) {
        const value = valueOf(node);
        const expressions = new Map();
        if (ts.isTemplateExpression(node)) for (const span of node.templateSpans) {
          const expression = valueOf(span.expression);
          if (!expression.known) expressions.set(expression.text, expression.text);
        }
        parseMarkup(value.text, owner, line, expressions);
        if (ts.isTaggedTemplateExpression(node.parent)) unknown('tagged-markup-template', file, owner, line, 'Tag semantics are not evaluated');
      }
      if (ts.isCallExpression(node)) {
        const method = property(node.expression);
        if (ts.isElementAccessExpression(node.expression) && !method) unknown('computed-method-call', file, owner, line, 'Computed method may contain an unsupported event binding');
        if (['getElementById', 'querySelector', 'querySelectorAll', 'closest', 'matches'].includes(method)) {
          const value = valueOf(node.arguments[0]);
          add('selector', file, owner, method, value.text, line);
          if (!value.known) unknown('dynamic-selector', file, owner, line, value.text);
        }
        if (method === 'addEventListener') handler(node, receiver(node.expression.expression), valueOf(node.arguments[0]), node.arguments[1], node.arguments[2]);
        // Element construction/mutation is tracked when the receiver and value are statically named.
        if (method === 'setAttribute' && node.arguments.length >= 2) {
          const name = valueOf(node.arguments[0]), value = valueOf(node.arguments[1]), target = receiver(node.expression.expression);
          if (name.known && (['id', 'name', 'href'].includes(name.text) || name.text.startsWith('data-'))) {
            const kind = name.text === 'id' ? 'id' : name.text === 'name' ? 'field' : name.text === 'href' ? 'link' : 'data';
            add(kind, file, owner, target.text, `${name.text}=${value.text}`, line);
            if (!value.known || !target.known) unknown('dynamic-dom-attribute', file, owner, line, `${name.text}:${value.text}`);
          }
        }
      }
      if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && (ts.isPropertyAccessExpression(node.left) || ts.isElementAccessExpression(node.left))) {
        const name = property(node.left);
        if (!name && (ts.isArrowFunction(unwrap(node.right)) || ts.isFunctionExpression(unwrap(node.right)))) unknown('computed-handler-property', file, owner, line, 'Computed callback assignment is not resolved');
        if (/^on[a-z]+$/.test(name)) handler(node, receiver(node.left.expression), { text: name.slice(2), known: true }, node.right);
        if (['id', 'name', 'href'].includes(name) || property(node.left.expression) === 'dataset') {
          const value = valueOf(node.right), target = receiver(node.left.expression);
          add(name === 'id' ? 'id' : name === 'name' ? 'field' : name === 'href' ? 'link' : 'data', file, owner, target.text, `${name}=${value.text}`, line);
          if (!value.known || !target.known) unknown('dynamic-dom-attribute', file, owner, line, `${name}:${value.text}`);
        }
      }
      if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
        if (!/^[a-z][a-z0-9-]*$/.test(node.tagName.getText(tree))) unknown('jsx-component', file, owner, line, 'Component render output is not expanded');
        const attributes = [], handlers = [];
        for (const attribute of node.attributes.properties) {
          if (ts.isJsxSpreadAttribute(attribute)) { unknown('jsx-spread', file, owner, line, 'Attributes require runtime resolution'); continue; }
          const name = attribute.name.getText(tree);
          if (attribute.initializer && ts.isJsxExpression(attribute.initializer)) {
            if (/^on[A-Z]/.test(name)) handlers.push([name, attribute.initializer.expression]);
            else attributes.push(`${name}="${valueOf(attribute.initializer.expression).text.replaceAll('"', '&quot;')}"`);
          } else attributes.push(`${name}="${attribute.initializer?.text ?? ''}"`);
        }
        const text = `<${node.tagName.getText(tree)} ${attributes.join(' ')}>`;
        parseMarkup(text, owner, line);
        const id = /\bid="([^"]*)"/.exec(text)?.[1];
        for (const [event, fn] of handlers) handler(node, { text: id ? `#${id}` : `jsx:${node.tagName.getText(tree)}`, known: Boolean(id) }, { text: event.toLowerCase().slice(2), known: true }, fn);
      }
      ts.forEachChild(node, visit);
    }
    visit(tree);
  }
  // Preserve only queried CSS classes, leaving purely visual classes free to change.
  const classes = new Set(entries.filter(entry => entry.kind === 'selector' && entry.subject !== 'getElementById').flatMap(entry => [...entry.value.matchAll(/\.([a-z_][\w-]*)/gi)].map(match => match[1])));
  for (const element of markup) for (const name of (element.attrs.get('class') ?? '').split(/\s+/)) {
    if (classes.has(name)) add('queried-class', element.file, element.owner, `.${name}`, element.tag, element.line);
  }
  if (!entries.length) unknown('empty-inventory', sources[0].path, '<module>', 1, 'No supported static wiring declarations found');
  const files = [...sources].sort((a, b) => order(a.path, b.path)).map(input => ({ path: input.path, sha256: hash(input.text), bytes: Buffer.byteLength(input.text) }));
  entries.sort((a, b) => order(entryKey(a), entryKey(b)) || a.line - b.line);
  unknowns.sort((a, b) => order(json(a), json(b)));
  return { schema: SCHEMA, extractor: { typescript: ts.version }, scope: SCOPE, source, files, entries, unknowns };
}

export function entryKey(entry) {
  return json([entry.kind, entry.file, entry.owner, entry.subject, entry.value]);
}

export function validateManifest(manifest) {
  if (!manifest || manifest.schema !== SCHEMA || manifest.extractor?.typescript !== ts.version || json(manifest.scope) !== json(SCOPE)) throw new Error('Unsupported manifest schema, compiler or inventory scope');
  if (!Array.isArray(manifest.files) || !manifest.files.length || manifest.files.length > LIMITS.files || !Array.isArray(manifest.entries) || !Array.isArray(manifest.unknowns)) throw new Error('Invalid or empty manifest');
  const paths = new Set();
  let total = 0;
  for (const file of manifest.files) {
    if (!scoped(file.path) || paths.has(file.path) || !/^[a-f0-9]{64}$/.test(file.sha256) || !Number.isSafeInteger(file.bytes) || file.bytes < 0 || file.bytes > LIMITS.fileBytes) throw new Error('Invalid manifest file');
    paths.add(file.path);
    total += file.bytes;
  }
  if (total > LIMITS.totalBytes) throw new Error('Manifest exceeds total byte limit');
  for (const entry of manifest.entries) {
    if (!KINDS.has(entry.kind) || !paths.has(entry.file) || !['owner', 'subject', 'value'].every(key => typeof entry[key] === 'string') || !Number.isSafeInteger(entry.line) || entry.line < 1) throw new Error('Invalid manifest entry');
  }
  for (const item of manifest.unknowns) if (!paths.has(item.file) || !['code', 'owner', 'detail'].every(key => typeof item[key] === 'string') || !Number.isSafeInteger(item.line) || item.line < 1) throw new Error('Invalid manifest unknown');
  return manifest;
}

/** Compare multisets: a surviving duplicate cannot conceal a removed control. */
export function compareManifests(before, current) {
  validateManifest(before); validateManifest(current);
  const counts = () => Object.fromEntries([...KINDS].sort(order).map(kind => [kind, 0]));
  const remaining = new Map(), removed = [], kept = counts(), added = counts();
  for (const entry of current.entries) remaining.set(entryKey(entry), (remaining.get(entryKey(entry)) ?? 0) + 1);
  for (const entry of before.entries) {
    const key = entryKey(entry), count = remaining.get(key) ?? 0;
    if (count) { remaining.set(key, count - 1); kept[entry.kind] = (kept[entry.kind] ?? 0) + 1; }
    else removed.push(entry);
  }
  for (const entry of current.entries) {
    const key = entryKey(entry), count = remaining.get(key) ?? 0;
    if (count) { added[entry.kind] = (added[entry.kind] ?? 0) + 1; remaining.set(key, count - 1); }
  }
  const currentFiles = new Set(current.files.map(file => file.path));
  const removedFiles = before.files.filter(file => !currentFiles.has(file.path)).map(file => file.path);
  const unknowns = [...before.unknowns.map(item => ({ phase: 'before', ...item })), ...current.unknowns.map(item => ({ phase: 'current', ...item }))];
  const totals = counts();
  for (const entry of before.entries) totals[entry.kind] = (totals[entry.kind] ?? 0) + 1;
  const status = removed.length || removedFiles.length ? 'failed' : unknowns.length ? 'incomplete' : 'kept';
  return {
    schema: SCHEMA, status, exitCode: status === 'failed' ? 1 : status === 'incomplete' ? 2 : 0,
    baseline: before.source, current: current.source,
    summary: { beforeFiles: before.files.length, currentFiles: current.files.length, totals, kept, added, removed: removed.length, unknowns: unknowns.length },
    removedFiles, removed, unknowns,
    limits: [SCOPE.evidence, 'Root files only; nested imports and generated markup are outside coverage.', 'No execution, runtime functionality, network, native state, rendering or accessibility proof.', 'Handler references/bodies/options are source identities, not semantic equivalence; moves/renames may require review.', 'Template holes, anonymous controls, unresolved receivers and parse errors remain incomplete, even unchanged.', 'Attribute-shaped DOM assignments are conservatively recorded; unresolved receivers may be non-DOM objects.', 'No path-sensitive reachability, wrapper/helper expansion, event delegation semantics or imported callback-body analysis.'],
  };
}

function git(repo, args) {
  const env = { ...process.env, GIT_NO_LAZY_FETCH: '1', GIT_TERMINAL_PROMPT: '0' };
  // A caller's Git overrides must not redirect -C to another owner's checkout or inject config.
  for (const key of Object.keys(env)) if (/^GIT_(?:DIR|WORK_TREE|INDEX_FILE|COMMON_DIR|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES|CONFIG(?:_.*)?)$/.test(key)) delete env[key];
  return execFileSync('git', ['--no-pager', '--no-optional-locks', '--no-replace-objects', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null', '-c', 'protocol.allow=never', '-C', repo, ...args], {
    encoding: 'utf8', maxBuffer: LIMITS.totalBytes, stdio: ['ignore', 'pipe', 'pipe'], env,
  });
}

export function readGitSources(repo, ref) {
  if (!/^[a-f0-9]{40}$/.test(ref)) throw new Error('--ref must be an exact lowercase 40-character Git commit');
  if (git(repo, ['rev-parse', '--verify', `${ref}^{commit}`]).trim() !== ref) throw new Error('Git reference is not the requested commit');
  const files = git(repo, ['ls-tree', '-r', '-z', '--full-tree', ref, '--', 'desktop']).split('\0').filter(Boolean).map(record => {
    const match = /^(\d+) (\w+) ([a-f0-9]+)\t(.+)$/.exec(record);
    if (!match) throw new Error('Unexpected Git tree record');
    return { mode: match[1], type: match[2], object: match[3], path: match[4] };
  }).filter(file => scoped(file.path));
  if (!files.length || files.length > LIMITS.files) throw new Error('No root sources or file limit exceeded');
  let total = 0;
  const sized = files.map(file => {
    if (!['100644', '100755'].includes(file.mode) || file.type !== 'blob') throw new Error(`Source is not a regular Git blob: ${file.path}`);
    const bytes = Number(git(repo, ['cat-file', '-s', file.object]).trim());
    if (!Number.isSafeInteger(bytes) || bytes > LIMITS.fileBytes) throw new Error('Git source exceeds byte limit');
    total += bytes;
    if (total > LIMITS.totalBytes) throw new Error('Git source inventory exceeds total byte limit');
    return file;
  });
  return sized.map(file => {
    return { path: file.path, text: git(repo, ['cat-file', 'blob', file.object]) };
  });
}

function inside(repo, path) {
  const real = realpathSync(path), rel = relative(realpathSync(repo), real);
  if (rel === '..' || rel.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) || isAbsolute(rel)) throw new Error('Source path escapes repository');
  return real;
}

export function readWorkingSources(repo) {
  const sources = [], observations = [];
  let total = 0;
  for (const directory of ['desktop', 'desktop/src']) {
    const root = inside(repo, join(repo, directory));
    for (const name of readdirSync(root).sort(order)) {
      const path = `${directory}/${name}`;
      if (!scoped(path)) continue;
      const absolute = join(root, name), stat = lstatSync(absolute);
      if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`Source is not a regular file: ${path}`);
      inside(repo, absolute);
      if (stat.size > LIMITS.fileBytes) throw new Error(`Source exceeds byte limit: ${path}`);
      total += stat.size;
      if (total > LIMITS.totalBytes || sources.length >= LIMITS.files) throw new Error('Working source inventory exceeds limits');
      sources.push({ path, text: readFileSync(absolute, 'utf8') });
      observations.push({ absolute, size: stat.size, mtime: stat.mtimeMs, ctime: stat.ctimeMs, ino: stat.ino });
    }
  }
  // Parent may be editing concurrently. An observed read race is incomplete, never a pass.
  for (const previous of observations) {
    const current = lstatSync(previous.absolute);
    if (!current.isFile() || current.size !== previous.size || current.mtimeMs !== previous.mtime || current.ctimeMs !== previous.ctime || current.ino !== previous.ino) throw new Error('Working source changed during inventory; rerun after the edit settles');
  }
  const paths = ['desktop', 'desktop/src'].flatMap(directory => readdirSync(join(repo, directory)).map(name => `${directory}/${name}`).filter(scoped)).sort(order);
  if (json(paths) !== json(sources.map(source => source.path).sort(order))) throw new Error('Working source file list changed during inventory');
  checkSources(sources);
  return sources;
}

export const HELP = `Usage (from desktop):
  node scripts/design-contract.mjs before --ref <full-40-character-commit> [--out /path/before.json] [--json]
  node scripts/design-contract.mjs kept --baseline /path/before.json [--out /path/kept.json] [--json]
Options: --repo <repository> (default: this script's repository), --help
before reads Git blobs only. kept revalidates the baseline against that exact Git commit,
then reads root current source files (including untracked sources, excluding symlinks).
JSON is always printed to stdout; --out additionally writes a new JSON receipt (no overwrite).
Exit: 0 = captured BEFORE or fully KEPT; 1 = static removals; 2 = incomplete/unknown or invalid input.
Capture exit 0 records unknowns; only KEPT exit 0 means a fully resolved static comparison.
Scope: desktop/*.html and desktop/src/*.{ts,tsx,html}, not recursive. Includes ui.ts,
main.ts and DEV/demo declarations as source evidence only. No application code is executed.
Copy, ordinary classes and element order are free; queried classes and wiring are pinned.
No runtime, rendered, accessibility, native or actual-data claim. See JSON unknowns and limits.
`;

export function runCli(args) {
  if (args.length === 0 || args.includes('--help')) { process.stdout.write(HELP); return 0; }
  const [command, ...rest] = args, options = {};
  if (!['before', 'kept'].includes(command)) throw new Error('Expected before or kept; use --help');
  for (let i = 0; i < rest.length; i++) {
    const key = rest[i];
    if (!['--repo', '--ref', '--baseline', '--out', '--json'].includes(key) || key in options) throw new Error(`Unknown or duplicate option: ${key}`);
    if (key === '--json') options[key] = true;
    else {
      if (!rest[i + 1] || rest[i + 1].startsWith('--')) throw new Error(`Missing value: ${key}`);
      options[key] = rest[++i];
    }
  }
  const repo = realpathSync(resolve(options['--repo'] ?? join(dirname(fileURLToPath(import.meta.url)), '../..')));
  let result, exitCode;
  if (command === 'before') {
    if (!options['--ref'] || options['--baseline']) throw new Error('before requires --ref and does not accept --baseline');
    result = inventorySources(readGitSources(repo, options['--ref']), { kind: 'git', ref: options['--ref'] });
    exitCode = 0;
  } else {
    if (!options['--baseline'] || options['--ref']) throw new Error('kept requires --baseline and does not accept --ref');
    const baselinePath = resolve(options['--baseline']);
    if (lstatSync(baselinePath).size > LIMITS.totalBytes) throw new Error('Baseline exceeds byte limit');
    const before = validateManifest(JSON.parse(readFileSync(baselinePath, 'utf8')));
    if (before.source?.kind !== 'git') throw new Error('CLI baseline must be captured from Git');
    const rebuilt = inventorySources(readGitSources(repo, before.source.ref), before.source);
    if (json(rebuilt) !== json(before)) throw new Error('Baseline differs from its exact Git source; recapture with this tool');
    const current = inventorySources(readWorkingSources(repo), { kind: 'worktree', baselineRef: before.source.ref });
    result = { ...compareManifests(before, current), currentFiles: current.files };
    exitCode = result.exitCode;
  }
  const output = `${JSON.stringify(result, null, 2)}\n`;
  if (options['--out']) {
    const path = resolve(options['--out']);
    const actual = join(realpathSync(dirname(path)), path.slice(dirname(path).length + 1));
    if (actual === repo || actual.startsWith(`${join(repo, 'desktop')}/`)) throw new Error('--out must be outside desktop source/dependencies');
    writeFileSync(path, output, { flag: 'wx', mode: 0o600 });
  }
  process.stdout.write(output);
  return exitCode;
}

// Importing from Vitest or another tool does no I/O, Git work, argument parsing or writes.
if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  try { process.exitCode = runCli(process.argv.slice(2)); }
  catch (error) {
    process.stdout.write(`${JSON.stringify({ schema: SCHEMA, status: 'error', exitCode: 2, error: error instanceof Error ? error.message : String(error) })}\n`);
    process.exitCode = 2;
  }
}

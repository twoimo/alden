// @vitest-environment happy-dom
// Source and synthetic DOM contracts only: no browser layout or a11y acceptance.
import { readFileSync } from 'node:fs';
import { URL as FileURL } from 'node:url';
import { describe, expect, it } from 'vitest';
import { terminalMarkup } from '../knowledge/terminal';

// A variable path avoids Vite's asset-URL transform; Node reads only these sources.
const read = (relative: string) => readFileSync(new FileURL(relative, import.meta.url), 'utf8');
const CSS = read('../knowledge/terminal.css');
const SHARED_CSS = read('../styles.css');
const UI = read('../ui.ts');
const TERMINAL = read('../knowledge/terminal.ts');
const COLLECTION = read('../knowledge/collection-graph.ts');
const controls = [
  'button.terminal-row',
  '.terminal-panel #terminal-follow',
  '.settings-view .knowledge-hologram-toolbar button',
  '.settings-shell[data-settings-page="memory"] .knowledge-hologram-toolbar button',
];
const panels = ['ingest', 'linked', 'trend', 'sectors', 'chord', 'tail'];
const graphScopes = ['.settings-shell[data-settings-page="memory"] .settings-main', 'body.panel-view'];
type Declarations = Pick<CSSStyleDeclaration, 'length' | 'item' | 'cssText' | 'getPropertyValue' | 'getPropertyPriority'>;
type Rule = { selectors: string[]; style: Declarations; media: string[] };

function rules(source: string): Rule[] {
  // This sheet has declarations and media blocks, no escaped braces or keyframes.
  // Preserve var() shorthands verbatim: Happy DOM misparses their font/outline values.
  const result: Rule[] = [];
  const visit = (text: string, media: string[] = []) => {
    let cursor = 0;
    while (cursor < text.length) {
      const open = text.indexOf('{', cursor); if (open < 0) break;
      const selector = text.slice(cursor, open).trim();
      let end = open + 1, depth = 1;
      for (; end < text.length && depth; end++) {
        if (text[end] === '{') depth++; else if (text[end] === '}') depth--;
      }
      if (depth) throw new Error('Unbalanced source stylesheet');
      const body = text.slice(open + 1, end - 1);
      if (selector.startsWith('@media')) visit(body, [...media, selector.slice(6).trim()]);
      else {
        if (selector.startsWith('@')) throw new Error(`Unsupported contract block: ${selector}`);
        const values = new Map(body.split(';').filter(value => value.trim()).map((value): [string, string] => {
          const colon = value.indexOf(':');
          if (colon < 0) throw new Error(`Missing declaration value: ${value}`);
          return [value.slice(0, colon).trim(), value.slice(colon + 1).trim()];
        }));
        const keys = [...values.keys()];
        result.push({ selectors: selector.split(',').map(value => value.trim()), media, style: {
          length: keys.length, item: index => keys[index] ?? '', cssText: body,
          getPropertyValue: key => (values.get(key) ?? '').replace(/\s*!important$/, ''),
          getPropertyPriority: key => /!important$/.test(values.get(key) ?? '') ? 'important' : '',
        } });
      }
      cursor = end;
    }
  };
  visit(source.replace(/\/\*[\s\S]*?\*\//g, ''));
  return result;
}

// Exact selector declarations, in source order. This deliberately does not simulate
// browser specificity, media matching, geometry, font fallback or runtime scrolling.
function declarations(selector: string, source = CSS): Map<string, string> {
  const values = new Map<string, string>();
  for (const rule of rules(source).filter(r => !r.media.length && r.selectors.includes(selector))) {
    for (let i = 0; i < rule.style.length; i++) {
      const property = rule.style.item(i);
      values.set(property, rule.style.getPropertyValue(property));
    }
  }
  return values;
}

function focusViolations(source: string): string[] {
  return controls.flatMap(selector => {
    const style = declarations(`${selector}:focus-visible`, source);
    const errors: string[] = [];
    if (!/^([2-9]|\d{2,})(?:\.\d+)?px solid var\(--terminal-focus\)$/.test(style.get('outline') ?? '')) errors.push('missing ring');
    if (!(Number.parseFloat(style.get('outline-offset') ?? '') <= -2)) errors.push('clipped ring');
    if (style.get('transition') !== 'none' || style.get('animation') !== 'none') errors.push('delayed focus');
    return errors.map(error => `${selector}: ${error}`);
  });
}

function concealedSurfaces(source: string): string[] {
  // Existing explicit collapse, duplicate ingest stage label and auxiliary layout
  // are allowed. No breakpoint can remove a terminal panel or functional control.
  const allowed = new Set(['.knowledge-terminal[hidden]', '.terminal-ingest .terminal-row > small', '.knowledge-observations', '.knowledge-toolbar-spacer']);
  return rules(source).flatMap(rule => rule.selectors.filter(selector => !allowed.has(selector)
    && (rule.style.getPropertyValue('display') === 'none' || rule.style.getPropertyValue('visibility') === 'hidden')));
}

function aliasScopeViolations(source: string): string[] {
  const aliases = [...declarations(':root', source)].filter(([, value]) => /var\(--graph-/.test(value));
  return graphScopes.flatMap(scope => {
    const local = declarations(scope, source);
    return aliases.filter(([token, value]) => local.get(token) !== value).map(([token]) => `${scope}: ${token}`);
  });
}

describe('terminal design source contract', () => {
  it('retains all six named, labelled panels, empty data hosts and the native follow action', () => {
    const host = document.createElement('div'); host.innerHTML = terminalMarkup();
    const articles = [...host.querySelectorAll('.terminal-panel')];
    expect(articles).toHaveLength(6);
    expect(articles.map(panel => panel.querySelector('h2')?.textContent)).toEqual(['INGEST', 'LINKED', 'f(x)', 'SECTORS', 'CHORD', 'TAIL-F']);
    articles.forEach((panel, index) => {
      const id = `terminal-${panels[index]}`;
      expect(panel.getAttribute('aria-labelledby')).toBe(`${id}-heading`);
      expect(panel.querySelector(`#${id}-heading`)).not.toBeNull();
      expect(panel.querySelector(`#${id}.terminal-content`)?.childElementCount).toBe(0);
    });
    expect(host.querySelectorAll('button')).toHaveLength(1);
    expect(host.querySelector('.terminal-tail #terminal-follow')?.getAttribute('type')).toBe('button');
    expect(host.querySelector('#terminal-follow')?.getAttribute('aria-pressed')).toBe('true');
  });

  it('preserves separate pause, follow and collapse controls and their distinct state wiring', () => {
    for (const id of ['knowledge-back', 'knowledge-overview', 'knowledge-expand-hop', 'knowledge-reflow', 'knowledge-camera-reset', 'knowledge-live-toggle', 'knowledge-terminal-toggle']) {
      expect(UI).toContain(`id="${id}"`);
    }
    expect(UI).toMatch(/id="knowledge-live-toggle"[^>]*aria-pressed="true"/);
    expect(UI).toMatch(/id="knowledge-terminal-toggle"[^>]*aria-controls="knowledge-terminal"[^>]*aria-expanded="true"/);
    const listener = (source: string, id: string) => source.split(`getElementById('${id}')?.addEventListener('click', () => {`)[1]?.split('}, { signal });')[0];
    expect(listener(COLLECTION, 'knowledge-live-toggle')).toContain('this.paused = !this.paused');
    expect(listener(TERMINAL, 'terminal-follow')).toContain('this.follow = !this.follow');
    expect(listener(TERMINAL, 'terminal-follow')).not.toContain('this.paused');
    expect(listener(TERMINAL, 'knowledge-terminal-toggle')).toContain('dock.hidden = !dock.hidden');
    expect(declarations('.knowledge-terminal[hidden]').get('display')).toBe('none');
    expect(declarations('.settings-view #knowledge-live-toggle[aria-pressed="false"]').get('color')).toBe('var(--terminal-warning)');
    expect(declarations('.terminal-panel #terminal-follow[aria-pressed="false"]').get('color')).toBe('var(--terminal-warning)');
  });

  it.each(controls)('keeps readable type and at least 32px interaction height: %s', selector => {
    const style = declarations(selector === 'button.terminal-row' ? '.terminal-row' : selector);
    const height = Number.parseFloat(style.get('min-height') ?? '');
    const size = Number.parseFloat(style.get('font-size') ?? style.get('font')?.match(/(\d+(?:\.\d+)?)px/)?.[1] ?? '');
    expect(height).toBeGreaterThanOrEqual(32);
    expect(height).toBeLessThanOrEqual(36);
    expect(size).toBeGreaterThanOrEqual(11.5);
    expect(style.get('box-sizing')).toBe('border-box');
  });

  it('provides immediate inset focus for rows, follow and both toolbar contexts', () => {
    expect(focusViolations(CSS)).toEqual([]);
  });

  it('rejects a deliberate missing-ring regression on all four control selectors', () => {
    const broken = CSS.replace('outline: 2px solid var(--terminal-focus);', 'outline: none;');
    expect(broken).not.toBe(CSS);
    expect(focusViolations(broken).filter(error => error.endsWith('missing ring'))).toHaveLength(4);
  });

  it('rejects hover-only focus styling even when hover decoration survives', () => {
    expect(focusViolations(CSS.replaceAll(':focus-visible', ':hover')).filter(error => error.endsWith('missing ring'))).toHaveLength(4);
  });

  it.each([
    ['outline-offset: -3px;', 'outline-offset: 3px;', 'clipped ring'],
    ['transition: none; animation: none;', 'transition: outline-color 150ms; animation: none;', 'delayed focus'],
  ])('rejects unsafe focus replacement: %s', (before, after, error) => {
    const broken = CSS.replace(before, after);
    expect(broken).not.toBe(CSS);
    expect(focusViolations(broken).filter(issue => issue.endsWith(error))).toHaveLength(4);
  });

  it('keeps the six-column dock and panel bodies internally scrollable at narrow widths', () => {
    const dock = declarations('.knowledge-terminal'), content = declarations('.terminal-content');
    expect(dock.get('min-width')).toBe('0');
    expect(dock.get('max-width')).toBe('100%');
    expect(dock.get('overflow')).toBe('auto');
    expect(dock.get('overscroll-behavior')).toBe('contain');
    const tracks = [...(dock.get('grid-template-columns') ?? '').matchAll(/minmax\((\d+)px,\s*[\d.]+fr\)/g)];
    expect(tracks).toHaveLength(6);
    expect(Number(tracks[5][1])).toBeGreaterThanOrEqual(260);
    expect(content.get('overflow-y')).toBe('auto');
    expect(content.get('min-height')).toBe('0');
    expect(declarations('.terminal-panel').get('min-height')).toBe('0');
    expect(declarations('.settings-view .knowledge-hologram-toolbar').get('flex-wrap')).toBe('wrap');
    expect(concealedSurfaces(CSS)).toEqual([]);
    for (const rule of rules(CSS).filter(rule => rule.media.some(media => /max-width/.test(media)) && rule.selectors.includes('.knowledge-terminal'))) {
      expect(rule.style.getPropertyValue('grid-template-columns')).toBe('');
      expect(rule.style.getPropertyValue('overflow')).toBe('');
    }
  });

  it('rejects a deliberate narrow-screen missing-panel regression', () => {
    expect(concealedSurfaces(`${CSS}\n@media (max-width: 720px) { .terminal-tail { display: none; } }`)).toEqual(['.terminal-tail']);
  });

  it('disables transitions, animation and smooth scrolling under reduced motion', () => {
    const reduced = rules(CSS).filter(rule => rule.media.some(media => /prefers-reduced-motion:\s*reduce/.test(media)));
    for (const selector of ['.knowledge-terminal', '.knowledge-terminal *', '.knowledge-terminal *::before', '.knowledge-terminal *::after', '.knowledge-hologram-toolbar', '.knowledge-hologram-toolbar *', '.knowledge-observations *']) {
      const rule = reduced.find(rule => rule.selectors.includes(selector));
      expect(rule, selector).toBeDefined();
      for (const [property, value] of [['animation', 'none'], ['transition', 'none'], ['scroll-behavior', 'auto']]) {
        expect(rule!.style.getPropertyValue(property)).toBe(value);
        expect(rule!.style.getPropertyPriority(property)).toBe('important');
      }
    }
    expect(CSS).not.toMatch(/transition\s*:\s*all\b/);
  });

  it('declares terminal color/font roles at root and retains the graph palette and system mono labels', () => {
    const root = declarations(':root');
    for (const role of ['canvas', 'surface', 'text', 'muted', 'border', 'accent', 'accent-ink', 'warning', 'selection']) {
      expect(root.get(`--terminal-${role}`)).toBe(`var(--graph-${role})`);
    }
    expect(root.get('--terminal-font-ui')).toBe('var(--font-ui)');
    expect(root.get('--terminal-font-mono')).toBe('var(--font-mono)');
    expect(root.get('--terminal-font-label')).toMatch(/ui-monospace.*SFMono-Regular.*monospace/);
    expect(declarations('.terminal-panel h2').get('font')).toContain('var(--terminal-font-label)');
    expect(declarations('.terminal-panel h2').get('letter-spacing')).toBe('0');
    for (const [, token] of CSS.matchAll(/var\((--terminal-[\w-]+)/g)) expect(root.has(token), token).toBe(true);
    for (const rule of rules(CSS).filter(rule => !rule.selectors.includes(':root'))) {
      expect(rule.style.cssText).not.toMatch(/#[\da-f]{3,8}\b|\b(?:rgba?|hsla?)\(/i);
      for (const property of ['font', 'font-family']) {
        const value = rule.style.getPropertyValue(property);
        if (value) expect(value).toContain('var(--terminal-font-');
      }
    }
    expect(declarations('.terminal-sector::before').get('width')).toBe('var(--sector-share)');
    expect(declarations('.terminal-sector::before').get('background')).toBe('var(--sector-color)');
  });

  it('rebinds every derived graph alias on both consuming scopes before inheritance', () => {
    expect(declarations(':root').get('--terminal-canvas')).toBe('var(--graph-canvas)');
    expect(aliasScopeViolations(CSS)).toEqual([]);
    for (const scope of graphScopes) {
      expect(declarations(scope).get('--terminal-canvas')).toBe('var(--graph-canvas)');
      expect(declarations(scope).get('--terminal-focus')).toBe('var(--graph-accent-ink)');
    }
  });

  it('rejects the observed root-only alias regression despite identical token values', () => {
    const rootOnly = CSS.replace(/:root,\s*\.settings-shell\[data-settings-page="memory"\] \.settings-main,\s*body\.panel-view\s*\{/, ':root {');
    expect(rootOnly).not.toBe(CSS);
    expect(declarations(':root', rootOnly).get('--terminal-canvas')).toBe('var(--graph-canvas)');
    expect(aliasScopeViolations(rootOnly)).toEqual(expect.arrayContaining(graphScopes.map(scope => `${scope}: --terminal-canvas`)));
  });

  it.each(['light', 'dark'])('has at least 3:1 declared default focus contrast in the %s theme', theme => {
    const tokens = declarations(':root', SHARED_CSS);
    if (theme === 'dark') {
      for (const rule of rules(SHARED_CSS).filter(rule => rule.selectors.includes(':root') && rule.media.some(media => media.includes('prefers-color-scheme: dark')))) {
        for (let i = 0; i < rule.style.length; i++) { const key = rule.style.item(i); tokens.set(key, rule.style.getPropertyValue(key)); }
      }
    }
    for (const [key, value] of declarations(':root')) tokens.set(key, value);
    const color = (token: string, underneath = [0, 0, 0]): number[] => {
      let value = tokens.get(token) ?? '';
      for (let hop = 0; /^var\(/.test(value) && hop < 8; hop++) value = tokens.get(value.slice(4, -1)) ?? '';
      if (/^#[\da-f]{6}$/i.test(value)) return [1, 3, 5].map(start => Number.parseInt(value.slice(start, start + 2), 16));
      const rgba = /^rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/.exec(value);
      expect(rgba, value).not.toBeNull();
      const alpha = Number(rgba![4]);
      return [1, 2, 3].map((channel, i) => Number(rgba![channel]) * alpha + underneath[i] * (1 - alpha));
    };
    const luminance = (rgb: number[]) => rgb.map(value => {
      const channel = value / 255;
      return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
    }).reduce((sum, value, i) => sum + value * [.2126, .7152, .0722][i], 0);
    const ring = luminance(color('--terminal-focus'));
    const canvas = color('--terminal-canvas');
    for (const background of ['--terminal-canvas', '--terminal-surface', '--terminal-selection']) {
      const surface = luminance(color(background, canvas));
      expect((Math.max(ring, surface) + .05) / (Math.min(ring, surface) + .05), background).toBeGreaterThanOrEqual(3);
    }
  });
});

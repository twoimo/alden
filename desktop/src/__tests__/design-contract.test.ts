// The design contract in desktop/DESIGN.md is only binding if something fails
// when the code drifts away from it. These checks pin the palette, the ban on
// cyberpunk treatments, and the ban on decorative shadows to real files.
import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { DARK_TOKENS, LAYOUT, LIGHT_TOKENS } from "../tokens";
// @ts-expect-error The standalone ESM sidecar has no separate declaration file.
import { compareManifests, inventorySources, validateManifest } from "../../scripts/design-contract.mjs";

const read = (relative: string): string =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const STYLES = read("../styles.css");
const DESIGN = read("../../DESIGN.md");
const CORE = read("../core/alden-core.ts");
const HOLOGRAM = read("../knowledge/hologram.ts");
const COSMOS = read("../knowledge/cosmos.ts");
const TOKENS_SOURCE = read("../tokens.ts");

const hexCodes = (text: string): string[] =>
  [...new Set(text.match(/#[0-9A-Fa-f]{6}\b/g) ?? [])].map((value) => value.toUpperCase());

const paletteLine = (label: string): string[] => {
  const line = DESIGN.split("\n").find((candidate) => candidate.startsWith(`${label}:`));
  if (!line) throw new Error(`DESIGN.md is missing the ${label} palette line`);
  return hexCodes(line);
};

const DESIGN_LIGHT = paletteLine("Light");
const DESIGN_DARK = paletteLine("Dark");
const DESIGN_PALETTE = new Set([...DESIGN_LIGHT, ...DESIGN_DARK, ...paletteLine("Cosmos")]);

const linearChannel = (value: number): number => {
  const channel = value / 255;
  return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
};

const luminance = (hex: string): number => {
  const channels = [1, 3, 5].map((index) => linearChannel(Number.parseInt(hex.slice(index, index + 2), 16)));
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
};

const contrastRatio = (first: string, second: string): number => {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
};

const composite = (foreground: string, background: string, alpha: number): string => {
  const channels = [1, 3, 5].map((index) => {
    const front = Number.parseInt(foreground.slice(index, index + 2), 16);
    const back = Number.parseInt(background.slice(index, index + 2), 16);
    return Math.round(front * alpha + back * (1 - alpha)).toString(16).padStart(2, "0");
  });
  return `#${channels.join("")}`.toUpperCase();
};

describe("design contract", () => {
  it("DESIGN.md light palette matches LIGHT_TOKENS", () => {
    expect([...DESIGN_LIGHT].sort()).toEqual(
      hexCodes(Object.values(LIGHT_TOKENS).join(" ")).sort()
    );
  });

  it("DESIGN.md dark palette matches DARK_TOKENS", () => {
    expect([...DESIGN_DARK].sort()).toEqual(
      hexCodes(Object.values(DARK_TOKENS).join(" ")).sort()
    );
  });

  it("styles.css declares exactly the token-module palette for both schemes", () => {
    const lightBlock = STYLES.slice(STYLES.indexOf(":root"), STYLES.indexOf("@media"));
    const darkBlock = STYLES.slice(STYLES.indexOf("@media"));
    expect(hexCodes(lightBlock).sort()).toEqual(
      hexCodes(Object.values(LIGHT_TOKENS).join(" ")).sort()
    );
    expect(hexCodes(darkBlock).sort()).toEqual(
      hexCodes(Object.values(DARK_TOKENS).join(" ")).sort()
    );
  });

  it("tokens.ts defines no color outside the DESIGN.md palette", () => {
    expect(hexCodes(TOKENS_SOURCE).filter((value) => !DESIGN_PALETTE.has(value))).toEqual([]);
  });

  it("styles.css introduces no color outside the DESIGN.md palette", () => {
    expect(hexCodes(STYLES).filter((value) => !DESIGN_PALETTE.has(value))).toEqual([]);
  });

  it("uses readable champagne text on light and dark selected surfaces", () => {
    const lightSelection = composite(LIGHT_TOKENS.accent, LIGHT_TOKENS.surface, 0.11);
    const darkSelection = composite(DARK_TOKENS.accent, DARK_TOKENS.surface, 0.13);
    expect(contrastRatio(LIGHT_TOKENS.accentInk, lightSelection)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(DARK_TOKENS.accentInk, darkSelection)).toBeGreaterThanOrEqual(4.5);
    expect(STYLES).not.toMatch(/color:\s*var\(--accent\)\s*[!;]/);
    expect(STYLES).toContain("--selection: rgba(184, 138, 69, 0.11)");
    expect(STYLES).toContain("--selection: rgba(213, 179, 110, 0.13)");
  });

  it("the shell and the 3D core introduce no color outside the DESIGN.md palette", () => {
    const offenders = [...hexCodes(CORE), ...hexCodes(HOLOGRAM), ...hexCodes(COSMOS)].filter(
      (value) => !DESIGN_PALETTE.has(value)
    );
    expect(offenders).toEqual([]);
  });

  it("no neon, bloom, glow, or cyberpunk treatment is applied", () => {
    const surfaces: Array<[string, string]> = [
      ["styles.css", STYLES],
      ["core/alden-core.ts", CORE],
    ];
    for (const [name, source] of surfaces) {
      const match = source.match(/neon|bloom|glow|cyberpunk|drop-shadow/i);
      expect(match?.[0], `${name} must not use ${match?.[0]}`).toBeUndefined();
    }
  });

  it("every box-shadow declaration is none, so no card carries a decorative shadow", () => {
    const declarations = [...STYLES.matchAll(/box-shadow:\s*([^;]+);/g)].map((match) =>
      match[1].trim().toLowerCase()
    );
    expect(declarations.length).toBeGreaterThan(0);
    expect(declarations.filter((value) => value !== "none")).toEqual([]);
  });

  it("uses the settings window width on desktop and keeps a single column on narrow screens", () => {
    expect(LAYOUT.settingsMaxWidth).toBe(912);
    expect(STYLES).toContain("width: min(912px, calc(100% - 48px))");
    expect(STYLES).toMatch(/@media \(min-width: 800px\)[\s\S]*grid-template-columns: minmax\(0, 61\.8fr\) minmax\(300px, 38\.2fr\)/);
    expect(STYLES).toMatch(/@media \(max-width: 799px\)[\s\S]*\.settings-shell \{ width: calc\(100% - 32px\)/);
    expect(STYLES).toContain("font-size: 16px; line-height: 1.3");
    expect(STYLES).toContain("font-size: 14px; line-height: 1.5");
    expect(STYLES).toContain('section[aria-labelledby="history-title"] { grid-column: 1 / -1; }');
    expect(STYLES).toContain("#knowledge-graph-canvas { height: 320px; }");
    expect(STYLES).toContain("#history-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }");
  });
});

// Synthetic sources only: never import the application, render demo data or start Alden.
describe("UI wiring preservation sidecar", () => {
  const source = (text: string, path = "desktop/src/ui.ts") => ({ path, text });
  const scan = (text: string, path?: string) => inventorySources([source(text, path)]);
  const compare = (before: string, after: string) => compareManifests(scan(before), scan(after));
  const fixture = `
    export function settingsMarkup() {
      return '<form id="settings"><input id="room" name="room" type="search" class="old"><button id="save" data-action="save" type="button">Save</button><a href="/help" id="help">Help</a></form>';
    }
    const save = document.getElementById('save');
    function persist() { store.save(); }
    save?.addEventListener('click', persist, { once: true });
  `;

  it("keeps IDs, named fields, data actions, links and callback identity through copy/classes/order changes", () => {
    const after = fixture.replace('class="old"', 'class="new spacious"')
      .replace('>Save<', '>보관<').replace('>Help<', '>도움말<')
      .replace('<input id="room" name="room" type="search" class="new spacious">', '')
      .replace('</form>', '<input class="new spacious" type="search" name="room" id="room"></form>');
    const result = compare(fixture, after);
    expect(result.status).toBe("kept");
    expect(result.summary.kept).toMatchObject({ id: 4, field: 1, data: 1, link: 1, handler: 1 });
    expect(result.removed).toEqual([]);
  });

  it("fails a deliberate lost named control even when its label and neighboring button survive", () => {
    const result = compare(fixture, fixture.replace('<input id="room" name="room" type="search" class="old">', '<span>Room</span>'));
    expect(result.status).toBe("failed");
    expect(result.exitCode).toBe(1);
    expect(result.removed.map((entry: { kind: string }) => entry.kind)).toEqual(expect.arrayContaining(["id", "control", "field"]));
  });

  it("fails a lost name even if the control and ID remain", () => {
    const result = compare(fixture, fixture.replace(' name="room"', ''));
    expect(result.status).toBe("failed");
    expect(result.removed).toEqual([expect.objectContaining({ kind: "field", value: "room:input:search" })]);
  });

  it.each([
    ["listener", "save?.addEventListener('click', persist, { once: true });", ""],
    ["event", "addEventListener('click'", "addEventListener('change'"],
    ["callback", "'click', persist", "'click', discard"],
    ["callback definition", "store.save()", "store.delete()"],
    ["listener options", "{ once: true }", "{ once: false }"],
    ["receiver", "getElementById('save')", "getElementById('help')"],
  ])("fails a deliberate %s mutation", (_label, from, to) => {
    const result = compare(fixture, fixture.replace(from, to));
    expect(result.status).toBe("failed");
    expect(result.removed).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "handler" })]));
  });

  it("uses a multiset so one remaining named control cannot hide a removed duplicate", () => {
    const input = '<form id="f"><input name="tag"><input name="tag"></form>';
    const result = compareManifests(scan(input, "desktop/index.html"), scan(input.replace('<input name="tag">', ''), "desktop/index.html"));
    expect(result.status).toBe("failed");
    expect(result.removed.filter((entry: { kind: string }) => entry.kind === "field")).toHaveLength(1);
  });

  it("resolves lexical receiver aliases, typed casts and selector string constants without conflating scopes", () => {
    const input = `const id = 'save';
      function outer() {
        const button = document.getElementById(id) as HTMLButtonElement;
        button.addEventListener('click', () => persist());
      }
      function inner() {
        const button = document.querySelector<HTMLButtonElement>('#cancel');
        button?.addEventListener('click', () => cancel());
      }`;
    const handlers = scan(input).entries.filter((entry: { kind: string }) => entry.kind === "handler");
    expect(handlers.map((entry: { subject: string }) => entry.subject)).toEqual(expect.arrayContaining([
      "document/getElementById:save", "document/querySelector:#cancel",
    ]));
    expect(compare(input, input.replaceAll("'click'", '"click"').replace('persist()', '/* keep */ persist ( )')).status).toBe("kept");
  });

  it("preserves queried class hooks while allowing decorative classes to change", () => {
    const input = `export const markup = '<canvas class="graph decorative" id="graph"></canvas>';
      document.querySelector('.graph')?.addEventListener('click', () => choose());`;
    expect(compare(input, input.replace('graph decorative', 'graph polished')).status).toBe("kept");
    const result = compare(input, input.replace('graph decorative', 'polished'));
    expect(result.status).toBe("failed");
    expect(result.removed).toEqual([expect.objectContaining({ kind: "queried-class", subject: ".graph" })]);
  });

  it("does not mistake mutable initializers or parameter defaults for fixed bindings", () => {
    const input = "let id = 'save'; id = choose(); const button = document.getElementById(id); button.addEventListener('click', () => save());"
      + "function wire(root: Document = document) { root.getElementById('save')?.addEventListener('click', () => save()); }";
    const result = compare(input, input);
    expect(result.status).toBe("incomplete");
    expect(result.unknowns).toEqual(expect.arrayContaining([
      expect.objectContaining({ code: "dynamic-selector" }),
      expect.objectContaining({ code: "unresolved-handler-target" }),
    ]));
  });

  it("recognizes a literal bracket-method listener and fails its removal", () => {
    const input = "document.getElementById('save')?.['addEventListener']('click', () => save());";
    expect(scan(input).entries).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "handler" })]));
    expect(compare(input, "document.getElementById('save');").status).toBe("failed");
  });

  it("recognizes bracket property handlers and flags opaque computed bindings", () => {
    const input = "const button = document.getElementById('save'); button['onclick'] = () => save();";
    expect(compare(input, input.replace("button['onclick'] = () => save();", '')).status).toBe("failed");
    const dynamic = scan("const button = document.getElementById('save'); button[method]('click', save); button[event] = () => save();");
    expect(compareManifests(dynamic, dynamic).status).toBe("incomplete");
    expect(dynamic.unknowns.map((item: { code: string }) => item.code)).toEqual(expect.arrayContaining(["computed-method-call", "computed-handler-property"]));
  });

  it("pins constant listener options and reports opaque option aliases", () => {
    const input = "const options = { once: true }; document.getElementById('save')?.addEventListener('click', () => save(), options);";
    expect(compare(input, input.replace('once: true', 'once: false')).status).toBe("failed");
    const opaque = scan(input.replace('const options = { once: true };', ''));
    expect(compareManifests(opaque, opaque).status).toBe("incomplete");
    expect(opaque.unknowns).toEqual(expect.arrayContaining([expect.objectContaining({ code: "unresolved-listener-options" })]));
  });

  it("reports unchanged dynamic IDs, markup holes and delegated receivers as incomplete", () => {
    const input = 'function render(key: string) { return `<input id="field-${key}" name="room">${externalMarkup()}</input>`; }\n'
      + "buttons.forEach(button => button.addEventListener('click', () => save()));";
    const result = compare(input, input);
    expect(result.status).toBe("incomplete");
    expect(result.exitCode).toBe(2);
    expect(result.unknowns.map((item: { code: string }) => item.code)).toEqual(expect.arrayContaining([
      "dynamic-attribute-value", "dynamic-markup-hole", "unresolved-handler-target",
    ]));
    expect(compare(input, input.replace(' name="room"', '')).status).toBe("failed");
  });

  it("does not turn comments, raw script bodies or copy into fake HTML controls", () => {
    const input = '<!-- <input name="fake"> --><style>.x::before { content: \'<input name="fake">\'; }</style>'
      + '<script src="/src/main.ts">const text = \'<input name="fake">\';</script>'
      + '<input id="real" name="real" title="a > b"><a id="docs" href="/docs?q=a>b">Docs</a>';
    const manifest = scan(input, "desktop/index.html");
    expect(manifest.entries.filter((entry: { kind: string }) => entry.kind === "field")).toEqual([
      expect.objectContaining({ subject: "#real", value: "real:input:text" }),
    ]);
    expect(manifest.entries).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "resource", value: "/src/main.ts" })]));
    expect(compareManifests(manifest, scan(input.replace('/docs?q=a>b', '/elsewhere'), "desktop/index.html")).status).toBe("failed");
  });

  it("extracts JSX handlers and refuses to call spread attributes fully resolved", () => {
    const input = 'const markup = <button id="save" name="save" onClick={() => persist()} {...props}>Save</button>;';
    const manifest = scan(input, "desktop/src/ui.tsx");
    expect(manifest.entries).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: "handler", subject: "#save" }),
      expect.objectContaining({ kind: "field", value: "save:button:submit" }),
    ]));
    expect(compareManifests(manifest, manifest).status).toBe("incomplete");
    expect(manifest.unknowns).toEqual(expect.arrayContaining([expect.objectContaining({ code: "jsx-spread" })]));
  });

  it("pins DOM property handlers and generated names without executing their source", () => {
    const input = "const button = document.createElement('button'); button.name = 'save'; button.onclick = () => persist();";
    const result = compare(input, input.replace('button.onclick = () => persist();', ''));
    expect(result.status).toBe("failed");
    expect(scan(input).entries).toEqual(expect.arrayContaining([expect.objectContaining({ kind: "field", value: "name=save" })]));
  });

  it("keeps callback copy assignments independent of action identity", () => {
    const input = "document.getElementById('save')?.addEventListener('click', () => { label.textContent = 'Saved'; persist(); });";
    expect(compare(input, input.replace("'Saved'", "'보관됨'")).status).toBe("kept");
    expect(compare(input, input.replace('persist()', 'discard()')).status).toBe("failed");
  });

  it("does not normalize away action calls hidden inside copy assignments", () => {
    const input = "document.getElementById('save')?.addEventListener('click', () => { label.textContent = persist(); });";
    expect(compare(input, input.replace('persist()', 'discard()')).status).toBe("failed");
  });

  it("reports tagged templates, incomplete tag fragments and non-function callback aliases", () => {
    const input = 'const html = custom`<button id="save">Save</button><input name="unfinished`;\n'
      + "const notKnown = externalHandler; document.getElementById('save')?.addEventListener('click', notKnown);";
    const manifest = scan(input);
    expect(compareManifests(manifest, manifest).status).toBe("incomplete");
    expect(manifest.unknowns.map((item: { code: string }) => item.code)).toEqual(expect.arrayContaining([
      "tagged-markup-template", "partial-markup", "unresolved-handler-reference",
    ]));
  });

  it("flags parse errors, empty inventories and malformed manifests instead of passing", () => {
    expect(compare("const empty = 1;", "const empty = 1;").status).toBe("incomplete");
    const invalid = scan('const x = ; const html = \'<input name="x">\';');
    expect(compareManifests(invalid, invalid).status).toBe("incomplete");
    expect(() => validateManifest({ ...scan(fixture), files: [] })).toThrow();
    expect(() => validateManifest({ ...scan(fixture), schema: "future" })).toThrow();
    expect(() => inventorySources([source(fixture, "desktop/src/nested/private.ts")])).toThrow();
    expect(() => inventorySources([source(fixture), source(fixture)])).toThrow();
  });

  it("fails removed root files even when another file has the same declarations", () => {
    const before = inventorySources([source(fixture), source(fixture, "desktop/src/main.ts")]);
    const result = compareManifests(before, scan(fixture));
    expect(result.status).toBe("failed");
    expect(result.removedFiles).toEqual(["desktop/src/main.ts"]);
  });

  it("is deterministic across input enumeration order and contains hashes, not full source bodies", () => {
    const inputs = [source(fixture), source('<main id="app"></main>', "desktop/index.html")];
    const first = inventorySources(inputs);
    expect(first).toEqual(inventorySources([...inputs].reverse()));
    expect(first.files).toEqual(expect.arrayContaining([expect.objectContaining({ sha256: expect.stringMatching(/^[a-f0-9]{64}$/) })]));
    expect(JSON.stringify(first)).not.toContain("function persist()");
  });

  it("imports without output/CLI side effects and exposes help plus structured invalid-input errors", () => {
    const script = fileURLToPath(new URL("../../scripts/design-contract.mjs", import.meta.url));
    const imported = spawnSync(process.execPath, ["--input-type=module", "-e", `await import(${JSON.stringify(new URL("../../scripts/design-contract.mjs", import.meta.url).href)})`], { encoding: "utf8" });
    expect(imported.status).toBe(0);
    expect(imported.stdout).toBe("");
    expect(imported.stderr).toBe("");
    const help = spawnSync(process.execPath, [script, "--help"], { encoding: "utf8" });
    expect(help.status).toBe(0);
    expect(help.stdout).toContain("No runtime");
    const invalid = spawnSync(process.execPath, [script, "before", "--ref", "HEAD; touch forbidden"], { encoding: "utf8" });
    expect(invalid.status).toBe(2);
    expect(JSON.parse(invalid.stdout)).toMatchObject({ status: "error", exitCode: 2 });
  });
});

// The UI module's own build steps, run by tools/web/build.mjs before anything is bundled:
//
// - theme.css, from tokens.json: every token as `--moxi-<name>` on `:root`, each colour
//   written as `light-dark(<the light set's value>, <the dark set's value>)` of the
//   default theme, then each other theme's colours under `:root[data-theme-name="<name>"]`;
// - manifest.json, the gateway's web app manifest, which it serves at
//   /manifest.webmanifest, its colours the default theme's light background;
// - tools/web/client.d.ts, the types of /m/ui/client.js, from src/client.ts by tsc's
//   declaration output. Every page's tsc resolves the client's URL to that file;
// - offline.js, the offline page's script, and sw.js, the gateway's worker, with the
//   offline page and that script built into it as OFFLINE_HTML and OFFLINE_JS: the worker
//   answers both from its own bytes, so it keeps no copy a page could write.

import * as esbuild from "esbuild";
import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import { createRequire } from "node:module";
import * as os from "node:os";
import * as path from "node:path";

const OUT = "packages/ui/src/moxi_ui/web";
const DECL = "tools/web/client.d.ts";
const NAME = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const COLOR = /^#[0-9a-f]{6}$/;
const DIMENSION = /^[0-9]+(\.[0-9]+)?(px|rem|em)$/;
const FAMILY = /^[A-Za-z0-9 -]+$/;

/** A font stack as CSS: a name with a space is quoted, a single word is left bare. */
function family(names, where) {
  if (!Array.isArray(names) || names.length === 0) throw new Error(`${where}: a fontFamily is a list of names`);
  return names.map((name) => {
    if (typeof name !== "string" || !FAMILY.test(name)) throw new Error(`${where}: bad font name ${JSON.stringify(name)}`);
    return name.includes(" ") ? `"${name}"` : name;
  }).join(", ");
}

/** `value` as a table (an object that is no list), or a refusal naming `what`. */
function table(value, what) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Error(`tokens.json: ${what}`);
  return value;
}

/**
 * The themes tokens.json names in `$themes`, in its order, each as `{light, dark}`: the
 * name of the set it shows in each mode. The first is the default theme. Every set in
 * `$sets` belongs to exactly one theme, in the mode the set names.
 */
export function themes(tokens) {
  const sets = table(tokens.$sets, "$sets names the colour sets");
  for (const [name, set] of Object.entries(sets)) {
    if (!NAME.test(name)) throw new Error(`tokens.json: $sets: ${name} is not a set name`);
    if (set?.mode !== "light" && set?.mode !== "dark") {
      throw new Error(`tokens.json: $sets: ${name}'s mode is light or dark`);
    }
  }
  const named = table(tokens.$themes, "$themes names the themes");
  if (Object.keys(named).length === 0) throw new Error("tokens.json: $themes names at least one theme");
  const owner = {};
  const out = {};
  for (const [theme, pair] of Object.entries(named)) {
    if (!NAME.test(theme)) throw new Error(`tokens.json: $themes: ${theme} is not a theme name`);
    const where = `tokens.json: $themes: ${theme}`;
    table(pair, `$themes: ${theme} names one light set and one dark set`);
    if (Object.keys(pair).sort().join() !== "dark,light") {
      throw new Error(`${where} names one light set and one dark set`);
    }
    for (const mode of ["light", "dark"]) {
      const set = pair[mode];
      if (typeof set !== "string" || !Object.hasOwn(sets, set)) {
        throw new Error(`${where}: its ${mode} set ${JSON.stringify(set)} is not a set in $sets`);
      }
      if (sets[set].mode !== mode) throw new Error(`${where}: ${set} is a ${sets[set].mode} set, not a ${mode} one`);
      if (owner[set] !== undefined) throw new Error(`tokens.json: $themes: ${set} is in ${owner[set]} and ${theme}`);
      owner[set] = theme;
    }
    out[theme] = { light: pair.light, dark: pair.dark };
  }
  for (const name of Object.keys(sets)) {
    if (owner[name] === undefined) throw new Error(`tokens.json: $sets: ${name} is in no theme`);
  }
  return out;
}

/** The default theme: `$themes`' first, as `{name, light, dark}`. */
export function defaultTheme(tokens) {
  const [[name, pair]] = Object.entries(themes(tokens));
  return { name, ...pair };
}

/** A colour token's value in each set, checked: every set named, each #rrggbb, no other. */
function colours(name, token, names) {
  const where = `tokens.json: ${name}`;
  const moxi = token.$extensions?.moxi ?? {};
  if (token.$value !== undefined || moxi.dark !== undefined) {
    throw new Error(`${where}: a colour has its values in $extensions.moxi.sets, not $value or dark`);
  }
  const values = moxi.sets;
  if (values === null || typeof values !== "object" || Array.isArray(values)) {
    throw new Error(`${where}: a colour has a value for each set in $extensions.moxi.sets`);
  }
  for (const set of Object.keys(values)) {
    if (!names.includes(set)) throw new Error(`${where}: ${set} is not a set in $sets`);
  }
  for (const set of names) {
    if (!COLOR.test(values[set] ?? "")) throw new Error(`${where}: ${set}'s value is #rrggbb`);
  }
  return values;
}

/** The CSS value of one token for the theme `pair`, checked against its type; `sets` is
 * every set's name. */
function value(name, token, pair, sets) {
  const where = `tokens.json: ${name}`;
  switch (token.$type) {
    case "color": {
      const values = colours(name, token, sets);
      return `light-dark(${values[pair.light]}, ${values[pair.dark]})`;
    }
    case "dimension":
    case "fontFamily": {
      const moxi = token.$extensions?.moxi ?? {};
      if (moxi.sets !== undefined || moxi.dark !== undefined) {
        throw new Error(`${where}: only a colour has a value per set`);
      }
      if (token.$type === "fontFamily") return family(token.$value, where);
      if (!DIMENSION.test(token.$value)) throw new Error(`${where}: a dimension is a number and px, rem or em`);
      return token.$value;
    }
    default:
      throw new Error(`${where}: $type is color, dimension or fontFamily`);
  }
}

/** theme.css's text from the parsed tokens.json: the default theme and every other token
 * on `:root`, then each other theme's colours where the root names it. */
export function themeCss(tokens) {
  const [[first, pair], ...others] = Object.entries(themes(tokens));
  const sets = Object.keys(tokens.$sets);
  const named = (name, theme) => `${name}: light ${theme.light}, dark ${theme.dark}.`;
  const lines = [
    "/* The shared theme, written by `npm run build` from packages/ui/web/tokens.json.",
    `   The default theme, ${named(first, pair)}`,
    ...others.map(([name, theme]) => `   ${named(name, theme)}`),
  ];
  lines[lines.length - 1] += " */";
  lines.push(":root {", "  color-scheme: light dark;");
  const colourNames = [];
  for (const [name, token] of Object.entries(tokens)) {
    if (name.startsWith("$")) continue;
    if (!NAME.test(name)) throw new Error(`tokens.json: ${name} is not a token name`);
    lines.push(`  --moxi-${name}: ${value(name, token, pair, sets)};`);
    if (token.$type === "color") colourNames.push(name);
  }
  lines.push("}", ':root[data-theme="light"] { color-scheme: light; }',
             ':root[data-theme="dark"] { color-scheme: dark; }');
  for (const [theme, its] of others) {
    lines.push(`:root[data-theme-name="${theme}"] {`);
    for (const name of colourNames) lines.push(`  --moxi-${name}: ${value(name, tokens[name], its, sets)};`);
    lines.push("}");
  }
  lines.push("");
  return lines.join("\n");
}

/** The icon sizes, each a PNG under web/ that the build copies: icon-<size>.png. */
export const ICON_SIZES = [192, 512];

/** manifest.json's text: the installed app's name, where it starts, its colours (the
 * default theme's light background: a manifest holds one colour) and its icons. Each icon fills its
 * square and keeps its mark in the middle, so the phone may crop it to any shape. */
export function manifest(tokens) {
  const background = tokens.bg?.$extensions?.moxi?.sets?.[defaultTheme(tokens).light];
  if (!COLOR.test(background ?? "")) throw new Error("tokens.json: bg is the manifest's colour and must be #rrggbb");
  const value = {
    name: "Moxi",
    short_name: "Moxi",
    start_url: "/m/ui/",
    scope: "/",
    display: "standalone",
    background_color: background,
    theme_color: background,
    icons: ICON_SIZES.map((size) => ({
      src: `/icon-${size}.png`, sizes: `${size}x${size}`, type: "image/png", purpose: "any maskable",
    })),
  };
  return `${JSON.stringify(value, null, 2)}\n`;
}

const HEADER = `// The types of the UI module's shared script, /m/ui/client.js, which every page imports
// by that absolute URL and every build leaves external. tools/web/tsconfig.base.json maps
// the URL to this file. Written by \`npm run build\` from packages/ui/web/src/client.ts;
// edit that file, not this one.

`;

/** client.d.ts's text: tsc's declaration output for src/client.ts, under a header. */
export function declarations(root, web) {
  const require = createRequire(path.join(root, "package.json"));
  const tsc = path.join(path.dirname(require.resolve("typescript/package.json")), "bin", "tsc");
  const out = fs.mkdtempSync(path.join(os.tmpdir(), "moxi-ui-decl-"));
  try {
    const proc = spawnSync(process.execPath, [
      tsc, "--declaration", "--emitDeclarationOnly", "--outDir", out,
      "--target", "es2022", "--module", "es2022", "--moduleResolution", "bundler",
      "--lib", "es2022,dom,dom.iterable", "--strict", "--skipLibCheck",
      "--allowImportingTsExtensions", "--newLine", "lf", "--types", "",
      path.join(web, "src", "client.ts"),
    ], { cwd: root, encoding: "utf8" });
    if (proc.status !== 0) {
      throw new Error(`tsc could not write client.d.ts:\n${proc.stdout}${proc.stderr}`);
    }
    const text = fs.readFileSync(path.join(out, "client.d.ts"), "utf8");
    if (/^import\b/m.test(text)) {
      throw new Error("client.d.ts would import another file; declare every public type in client.ts");
    }
    return HEADER + text;
  } finally {
    fs.rmSync(out, { recursive: true, force: true });
  }
}

/** tools/web/build.mjs's esbuild options, which this script can't import: the build is
 * still loading when it runs this one. A test holds the two the same. */
export const OPTIONS = Object.freeze({
  bundle: true,
  format: "esm",
  platform: "browser",
  target: "es2022",
  minify: true,
  charset: "utf8",
  legalComments: "eof",
  sourcemap: false,
  external: ["/m/ui/client.js"],
  logLevel: "warning",
});

/** One script bundled from `source` under web/, with the build's options and `define`. */
async function bundle(root, web, source, define = {}) {
  const result = await esbuild.build({
    ...OPTIONS,
    absWorkingDir: root,
    entryPoints: [path.join(web, source)],
    tsconfig: path.join(web, "tsconfig.json"),
    define,
    write: false,
  });
  if (result.outputFiles.length !== 1) throw new Error(`${source} bundled into more than one file`);
  return result.outputFiles[0].text;
}

/** offline.js's and sw.js's text: the worker carries the offline page and its script. */
export async function worker(root, web) {
  const script = await bundle(root, web, "src/offline.ts");
  const html = fs.readFileSync(path.join(web, "offline.html"), "utf8");
  const sw = await bundle(root, web, "src/sw.ts", {
    OFFLINE_HTML: JSON.stringify(html), OFFLINE_JS: JSON.stringify(script),
  });
  return { script, sw };
}

export default async function generate({ root, web, emit }) {
  const tokens = JSON.parse(fs.readFileSync(path.join(web, "tokens.json"), "utf8"));
  emit(`${OUT}/theme.css`, themeCss(tokens));
  emit(`${OUT}/manifest.json`, manifest(tokens));
  emit(DECL, declarations(root, web));
  const { script, sw } = await worker(root, web);
  emit(`${OUT}/offline.js`, script);
  emit(`${OUT}/sw.js`, sw);
}

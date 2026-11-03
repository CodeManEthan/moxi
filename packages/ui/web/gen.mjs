// The UI module's own build steps, run by tools/web/build.mjs before anything is bundled:
//
// - theme.css, from tokens.json: every token as `--moxi-<name>` on `:root`, each colour
//   written once as `light-dark(<light>, <dark>)`;
// - manifest.json, the gateway's web app manifest, which it serves at
//   /manifest.webmanifest, its colours tokens.json's;
// - tools/web/client.d.ts, the types of /m/ui/client.js, from src/client.ts by tsc's
//   declaration output. Every page's tsc resolves the client's URL to that file.

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

/** The CSS value of one token, checked against its type. */
function value(name, token) {
  const where = `tokens.json: ${name}`;
  const dark = token.$extensions?.moxi?.dark;
  switch (token.$type) {
    case "color":
      if (!COLOR.test(token.$value) || !COLOR.test(dark ?? "")) {
        throw new Error(`${where}: a colour has a #rrggbb $value and $extensions.moxi.dark`);
      }
      return `light-dark(${token.$value}, ${dark})`;
    case "dimension":
    case "fontFamily":
      if (dark !== undefined) throw new Error(`${where}: only a colour has a dark value`);
      if (token.$type === "fontFamily") return family(token.$value, where);
      if (!DIMENSION.test(token.$value)) throw new Error(`${where}: a dimension is a number and px, rem or em`);
      return token.$value;
    default:
      throw new Error(`${where}: $type is color, dimension or fontFamily`);
  }
}

/** theme.css's text from the parsed tokens.json. */
export function themeCss(tokens) {
  const lines = [
    "/* The shared theme, written by `npm run build` from packages/ui/web/tokens.json. */",
    ":root {",
    "  color-scheme: light dark;",
  ];
  for (const [name, token] of Object.entries(tokens)) {
    if (name.startsWith("$")) continue;
    if (!NAME.test(name)) throw new Error(`tokens.json: ${name} is not a token name`);
    lines.push(`  --moxi-${name}: ${value(name, token)};`);
  }
  lines.push("}", ':root[data-theme="light"] { color-scheme: light; }',
             ':root[data-theme="dark"] { color-scheme: dark; }', "");
  return lines.join("\n");
}

/** The icon sizes, each a PNG under web/ that the build copies: icon-<size>.png. */
export const ICON_SIZES = [192, 512];

/** manifest.json's text: the installed app's name, where it starts, its colours (the
 * light theme's background) and its icons. Each icon fills its square and keeps its mark
 * in the middle, so the phone may crop it to any shape. */
export function manifest(tokens) {
  const background = tokens.bg?.$value;
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

export default async function generate({ root, web, emit }) {
  const tokens = JSON.parse(fs.readFileSync(path.join(web, "tokens.json"), "utf8"));
  emit(`${OUT}/theme.css`, themeCss(tokens));
  emit(`${OUT}/manifest.json`, manifest(tokens));
  emit(DECL, declarations(root, web));
}

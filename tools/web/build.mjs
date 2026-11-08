// The one build for every module's web page: `npm run build` from the repository.
//
// A module with a page has packages/<name>/web/: its TypeScript, a package.json that pins
// its own Lit, a tsconfig.json that extends tools/web/tsconfig.base.json, and build.json:
//
//   {"entries": {"page.js": "src/page.ts"}, "copy": ["index.html"], "generate": "gen.mjs"}
//
// - entries: each output file name, with the source esbuild bundles into it.
// - copy (optional): files copied unchanged, each under its own base name.
// - generate (optional): a script of the package's own whose default export is called as
//   `await generate({root, web, emit})` before anything is bundled. `emit(path, content)`
//   writes a repository-relative path: a file in the package's output directory, or a
//   `.d.ts` under tools/web/.
//
// Everything goes to packages/<name>/src/moxi_<name>/web/, the module's flat asset
// directory, which holds only what the build writes: a file the build no longer makes
// is removed. Beside the outputs goes build-stamp.txt, the hash of the build's inputs
// (see `stamp`). The repository's Python pre-commit check recomputes that hash without
// Node and refuses a commit whose sources changed without a rebuild.
//
// Before it bundles anything, the build refuses a colour written into a page's source
// rather than drawn from the theme's tokens (see `checkColours`).
//
// `node tools/web/build.mjs --out DIR` writes the same files under DIR instead, at their
// repository-relative paths; tools/web/check.mjs uses it to compare a fresh build with
// the committed files. `--root DIR` builds another checkout with this toolchain.

import * as esbuild from "esbuild";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

/** esbuild's one option set. Each page bundles its own Lit; the shared client stays out. */
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

export const STAMP_NAME = "build-stamp.txt";
export const LOCK = "package-lock.json";

// The names and file types a module's asset directory may hold, as the page server
// serves them.
const ASSET_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const ASSET_TYPES = new Set([".html", ".js", ".mjs", ".css", ".json", ".map", ".svg",
                             ".png", ".ico", ".woff2", ".txt"]);
const BUILD_KEYS = new Set(["entries", "copy", "generate"]);

export class BuildError extends Error {}

/** The repository this script belongs to. */
export function repoRoot() {
  return path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
}

function git(root, args, input) {
  const proc = spawnSync("git", args, { cwd: root, input, maxBuffer: 1 << 28 });
  if (proc.error) throw new BuildError(`could not run git (${proc.error.message})`);
  if (proc.status !== 0) {
    throw new BuildError(`git ${args.join(" ")}: ${proc.stderr.toString().trim()}`);
  }
  return proc.stdout;
}

function split(buffer) {
  return buffer.toString("utf8").split("\0").filter((name) => name !== "");
}

/** Sorts repository paths by their UTF-8 bytes, the order the stamp takes them in. */
export function byPath(a, b) {
  return Buffer.compare(Buffer.from(a, "utf8"), Buffer.from(b, "utf8"));
}

/** True for a path the stamp reads: a package's web/ files, tools/web/, the root lock. */
export function isInput(rel) {
  return rel === LOCK || rel.startsWith("tools/web/") || /^packages\/[^/]+\/web\//.test(rel);
}

/** The module's output directory, relative to the repository. */
export function outdir(name) {
  return `packages/${name}/src/moxi_${name.replaceAll("-", "_")}/web`;
}

/**
 * Every stamp input in the working tree, sorted: the files git tracks or would track
 * (untracked and not ignored), so a new source counts before it is staged. A tracked
 * file deleted from the tree is left out; a link is refused, since its bytes differ by
 * system.
 */
export function listInputs(root) {
  const listed = split(git(root, ["ls-files", "-z", "--cached", "--others",
                                  "--exclude-standard"]));
  const inputs = [];
  for (const rel of new Set(listed)) {
    if (!isInput(rel)) continue;
    let stat;
    try {
      stat = fs.lstatSync(path.join(root, rel));
    } catch {
      continue;
    }
    if (stat.isSymbolicLink()) throw new BuildError(`${rel} is a link; a build input must be a file`);
    if (stat.isFile()) inputs.push(rel);
  }
  return inputs.sort(byPath);
}

/**
 * Refuses an input whose bytes git may rewrite at checkout. Every input must be `text
 * eol=lf` or `binary` by .gitattributes, so its working-tree bytes are its committed
 * bytes on every system, and the stamp is the same everywhere.
 */
export function checkAttributes(root, inputs) {
  if (inputs.length === 0) return;
  const out = split(git(root, ["check-attr", "-z", "--stdin", "text", "eol"],
                        inputs.join("\0") + "\0"));
  const attrs = new Map();
  for (let i = 0; i + 2 < out.length; i += 3) {
    const [rel, attr, value] = out.slice(i, i + 3);
    if (!attrs.has(rel)) attrs.set(rel, {});
    attrs.get(rel)[attr] = value;
  }
  const bad = inputs.filter((rel) => {
    const { text, eol } = attrs.get(rel) ?? {};
    return !((text === "set" && eol === "lf") || text === "unset");
  });
  if (bad.length) {
    throw new BuildError("these build inputs are neither `text eol=lf` nor `binary` in "
      + ".gitattributes, so a checkout may change their bytes; add a rule for their "
      + `type or remove them: ${bad.join(", ")}`);
  }
}

/** The packages with a page: each packages/<name>/web/build.json among the inputs. */
export function webPackages(inputs) {
  return inputs
    .map((rel) => /^packages\/([^/]+)\/web\/build\.json$/.exec(rel))
    .filter(Boolean)
    .map((match) => match[1])
    .sort(byPath);
}

/** A package's stamp inputs: its web/ files, tools/web/ and the lock, sorted. */
export function packageInputs(inputs, name) {
  const own = `packages/${name}/web/`;
  return inputs.filter((rel) => rel.startsWith(own) || rel.startsWith("tools/web/")
                                || rel === LOCK);
}

/**
 * The stamp: SHA-256, in hex, over `esbuild@<version>` and a NUL, then for each input in
 * order its path relative to the repository (POSIX, UTF-8), a NUL, its bytes and a NUL.
 * The Python check in moxi-dev computes the same value; a change here is a change there.
 */
export function stamp(version, files) {
  const hash = createHash("sha256");
  hash.update(`esbuild@${version}\0`, "utf8");
  for (const [rel, bytes] of files) {
    hash.update(rel, "utf8");
    hash.update("\0");
    hash.update(bytes);
    hash.update("\0");
  }
  return hash.digest("hex");
}

/** The esbuild version the lock pins, which must be the one installed. */
export function lockedEsbuild(root) {
  let lock;
  try {
    lock = JSON.parse(fs.readFileSync(path.join(root, LOCK), "utf8"));
  } catch (err) {
    throw new BuildError(`${LOCK} cannot be read (${err.message})`);
  }
  const version = lock?.packages?.["node_modules/esbuild"]?.version;
  if (typeof version !== "string") throw new BuildError(`${LOCK} pins no esbuild`);
  if (version !== esbuild.version) {
    throw new BuildError(`${LOCK} pins esbuild ${version} but ${esbuild.version} is `
      + "installed; run `npm ci`");
  }
  return version;
}

function readConfig(root, name) {
  const rel = `packages/${name}/web/build.json`;
  let config;
  try {
    config = JSON.parse(fs.readFileSync(path.join(root, rel), "utf8"));
  } catch (err) {
    throw new BuildError(`${rel} cannot be read (${err.message})`);
  }
  const unknown = Object.keys(config ?? {}).filter((key) => !BUILD_KEYS.has(key));
  if (typeof config !== "object" || config === null || Array.isArray(config) || unknown.length) {
    throw new BuildError(`${rel} is an object of entries, copy and generate`
      + (unknown.length ? `; it also has ${unknown.join(", ")}` : ""));
  }
  const entries = config.entries ?? {};
  if (typeof entries !== "object" || Array.isArray(entries)
      || !Object.entries(entries).every(([out, src]) => out.endsWith(".js")
                                         && typeof src === "string")) {
    throw new BuildError(`${rel}: entries maps each output name ending .js to a source`);
  }
  const copy = config.copy ?? [];
  if (!Array.isArray(copy) || !copy.every((item) => typeof item === "string")) {
    throw new BuildError(`${rel}: copy is a list of files under web/`);
  }
  if (config.generate !== undefined && typeof config.generate !== "string") {
    throw new BuildError(`${rel}: generate names one script under web/`);
  }
  return { entries, copy, generate: config.generate };
}

// The colour check. A page draws its colours from the theme's tokens (var(--moxi-*)), so
// every page follows the light and dark sets with no code of its own. A colour written
// into a page's source would not, so the build refuses one in a module's web/src/**/*.ts
// and in the .css and .html files it copies, unless its line says why it is fixed in a
// comment holding `colour-fixed`. It finds:
//
// - a hex colour: `#` and 3, 4, 6 or 8 hex digits, not after `.`, `&` or an identifier
//   character, and not followed by an identifier character, `:`, `(`, `?`, `.` or `=`;
//   so a private name (`this.#feed`, `#feed: Feed`, `#feed = null`, `#add()`) passes;
// - a colour function: rgb(), rgba(), hsl(), hsla(), hwb(), lab(), lch(), oklab(),
//   oklch(), color(), color-mix();
// - a named colour or a system colour as a whole word in the value of a colour property
//   (`color`, `background`, `border*`, `outline*`, `fill`, `stroke`, `box-shadow`,
//   `accent-color`, `caret-color`, `text-decoration*`). `transparent`, `currentColor`
//   and `inherit` pass.

const HEX_COLOUR = /(?<![\w$.&])#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w$:(?.]|\s*=)/gi;
const COLOUR_FUNCTION = /(?<![\w$.-])(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\(/gi;
const COLOUR_PROPERTY = new RegExp("(?<![\\w$-])(?:color|background(?:-color)?|border(?:-[a-z-]+)?"
  + "|outline(?:-[a-z-]+)?|fill|stroke|box-shadow|accent-color|caret-color"
  + "|text-decoration(?:-[a-z-]+)?)\\s*:([^;{}]*)", "gi");
const VALUE_WORD = /(?<![\w$.-])[a-z]+(?![\w$.(-])/gi;
const COLOUR_FIXED = /(?:\/\/|\/\*|<!--).*colour-fixed/;
const COLOUR_NAMES = new Set(`
  aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue
  blueviolet brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk
  crimson cyan darkblue darkcyan darkgoldenrod darkgray darkgreen darkgrey darkkhaki
  darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
  darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue
  dimgray dimgrey dodgerblue firebrick floralwhite forestgreen fuchsia gainsboro
  ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred indigo
  ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan
  lightgoldenrodyellow lightgray lightgreen lightgrey lightpink lightsalmon lightseagreen
  lightskyblue lightslategray lightslategrey lightsteelblue lightyellow lime limegreen
  linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple
  mediumseagreen mediumslateblue mediumspringgreen mediumturquoise mediumvioletred
  midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive olivedrab
  orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip
  peachpuff peru pink plum powderblue purple rebeccapurple red rosybrown royalblue
  saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue slateblue
  slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet
  wheat white whitesmoke yellow yellowgreen
  accentcolor accentcolortext activetext buttonborder buttonface buttontext canvas
  canvastext field fieldtext graytext highlight highlighttext linktext mark marktext
  selecteditem selecteditemtext visitedtext
`.trim().split(/\s+/));

/** The colour literals in a source's text, each with its 1-based line. */
export function colourLiterals(text) {
  const found = [];
  text.split("\n").forEach((line, index) => {
    if (COLOUR_FIXED.test(line)) return;
    const literals = [];
    for (const match of line.matchAll(HEX_COLOUR)) literals.push([match.index, match[0]]);
    for (const match of line.matchAll(COLOUR_FUNCTION)) literals.push([match.index, match[0]]);
    for (const match of line.matchAll(COLOUR_PROPERTY)) {
      const start = match.index + match[0].length - match[1].length;
      for (const word of match[1].matchAll(VALUE_WORD)) {
        if (COLOUR_NAMES.has(word[0].toLowerCase())) literals.push([start + word.index, word[0]]);
      }
    }
    literals.sort((a, b) => a[0] - b[0]);
    for (const [, literal] of literals) found.push({ line: index + 1, literal });
  });
  return found;
}

/** The sources the colour check reads: each package's .ts files under web/src/, and the
 * .css and .html files it copies. */
export function colourSources(inputs, configs) {
  const sources = [];
  for (const [name, config] of configs) {
    const web = `packages/${name}/web/`;
    sources.push(...inputs.filter((rel) => rel.startsWith(`${web}src/`) && rel.endsWith(".ts")));
    for (const item of config.copy) {
      const rel = path.posix.normalize(web + item);
      if (/\.(css|html)$/.test(rel)) sources.push(rel);
    }
  }
  return sources.sort(byPath);
}

/** Refuses the build when a page source holds a colour literal, naming each. */
export function checkColours(root, inputs, configs) {
  const problems = [];
  for (const rel of colourSources(inputs, configs)) {
    const text = fs.readFileSync(path.join(root, ...rel.split("/")), "utf8");
    for (const { line, literal } of colourLiterals(text)) {
      problems.push(`${rel}:${line}: ${literal}`);
    }
  }
  if (problems.length) {
    throw new BuildError("a page draws its colours from the theme's tokens (var(--moxi-*)); "
      + "these sources hold a colour of their own. Use a token, or say why the colour is "
      + "fixed in a comment holding colour-fixed on its line:\n  " + problems.join("\n  "));
  }
}

function within(base, rel, what) {
  const full = path.resolve(base, rel);
  if (!full.startsWith(base + path.sep)) throw new BuildError(`${what} ${rel} is outside ${base}`);
  return full;
}

/**
 * Builds every package. Returns the files written, as a map of repository-relative path
 * to bytes, and each package's output directory. With `outRoot` other than `root`, the
 * files go under it and the checkout is not touched.
 */
export async function build({ root = repoRoot(), outRoot = root } = {}) {
  const version = lockedEsbuild(root);
  const packages = webPackages(listInputs(root));
  const files = new Map();
  const put = (rel, bytes, owner) => {
    if (files.has(rel)) throw new BuildError(`${owner} makes ${rel} twice`);
    files.set(rel, Buffer.from(bytes));
  };
  const written = (rel) => {
    const full = path.join(outRoot, ...rel.split("/"));
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, files.get(rel));
  };
  const configs = new Map(packages.map((name) => [name, readConfig(root, name)]));

  // Generated files come first: one may be a stamp input (a .d.ts under tools/web/),
  // which is written before any stamp is taken.
  for (const [name, config] of configs) {
    if (config.generate === undefined) continue;
    const web = path.join(root, "packages", name, "web");
    const script = within(web, config.generate, "the generate script");
    const out = outdir(name);
    const emit = (rel, content) => {
      if (typeof rel !== "string") throw new BuildError(`${name}'s generate emitted a bad path`);
      const flat = rel.startsWith(out + "/") && !rel.slice(out.length + 1).includes("/");
      const decl = /^tools\/web\/[^/]+\.d\.ts$/.test(rel);
      if (!flat && !decl) {
        throw new BuildError(`${name}'s generate may write only into ${out}/ or a .d.ts `
          + `under tools/web/, not ${rel}`);
      }
      put(rel, typeof content === "string" ? Buffer.from(content, "utf8") : content, name);
      if (decl) written(rel);
    };
    const module = await import(pathToFileURL(script).href);
    await module.default({ root, web, emit });
  }

  const inputs = listInputs(root);
  checkAttributes(root, inputs);
  checkColours(root, inputs, configs);
  for (const [name, config] of configs) {
    const out = outdir(name);
    const web = `packages/${name}/web`;
    const entryPoints = {};
    for (const [output, source] of Object.entries(config.entries)) {
      within(path.join(root, web), source, "the entry");
      entryPoints[output.slice(0, -3)] = `${web}/${source}`;
    }
    if (Object.keys(entryPoints).length) {
      const result = await esbuild.build({
        ...OPTIONS,
        absWorkingDir: root,
        entryPoints,
        outdir: path.join(root, ...out.split("/")),
        tsconfig: path.join(root, ...web.split("/"), "tsconfig.json"),
        write: false,
      });
      for (const file of result.outputFiles) {
        const rel = path.relative(path.join(root, ...out.split("/")), file.path);
        put(`${out}/${rel.split(path.sep).join("/")}`, file.contents, name);
      }
    }
    for (const item of config.copy) {
      const full = within(path.join(root, web), item, "the copied file");
      put(`${out}/${path.basename(full)}`, fs.readFileSync(full), name);
    }
    const own = packageInputs(inputs, name);
    const digest = stamp(version, own.map((rel) => [rel, fs.readFileSync(path.join(root, rel))]));
    put(`${out}/${STAMP_NAME}`, `${digest}\n`, name);
    for (const rel of files.keys()) {
      if (!rel.startsWith(out + "/")) continue;
      const base = rel.slice(out.length + 1);
      const ext = path.extname(base);
      if (base.includes("/") || !ASSET_NAME.test(base) || !ASSET_TYPES.has(ext)) {
        throw new BuildError(`${name} makes ${base}, which its flat asset directory `
          + "cannot serve: one plain file name with a served file type");
      }
    }
  }

  const outdirs = packages.map(outdir);
  for (const rel of files.keys()) written(rel);
  if (outRoot === root) {
    for (const out of outdirs) {
      const full = path.join(root, ...out.split("/"));
      for (const entry of fs.readdirSync(full, { withFileTypes: true })) {
        if (!files.has(`${out}/${entry.name}`)) fs.rmSync(path.join(full, entry.name), { recursive: true });
      }
    }
  }
  return { files, outdirs, stamps: new Map(packages.map((name) => [
    name, files.get(`${outdir(name)}/${STAMP_NAME}`).toString("utf8").trim()])) };
}

function parseArgs(argv) {
  const args = { root: repoRoot(), outRoot: null };
  for (let i = 0; i < argv.length; i += 1) {
    if ((argv[i] === "--root" || argv[i] === "--out") && i + 1 < argv.length) {
      args[argv[i] === "--root" ? "root" : "outRoot"] = path.resolve(argv[i + 1]);
      i += 1;
    } else {
      throw new BuildError(`usage: node tools/web/build.mjs [--root DIR] [--out DIR]`);
    }
  }
  args.outRoot ??= args.root;
  return args;
}

async function main(argv) {
  try {
    const { root, outRoot } = parseArgs(argv);
    const { files, stamps } = await build({ root, outRoot });
    if (stamps.size === 0) console.log("build: no package has a web/build.json");
    for (const [name, digest] of stamps) {
      const count = [...files.keys()].filter((rel) => rel.startsWith(outdir(name) + "/")).length;
      console.log(`build: ${name}: ${count} files in ${outdir(name)}, stamp ${digest}`);
    }
    return 0;
  } catch (err) {
    if (!(err instanceof BuildError) && !err?.errors) throw err;
    // esbuild has already printed its own errors.
    console.error(`build: ${err.message}`);
    return 1;
  }
}

// Run as a script, not imported by check.mjs. Node gives a module its real path, so the
// script's is resolved the same way before they are compared.
if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main(process.argv.slice(2));
}

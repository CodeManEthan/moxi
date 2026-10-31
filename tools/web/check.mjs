// The check CI runs after `npm ci`: `npm run check` from the repository.
//
// 1. `tsc --noEmit` on each package with a page, since esbuild strips types and checks
//    none.
// 2. A fresh build into a temporary directory, compared byte for byte with the files in
//    the checkout: each file the build makes must be there with the same bytes, and a
//    module's asset directory may hold nothing else. Every difference is named.
//
// It exits 0 when both pass and 1 otherwise.

import { spawnSync } from "node:child_process";
import * as fs from "node:fs";
import { createRequire } from "node:module";
import * as os from "node:os";
import * as path from "node:path";

import { BuildError, build, listInputs, repoRoot, webPackages } from "./build.mjs";

const require = createRequire(import.meta.url);

function typecheck(root, packages) {
  const tsc = path.join(path.dirname(require.resolve("typescript/package.json")), "bin", "tsc");
  const failed = [];
  for (const name of packages) {
    const project = path.join(root, "packages", name, "web");
    const proc = spawnSync(process.execPath, [tsc, "--noEmit", "-p", project],
                           { cwd: root, stdio: "inherit" });
    if (proc.status !== 0) failed.push(name);
  }
  return failed;
}

/** The differences between the files a fresh build made and the checkout at `root`. */
export function compare(root, files, outdirs) {
  const problems = [];
  for (const [rel, bytes] of files) {
    let committed;
    try {
      committed = fs.readFileSync(path.join(root, ...rel.split("/")));
    } catch {
      problems.push(`${rel}: made by the build but not in the checkout`);
      continue;
    }
    if (!committed.equals(bytes)) problems.push(`${rel}: differs from a fresh build`);
  }
  for (const out of outdirs) {
    let names = [];
    try {
      names = fs.readdirSync(path.join(root, ...out.split("/")));
    } catch {
      continue;
    }
    for (const name of names) {
      if (!files.has(`${out}/${name}`)) problems.push(`${out}/${name}: not made by the build`);
    }
  }
  return problems.sort();
}

async function main() {
  const root = repoRoot();
  const packages = webPackages(listInputs(root));
  const failed = typecheck(root, packages);
  for (const name of failed) console.error(`check: tsc found errors in packages/${name}/web`);

  const fresh = fs.mkdtempSync(path.join(os.tmpdir(), "moxi-web-check-"));
  let problems;
  try {
    const { files, outdirs } = await build({ root, outRoot: fresh });
    problems = compare(root, files, outdirs);
  } finally {
    fs.rmSync(fresh, { recursive: true, force: true });
  }
  for (const problem of problems) console.error(`check: ${problem}`);
  if (problems.length) console.error("check: run `npm run build` and commit what it writes");
  const ok = failed.length === 0 && problems.length === 0;
  console.log(`check: ${packages.length} package(s), `
    + `${ok ? "types and built files match" : "failed"}`);
  return ok ? 0 : 1;
}

try {
  process.exitCode = await main();
} catch (err) {
  if (!(err instanceof BuildError) && !err?.errors) throw err;
  console.error(`check: ${err.message}`);
  process.exitCode = 1;
}

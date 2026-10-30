"""Bump the vendored gitleaks rules to a tag, and check them before they ship.

    uv run packages/secrets/tools/bump_gitleaks.py v8.31.0

Dev-only: it needs the network and Go, and it is not in the wheel or the sdist. It runs
these steps in order and stops at the first that fails:

1. Fetches `config/gitleaks.toml` and `LICENSE` at the tag and resolves the tag's commit.
2. Writes the three files of `rules/upstream/`: the two fetched files byte for byte and
   `PIN` (tag, commit, sha256 of gitleaks.toml, today's date).
3. Regenerates `sets.json` and prints the added, removed and changed rule ids, and every
   dropped rule with its reason.
4. Compares Go with RE2 and with the fallback: `re2_samples` (Go) makes samples from each
   pattern's parse tree, with variants, and reports Go's matches and secrets; this tool
   runs the same patterns on google-re2 and their translations on Python's re, then the
   in-process scan of both sets on each engine over the same texts. A rule fails unless,
   on every text, RE2's matches and secrets equal Go's, the fallback's equal Go's, and the
   fallback's hits equal RE2's. No difference is allowed, on the variants with a dotless
   i for each i and a dotted I for each i and I as on any other: the translator writes
   Go's case folds out. The step also checks Go's fold orbit (unicode.SimpleFold) of every
   ASCII character against the translator's. No sample is stored.
5. Times the scan on RE2 over generated adversarial texts against the cost budget, and
   names the slowest rules where a text goes over it.
6. Runs the gitleaks binary of the tag on the package's own directory with
   `credentials.toml`, from `rules/`, and expects no hit.
7. Runs the package's tests.
8. Reads google-re2's newest release on PyPI, prints its wheel tags, and checks
   `engine.re2_wheel_fits()` and the `re2` extra's bound against them.

A rule that fails step 4 is fixed in the translator or added to `rules/drop.txt` with the
difference as its reason; a rule that fails step 5 goes into `rules/drop.txt` with its
measured cost. Then run the bump again. When it passes, commit with a subject naming the
tag. Exit status: 0 when every step passes, 1 when a check fails, 2 when the tool cannot
run (no network, no Go, a tag that does not exist, rule files that will not generate).
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import random
import re
import shutil
import string
import subprocess
import sys
import time
import tomllib
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
PACKAGE = TOOLS.parent
RULES = PACKAGE / "src" / "moxi_secrets" / "rules"
SAMPLER = TOOLS / "re2_samples"

UPSTREAM_GIT = "https://github.com/gitleaks/gitleaks"
UPSTREAM_RAW = "https://raw.githubusercontent.com/gitleaks/gitleaks/{tag}/{path}"
GO_MODULE = "github.com/zricethezav/gitleaks/v8"
PYPI = "https://pypi.org/pypi/google-re2/json"
TAG = re.compile(r"v\d+\.\d+\.\d+")
SETS = ("credentials", "broad")

PIN_HEADER = (
    "# The gitleaks release that gitleaks.toml and LICENSE beside this file come from, kept\n"
    "# byte for byte. sha256 is over gitleaks.toml's bytes. The bump tool rewrites this file.\n"
)

#: The scan's budget on RE2, per set: seconds per MB of UTF-8, plus seconds per candidate.
BUDGET_PER_MB = 0.4
BUDGET_PER_CANDIDATE = 50e-6


class Stop(Exception):
    """The tool cannot go on; the message says why. Exit status 2."""


def say(text: str = "") -> None:
    print(text, flush=True)


def heading(text: str) -> None:
    say()
    say(f"== {text}")


# -- steps 1 and 2: fetch and pin -----------------------------------------------------------

def fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as reply:
            return reply.read()
    except OSError as exc:
        raise Stop(f"cannot fetch {url}: {exc}") from None


def tag_commit(tag: str) -> str:
    """The commit the tag points at (the peeled commit of an annotated tag)."""
    try:
        out = subprocess.run(
            ["git", "ls-remote", UPSTREAM_GIT, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
            capture_output=True, text=True, timeout=120, check=True).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise Stop(f"git ls-remote {UPSTREAM_GIT} failed: {exc}") from None
    refs = dict(reversed(line.split("\t", 1)) for line in out.splitlines() if "\t" in line)
    commit = refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")
    if not commit:
        raise Stop(f"{UPSTREAM_GIT} has no tag {tag}")
    return commit


def pin_text(tag: str, commit: str, digest: str, fetched: datetime.date) -> str:
    return (PIN_HEADER + f'tag = "{tag}"\ncommit = "{commit}"\nsha256 = "{digest}"\n'
            f"fetched = {fetched.isoformat()}\n")


def write_upstream(tag: str) -> None:
    from moxi import files

    commit = tag_commit(tag)
    toml = fetch(UPSTREAM_RAW.format(tag=tag, path="config/gitleaks.toml"))
    licence = fetch(UPSTREAM_RAW.format(tag=tag, path="LICENSE"))
    digest = hashlib.sha256(toml).hexdigest()
    upstream = RULES / "upstream"
    files.atomic_write(upstream / "gitleaks.toml", toml, mode=0o644)
    files.atomic_write(upstream / "LICENSE", licence, mode=0o644)
    files.atomic_write(upstream / "PIN", pin_text(tag, commit, digest, datetime.date.today()),
                       mode=0o644)
    say(f"{tag}: commit {commit}")
    say(f"gitleaks.toml {len(toml)} bytes, sha256 {digest}; LICENSE {len(licence)} bytes")


# -- step 3: regenerate ---------------------------------------------------------------------

def rule_changes(old: dict, new: dict) -> dict:
    """{set: {"added": [...], "removed": [...], "changed": [...]}} between two sets.json."""
    out = {}
    for name in SETS:
        before = {r["id"]: r for r in old.get("sets", {}).get(name, {}).get("rules", [])}
        after = {r["id"]: r for r in new["sets"][name]["rules"]}
        out[name] = {
            "added": sorted(set(after) - set(before)),
            "removed": sorted(set(before) - set(after)),
            "changed": sorted(i for i in set(before) & set(after) if before[i] != after[i]),
        }
    return out


def regenerate() -> dict:
    from moxi import files
    from moxi_secrets import _generate

    if _generate.RULES.resolve() != RULES.resolve():
        raise Stop(f"moxi_secrets is imported from {_generate.RULES.parent}, not this "
                   "package; run the tool with the workspace's uv run")
    target = RULES / _generate.OUTPUT
    try:
        old = json.loads(target.read_bytes())
    except (OSError, ValueError):
        old = {}
    try:
        data = _generate.generate(RULES)
    except _generate.GenerationError as exc:
        raise Stop(f"generation failed: {exc}") from None
    files.atomic_write(target, data, mode=0o644)
    new = json.loads(data)
    for name, change in rule_changes(old, new).items():
        body = new["sets"][name]
        say(f"{name}: {len(body['rules'])} rules, {len(body['dropped'])} dropped")
        for kind in ("added", "removed", "changed"):
            if change[kind]:
                say(f"  {kind}: {', '.join(change[kind])}")
        for d in body["dropped"]:
            say(f"  dropped {d['id']}: {d['reason']}")
    return new


# -- step 4: Go, RE2 and the fallback -------------------------------------------------------

def patterns(data: dict) -> list[dict]:
    """Every pattern in sets.json: id, owner (a rule id, or a global allowlist), the rule's
    secret group, the verbatim pattern, its translation, and what it is."""
    out = []

    def add(pid, owner, kind, pattern, group=0):
        if pattern:
            out.append({"id": pid, "owner": owner, "kind": kind, "group": group,
                        "pattern": pattern["pattern"], "fallback": pattern["fallback"]})

    for i, a in enumerate(data["allowlists"]):
        for j, p in enumerate(a["regexes"]):
            add(f"global{i}.re{j}", f"global allowlist {i}", "allow", p)
        for j, p in enumerate(a["paths"]):
            add(f"global{i}.path{j}", f"global allowlist {i}", "path", p)
    for name in SETS:
        for r in data["sets"][name]["rules"]:
            add(r["id"] + ".path", r["id"], "path", r["path"])
            for i, a in enumerate(r["allowlists"]):
                for j, p in enumerate(a["regexes"]):
                    add(f"{r['id']}.allow{i}.re{j}", r["id"], "allow", p)
                for j, p in enumerate(a["paths"]):
                    add(f"{r['id']}.allow{i}.path{j}", r["id"], "path", p)
            add(r["id"], r["id"], "rule", r["regex"], r["secretGroup"])
    # Paths first, so a path-scoped rule's samples meet a sampled path.
    out.sort(key=lambda p: p["kind"] != "path")
    return out


def secret_span(spans, group: int) -> tuple[int, int]:
    """The secret as gitleaks takes it from a match's group spans [(start, end), ...]: the
    group `group` names, else the first non-empty group, else the whole match."""
    groups = len(spans) - 1
    if groups == 0:
        return spans[0]
    if group > 0:
        return spans[group] if group <= groups else (-1, -1)
    for s, e in spans[1:]:
        if s >= 0 and e > s:
            return (s, e)
    return spans[0]


def matches(rx, target, group: int, to_bytes=None) -> list:
    """Every non-empty match of `rx` in `target` as [start, end, secret start, secret end],
    in UTF-8 byte offsets (`to_bytes` maps `str` offsets)."""
    out = []
    for m in rx.finditer(target):
        spans = [m.span(g) for g in range((rx.groups or 0) + 1)]
        if spans[0][0] == spans[0][1]:
            continue
        ss, se = secret_span(spans, group)
        row = [spans[0][0], spans[0][1], ss, se]
        if to_bytes is not None:
            row = [to_bytes(x) if x >= 0 else x for x in row]
        out.append(row)
    return out


def hit_list(found: dict) -> list:
    return sorted((name, rule, s, e) for name, hits in found.items() for rule, s, e in hits)


class Comparison:
    """Go's rows against RE2 and the fallback, one row at a time; keeps counts, never texts."""

    def __init__(self, pats, re2_engine, py_engine, run_gitleaks, text_class, show=False):
        self.pats = {p["id"]: p for p in pats}
        self.re2, self.py = re2_engine, py_engine
        self.run_gitleaks, self.Text = run_gitleaks, text_class
        self.show = show
        self.rows = Counter()
        self.hit_lists = 0
        self.orbits_checked = 0
        self.go_unicode = ""
        self.orbit_faults: list[str] = []
        self.no_path = set()
        self.paths: dict[str, str] = {}
        self.failures: dict[str, Counter] = defaultdict(Counter)
        self.examples: dict[str, str] = {}

    def fail(self, owner: str, what: str, text: str) -> None:
        self.failures[owner][what] += 1
        if self.show and owner not in self.examples:
            self.examples[owner] = f"{what}: {text!r}"

    def orbits(self, row: dict) -> None:
        """Go's fold orbit of each ASCII character against the translator's."""
        from moxi_secrets._generate import orbit

        self.go_unicode = row["unicode"]
        for members in row["orbits"]:
            self.orbits_checked += 1
            cp = members[0]
            if tuple(sorted(members)) != orbit(cp):
                self.orbit_faults.append(
                    f"U+{cp:04X}: Go folds it to {sorted(members)}, the translator to "
                    f"{list(orbit(cp))}")

    def row(self, row: dict) -> None:
        if "orbits" in row:
            self.orbits(row)
            return
        p = self.pats[row["id"]]
        text, kind = row["text"], row["kind"]
        self.rows[kind] += 1
        t = self.Text(text)
        go = row["matches"]

        r2 = self.re2.compile(p["pattern"])
        r2_rows = matches(r2, t.data, p["group"])
        if r2_rows != go or (r2.search(t.data) is not None) != row["found"]:
            self.fail(p["owner"], f"re2 {p['kind']} {kind}", text)

        rp = self.py.compile(p["fallback"])
        py_rows = matches(rp, t.etext, p["group"], t.byte_pos)
        py_found = rp.search(t.etext) is not None
        if py_rows != go or py_found != row["found"]:
            self.fail(p["owner"], f"fallback {p['kind']} {kind}", text)

        if p["kind"] == "path" and p["id"].endswith(".path") and not p["id"].startswith("global"):
            # A rule's own path pattern: keep one path Go matches for the rule's samples,
            # and compare the hits a path-only rule gives on it.
            if kind == "plain" and row["found"]:
                self.paths.setdefault(p["owner"], text)
            if row["found"]:
                self.hits(self.Text("x"), text, p["owner"], text)
        elif p["kind"] == "rule":
            path = self.paths.get(p["owner"])
            if path is None and (p["owner"] + ".path") in self.pats:
                self.no_path.add(p["owner"])
            self.hits(t, path, p["owner"], text)

    def hits(self, t, path, owner, shown) -> None:
        self.hit_lists += 1
        r2 = hit_list(self.run_gitleaks(t, SETS, path, self.re2))
        py = hit_list(self.run_gitleaks(t, SETS, path, self.py))
        if r2 == py:
            return
        rules = {h[1] for h in set(r2) ^ set(py)} or {owner}
        for rule in rules:
            self.fail(rule, f"hits {('path ' if t.text == 'x' else '')}{owner}", shown)

    def report(self) -> bool:
        total = sum(self.rows.values())
        plain = self.rows["plain"]
        say(f"{len(self.pats)} patterns, {plain} samples, {total - plain} variants; "
            f"{self.hit_lists} hit lists on each engine")
        if self.orbits_checked:
            say(f"Go's fold orbits (Unicode {self.go_unicode}) of {self.orbits_checked} ASCII "
                f"characters: {len(self.orbit_faults)} differ from the translator's")
        else:
            self.orbit_faults.append("the sampler sent no fold orbits")
        for fault in self.orbit_faults:
            say(f"  FAIL orbit {fault}")
        if self.orbit_faults:
            say("The translator's orbit table must equal Go's: fix _generate.py.")
        for owner in sorted(self.no_path):
            say(f"  note: no sampled path matched {owner}'s path pattern; its hits ran "
                "without a path")
        if not self.failures:
            say("every rule agrees")
            return not self.orbit_faults
        for owner in sorted(self.failures):
            counts = ", ".join(f"{what} x{n}" for what, n in sorted(self.failures[owner].items()))
            say(f"  FAIL {owner}: {counts}")
            if owner in self.examples:
                say(f"    first: {self.examples[owner]}")
        if any(o.startswith("global") for o in self.failures):
            say("A global allowlist cannot be dropped: fix the translator.")
        say("Fix the translator, or add each failing rule to rules/drop.txt with the "
            "difference as its reason, and run the bump again.")
        return False


def go_rows(pats: list[dict], samples: int, seed: int):
    """Run the Go sampler and yield its rows as they come."""
    if shutil.which("go") is None:
        raise Stop("Go is not on PATH; the comparison needs it")
    request = json.dumps({"samples": samples, "seed": seed,
                          "patterns": [{"id": p["id"], "pattern": p["pattern"],
                                        "group": p["group"]} for p in pats]})
    proc = subprocess.Popen(["go", "run", "."], cwd=SAMPLER, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdin.write(request.encode("utf-8"))
    proc.stdin.close()
    for line in proc.stdout:
        yield json.loads(line)
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    if proc.wait() != 0:
        raise Stop(f"re2_samples failed: {err.splitlines()[-1] if err else proc.returncode}")


def scan_module():
    """moxi_secrets.scan, the module (the package's `scan` attribute is the function)."""
    import importlib

    return importlib.import_module("moxi_secrets.scan")


def compare(data: dict, samples: int, seed: int, show: bool) -> bool:
    from moxi_secrets import engine

    scan = scan_module()
    scan._sets = None   # read the sets.json step 3 wrote
    module = engine.re2_module()
    if module is None:
        raise Stop("google-re2 does not import; the comparison needs it")
    pats = patterns(data)
    comparison = Comparison(pats, engine.Re2Engine(module), engine.python(),
                            scan.run_gitleaks, engine.Text, show)
    for row in go_rows(pats, samples, seed):
        comparison.row(row)
    return comparison.report()


# -- step 5: cost ---------------------------------------------------------------------------

def _words(rng: random.Random, size: int, sep: str = " ") -> str:
    out, n = [], 0
    while n < size:
        word = "".join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 12)))
        out.append(word)
        n += len(word) + len(sep)
    return sep.join(out)[:size]


def _token(rng: random.Random, k: int) -> str:
    return "".join(rng.choices(string.ascii_letters + string.digits, k=k))


def shapes(keywords, size: int, seed: int = 11) -> list[tuple[str, str]]:
    """The adversarial texts of the cost budget, each about `size` characters, generated
    from a seed: nothing secret-shaped is written down."""
    rng = random.Random(seed)
    counts = Counter(keywords)
    top = [k for k, _ in counts.most_common(2)] or ["token", "key"]
    first, second = top[0], top[-1]
    image = base64.b64encode(rng.randbytes(max(size - 200, 0) * 3 // 4)).decode()
    markdown = (f"# Notes\n\nThe {first} and the {second} live elsewhere.\n\n"
                f"![diagram](data:image/png;base64,{image})\n")[:size]
    hex_line = f"{first} = " + "".join(rng.choices("0123456789abcdef", k=size))
    curl_line = []
    while sum(map(len, curl_line)) < 20_000:
        user, word = _token(rng, 8), _token(rng, 24)
        curl_line.append(f"curl -s -u {user}:{word} -H 'Authorization: Bearer {_token(rng, 40)}' "
                         f"https://{_token(rng, 10).lower()}.example/{_token(rng, 6)} ")
    curl_row = json.dumps({"cmd": "".join(curl_line)}) + "\n"
    jsonl = (curl_row * (size // len(curl_row) + 1))[:size]
    repeats = ((first + " ") * (size // (len(first) + 1) + 1))[:size]
    raw = base64.b64encode(rng.randbytes(size * 3 // 4)).decode()
    wrapped = "\n".join(raw[i:i + 76] for i in range(0, len(raw), 76))
    unique = sorted(set(keywords))
    every, n = [], 0
    while n < size:
        line = " ".join(f"{k}={_token(rng, 32)}" for k in unique) + "\n"
        every.append(line)
        n += len(line)
    return [
        ("markdown with an image", markdown),
        ("hex on one line", hex_line[:size]),
        ("jsonl of curl lines", jsonl),
        ("keyword repeats", repeats),
        ("word runs", _words(rng, size)),
        ("base64 wrapped", wrapped[:size]),
        ("base64 on one line", raw[:size]),
        ("every keyword", "".join(every)[:size]),
    ]


def cost(mb: float, factor: float) -> bool:
    from moxi_secrets import engine

    scan = scan_module()
    module = engine.re2_module()
    if module is None:
        raise Stop("google-re2 does not import; the cost check needs it")
    eng = engine.Re2Engine(module)
    sets = scan.load_sets()
    keywords = [k for rules in sets.rules.values() for r in rules for k in r.keywords]
    ok = True
    for name, text in shapes(keywords, int(mb * 1_000_000)):
        size = len(text.encode("utf-8")) / 1_000_000
        line = [f"{name} ({size:.2f} MB):"]
        for set_name in SETS:
            t = engine.Text(text)
            admitted = sets.keywords_in(t, eng)
            per_rule, candidates = [], 0
            for rule in sets.rules[set_name]:
                if rule.regex is None or rule.path is not None:
                    continue
                if rule.keywords and not any(k in admitted for k in rule.keywords):
                    continue
                rx = eng.compile(rule.regex["pattern"])
                start = time.perf_counter()
                n = sum(1 for _ in rx.finditer(t.data))
                per_rule.append((time.perf_counter() - start, rule.id))
                candidates += n
            t = engine.Text(text)
            start = time.perf_counter()
            scan.run_gitleaks(t, (set_name,), None, eng)
            took = time.perf_counter() - start
            budget = factor * (BUDGET_PER_MB * size + BUDGET_PER_CANDIDATE * candidates)
            line.append(f"{set_name} {took:.3f}s of {budget:.3f}s ({candidates} candidates)")
            if took > budget:
                ok = False
                slow = sorted(per_rule, reverse=True)[:3]
                line.append("OVER BUDGET; slowest rules: " + ", ".join(
                    f"{rid} {sec / size:.2f}s/MB" for sec, rid in slow))
        say(" ".join(line))
    if not ok:
        say("A rule that breaks the budget goes into rules/drop.txt with its measured cost "
            "as its reason; then run the bump again.")
    return ok


# -- step 6: the gitleaks binary on the package's own files ---------------------------------

def own_tree(tag: str) -> bool:
    if shutil.which("go") is None:
        raise Stop("Go is not on PATH; the own-tree check needs it")
    cmd = ["go", "run", f"{GO_MODULE}@{tag}", "dir", str(PACKAGE), "--config",
           "credentials.toml"]
    say(f"in {RULES}: {' '.join(cmd)}")
    try:
        done = subprocess.run(cmd, cwd=RULES, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Stop(f"gitleaks did not run: {exc}") from None
    log = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in done.stderr.splitlines()]
    summary = [line for line in log if "leaks found" in line] or log[-1:] or [""]
    say(f"exit {done.returncode}: {summary[-1].strip()}")
    if done.returncode == 0:
        return True
    say("The package's own files must raise no hit: change the file it names, never the "
        "allowance.")
    return False


# -- step 7: the package's tests ------------------------------------------------------------

def package_tests() -> bool:
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=PACKAGE,
                              capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Stop(f"the tests did not run: {exc}") from None
    tail = done.stdout.strip().splitlines()[-1:] or ["(no output)"]
    say(f"exit {done.returncode}: {tail[0]}")
    if done.returncode != 0:
        say(done.stdout[-4000:])
    return done.returncode == 0


# -- step 8: google-re2's wheels ------------------------------------------------------------

def wheel_tags(filenames) -> list[tuple[str, str, str]]:
    """(python, abi, platform) for each wheel file name, compressed tag sets expanded."""
    out = []
    for name in filenames:
        if not name.endswith(".whl"):
            continue
        py, abi, plat = name[:-4].split("-")[-3:]
        for p in py.split("."):
            for a in abi.split("."):
                for pl in plat.split("."):
                    out.append((p, a, pl))
    return sorted(set(out))


_CP = re.compile(r"cp3(\d+)$")
_LINUX = re.compile(r"(many|musl)linux_(\d+)_(\d+)_(\w+)$")
_LEGACY = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
_MAC = re.compile(r"macosx_(\d+)_(\d+)_(\w+)$")
_MAC_ARCHES = {"universal2": ("arm64", "x86_64"), "intel": ("x86_64",),
               "universal": ("x86_64",)}


def _libc(facts) -> tuple[str, tuple]:
    libc = facts["libc"]
    if not libc:
        return "musl", ()
    found = re.search(r"(\d+)\.(\d+)", libc)
    return "glibc", (int(found.group(1)), int(found.group(2))) if found else ()


def wheel_covers(tag: tuple[str, str, str], f: dict) -> bool:
    """True when pip would install the wheel with this tag on the platform `f` describes."""
    py, abi, plat = tag
    version = tuple(f["version"])
    if py.startswith("cp"):
        found = _CP.match(py)
        if f["implementation"] != "CPython" or not found:
            return False
        want = (3, int(found.group(1)))
        if abi == "abi3":
            if version < want or f["gil_disabled"]:
                return False
        elif abi.endswith("t"):
            if version != want or not f["gil_disabled"]:
                return False
        elif version != want or f["gil_disabled"]:
            return False
    elif not py.startswith("py3"):
        return False
    machine, bits = f["machine"].lower(), f["bits"]
    if plat == "any":
        return True
    if plat.startswith(("manylinux", "musllinux")):
        if f["system"] != "Linux":
            return False
        found = _LINUX.match(plat)
        if found:
            kind = "glibc" if found.group(1) == "many" else "musl"
            floor, arch = (int(found.group(2)), int(found.group(3))), found.group(4)
        else:
            head, _, arch = plat.partition("_")
            kind, floor = "glibc", _LEGACY.get(head, (99, 0))
        libc, have = _libc(f)
        if libc != kind or (kind == "glibc" and have < floor):
            return False
        width = 32 if arch in ("i686", "armv7l") else 64
        return machine == arch and bits == width
    if plat.startswith("macosx"):
        found = _MAC.match(plat)
        if f["system"] != "Darwin" or not found:
            return False
        floor, arch = (int(found.group(1)), int(found.group(2))), found.group(3)
        mac = tuple(int(x) for x in (f["mac"] or "0.0").split(".")[:2])
        return mac >= floor and machine in _MAC_ARCHES.get(arch, (arch,))
    if f["system"] != "Windows":
        return False
    if plat == "win32":
        return bits == 32
    if plat == "win_amd64":
        return bits == 64 and machine in ("amd64", "x86_64")
    if plat == "win_arm64":
        return bits == 64 and machine in ("arm64", "aarch64")
    return False


def fact_grid(top: tuple[int, int]) -> list[dict]:
    """Platforms to ask both the wheels and `re2_wheel_fits` about, CPython 3.12 (the
    floor) up to `top`."""
    base = {"implementation": "CPython", "libc": None, "mac": ""}
    places = []
    for libc in ("glibc 2.17", "glibc 2.26", "glibc 2.27", "glibc 2.28", "glibc 2.39", None):
        for machine, bits in (("x86_64", 64), ("aarch64", 64), ("i686", 32), ("armv7l", 32),
                              ("ppc64le", 64), ("s390x", 64)):
            places.append({"system": "Linux", "machine": machine, "bits": bits, "libc": libc})
    for mac in ("11.7", "12.7", "13.0", "14.5", "15.1"):
        for machine in ("arm64", "x86_64"):
            places.append({"system": "Darwin", "machine": machine, "bits": 64, "mac": mac})
    for machine, bits in (("AMD64", 64), ("ARM64", 64), ("AMD64", 32), ("x86", 32)):
        places.append({"system": "Windows", "machine": machine, "bits": bits})
    places.append({"system": "FreeBSD", "machine": "amd64", "bits": 64})
    grid = []
    for minor in range(12, top[1] + 1):
        for gil in (False, True):
            for place in places:
                grid.append({**base, **place, "version": (3, minor), "gil_disabled": gil})
    grid.append({**base, **places[3], "implementation": "PyPy", "version": (3, 12),
                 "gil_disabled": False})
    return grid


def _fact_text(f: dict) -> str:
    if f["system"] == "Linux":
        where = f["libc"] or "musl"
    elif f["system"] == "Darwin":
        where = f"macOS {f['mac']}"
    else:
        where = f["system"]
    return (f"{f['implementation']} {f['version'][0]}.{f['version'][1]}"
            f"{' free-threaded' if f['gil_disabled'] else ''} {where} {f['machine']} "
            f"{f['bits']}-bit")


def wheel_check(tags, fits, bound: tuple[int, int], marker_bound) -> dict:
    """Where `fits` (re2_wheel_fits) and the wheels disagree, and the bounds they imply.

    `unsafe` lists platforms the check passes and no wheel covers (an install there would
    build the sdist); `stricter` lists platforms a wheel covers and the check refuses."""
    minors = [int(m.group(1)) for p, _, _ in tags if (m := _CP.match(p))]
    newest = (3, max(minors)) if minors else (3, 0)
    want = (3, newest[1] + 1)
    unsafe, stricter = [], []
    for f in fact_grid((3, max(want[1], bound[1]))):
        covered = any(wheel_covers(t, f) for t in tags)
        passes = fits(f)
        if passes and not covered:
            unsafe.append(_fact_text(f))
        elif covered and not passes:
            stricter.append(_fact_text(f))
    return {"bound": want, "unsafe": unsafe, "stricter": stricter,
            "bound_ok": bound == want and marker_bound == want}


def marker_bound() -> tuple[int, int] | None:
    data = tomllib.loads((PACKAGE / "pyproject.toml").read_text("utf-8"))
    for spec in data["project"]["optional-dependencies"]["re2"]:
        found = re.search(r"python_version\s*<\s*'3\.(\d+)'", spec)
        if found:
            return (3, int(found.group(1)))
    return None


def wheels() -> bool:
    from moxi_secrets import engine

    try:
        release = json.loads(fetch(PYPI))
    except ValueError:
        raise Stop(f"{PYPI} did not answer JSON") from None
    version = release["info"]["version"]
    names = [f["filename"] for f in release["urls"]]
    tags = wheel_tags(names)
    say(f"google-re2 {version}: {len([n for n in names if n.endswith('.whl')])} wheels")
    by_platform = defaultdict(list)
    for py, abi, plat in tags:
        by_platform[plat].append(py if abi == py else f"{py}/{abi}")
    for plat in sorted(by_platform):
        say(f"  {plat}: {' '.join(sorted(set(by_platform[plat])))}")
    found = wheel_check(tags, engine.re2_wheel_fits, engine.RE2_BOUND, marker_bound())
    want = found["bound"]
    if found["bound_ok"]:
        say(f"bound: python_version < '3.{want[1]}' in the re2 extra and RE2_BOUND agree")
    else:
        say(f"bound: the newest wheel is for 3.{want[1] - 1}; set the re2 extra's marker to "
            f"python_version < '3.{want[1]}' and RE2_BOUND to {want}")
    for f in found["stricter"]:
        say(f"  re2_wheel_fits() refuses where a wheel fits: {f}")
    for f in found["unsafe"]:
        say(f"  UNSAFE: re2_wheel_fits() passes where no wheel fits: {f}")
    ok = found["bound_ok"] and not found["unsafe"]
    if not ok:
        say("Update the re2 extra's bound and engine.re2_wheel_fits() to these wheels.")
    return ok


# -- the run --------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bump_gitleaks.py",
        description="Bump the vendored gitleaks rules to a tag and check them.")
    parser.add_argument("tag", help="the gitleaks release tag, such as v8.31.0")
    parser.add_argument("--samples", type=int, default=60,
                        help="samples per pattern for the Go comparison (default 60)")
    parser.add_argument("--seed", type=int, default=7, help="the sampler's seed (default 7)")
    parser.add_argument("--show", action="store_true",
                        help="print the first failing text of each failing rule")
    parser.add_argument("--cost-mb", type=float, default=1.0,
                        help="size of each adversarial text in MB (default 1)")
    parser.add_argument("--cost-factor", type=float, default=1.0,
                        help="multiply the cost budget by this (default 1)")
    args = parser.parse_args(argv)
    if not TAG.fullmatch(args.tag):
        parser.error(f"{args.tag} is not a release tag like v8.31.0")
    started = time.perf_counter()
    try:
        heading(f"1-2. fetch {args.tag} and write rules/upstream/")
        write_upstream(args.tag)
        heading("3. regenerate sets.json")
        data = regenerate()
        steps = (
            ("4. compare Go, RE2 and the fallback",
             lambda: compare(data, args.samples, args.seed, args.show)),
            ("5. cost on RE2", lambda: cost(args.cost_mb, args.cost_factor)),
            (f"6. gitleaks {args.tag} on the package's own files", lambda: own_tree(args.tag)),
            ("7. the package's tests", package_tests),
            ("8. google-re2's wheels", wheels),
        )
        for title, step in steps:
            heading(title)
            if not step():
                say(f"\nstopped at step {title.split('.')[0]} after "
                    f"{time.perf_counter() - started:.0f}s")
                return 1
    except Stop as exc:
        say(f"\nbump_gitleaks: {exc}")
        return 2
    say(f"\nall steps pass in {time.perf_counter() - started:.0f}s. Commit the rules/ "
        f"changes with a subject naming {args.tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

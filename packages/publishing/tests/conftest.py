"""Shared fixtures: real git repos copied from one template, and config in the test's home.

Git runs with its own global config in tmp (`GIT_CONFIG_GLOBAL`) and no system config, so
nothing here reads or writes the real ones. The git hooks repo writes run this
environment's Python, where publishing is installed, so the pieces run for real.

Credential-shaped values come from `token(seed)`, built from parts with a seeded
generator, so no test file holds one whole.
"""

from __future__ import annotations

import os
import random
import shutil
import string
import subprocess
from pathlib import Path

import pytest

GITCONFIG = ("[user]\n\tname = operator\n\temail = operator@example.test\n"
             "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n"
             "[protocol \"file\"]\n\tallow = always\n")


def git(cwd: Path, *args: str, check: bool = True, env: dict | None = None,
        stdin_data: str | None = None) -> subprocess.CompletedProcess:
    """Run git in `cwd` with this process's (fenced) environment plus `env`."""
    full = dict(os.environ)
    full.update(env or {})
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          env=full, timeout=60, input=stdin_data)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed ({done.returncode}):\n"
                             f"{done.stdout}{done.stderr}")
    return done


def token(seed: int = 7) -> str:
    """A made-up GitHub token: the shape secrets' credentials set finds."""
    r = random.Random(seed)
    return "gh" + "p_" + "".join(r.choice(string.ascii_letters + string.digits)
                                 for _ in range(36))


def fingerprint(value: str) -> str:
    """The fingerprint an allowlist line names for the credential `value`: the one
    secrets' scan gives the hit that spans the whole value."""
    import moxi_secrets

    found = {hit.fingerprint for hit in moxi_secrets.scan(value, ("credentials",))
             if (hit.start, hit.end) == (0, len(value))}
    assert len(found) == 1, (value, found)
    return found.pop()


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    """A repo with one commit, made once and copied into each test that wants one."""
    where = tmp_path_factory.mktemp("template")
    config = where / "gitconfig"
    config.write_text(GITCONFIG, encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_GLOBAL": str(config),
           "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(where)}
    repo = where / "proj-a"
    for args in (("init", "-q", str(repo)),):
        subprocess.run(["git", *args], check=True, env=env, capture_output=True)
    (repo / "README.md").write_text("proj-a\n", encoding="utf-8")
    for args in (("add", "README.md"), ("commit", "-q", "-m", "start")):
        subprocess.run(["git", *args], cwd=repo, check=True, env=env, capture_output=True)
    return repo


@pytest.fixture
def gitenv(tmp_path, monkeypatch) -> Path:
    """Git's global config in the test's tmp."""
    config = tmp_path / "gitconfig"
    config.write_text(GITCONFIG, encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return config


@pytest.fixture
def repo(template, gitenv, tmp_path) -> Path:
    """A copy of the template repo, `proj-a`, in the test's tmp."""
    dest = tmp_path / "proj-a"
    shutil.copytree(template, dest, symlinks=True)
    return dest


def write_config(module: str, text: str) -> Path:
    """Write module `module`'s config file in the test's home."""
    from moxi import paths

    path = paths.Layout(module).config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def names() -> Path:
    """secrets' private names list, with made-up names."""
    return write_config("secrets", '[private_names]\nwords = ["bobsecret"]\n'
                                   'emails = ["bob@private.example.test"]\n'
                                   '[author]\nname = "operator"\n'
                                   'email = "operator@example.test"\n')


def commit(repo: Path, message: str, *, name: str = "note.md", text: str = "a line\n",
           flags: tuple[str, ...] = (), env: dict | None = None
           ) -> subprocess.CompletedProcess:
    """Append `text` to `name`, stage it and commit with `message` (from a file, so every
    line reaches the git hook as written); returns the finished process."""
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
    git(repo, "add", "--", name)
    msg = repo.parent / "message.txt"
    msg.write_text(message, encoding="utf-8")
    return git(repo, "commit", "-q", *flags, "-F", str(msg), check=False, env=env)


def last_message(repo: Path) -> str:
    """HEAD's message as committed (`%B` less the newline `log` adds)."""
    return git(repo, "log", "-1", "--format=%B").stdout.removesuffix("\n")


def public_repo(repo: Path, *pieces: str, sets: tuple[str, ...] = ()) -> Path:
    """Mark `repo` public and register only `pieces`, as a test of one piece wants."""
    from moxi_repo import api

    git(repo, "config", "moxi-publishing.public", "true")
    api.add(repo, *pieces, sets=list(sets))
    return repo


@pytest.fixture
def remote(repo, tmp_path) -> Path:
    """A bare remote, `origin` of `repo`, holding its `main` (pushed before any piece)."""
    bare = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "main")
    return bare


# -- releases: a workspace, and GitHub and the package index faked -------------------------

#: The root `pyproject.toml` of a repo that names only the check `main` waits for.
CHECK_TOML = """[tool.moxi-publishing]
check-workflow = "ci.yml"
check-job = "linux"
"""

ROOT_TOML = """[tool.uv.workspace]
members = ["packages/a", "packages/a-extra"]

[tool.moxi-publishing]
not-ready = {not_ready}
check-workflow = "ci.yml"
check-job = "linux"
full-jobs = {full_jobs}
"""

#: The `full-jobs` of the release workspace: a matrix leg and a job of its own.
FULL_JOBS = ["other (windows-latest)", "secrets-windows"]


def member_toml(name: str, version: str, deps=(), extras=None) -> str:
    text = (f'[project]\nname = "{name}"\nversion = "{version}"\n'
            f"dependencies = {list(deps)!r}\n".replace("'", '"'))
    if extras:
        text += "[project.optional-dependencies]\n" + "".join(
            f"{k} = {v!r}\n".replace("'", '"') for k, v in extras.items())
    return text


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class Fake:
    """GitHub's API and the package index, answering from what a test sets."""

    def __init__(self):
        self.default_branch = "main"
        # commit: [{"event", "branch", "jobs": [(job, conclusion)]}], one dict per run; a
        # conclusion None is a job still running. A run's "earlier" holds the jobs of its
        # earlier attempts, a list per attempt, which only `filter=all` answers with.
        self.runs: dict[str, list[dict]] = {}
        self.full_jobs = list(FULL_JOBS)                   # each new run's, concluded success
        self.index: dict[str, dict[str, list[dict]]] = {}  # dist: {version: [file]}
        self.tags: list[str] = []
        self.broken: set[str] = set()                      # path prefixes answering 500
        self.asked: list[str] = []
        self.queries: list[str] = []                       # each URL's query string

    def publish(self, dist: str, version: str, *, sha256: str = "0" * 64,
                filename: str | None = None, yanked: bool = False) -> None:
        self.index.setdefault(dist, {}).setdefault(version, []).append(
            {"filename": filename or f"{dist.replace('-', '_')}-{version}.tar.gz",
             "digests": {"sha256": sha256}, "yanked": yanked})

    def green(self, commit: str, job: str = "linux", conclusion: str = "success", *,
              event: str = "push", branch: str | None = None) -> None:
        """A job of the workflow run at `commit` from `event` on `branch` (default: the
        default branch), the run made the first time it is named, holding then each of
        `full_jobs` concluded `success`."""
        branch = branch or self.default_branch
        runs = self.runs.setdefault(commit, [])
        run = next((r for r in runs if (r["event"], r["branch"]) == (event, branch)), None)
        if run is None:
            run = {"event": event, "branch": branch,
                   "jobs": [(name, "success") for name in self.full_jobs]}
            runs.append(run)
        run["jobs"].append((job, conclusion))

    def answer(self, url: str) -> tuple[int, object]:
        from urllib.parse import parse_qs, urlsplit

        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        self.asked.append(path)
        self.queries.append(parts.query)
        if any(path.startswith(p) for p in self.broken):
            return 500, None
        bits = path.strip("/").split("/")
        if bits[0] == "pypi":
            files = self.index.get(bits[1])
            if files is None:
                return 404, None
            if len(bits) == 3:
                return 200, {"releases": files}
            return (200, {"urls": files[bits[2]]}) if bits[2] in files else (404, None)
        rest = bits[3:]
        if not rest:
            return 200, {"default_branch": self.default_branch}
        if rest[:2] == ["actions", "workflows"]:
            # Every run at the commit, whatever event or branch the query names, so the
            # gate's own reading of each run is what a test holds. A run may give another
            # commit as its own, as a wrong answer would.
            sha = query.get("head_sha", [""])[0]
            runs = [{"id": f"{sha}-{n}", "head_sha": run.get("head_sha", sha),
                     "event": run["event"], "head_branch": run["branch"]}
                    for n, run in enumerate(self.runs.get(sha, []))]
            return 200, {"workflow_runs": runs}
        if rest[:2] == ["actions", "runs"]:
            sha, _, n = rest[2].rpartition("-")
            runs = self.runs.get(sha, [])
            run = runs[int(n)] if n.isdigit() and int(n) < len(runs) else {}
            jobs = list(run.get("jobs", []))
            if query.get("filter", ["latest"])[0] == "all":
                jobs = [job for attempt in run.get("earlier", []) for job in attempt] + jobs
            return 200, {"jobs": [{"name": name, "conclusion": c,
                                   "status": "in_progress" if c is None else "completed"}
                                  for name, c in jobs]}
        if rest[:3] == ["git", "matching-refs", "tags"]:
            prefix = "/".join(rest[3:])
            return 200, [{"ref": f"refs/tags/{t}"} for t in self.tags if t.startswith(prefix)]
        return 404, None


def fake_web(fake: Fake):
    """A `tags.Web` that asks `fake` instead of the network."""
    import json

    from moxi_publishing import tags

    class FakeWeb(tags.Web):
        def fetch(self, url, headers):
            status, data = fake.answer(url)
            return status, json.dumps(data).encode() if data is not None else b""

    return FakeWeb(github_api="http://github.example.test", pypi="http://pypi.example.test")


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def served(fake, monkeypatch):
    """`fake` on a loopback port, with the environment pointing the gate at it, for a git
    hook running in its own process."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- the name http.server calls
            status, data = fake.answer(self.path)
            body = json.dumps(data).encode() if data is not None else b""
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setenv("MOXI_PUBLISHING_GITHUB_API_URL", base)
    monkeypatch.setenv("MOXI_PUBLISHING_PYPI_URL", base)
    yield fake
    server.shutdown()
    server.server_close()


def release_files(repo: Path, *, not_ready=(), a_version="0.1.0",
                  a_heading="## 0.1.0 (2026-01-05)") -> None:
    """Write the release workspace: `proj-a` and `proj-a-extra`, which requires
    `proj-a>=0.0.9` and has an extra wanting `proj-a>=0.9`."""
    write(repo, "pyproject.toml", ROOT_TOML.format(
        not_ready=repr(list(not_ready)).replace("'", '"'),
        full_jobs=repr(FULL_JOBS).replace("'", '"')))
    write(repo, "packages/a/pyproject.toml", member_toml("proj-a", a_version))
    write(repo, "packages/a/CHANGELOG.md",
          f"# Changelog\n\n## Unreleased\n\n{a_heading}\n\n- First.\n\n### Fixed\n\n- A fix.\n"
          "\n## 0.0.9 (2026-01-01)\n\n- Older.\n")
    write(repo, "packages/a-extra/pyproject.toml",
          member_toml("proj-a-extra", "0.2.0", ["proj-a>=0.0.9"], {"more": ["proj-a>=0.9"]}))
    write(repo, "packages/a-extra/CHANGELOG.md", "## 0.2.0 (2026-01-06)\n\n- Extra.\n")


@pytest.fixture
def released_repo(repo, remote, fake):
    """`repo` holding the release workspace on main, pushed to `origin`, with its check
    green there and `proj-a` 0.0.9 on the index."""
    release_files(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "The workspace")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "config", "moxi-publishing.github", "operator/proj-a")
    fake.green(git(repo, "rev-parse", "HEAD").stdout.strip())
    fake.publish("proj-a", "0.0.9")
    return repo

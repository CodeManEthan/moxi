"""`moxi install`, by its parts and by its steps against a fake environment.

The steps run against fake distributions in the `site` fixture: a fake runner stands in
for uv, installing the fake modules a command names and writing the receipt uv would.
`test_install_uv.py` runs the same installer against uv and local wheels.
"""

from __future__ import annotations

import importlib
import io
import shutil
import sys

import pytest

import moxi.cli as cli
import moxi.install as install
import moxi.modules as modules
from moxi.install import InstallError, Req, receipt_entry, receipt_options, union

# -- requirements --------------------------------------------------------------------------


def test_req_text():
    assert Req("moxi-comms").text() == "moxi-comms"
    assert Req("moxi-comms", ["cache", "codex"], ">=0.1,<0.2").text() == (
        "moxi-comms[cache,codex]>=0.1,<0.2")
    assert Req("moxi-turns", url="git+https://h/turns@v1").text() == (
        "moxi-turns @ git+https://h/turns@v1")


def test_union_keeps_every_distribution_and_extra_the_receipt_holds():
    held = [Req("moxi-comms", ["cache"]), Req("moxi-turns", url="git+https://h/t")]
    out = union(held, [Req("moxi-comms", ["codex"]), Req("moxi-turns"), Req("moxi-cache")])
    assert [r.text() for r in out] == [
        "moxi-comms[cache,codex]", "moxi-turns @ git+https://h/t", "moxi-cache"]


def test_union_a_from_url_replaces_an_index_name_and_keeps_extras():
    out = union([Req("Moxi_Comms", ["cache"], ">=0.1")],
                [Req("moxi-comms", url="git+https://h/comms@main")])
    assert [r.text() for r in out] == ["Moxi_Comms[cache] @ git+https://h/comms@main"]


def test_parse_from():
    req = install.parse_from("moxi-turns @ git+https://github.com/o/turns@v0.1.0")
    assert (req.name, req.url) == ("moxi-turns", "git+https://github.com/o/turns@v0.1.0")
    assert install.parse_from("moxi @ file:///w/moxi.whl").url == "file:///w/moxi.whl"
    req = install.parse_from("Moxi_Comms[Codex,cache] @ file:///w/c.whl")
    assert (req.name, req.extras) == ("moxi-comms", ["cache", "codex"])
    for bad, why in [("moxi-turns", "names no URL"),
                     ("requests @ git+https://h/r", "not a Moxi distribution"),
                     ("moxi-Bad_Name2x @ git+https://h", "not a Moxi distribution"),
                     ("moxi-turns @ git+https://h ; python_version>'3'", "marker"),
                     ("moxi-turns @ git+https://h junk", "not a requirement")]:
        with pytest.raises(InstallError, match=why):
            install.parse_from(bad)


# -- the receipt ---------------------------------------------------------------------------

@pytest.mark.parametrize("entry, text", [
    ({"name": "moxi-comms"}, "moxi-comms"),
    ({"name": "moxi-comms", "extras": ["cache"], "specifier": ">=0.1, <0.2"},
     "moxi-comms[cache]>=0.1,<0.2"),
    ({"name": "moxi-turns", "path": "/w/moxi_turns-0.1.0-py3-none-any.whl"},
     "moxi-turns @ file:///w/moxi_turns-0.1.0-py3-none-any.whl"),
    ({"name": "moxi-turns", "directory": "/src/turns"}, "moxi-turns @ file:///src/turns"),
    ({"name": "x", "url": "https://h/x.tar.gz", "subdirectory": "pkg"},
     "x @ https://h/x.tar.gz#subdirectory=pkg"),
    ({"name": "moxi-cache", "extras": ["a"], "git": "https://github.com/o/cache?rev=private"},
     "moxi-cache[a] @ git+https://github.com/o/cache@private"),
    ({"name": "moxi-cache", "git": "file:///r/cache?subdirectory=sub"},
     "moxi-cache @ git+file:///r/cache#subdirectory=sub"),
    ({"name": "moxi-cache", "git": "ssh://git@github.com/o/cache"},
     "moxi-cache @ git+ssh://git@github.com/o/cache"),
])
def test_receipt_entry_writes_back(entry, text):
    assert receipt_entry(entry).text() == text


def test_receipt_entry_editable():
    req = receipt_entry({"name": "moxi-cache", "editable": "/src/cache"})
    assert req.editable == "/src/cache"


@pytest.mark.parametrize("entry, why", [
    ({"name": "x", "marker": "python_version > '3'"}, "unknown key marker"),
    ({"name": "x", "editable": "/src/x", "extras": ["a"]}, "editable install with extras"),
    ({"name": "x", "git": "https://h/x?lfs=true"}, "lfs="),
    ({"name": "x", "path": "/a", "url": "https://b"}, "more than one source"),
    ({"name": "x", "subdirectory": "s"}, "subdirectory without a URL"),
    ({"extras": []}, "names no distribution"),
])
def test_receipt_entry_refuses_what_it_cannot_write_back(entry, why):
    with pytest.raises(InstallError, match=why):
        receipt_entry(entry)


def test_receipt_options_are_passed_back():
    options = {
        "find-links": ["file:///w"], "index-url": "https://i/simple",
        "extra-index-url": ["https://e/simple"], "no-index": False,
        "compile-bytecode": True, "prerelease": "allow",
        "index": [{"name": "foo", "url": "https://f/simple", "explicit": False,
                   "default": False, "format": "simple", "authenticate": "auto"},
                  {"url": "https://d/simple", "default": True}],
    }
    assert receipt_options(options) == [
        "--find-links", "file:///w", "--index-url", "https://i/simple",
        "--extra-index-url", "https://e/simple", "--compile-bytecode",
        "--prerelease", "allow", "--index", "foo=https://f/simple",
        "--default-index", "https://d/simple"]
    with pytest.raises(InstallError, match="config-settings"):
        receipt_options({"config-settings": {"a": "b"}})
    with pytest.raises(InstallError, match="index"):
        receipt_options({"index": [{"url": "https://f", "explicit": True}]})


def test_tool_command_re_runs_with_everything_the_receipt_holds(tmp_path):
    prefix = tmp_path / "tools" / "moxi-comms"
    receipt = {"tool": {
        "requirements": [{"name": "moxi-comms", "extras": ["cache"]},
                         {"name": "moxi-turns", "git": "https://h/turns?rev=v1"},
                         {"name": "moxi-x", "editable": "/src/x"}],
        "constraints": [{"name": "websockets", "specifier": "<16"}],
        "excludes": ["foo"],
        "python": "3.12",
        "entrypoints": [{"name": "moxi", "install-path": "/b/moxi", "from": "moxi-comms"}],
        "options": {"find-links": ["file:///w"]},
    }}
    argv, env, reqs = install.tool_command(receipt, prefix, [Req("moxi-comms", ["codex"])],
                                           "uv", tmp_path)
    assert argv == [
        "uv", "tool", "install", "moxi-comms[cache,codex]",
        "--with", "moxi-turns @ git+https://h/turns@v1", "--with-editable", "/src/x",
        "--constraints", str(tmp_path / "constraints.txt"),
        "--excludes", str(tmp_path / "excludes.txt"),
        "--python", "3.12", "--find-links", "file:///w"]
    assert (tmp_path / "constraints.txt").read_text() == "websockets<16\n"
    assert env == {"UV_TOOL_DIR": str(tmp_path / "tools"), "UV_TOOL_BIN_DIR": "/b"}
    assert [r.key for r in reqs] == ["moxi-comms", "moxi-turns", "moxi-x"]


def test_tool_command_refuses_an_unknown_receipt_key(tmp_path):
    receipt = {"tool": {"requirements": [{"name": "moxi"}], "lockfile": "x"}}
    with pytest.raises(InstallError, match="lockfile"):
        install.tool_command(receipt, tmp_path, [], "uv", tmp_path)


# -- the environment -----------------------------------------------------------------------

def test_environment_refuses_the_system_python(monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    with pytest.raises(InstallError, match="system Python"):
        install.environment()


def test_environment_refuses_an_editable_moxi(monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", "/somewhere/else")
    monkeypatch.setattr(install, "_direct_url", lambda name: {
        "url": "file:///w/moxi", "dir_info": {"editable": True}})
    with pytest.raises(InstallError, match="editable"):
        install.environment()


def test_environment_tells_a_tool_environment_by_its_receipt(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "base_prefix", "/somewhere/else")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(install, "_direct_url", lambda name: None)
    assert install.environment().kind == install.PLAIN
    (tmp_path / "uv-receipt.toml").write_text('[tool]\nrequirements = [{ name = "moxi" }]\n')
    env = install.environment()
    assert env.kind == install.TOOL
    assert env.receipt["tool"]["requirements"] == [{"name": "moxi"}]


def test_environment_runs_fresh_processes_on_moxi_paths_python(monkeypatch, tmp_path):
    import moxi.paths

    monkeypatch.setattr(sys, "base_prefix", "/somewhere/else")
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(install, "_direct_url", lambda name: None)
    monkeypatch.setattr(moxi.paths, "python", lambda: tmp_path / "Scripts" / "python.exe")
    assert install.environment().python == str(tmp_path / "Scripts" / "python.exe")


def test_installed_source_reads_direct_url(site):
    site.distribution("moxi-comms", direct_url={
        "url": "https://github.com/o/comms",
        "vcs_info": {"vcs": "git", "commit_id": "abc", "requested_revision": "v0.1.0"}})
    site.distribution("moxi-turns", direct_url={"url": "file:///w/t.whl",
                                                "archive_info": {}})
    site.distribution("moxi-cache")
    assert install.installed_source("moxi-comms").text() == (
        "moxi-comms @ git+https://github.com/o/comms@v0.1.0")
    assert install.installed_source("moxi-turns").text() == "moxi-turns @ file:///w/t.whl"
    assert install.installed_source("moxi-cache") is None


# -- the steps, against a fake environment -------------------------------------------------

#: What the fake index holds: each distribution's module, requirements and extras.
CATALOG = {
    "moxi-turns": dict(module="turns", extras={"codex": ["websockets>=13"]},
                       OPTIONAL="{'codex': 'Reaches Codex sessions.'}",
                       SETUP="'moxi_turns.setup:run'"),
    "moxi-cache": dict(module="cache", SETUP="'moxi_cache.setup:run'"),
    "moxi-comms": dict(module="comms", requires=("moxi-turns>=0.1,<0.2",),
                       extras={"cache": ["moxi-cache>=0.1,<0.2"],
                               "codex": ["moxi-turns[codex]>=0.1,<0.2"]},
                       OPTIONAL="{'cache': 'Holds a cold send.', "
                                "'codex': 'Delivers to Codex.'}"),
    "moxi-codex": dict(module="codex"),
    "websockets": dict(),
}


def _requirement(text):
    """A requirement as the fake uv reads it, its name and extras normalized."""
    from moxi._vendor.packaging.requirements import Requirement

    req = Requirement(text)
    req.name = cli.normalize(req.name)
    req.extras = {cli.normalize(e) for e in req.extras}
    return req


class Fake:
    """A fake uv and a fake environment over the `site` fixture."""

    def __init__(self, site, tmp_path, monkeypatch, kind=install.TOOL,
                 receipt=({"name": "moxi"},)):
        self.site = site
        self.prefix = tmp_path / "tools" / "moxi"
        self.prefix.mkdir(parents=True)
        self.kind = kind
        self.runs: list[list[str]] = []
        self.envs: list[dict] = []
        self.setups: list[str] = []
        self.fail_setup: set[str] = set()
        self.interrupt: set[str] = set()
        self.replaces: list[bool] = []
        self.skills: list = []
        self.fail_run = False
        self.installed: set[str] = set()
        self.receipt = list(receipt)
        if kind == install.TOOL:
            self._write_receipt()
        monkeypatch.setattr(install, "environment", self.environment)
        monkeypatch.setattr(install, "run", self.run)
        monkeypatch.setattr(install, "snapshot", self.snapshot)
        monkeypatch.setattr(install, "run_settle", self.run_settle)
        real_which = shutil.which
        monkeypatch.setattr(shutil, "which",
                            lambda name, *a, **k: "/fake/uv" if name == "uv"
                            else real_which(name, *a, **k))

    def environment(self):
        receipt = None
        if self.kind == install.TOOL:
            import tomllib

            receipt = tomllib.loads((self.prefix / "uv-receipt.toml").read_text())
        return install.Environment(self.kind, self.prefix, "/fake/python", receipt)

    def snapshot(self, python):
        assert python == "/fake/python"
        modules.reset()
        importlib.invalidate_caches()
        return cli.describe()

    def run_settle(self, python, module, *, replace=False, skills=None):
        from moxi import _settle, files, paths

        # The install holds the core lock while it settles each module.
        files._descriptor(paths.core_lock())
        self.setups.append(module)
        self.replaces.append(replace)
        self.skills.append(skills)
        if module in self.interrupt:
            return _settle.Outcome(_settle.INTERRUPTED)
        return _settle.Outcome(1 if module in self.fail_setup else 0)

    def run(self, argv, env=None):
        self.runs.append(argv)
        self.envs.append(env or {})
        if self.fail_run:
            return 1
        if self.kind == install.TOOL:
            assert argv[:3] == ["/fake/uv", "tool", "install"]
            texts = [argv[3]] + [argv[i + 1] for i, a in enumerate(argv) if a == "--with"]
            self.receipt = []
            for text in texts:
                req = install.parse_from(text) if " @ " in text else None
                head = _requirement(text)
                entry = {"name": head.name}
                if head.extras:
                    entry["extras"] = sorted(head.extras)
                if req is not None:
                    entry["url"] = req.url
                self.receipt.append(entry)
            self._write_receipt()
        else:
            texts = argv[5:] if argv[1:3] == ["pip", "install"] else argv[4:]
        for text in texts:
            self._install(_requirement(text))
        return 0

    def _install(self, req):
        name, extras = req.name, req.extras
        if name == "moxi":
            return
        spec = CATALOG[name]
        if name not in self.installed:
            self.installed.add(name)
            fields = {k: v for k, v in spec.items()
                      if k not in ("module", "requires", "extras")}
            if "module" in spec:
                self.site.module(spec["module"], requires=spec.get("requires", ()),
                                 extras=spec.get("extras"), **fields)
            else:
                self.site.distribution(name, "13.1")
        for dep in spec.get("requires", ()):
            self._install(_requirement(dep))
        for extra in extras:
            for dep in spec.get("extras", {}).get(extra, ()):
                self._install(_requirement(dep))

    def _write_receipt(self):
        lines = ["[tool]", "requirements = ["]
        for entry in self.receipt:
            parts = [f'name = "{entry["name"]}"']
            if entry.get("extras"):
                parts.append("extras = [" + ", ".join(f'"{e}"' for e in entry["extras"])
                             + "]")
            if entry.get("url"):
                parts.append(f'url = "{entry["url"]}"')
            lines.append("    { " + ", ".join(parts) + " },")
        lines += ["]", "entrypoints = [",
                  '    { name = "moxi", install-path = "/b/moxi", from = "moxi" },', "]",
                  "", "[tool.options]", 'find-links = ["file:///w"]', ""]
        (self.prefix / "uv-receipt.toml").write_text("\n".join(lines))


@pytest.fixture
def fake(site, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    return Fake(site, tmp_path, monkeypatch)


def moxi_install(capsys, *argv):
    code = cli.main(["install", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_install_a_module_with_its_required_modules_only(fake, capsys):
    code, out, err = moxi_install(capsys, "comms")
    assert code == 0, err
    # One re-run, keeping the receipt's moxi and its options, adding moxi-comms.
    assert fake.runs == [["/fake/uv", "tool", "install", "moxi", "--with", "moxi-comms",
                          "--find-links", "file:///w"]]
    assert fake.envs[0] == {"UV_TOOL_DIR": str(fake.prefix.parent),
                            "UV_TOOL_BIN_DIR": "/b"}
    assert fake.installed == {"moxi-comms", "moxi-turns"}
    # Each new module is settled, required before requiring.
    assert fake.setups == ["turns", "comms"]
    # Standard input is not a terminal: every extra is no, with the line that adds it.
    assert "comms [cache] not installed: Holds a cold send." in out
    assert "add it later with: moxi install comms --with cache" in out
    assert "installed comms (moxi-comms 0.1.0)" in out
    assert "installed turns (moxi-turns 0.1.0)" in out
    # A required module's own extras are named, not asked for.
    assert "turns offers codex too: moxi install turns" in out


def test_install_beside_a_depending_module_goes_through_its_extra(fake, capsys):
    moxi_install(capsys, "comms")
    fake.runs.clear()
    code, out, err = moxi_install(capsys, "cache")
    assert code == 0, err
    assert "comms declares the extra cache (moxi-cache>=0.1,<0.2)" in out
    assert fake.runs[0][3:6] == ["moxi", "--with", "moxi-comms[cache]"]
    assert fake.setups[-1] == "cache"
    assert "installed cache" in out


def test_install_a_module_named_like_a_feature_extra_goes_to_its_own_distribution(
        fake, capsys):
    """Comms' feature extra codex (moxi-turns[codex]) does not route a module
    named codex; it installs from moxi-codex."""
    moxi_install(capsys, "comms")
    fake.runs.clear()
    code, out, err = moxi_install(capsys, "codex", "--third-party")
    assert code == 0, err
    assert "codex is not in Moxi's catalogue" in out
    assert "declares the extra codex" not in out
    assert fake.runs[0][3:] == ["moxi", "--with", "moxi-comms", "--with", "moxi-codex",
                                "--find-links", "file:///w"]
    assert "installed codex" in out


def test_install_extras_keep_what_the_receipt_holds(fake, capsys):
    moxi_install(capsys, "comms", "--with", "cache")
    assert fake.runs[-1][3:6] == ["moxi", "--with", "moxi-comms[cache]"]
    fake.setups.clear()
    code, out, err = moxi_install(capsys, "comms", "--with", "codex")
    assert code == 0, err
    # Already present: no step 2, its setup again (none declared), then codex added
    # beside the cache the receipt already holds.
    assert "comms is already installed" in out
    assert fake.runs[-1][3:6] == ["moxi", "--with", "moxi-comms[cache,codex]"]
    assert "now on: comms[codex]: Delivers to Codex." in out
    assert "websockets" in fake.installed


def test_install_a_present_module_re_runs_its_setup(fake, capsys):
    moxi_install(capsys, "turns", "--none")
    fake.setups.clear()
    runs = len(fake.runs)
    code, out, _ = moxi_install(capsys, "turns", "--none")
    assert code == 0
    assert fake.setups == ["turns"] and len(fake.runs) == runs


def test_install_all_and_none(fake, capsys):
    code, out, _ = moxi_install(capsys, "comms", "--all")
    assert code == 0
    assert fake.runs[-1][3:6] == ["moxi", "--with", "moxi-comms[cache,codex]"]
    assert "now on: comms[cache]: Holds a cold send." in out


def test_install_all_names_the_extras_in_sorted_order(fake, capsys, monkeypatch):
    # The module declares codex before cache; the requirement is the same either way.
    spec = dict(CATALOG["moxi-comms"],
                OPTIONAL="{'codex': 'Delivers to Codex.', 'cache': 'Holds a cold send.'}")
    monkeypatch.setitem(CATALOG, "moxi-comms", spec)
    code, _, err = moxi_install(capsys, "comms", "--all")
    assert code == 0, err
    assert fake.runs[-1][3:6] == ["moxi", "--with", "moxi-comms[cache,codex]"]


def test_install_asks_at_a_terminal(fake, capsys, monkeypatch):
    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr(sys, "stdin", Tty("y\nn\n"))
    code, out, _ = moxi_install(capsys, "comms")
    assert code == 0
    assert "comms [cache]: Holds a cold send.\nInstall cache? [y/N] " in out
    assert "Install codex? [y/N] " in out
    assert fake.runs[-1][3:6] == ["moxi", "--with", "moxi-comms[cache]"]


def test_install_without_answers_ahead(fake, capsys):
    code, out, _ = moxi_install(capsys, "comms", "--all", "--without", "codex")
    assert code == 0
    assert fake.runs[-1][3:6] == ["moxi", "--with", "moxi-comms[cache]"]


def test_install_refuses_an_extra_the_module_does_not_declare(fake, capsys):
    code, _, err = moxi_install(capsys, "comms", "--with", "nope")
    assert code == 1 and "comms declares no extra nope (its extras: cache, codex)" in err


def test_install_refuses_with_and_without_the_same_extra(fake, capsys):
    code, _, err = moxi_install(capsys, "comms", "--with", "cache", "--without", "cache")
    assert code == 1 and "both name cache" in err and fake.runs == []


def test_install_refuses_a_bad_name_or_a_verb(fake, capsys):
    assert "not a module name" in moxi_install(capsys, "Comms")[2]
    assert "moxi's own commands" in moxi_install(capsys, "config")[2]
    assert "moxi's own commands" in moxi_install(capsys, "check")[2]
    assert "moxi's own commands" in moxi_install(capsys, "doctor")[2]
    assert "a name moxi keeps for itself" in moxi_install(capsys, "installs")[2]


def test_install_refuses_test_saying_why(fake, capsys):
    code, _, err = moxi_install(capsys, "test")
    assert code == 1 and fake.runs == []
    assert ("'test' is a name moxi keeps for itself, not a module: its variables would "
            "start MOXI_TEST_, which moxi-dev's test tiers use") in err


def test_install_from_puts_every_spec_into_the_same_run(fake, capsys):
    code, _, err = moxi_install(capsys, "comms", "--none",
                                "--from", "moxi-comms @ git+https://h/comms@main",
                                "--from", "moxi-turns @ git+https://h/turns@main")
    assert code == 0, err
    assert fake.runs == [["/fake/uv", "tool", "install", "moxi",
                          "--with", "moxi-comms @ git+https://h/comms@main",
                          "--with", "moxi-turns @ git+https://h/turns@main",
                          "--find-links", "file:///w"]]
    # The next run keeps both sources from the receipt.
    moxi_install(capsys, "comms", "--with", "cache",
                 "--from", "moxi-cache @ git+https://h/cache@main")
    assert fake.runs[-1][3:10] == [
        "moxi", "--with", "moxi-comms[cache] @ git+https://h/comms@main",
        "--with", "moxi-turns @ git+https://h/turns@main",
        "--with", "moxi-cache @ git+https://h/cache@main"]


def test_install_failure_sets_nothing_up_and_names_the_closure(fake, capsys):
    fake.fail_run = True
    code, _, err = moxi_install(capsys, "comms",
                                "--from", "moxi-comms @ git+https://h/comms")
    assert code == 1
    assert "the install failed (exit 1); nothing was set up" in err
    assert "one --from for each Moxi distribution the module needs" in err
    assert fake.setups == []


def test_install_a_failed_setup_keeps_the_install(fake, capsys):
    fake.fail_setup.add("turns")
    code, out, err = moxi_install(capsys, "comms", "--none")
    assert code == 1
    assert "setup turns: failed (exit 1). The install stays" in err
    assert "installed comms" in out


def test_install_a_distribution_that_is_no_module(fake, capsys):
    CATALOG["moxi-nomod"] = {}
    try:
        code, _, err = moxi_install(capsys, "nomod", "--third-party")
    finally:
        del CATALOG["moxi-nomod"]
    assert code == 1 and "no installed distribution declares the Moxi module" in err


def test_install_in_a_plain_environment(site, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    fake = Fake(site, tmp_path, monkeypatch, kind=install.PLAIN)
    code, _, err = moxi_install(capsys, "comms", "--with", "cache")
    assert code == 0, err
    assert fake.runs[0] == ["/fake/uv", "pip", "install", "--python", sys.executable,
                            "moxi-comms"]
    assert fake.runs[1][-1] == "moxi-comms[cache]"


def test_install_in_a_plain_environment_keeps_a_direct_source(site, tmp_path, monkeypatch,
                                                             capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    fake = Fake(site, tmp_path, monkeypatch, kind=install.PLAIN)
    fake.installed |= {"moxi-comms", "moxi-turns"}
    spec = CATALOG["moxi-comms"]
    site.module("comms", requires=spec["requires"], extras=spec["extras"],
                OPTIONAL=spec["OPTIONAL"],
                direct_url={"url": "https://h/comms",
                            "vcs_info": {"vcs": "git", "commit_id": "abc",
                                         "requested_revision": "main"}})
    site.module("turns")
    code, _, err = moxi_install(capsys, "cache")
    assert code == 0, err
    assert fake.runs[0][-1] == "moxi-comms[cache] @ git+https://h/comms@main"


def test_install_snapshot_and_run_in_a_fresh_process(tmp_path, monkeypatch):
    """The real `snapshot` and `run`, against this interpreter."""
    info = install.snapshot(sys.executable)
    assert info["python"] == sys.executable
    assert install.run(["/nonexistent/uv"]) == 127


def test_install_passes_replace_to_each_settle_step(fake, capsys):
    code, _, err = moxi_install(capsys, "comms", "--replace")
    assert code == 0, err
    assert fake.setups == ["turns", "comms"] and fake.replaces == [True, True]


@pytest.mark.parametrize("flag, answer", [("--skills", True), ("--no-skills", False),
                                          (None, None)])
def test_install_passes_the_skills_answer_to_each_settle_step(fake, capsys, flag, answer):
    code, _, err = moxi_install(capsys, "comms", *([flag] if flag else []))
    assert code == 0, err
    assert fake.setups == ["turns", "comms"] and fake.skills == [answer, answer]


def test_skills_and_no_skills_together_are_refused(fake, capsys):
    code, _, err = moxi_install(capsys, "comms", "--skills", "--no-skills")
    assert code == 2 and "not allowed with argument --skills" in err
    assert fake.runs == []


def test_skills_form_sets_the_core_setting_before_any_settle_step(fake, capsys, monkeypatch):
    from moxi import config

    seen = []
    real = fake.run_settle

    def settle(python, module, **kwargs):
        seen.append(config.load("moxi", config._core_schema())["skills.form"])
        return real(python, module, **kwargs)
    monkeypatch.setattr(install, "run_settle", settle)
    code, out, err = moxi_install(capsys, "comms", "--skills-form", "copy")
    assert code == 0, err
    assert "skills.form set to copy" in out
    assert seen == ["copy", "copy"]
    assert config.history("moxi", "skills.form")[-1]["by"] == "moxi install"
    code, _, err = moxi_install(capsys, "comms", "--skills-form", "hardlink")
    assert code == 2 and "invalid choice" in err


def test_an_interrupted_settle_step_ends_the_install(fake, capsys):
    fake.interrupt.add("turns")
    code, out, err = moxi_install(capsys, "comms", "--none")
    assert code == 1
    assert fake.setups == ["turns"]            # comms' settle step never ran
    assert "interrupted in turns's settle step; no later step ran" in err
    assert "installed comms" not in out        # nor the report


def test_a_second_install_waits_for_the_first(fake, capsys):
    import subprocess
    import textwrap

    import moxi.paths

    lock = moxi.paths.core_lock()
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        from moxi import files
        with files.lock({str(lock)!r}):
            print("held", flush=True)
            time.sleep(1.5)
        """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        code, _, err = moxi_install(capsys, "turns", "--none")
    finally:
        holder.wait(10)
    assert code == 0, err
    assert f"waiting for another install or uninstall (pid {holder.pid}) to finish" in err


def test_plain_environment_without_uv_uses_pip(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    assert install._plain_command([Req("moxi-comms", ["cache"])]) == [
        sys.executable, "-m", "pip", "install", "moxi-comms[cache]"]
    with pytest.raises(InstallError, match="editable"):
        install._plain_command([Req("moxi-x", editable="/src/x")])


# -- the catalogue gate ----------------------------------------------------------------------

def test_an_unlisted_name_is_refused_with_the_nearest_names(fake, capsys):
    code, out, err = moxi_install(capsys, "comm")
    assert code == 3
    assert ("moxi install: 'comm' is not a first-party Moxi module (`moxi modules "
            "--available` lists them). did you mean comms? To install a third-party "
            "module, give --third-party to take moxi-comm from the index, or --from "
            "with its source.") in err
    assert fake.runs == []


def test_an_unlisted_name_far_from_every_listed_one_suggests_nothing(fake, capsys):
    code, _, err = moxi_install(capsys, "zzzz")
    assert code == 3 and "did you mean" not in err


def test_third_party_installs_moxi_name_from_the_index_and_says_so(fake, capsys):
    CATALOG["moxi-extra"] = dict(module="extra")
    try:
        code, out, err = moxi_install(capsys, "extra", "--third-party")
    finally:
        del CATALOG["moxi-extra"]
    assert code == 0, err
    assert ("extra is not in Moxi's catalogue: installing moxi-extra from the index as a "
            "third-party module, not a first-party one.") in out
    assert fake.runs[0][3:5] == ["moxi", "--with"] and fake.runs[0][5] == "moxi-extra"


def test_a_from_for_the_module_s_own_distribution_passes_the_gate(fake, capsys):
    CATALOG["moxi-extra"] = dict(module="extra")
    try:
        code, _, err = moxi_install(capsys, "extra", "--from",
                                    "moxi-extra @ git+https://h/extra")
    finally:
        del CATALOG["moxi-extra"]
    assert code == 0, err


def test_a_from_for_another_distribution_does_not_pass_the_gate(fake, capsys):
    code, _, err = moxi_install(capsys, "extra", "--from",
                                "moxi-turns @ git+https://h/turns")
    assert code == 3 and "not a first-party Moxi module" in err


def test_an_unlisted_module_already_installed_runs_its_setup_again(fake, capsys):
    fake.site.module("extra")
    code, out, err = moxi_install(capsys, "extra")
    assert code == 0, err
    assert "extra is already installed" in out


def test_a_listed_name_installs_the_catalogue_s_distribution(fake, capsys, monkeypatch):
    import moxi.catalogue as catalogue

    seen = []
    real = catalogue.load

    def load(env=None):
        found = real(env)
        seen.append(found.get("turns"))
        return found

    monkeypatch.setattr(catalogue, "load", load)
    code, _, err = moxi_install(capsys, "turns", "--none")
    assert code == 0, err
    assert seen[0].distribution == "moxi-turns"
    assert fake.runs[0][5] == "moxi-turns"

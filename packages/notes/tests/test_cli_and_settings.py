"""`moxi notes dir` and `handoffs dir`, `handoffs_dir`, notes' settings, and no user path
in the package's code."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from moxi.config import ConfigError
from moxi_notes import api, settings

from notes_kit import configure, git_env, home, moxi_cmd, projects_store, repo

SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


# --- moxi notes dir ---------------------------------------------------------------------

def test_dir_prints_the_folder(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    code, out, _ = moxi_cmd("notes", "dir", str(alpha))
    assert (code, out.strip()) == (0, str(home() / "notes" / "code" / "alpha"))
    monkeypatch.chdir(alpha)
    assert moxi_cmd("notes", "dir")[1].strip() == str(home() / "notes" / "code" / "alpha")
    assert moxi_cmd("notes", "dir", ".")[1].strip() == out.strip()


def test_dir_exits_1_with_no_folder_and_prints_json():
    code, out, err = moxi_cmd("notes", "dir", str(home()))
    assert (code, out) == (1, "")
    assert err.strip().startswith("no notes directory: notes computes one only for a folder "
                                  "below your home folder")
    code, out, _ = moxi_cmd("notes", "dir", str(home()), "--json")
    assert code == 1 and json.loads(out) == {"dir": None, "rule": "none", "warning": None}


def test_dir_says_why_an_excluded_folder_has_none():
    configure(exclude=[str(home() / "private")])
    code, out, err = moxi_cmd("notes", "dir", str(home() / "private" / "x"))
    assert (code, out) == (1, "")
    assert "exclude setting" in err


def test_dir_takes_the_override_from_its_own_environment(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    monkeypatch.setenv("MOXI_NOTES_DIR", "~/elsewhere")
    code, out, _ = moxi_cmd("notes", "dir", str(alpha), "--json")
    assert code == 0 and json.loads(out)["dir"] == str(home() / "elsewhere")
    assert json.loads(out)["rule"] == "override"


def test_dir_prints_the_projects_warning_on_stderr():
    store = projects_store()
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(b"not a database" * 100)
    code, out, err = moxi_cmd("notes", "dir", str(repo(home() / "code" / "alpha")))
    assert code == 0 and out.strip() == str(home() / "notes" / "code" / "alpha")
    assert err.startswith("warning: projects could not be read")


def test_a_bad_setting_is_named_with_its_fix():
    import moxi.paths

    config = Path(moxi.paths.Layout("notes").config_file())
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('prefix = "../up"\n')
    code, _out, err = moxi_cmd("notes", "dir", str(home() / "code"))
    assert code == 2 and "prefix" in err and "climbs out" in err


# --- handoffs ---------------------------------------------------------------------------

def test_handoffs_dir_joins_handoffs_and_makes_it_only_when_asked():
    alpha = repo(home() / "code" / "alpha")
    folder = home() / "notes" / "code" / "alpha" / "handoffs"
    assert api.handoffs_dir(alpha) == folder and not folder.exists()
    assert api.handoffs_dir(alpha, create=True) == folder and folder.is_dir()
    assert api.handoffs_dir(home()) is None
    assert api.handoffs_dir(home(), create=True) is None


def test_handoffs_dir_takes_the_override():
    alpha = repo(home() / "code" / "alpha")
    told = home() / "notes" / "inbox" / "one-off"
    assert api.handoffs_dir(alpha, override=str(told)) == told / "handoffs"


def test_the_handoffs_verb(monkeypatch):
    alpha = repo(home() / "code" / "alpha")
    code, out, _ = moxi_cmd("notes", "handoffs", "dir", str(alpha))
    assert (code, out.strip()) == (0, str(home() / "notes" / "code" / "alpha" / "handoffs"))
    assert not Path(out.strip()).exists()
    code, out, err = moxi_cmd("notes", "handoffs", "dir", str(home()))
    assert code == 1 and out == "" and "no notes directory" in err
    monkeypatch.setenv("MOXI_NOTES_DIR", str(home() / "told"))
    assert moxi_cmd("notes", "handoffs", "dir", str(alpha))[1].strip() == str(
        home() / "told" / "handoffs")


def test_root_is_the_setting_expanded(tmp_path):
    assert api.root() == home() / "notes"
    configure(root="~/vault")
    assert api.root() == home() / "vault"


# --- settings ---------------------------------------------------------------------------

def test_the_defaults():
    places = settings.read()
    s = places.settings
    assert (places.root, places.base, places.exclude) == (home() / "notes",
                                                          home() / "notes", ())
    assert (s["action"], s["max_mb"], s["types"]) == ("leave-out", 10, "doc-types.toml")
    assert (s["snapshot.auto"], s["snapshot.interval"], s["snapshot.push"],
            s["snapshot.author_name"], s["snapshot.author_email"],
            s["snapshot.timer"]) == (True, 15, "", "moxi notes", "", False)


@pytest.mark.parametrize("text, key", [
    ('prefix = "/abs"', "prefix"),
    ('prefix = "a/../../b"', "prefix"),
    ('prefix = "C:\\\\x"', "prefix"),
    ('exclude = ["relative/path"]', "exclude"),
    ('exclude = [""]', "exclude"),
    ('action = "drop"', "action"),
])
def test_the_config_check_refuses(text, key):
    with pytest.raises(ConfigError) as caught:
        settings.check_text(text)
    assert caught.value.key == key


def test_the_config_check_takes_good_values_and_set_runs_it():
    settings.check_text('prefix = "team/sub"\nexclude = ["~/personal", "/srv"]\n')
    configure(prefix="team/sub")
    assert settings.read().base == home() / "notes" / "team" / "sub"
    with pytest.raises(ConfigError):
        configure(prefix="..")


# --- no user path in code ---------------------------------------------------------------

def test_no_home_path_is_spelled_in_the_package():
    found = []
    for path in sorted(SRC.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".toml"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"/home\b|/Users\b", line):
                    found.append(f"{path.relative_to(SRC)}:{n}")
    assert found == []

"""The projects link: the remove refusal, read by projects through its own loader, and the
project key checked at the point of use. projects is reached only through the core."""

from __future__ import annotations

import io
import sqlite3
from contextlib import redirect_stderr, redirect_stdout

import pytest

import moxi.cli
import moxi.modules
from moxi_services import projects_link, store
from moxi_services.errors import Busy, Refused, Unreadable

from services_kit import add, adopted, written


class Project:
    """projects' `Project` as refuse_remove reads it: only its key."""

    def __init__(self, key: str):
        self.key = key


def moxi_cmd(*argv: str) -> tuple[int, str]:
    """Run `moxi <argv>` in process; its exit code and what it printed."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(out):
        code = moxi.cli.main(list(argv))
    return code, out.getvalue()


# --- refuse_remove ----------------------------------------------------------------------

def test_the_link_declares_interface_1():
    assert projects_link.INTERFACE == 1
    assert callable(projects_link.refuse_remove)


def test_no_store_holds_no_project(store_path):
    assert projects_link.refuse_remove(Project("portfolio")) is None
    assert not store_path.exists()


def test_a_store_with_no_row_of_the_project_holds_nothing():
    add(**written("web", project="other"))
    add(**written("mail"))
    assert projects_link.refuse_remove(Project("portfolio")) is None


def test_two_rows_are_named_with_the_command_for_the_first():
    add(**written("portfolio-web", project="portfolio"))
    add(**adopted("portfolio-mail", unit="mail.service", kind="daemon",
                  project="portfolio"))
    assert projects_link.refuse_remove(Project("portfolio")) == (
        "project portfolio has 2 services (portfolio-mail, portfolio-web); remove them "
        "first: moxi services remove portfolio-mail")


def test_one_row_and_a_row_being_removed_are_named_as_such():
    add(**written("web", project="Portfolio"))
    store.write(lambda conn: store.set_removing(conn, "web", by="test"))
    assert projects_link.refuse_remove(Project("portfolio")) == (
        "project portfolio has 1 service (web (remove not finished)); remove it first: "
        "moxi services remove web")


@pytest.mark.parametrize("make", ["too_new", "garbage", "busy"])
def test_a_read_that_fails_raises(make, store_path, monkeypatch, request):
    holder = None
    if make == "too_new":
        request.getfixturevalue("too_new")
    elif make == "garbage":
        store_path.parent.mkdir(parents=True)
        store_path.write_bytes(b"not a database" * 100)
    else:
        add(**written("web", project="portfolio"))
        monkeypatch.setattr(store, "TIMEOUT", 0.05)
        holder = sqlite3.connect(store_path, isolation_level=None)
        holder.execute("PRAGMA locking_mode = EXCLUSIVE")
        holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(Unreadable):
            projects_link.refuse_remove(Project("portfolio"))
    finally:
        if holder is not None:
            holder.execute("ROLLBACK")
            holder.close()


# --- through projects' own loader -------------------------------------------------------

def test_projects_refuses_to_remove_a_project_a_service_names(tmp_path):
    root = tmp_path / "portfolio"
    root.mkdir()
    code, said = moxi_cmd("projects", "add", "portfolio", "--root", str(root))
    assert code == 0, said
    add(**written("portfolio-web", project="portfolio"))
    code, said = moxi_cmd("projects", "remove", "portfolio")
    assert code == 3, said
    assert "project portfolio has 1 service (portfolio-web)" in said
    assert "moxi services remove portfolio-web" in said
    store.write(lambda conn: store.delete(conn, "portfolio-web"))
    code, said = moxi_cmd("projects", "remove", "portfolio")
    assert code == 0, said


def test_projects_refuses_the_remove_when_services_cannot_read_its_store(tmp_path,
                                                                         too_new):
    root = tmp_path / "portfolio"
    root.mkdir()
    assert moxi_cmd("projects", "add", "portfolio", "--root", str(root))[0] == 0
    code, said = moxi_cmd("projects", "remove", "portfolio")
    assert code == 1, said
    assert "services's refuse_remove raised TooNew" in said
    assert moxi_cmd("projects", "show", "portfolio")[0] == 0


def test_projects_lists_no_broken_contribution_from_services():
    code, said = moxi_cmd("projects", "kinds")
    assert code == 0, said
    assert "services" not in said


# --- lookup and known_keys --------------------------------------------------------------

def test_lookup_gives_the_key_as_projects_stores_it(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    assert moxi_cmd("projects", "add", "Portfolio", "--root", str(root))[0] == 0
    assert projects_link.lookup("portfolio") == "Portfolio"
    assert projects_link.known_keys() == frozenset({"portfolio"})


def test_lookup_of_an_unknown_key_is_refused():
    with pytest.raises(Refused) as caught:
        projects_link.lookup("nosuch")
    assert caught.value.exit_code == 3
    assert "nosuch" in caught.value.reason
    assert projects_link.known_keys() == frozenset()


def test_without_projects_the_flag_is_refused_naming_the_install(monkeypatch):
    real = moxi.modules.available
    monkeypatch.setattr(moxi.modules, "available",
                        lambda name: False if name == "projects" else real(name))
    with pytest.raises(Refused) as caught:
        projects_link.lookup("portfolio")
    assert caught.value.exit_code == 3
    assert "not installed" in caught.value.reason
    assert "moxi install" in caught.value.fix and "projects" in caught.value.fix
    assert projects_link.known_keys() is None


class _Fails(Exception):
    def __init__(self, code: int):
        self.exit_code = code
        self.reason = f"failed with {code}"
        self.fix = "a fix"
        super().__init__(self.reason)


class _Api:
    def __init__(self, code: int):
        self.code = code

    def get(self, key):
        raise _Fails(self.code)

    def list_projects(self):
        raise _Fails(self.code)


@pytest.mark.parametrize("code,error", [(75, Busy), (1, Unreadable), (3, Refused)])
def test_projects_errors_keep_their_exit_code(code, error, monkeypatch):
    monkeypatch.setattr(projects_link, "_projects", lambda: _Api(code))
    with pytest.raises(error) as caught:
        projects_link.lookup("portfolio")
    assert caught.value.exit_code == code
    with pytest.raises(error):
        projects_link.known_keys()


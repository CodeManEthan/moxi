"""The snapshot's prepare step, through real snapshot passes and called directly."""

from __future__ import annotations

from pathlib import Path

import pytest
from moxi_repo.api import Change, PrepareContext, Prepared

from moxi_notes import judge, prepare

from gate_kit import (allow_line, break_private_names, notes_repo, private_names, sh,
                      snapshot, token, tree, write)
from notes_kit import configure, git_env


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


@pytest.fixture
def root() -> Path:
    return notes_repo()


def left(result) -> dict[str, str]:
    return dict(result.left)


def test_a_file_with_a_hit_is_dropped_with_its_rule_and_line(root):
    write(root, "plan.md", "intro\nkey = " + token() + "\n")
    write(root, "clean.md", "nothing here\n")
    result = snapshot(root)
    assert result.status == "committed"
    assert left(result) == {"plan.md": "dropped: credentials/github-pat line 2"}
    assert token() not in "".join(left(result).values())
    assert "clean.md" in tree(root) and "plan.md" not in tree(root)


def test_a_private_name_is_a_hit(root):
    private_names("quillon")
    write(root, "people.md", "met Quillon today\n")
    result = snapshot(root)
    assert left(result) == {"people.md": "dropped: private-names/private-name line 1"}


def test_a_rename_is_dropped_whole_and_a_deletion_is_held(root):
    body = "".join(f"line {i}\n" for i in range(30))
    write(root, "old.md", body)
    write(root, "gone.md", "to be deleted\n")
    assert snapshot(root).status == "committed"
    (root / "old.md").rename(root / "new.md")
    with open(root / "new.md", "a", encoding="utf-8") as fh:
        fh.write("key = " + token() + "\n")
    (root / "gone.md").unlink()
    write(root, "other.md", "fine\n")
    result = snapshot(root)
    assert result.status == "committed"
    why = left(result)
    assert why["new.md"].startswith("dropped: credentials/github-pat")
    assert why["old.md"].startswith("dropped with its rename to new.md")
    assert why["gone.md"] == "held back: an added file was dropped"
    assert {"old.md", "gone.md", "other.md"} <= tree(root)
    assert "new.md" not in tree(root)


def test_an_allowed_hit_is_kept(root):
    write(root, ".secrets-allow", allow_line("plan.md", "credentials/github-pat"))
    write(root, "plan.md", "key = " + token() + "\n")
    result = snapshot(root)
    assert result.status == "committed" and left(result) == {}
    assert "plan.md" in tree(root)


def test_a_malformed_allowlist_leaves_out_only_the_files_with_hits(root):
    write(root, ".secrets-allow", allow_line("plan.md", "credentials/github-pat")
          + "not a line\n")
    write(root, "plan.md", "key = " + token() + "\n")
    write(root, "clean.md", "nothing here\n")
    result = snapshot(root)
    assert result.status == "committed"
    why = left(result)
    assert list(why) == ["plan.md"]
    assert ".secrets-allow: line 2:" in why["plan.md"]
    assert "judged with no allowances" in why["plan.md"]
    assert {"clean.md", ".secrets-allow"} <= tree(root)


def test_the_private_allowlist_is_read_in_the_common_git_dir(root):
    private_names("quillon")
    common = Path(sh(root, "rev-parse", "--path-format=absolute",
                     "--git-common-dir").stdout.strip())
    (common / judge.PRIVATE).write_text(
        allow_line("people.md", "private-names/private-name", "text:quillon"),
        encoding="utf-8")
    write(root, "people.md", "met Quillon today\n")
    result = snapshot(root)
    assert left(result) == {} and "people.md" in tree(root)


def test_a_set_that_cant_run_refuses_the_pass(root):
    break_private_names()
    write(root, "plan.md", "anything\n")
    result = snapshot(root)
    assert result.status == "refused"
    assert "a set notes judges with can't run" in result.detail
    assert "plan.md" not in tree(root)


# -- called directly ----------------------------------------------------------------------

def blob(root: Path, data: bytes) -> str:
    path = root / ".blob-input"
    path.write_bytes(data)
    oid = sh(root, "hash-object", "-w", ".blob-input").stdout.strip()
    path.unlink()
    return oid


class Recording(PrepareContext):
    """A prepare context that remembers each git call's arguments."""

    def git(self, *args, **kw):
        self.calls.append(args)
        return super().git(*args, **kw)


def context(root: Path, *changes: Change) -> Recording:
    ctx = Recording(root=root, changes=tuple(changes), spec="notes/snapshot")
    object.__setattr__(ctx, "calls", [])
    return ctx


def test_blob_ids_come_from_the_changes_and_no_path_goes_in_argv(root):
    hit = blob(root, ("key = " + token() + "\n").encode())
    clean = blob(root, b"fine\n")
    ctx = context(root, Change("A", "notes/plan.md", None, hit),
                  Change("M", "notes/clean.md", None, clean), Change("D", "notes/gone.md"))
    got = prepare.prepare(ctx)
    assert got == Prepared(drop=[("notes/plan.md", "credentials/github-pat line 1")])
    assert [c[0] for c in ctx.calls] == ["rev-parse", "cat-file", "cat-file"]
    assert not any("notes/" in arg for call in ctx.calls for arg in call)


def test_a_blob_over_max_mb_is_left_out_unread(root):
    configure(max_mib=1)
    big = blob(root, b"x" * (1024 * 1024 + 1))
    ctx = context(root, Change("A", "big.txt", None, big))
    got = prepare.prepare(ctx)
    assert [p for p, _ in got.drop] == ["big.txt"]
    assert got.drop[0][1].startswith("notes/too-large (1 MiB, over max_mib 1")
    assert sum(1 for c in ctx.calls if "--batch" in c) == 0


def test_a_blob_with_a_nul_byte_is_scanned_as_text(root):
    data = b"\0\x01binary" + ("key = " + token() + "\n").encode()
    ctx = context(root, Change("A", "data.md", None, blob(root, data)))
    assert prepare.prepare(ctx).drop == (("data.md", "credentials/github-pat line 1"),)


def test_a_git_call_that_fails_refuses(root):
    ctx = context(root, Change("A", "plan.md", None, "1" * 40))
    got = prepare.prepare(ctx)
    assert got.refuse and "could not be judged" in got.refuse

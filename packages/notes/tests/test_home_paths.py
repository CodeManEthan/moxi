"""`moxi notes home-paths`: home paths written as `~`, session paths as their ids, links
made relative; a dry run unless `--apply`, which changes only committed files."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from moxi_notes import home_paths as hp

from notes_kit import configure, git, git_env, home, moxi_cmd, repo

ID = "0123abcd-1111-2222-3333-444455556666"
ID2 = "aaaabbbb-1111-2222-3333-444455556666"


@pytest.fixture(autouse=True)
def _git(tmp_path, monkeypatch):
    git_env(tmp_path, monkeypatch)


def hh() -> str:
    """The home folder, H."""
    return str(home())


def ff() -> str:
    """The Claude Code project folder name for the home folder."""
    return "".join(c if c.isascii() and c.isalnum() else "-" for c in hh())


def login() -> str:
    return os.path.basename(hh())


def one(text: str, *, path: str = "a.md", home_dir: str | None = None) -> str:
    rules = hp.Rules(home_dir or hh())
    is_json = os.path.splitext(path)[1] in hp.JSON_SUFFIXES
    return hp.rewrite(text.encode(), rules, is_json=is_json).data.decode()


def root() -> Path:
    return home() / "notes"


def notes(files: dict[str, str | bytes], *, commit: bool = True) -> Path:
    """`~/notes` as a repo holding `files`, committed when `commit`."""
    path = repo(root())
    for name, body in files.items():
        full = path / name
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            full.write_bytes(body)
        else:
            full.write_bytes(body.encode())
    if commit:
        git("add", "-A", cwd=path)
        git("commit", "-q", "-m", "notes", cwd=path)
    return path


def run(*args: str) -> tuple[int, str, str]:
    return moxi_cmd("notes", "home-paths", *args)


def as_json(*args: str) -> tuple[int, dict]:
    code, out, err = run("--json", *args)
    return code, json.loads(out)


def left(found: dict) -> set[tuple[str, str]]:
    return {(x["path"], x["reason"]) for x in found["left"]}


# -- Rule HP -----------------------------------------------------------------------------

@pytest.mark.parametrize("before", [" ", "\t", '"', "'", "`", "(", "[", "<", "=", ",", ":",
                                    ">", "|"])
def test_a_token_start_before_the_home_path(before):
    text = f"x {before}{hh()}/a"
    # Rule HQ leaves a quote and a non-assignment = alone in a .md file; JSON takes them.
    expect = text if before in "\"'=" else f"x {before}~/a"
    assert one(text) == expect
    assert one(text, path="a.json") == f"x {before}~/a"


def test_the_start_of_a_line_starts_a_token():
    assert one(f"{hh()}/a\n{hh()}\r\n{hh()}") == "~/a\n~\r\n~"


@pytest.mark.parametrize("after,changed", [
    ("/x", True), ("", True), (".", True), (". next", True), (")", True), (":", True),
    (".bak", False), ("2", False), ("-old", False), ("_x", False), ("é", False),
])
def test_what_continues_a_name_after_the_home_path(after, changed):
    text = f"see {hh()}{after}"
    assert one(text) == (f"see ~{after}" if changed else text)


def test_a_url_a_longer_path_another_home_and_another_case_stay():
    text = (f"file://{hh()}/x /var{hh()}/x x{hh()}/x {hh()}2/x {hh()}-old/x "
            f"{hh().upper()}/x")
    assert one(text) == text


def test_fenced_inline_json_and_diff_text_are_rewritten_like_prose():
    text = f"```sh\nls {hh()}/a\n```\nrun `cat {hh()}/b`\n"
    assert one(text) == "```sh\nls ~/a\n```\nrun `cat ~/b`\n"
    assert one(f'{{"p": "{hh()}/x"}}', path="a.json") == '{"p": "~/x"}'
    assert one(f"+ see {hh()}/x\n- see {hh()}/y\n", path="a.diff") == "+ see ~/x\n- see ~/y\n"


# -- Rule HQ -----------------------------------------------------------------------------

def test_quoted_places_stay_in_a_md_file_and_the_file_is_listed_twice():
    body = (f'a "{hh()}/x"\nb \'{hh()}/x\'\nc --opt={hh()}/x\nd editable = "{hh()}/x"\n'
            f'e "NAME={hh()}/x"\nNAME={hh()}/x\nexport PATH={hh()}/bin\n')
    notes({"q.md": body, "d.json": f'{{"p": "{hh()}/x"}}\n'})
    code, found = as_json()
    assert code == 0
    changes = {f["path"]: f["changes"] for f in found["files"]}
    assert [c["after"] for c in changes["q.md"]] == ["NAME=~/x", "export PATH=~/bin"]
    assert changes["d.json"][0]["after"] == '{"p": "~/x"}'
    assert found["counts"]["quoted_left"] == 5
    assert {("q.md", "quoted"), ("q.md", "login")} <= left(found)
    assert ("d.json", "quoted") not in left(found)


def test_an_assignment_after_a_backtick_is_still_an_assignment():
    assert one(f"`NAME={hh()}/x`") == "`NAME=~/x`"
    assert one(f"x.NAME={hh()}/x") == f"x.NAME={hh()}/x"


# -- Rule E ------------------------------------------------------------------------------

def test_bytes_outside_the_changes_stay_byte_equal(tmp_path):
    latin = "café".encode("latin-1")
    body = (b"line one\r\n" + latin + b" " + hh().encode() + b"/x\r\n" + b"tail "
            + hh().encode())
    notes({"crlf.md": body})
    code, out, err = run("--apply")
    assert code == 0, out + err
    assert (root() / "crlf.md").read_bytes() == (b"line one\r\n" + latin + b" ~/x\r\n"
                                                 + b"tail ~")


# -- Rules L and B -----------------------------------------------------------------------

def test_a_link_inside_the_root_is_made_relative_and_one_outside_is_listed(tmp_path):
    path = notes({"target/a.md": "a\n"}, commit=False)
    (path / "in.md").symlink_to(str(path / "target" / "a.md"))
    (path / "sub").mkdir()
    (path / "sub" / "deep.md").symlink_to(str(path / "target" / "a.md"))
    (home() / "outside.md").write_text("o\n")
    (path / "out.md").symlink_to(str(home() / "outside.md"))
    git("add", "-A", cwd=path)
    git("commit", "-q", "-m", "links", cwd=path)
    code, found = as_json()
    links = {f["path"]: f["changes"][0] for f in found["files"]}
    assert links["in.md"]["kind"] == "link" and links["in.md"]["after"] == "target/a.md"
    assert links["sub/deep.md"]["after"] == "../target/a.md"
    assert ("out.md", "link-outside") in left(found)
    code, out, err = run("--apply")
    assert code == 0, out + err
    assert os.readlink(path / "in.md") == "target/a.md"
    assert os.readlink(path / "sub" / "deep.md") == "../target/a.md"
    assert (path / "sub" / "deep.md").read_text() == "a\n"
    assert os.readlink(path / "out.md") == str(home() / "outside.md")


def test_a_binary_file_and_one_over_max_mib_are_listed_not_read():
    configure(max_mib=1)
    notes({"deck.pptx": b"PK\0\0" + hh().encode() + b"/x",
           "big.md": (hh() + "/x\n").encode() * (1024 * 1024 // len(hh()) + 10),
           "small.md": f"{hh()}/x\n"})
    code, found = as_json()
    assert {("deck.pptx", "binary"), ("big.md", "too-large")} <= left(found)
    assert [f["path"] for f in found["files"]] == ["small.md"]


# -- Rule SI -----------------------------------------------------------------------------

def rows() -> list[tuple[str, str]]:
    f = ff()
    return [
        (f"{hh()}/.claude/projects/{f}-core-voice/{ID}.jsonl", ID),
        (f"~/.claude/projects/{f}-moxi/{ID}/subagents/agent-a1.jsonl",
         f"{ID}/subagents/agent-a1.jsonl"),
        (f"~/.claude/projects/{f}-x/{ID}/tool-results/t.txt", f"{ID}/tool-results/t.txt"),
        (f"/tmp/claude-1000/{f}-moxi/{ID}/scratchpad/probe.py", f"{ID}/scratchpad/probe.py"),
        (f"/tmp/claude-1000/{f}-core/{ID}/tasks/t1.output", f"{ID}/tasks/t1.output"),
        (f"{f}-tmp-probe/{ID}.jsonl", ID),
    ]


@pytest.mark.parametrize("n", range(6))
def test_each_row_of_the_session_table(n):
    written, becomes = rows()[n]
    assert one(f"see {written} here") == f"see {becomes} here"


def test_the_root_spelled_three_ways_and_quoted():
    f = ff()
    for root_text in ("~/", f"{hh()}/"):
        assert one(f"{root_text}.claude/projects/{f}-x/{ID}.jsonl") == ID
    assert one(f'"{hh()}/.claude/projects/{f}-x/{ID}.jsonl"') == f'"{ID}"'
    assert one(f".claude/projects/{f}-x/{ID}.jsonl") == ID


@pytest.mark.parametrize("tail,kept", [(".", "."), ("`", "`"), (":42", ":42"),
                                       (".bak", ".jsonl.bak")])
def test_jsonl_after_the_id(tail, kept):
    text = f"see `{ff()}-x/{ID}.jsonl{tail}"
    assert one(text) == f"see `{ID}{kept}"


def test_a_project_name_with_a_double_dash_is_taken():
    assert one(f"{ff()}--local-state-x/{ID}") == ID


@pytest.mark.parametrize("text", [
    "{f}-x/ alone",
    "~/.claude/projects/{f}-x/*.jsonl",
    "~/.claude/projects/{f}-x/<uuid>.jsonl",
    "~/.claude/projects/{f}-x/0123abcd.jsonl",
    "~/.claude/projects/{f}-x/0123abcd-….jsonl",
    "{f}-x\t" + ID,
    "~/.cache/claude-cli-nodejs/{f}-x/" + ID,
    "file:///tmp/claude-1000/{f}-x/" + ID,
    "x/.claude/projects/{f}-a/" + ID,
    "{f}2-x/" + ID,
])
def test_what_rule_si_leaves(text):
    text = text.format(f=ff())
    assert one(text) == text


def test_what_rule_si_leaves_is_listed_login():
    notes({"a.tsv": f"{ff()}-x\t{ID}\n", "b.md": f"x/.claude/projects/{ff()}-a/{ID}\n",
           "c.md": f"{ff()}2-x/{ID}\n"})
    code, found = as_json()
    assert {("a.tsv", "login"), ("b.md", "login"), ("c.md", "login")} <= left(found)


def test_the_glued_two_path_case_and_a_second_run():
    f = ff()
    text = (f"/tmp/claude-1000/{f}-a/{ID}/scratchpad/../../.claude/projects/{f}-b/{ID2}")
    first = one(text)
    assert first == f"{ID}/scratchpad/../../.claude/projects/{f}-b/{ID2}"
    assert one(first) == first


def test_the_project_name_follows_home():
    assert one(f"-home-a-b-x/{ID}", home_dir="/home/a.b") == ID
    assert one(f"~/.claude/projects/-Users-x-y/{ID}.jsonl", home_dir="/Users/x") == ID
    assert one(f"-home-a-b-x/{ID}", home_dir="/home/a_c") == f"-home-a-b-x/{ID}"


def test_a_windows_session_path_is_taken_by_windows_rules():
    home_dir = "C:\\Users\\ann"
    f = "C--Users-ann"
    rules = hp.Rules(home_dir, windows=True)
    for written in (f"{home_dir}\\.claude\\projects\\{f}-x\\{ID}.jsonl",
                    f"{home_dir}/.claude/projects/{f}-x/{ID}.jsonl",
                    f"~\\.claude\\projects\\{f}-x\\{ID}.jsonl",
                    f"{f}-x\\{ID}"):
        assert hp.rewrite(f"see {written} here".encode(), rules,
                          is_json=False).data.decode() == f"see {ID} here"
    posix = hp.Rules(home_dir, windows=False)
    text = f"{f}-x\\{ID}"
    assert hp.rewrite(text.encode(), posix, is_json=False).data.decode() == text


def test_a_home_spelled_whole_is_a_root():
    assert one(f"/home/First Last/.claude/projects/-home-First-Last-x/{ID}.jsonl",
               home_dir="/home/First Last") == ID


def test_si_runs_before_hp():
    out = one(f"{hh()}/.claude/projects/{ff()}-x/{ID}.jsonl")
    assert out == ID
    assert "~/.claude/projects/" not in out


def test_the_session_kind_and_the_sessions_count():
    notes({"a.md": f"{rows()[0][0]}\n{rows()[3][0]}\n{ff()}-y/{ID2}.jsonl\n{hh()}/z\n"})
    code, found = as_json()
    kinds = [c["kind"] for c in found["files"][0]["changes"]]
    assert kinds == ["session", "session", "session", "home"]
    assert found["counts"]["sessions"] == 2
    assert found["counts"]["by_kind"] == {"home": 1, "session": 3, "link": 0}


# -- Rules U and C -----------------------------------------------------------------------

def test_an_untracked_file_refuses_the_run_and_changes_nothing():
    notes({"a.md": f"{hh()}/a\n"})
    (root() / "new.md").write_text(f"{hh()}/new\n")
    code, found = as_json("--apply")
    assert code == 3
    assert ("new.md", "untracked") in left(found) and found["error"]
    assert (root() / "a.md").read_text() == f"{hh()}/a\n"
    assert (root() / "new.md").read_text() == f"{hh()}/new\n"


def test_a_modified_file_refuses_the_run_and_changes_nothing():
    notes({"a.md": f"{hh()}/a\n", "b.md": f"{hh()}/b\n"})
    (root() / "b.md").write_text(f"{hh()}/b changed\n")
    code, out, err = run("--apply")
    assert code == 3
    assert "modified: b.md" in out
    assert (root() / "a.md").read_text() == f"{hh()}/a\n"
    git("add", "b.md", cwd=root())
    code, found = as_json("--apply")
    assert code == 3 and ("b.md", "modified") in left(found)


def test_a_file_changed_between_read_and_write_is_skipped(monkeypatch):
    notes({"a.md": f"{hh()}/a\n", "b.md": f"{hh()}/b\n"})
    real = hp.refuse

    def then_someone_writes(found):
        refused = real(found)
        (root() / "b.md").write_text(f"{hh()}/b by someone else\n")
        return refused

    monkeypatch.setattr(hp, "refuse", then_someone_writes)
    code, out, err = run("--apply")
    assert code == 1
    assert "changed: b.md" in out and "commit it or drop it, then run again" in out
    assert (root() / "a.md").read_text() == "~/a\n"
    assert (root() / "b.md").read_text() == f"{hh()}/b by someone else\n"
    monkeypatch.setattr(hp, "refuse", real)
    git("commit", "-q", "-m", "the rewrite of a", "a.md", cwd=root())
    code, found = as_json("--apply")
    assert code == 3 and ("b.md", "modified") in left(found)
    git("commit", "-q", "-am", "someone's change", cwd=root())
    code, out, err = run("--apply")
    assert code == 0, out + err
    assert (root() / "b.md").read_text() == "~/b by someone else\n"


def test_a_read_only_file_is_left_alone():
    notes({"a.md": f"{hh()}/a\n"})
    (root() / "a.md").chmod(0o444)
    code, found = as_json("--apply")
    assert code == 1 and ("a.md", "read-only") in left(found)
    assert (root() / "a.md").read_text() == f"{hh()}/a\n"


def test_apply_keeps_the_mode_and_prints_the_commit_and_commits_nothing():
    notes({"a.md": f"{hh()}/a\n"})
    (root() / "a.md").chmod(0o640)
    head = git("rev-parse", "HEAD", cwd=root())
    code, out, err = run("--apply")
    assert code == 0, out + err
    if os.name == "posix":
        assert (root() / "a.md").stat().st_mode & 0o777 == 0o640
    assert out.rstrip().splitlines()[-1] == (
        'Commit the rewrite: git -C ~/notes commit -am "notes: home paths written as ~, '
        'session paths as ids"')
    assert git("rev-parse", "HEAD", cwd=root()) == head


# -- idempotence ---------------------------------------------------------------------------

def test_a_second_apply_finds_nothing():
    f = ff()
    notes({"a.md": (f"/tmp/claude-1000/{f}-a/{ID}/scratchpad/../../.claude/projects/"
                    f"{f}-b/{ID2}\n{hh()}~/.claude/projects/{f}-c/{ID}.jsonl\n"
                    f"{hh()}/x `{hh()}` \"{hh()}/q\"\n"),
           "b.json": f'{{"p": "{hh()}/x", "s": "{hh()}/.claude/projects/{f}-x/{ID}.jsonl"}}\n'})
    code, out, err = run("--apply")
    assert code == 0, out + err
    git("commit", "-q", "-am", "rewrite", cwd=root())
    code, found = as_json("--apply")
    assert code == 0
    assert found["files"] == [] and found["counts"]["changes"] == 0


# -- the dry run and its output ----------------------------------------------------------

def test_the_dry_run_writes_nothing():
    notes({"a.md": f"{hh()}/a\n"})
    before = git("status", "--porcelain", cwd=root())
    code, out, err = run()
    assert code == 0
    assert (root() / "a.md").read_text() == f"{hh()}/a\n"
    assert git("status", "--porcelain", cwd=root()) == before
    assert "a dry run; nothing was written" in out
    assert "1:" in out and "-> ~/a" in out


def test_the_json_shape_kinds_classes_and_reasons():
    f = ff()
    notes({"code.md": f"text\n```\ncd {hh()}/x\n```\n",
           "span.md": f"run `ls {hh()}/x` now\n",
           "data.tsv": f"{hh()}/x\tb\n", "data.csv": f"{hh()}/x,b\n",
           "p.patch": f"+see {hh()}/x\n", "q.md": f"> said {hh()}/x\n",
           "plain.md": f"see {hh()}/x and {f}-x/{ID}\n"})
    code, found = as_json()
    assert set(found) == {"root", "home", "files", "left", "counts", "error"}
    assert found["home"] == hh() and found["error"] is None
    classes = {x["path"]: x["class"] for x in found["files"]}
    assert classes == {"code.md": "code", "span.md": "code", "data.tsv": "data",
                       "data.csv": "data", "p.patch": "diff", "q.md": "quote",
                       "plain.md": "prose"}
    change = found["files"][0]["changes"][0]
    assert set(change) == {"line", "kind", "before", "after"}
    assert set(found["counts"]) == {"files", "changes", "by_class", "by_kind", "sessions",
                                    "quoted_left"}
    assert found["counts"]["by_class"] == {"code": 2, "data": 2, "diff": 1, "quote": 1,
                                           "prose": 1}
    lines = {x["path"]: x["changes"][0]["line"] for x in found["files"]}
    assert lines["code.md"] == 3


def test_the_limit_caps_the_files_shown():
    notes({f"n{i}.md": f"{hh()}/x\n" for i in range(5)})
    code, out, err = run("--limit", "2")
    assert out.count("prose: n") == 2
    assert "...and 3 more files" in out


def test_a_root_that_is_no_repo_is_an_error():
    root().mkdir(parents=True)
    code, out, err = run()
    assert code == 1 and "no git repo's top" in err


def test_the_help_states_the_json_shape(capsys):
    code, out, err = run("--help")
    assert code == 0 and '"kind": "home" | "session" | "link"' in out

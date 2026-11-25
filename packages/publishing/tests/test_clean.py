"""`moxi publishing clean-history`: a private repo whose history stays publishable."""

from __future__ import annotations

from conftest import commit, git, last_message, token, write_config

from moxi_publishing import clean, cli, doctor, flag

RULES = "repo/commit-rules"


def local_config(repo) -> list[str]:
    return sorted(git(repo, "config", "--local", "--list").stdout.splitlines())


def rules_of(repo) -> dict | None:
    return flag.piece_settings(str(repo), RULES)


def test_enable_then_disable_leaves_the_git_config_as_it_was(repo, names, capsys):
    before = local_config(repo)
    assert cli.main(["clean-history", "enable", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "keeps a clean history" in out
    assert f"{RULES} refuses Round, Reconciles" in out
    assert flag.is_clean(str(repo)) and not flag.is_public(str(repo))
    assert set(flag.registered(str(repo))) == {flag.MESSAGE, flag.CONTENT}
    assert rules_of(repo)["refused-trailer"] == ["Round", "Reconciles"]
    assert cli.main(["clean-history", "disable", str(repo)]) == 0
    assert "no longer keeps a clean history" in capsys.readouterr().out
    assert local_config(repo) == before
    assert rules_of(repo) is None


def test_a_commit_rules_piece_the_user_had_is_added_to_and_given_back(repo, names):
    from moxi_repo import api

    api.add(repo, RULES, sets=["refused-trailer=Fixes", "refused-trailer=round",
                               "rule=One subject line."])
    before = local_config(repo)
    clean.enable(repo)
    # round is already refused, ignoring case: only Reconciles is added, nothing replaced.
    assert rules_of(repo)["refused-trailer"] == ["Fixes", "round", "Reconciles"]
    assert rules_of(repo)["rule"] == ["One subject line."]
    lines = clean.disable(repo)
    assert not any(line.startswith("kept") for line in lines)
    assert local_config(repo) == before


def test_disable_keeps_commit_rules_it_registered_once_something_else_is_set(repo, names):
    from moxi_repo import api

    clean.enable(repo)
    api.add(repo, RULES, sets=["rule=Write the subject in the present tense."])
    lines = clean.disable(repo)
    assert f"kept {RULES}: rule is set there beyond what clean history wrote" in lines
    assert rules_of(repo) == {"rule": ["Write the subject in the present tense."]}
    assert git(repo, "config", "--get-regexp", r"^moxi-publishing\.",
               check=False).stdout == ""


def test_enable_twice_adds_nothing_more(repo, names):
    clean.enable(repo)
    lines = clean.enable(repo)
    assert not any("refuses" in line for line in lines)
    assert rules_of(repo)["refused-trailer"] == ["Round", "Reconciles"]
    assert flag.config_get_all(str(repo), clean.CLEAN_TRAILERS) == ["Round", "Reconciles"]


def test_a_private_clean_repo_drops_claude_session_and_refuses_private_text(repo, names):
    clean.enable(repo)
    done = commit(repo, "Subject\n\nBody.\n\nClaude-Session: https://example.test/s/1\n")
    assert done.returncode == 0, done.stderr
    assert "Claude-Session" not in last_message(repo)
    assert "keeps a clean history and drops it" in done.stderr
    done = commit(repo, "Ask bobsecret about it\n")
    assert done.returncode != 0
    assert "these lines of the message can't be published" in done.stderr
    git(repo, "reset", "-q", "--hard")
    done = commit(repo, "Add a note", text="ask bobsecret\n")
    assert done.returncode != 0
    assert "what this commit adds can't be published" in done.stderr
    git(repo, "reset", "-q", "--hard")
    done = commit(repo, "Add a token", text=f"key = {token()}\n")
    assert done.returncode != 0


def test_a_clean_repo_refuses_the_round_trailer(repo, names):
    clean.enable(repo)
    done = commit(repo, "Subject\n\nRound: one\n")
    assert done.returncode != 0
    assert "Round" in done.stderr


def test_refused_trailers_comes_from_publishings_config(repo, names):
    write_config("publishing", '[defaults]\nrefused_trailers = ["Ticket"]\n')
    clean.enable(repo)
    assert rules_of(repo)["refused-trailer"] == ["Ticket"]


def test_with_no_refused_trailers_commit_rules_is_left_out(repo, names):
    write_config("publishing", '[defaults]\nrefused_trailers = []\n')
    clean.enable(repo)
    assert rules_of(repo) is None
    clean.disable(repo)
    assert git(repo, "config", "--get-regexp", r"^moxi-publishing\.",
               check=False).stdout == ""


def test_disable_refuses_in_a_public_repo(repo, names, capsys):
    flag.enable(repo)
    assert cli.main(["clean-history", "disable", str(repo)]) == 3
    err = capsys.readouterr().err
    assert "a public repo keeps clean history; run moxi publishing disable" in err
    assert set(flag.registered(str(repo))) == set(flag.PIECES)


def test_the_doctor_reads_a_clean_repo_as_ok(repo, names):
    clean.enable(repo)
    found = [(row.state, row.detail, row.fix) for row in doctor.rows()
             if row.name == f"repo {repo}"]
    assert found == [("ok", "keeps a clean history; the message and content pieces are "
                            "enabled", None)]


def test_the_doctor_names_a_missing_piece_of_a_clean_repo(repo, names):
    from moxi_repo import api

    clean.enable(repo)
    api.remove(repo, flag.CONTENT)
    [(state, detail, fix)] = [(row.state, row.detail, row.fix) for row in doctor.rows()
                              if row.name == f"repo {repo}"]
    assert state == "fail" and f"missing: {flag.CONTENT}" in detail
    assert fix == f"moxi publishing clean-history enable {repo}"

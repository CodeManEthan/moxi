"""Declarations, the context a piece is given, and `--set` planning, without git."""

from __future__ import annotations

import os

import pytest

from moxi_repo import pieces
from moxi_repo.pieces import DeclarationError, HookContext, PushUpdate

GOOD = {"event": "pre-commit", "target": "pkg.mod:fn", "fail": "closed"}


def refused(entry: str, value) -> str:
    with pytest.raises(DeclarationError) as caught:
        pieces.check(entry, value)
    return caught.value.reason


def test_a_good_declaration_and_its_defaults():
    piece = pieces.check("demo.secret-gate", GOOD)
    assert (piece.id, piece.module, piece.name) == ("demo/secret-gate", "demo", "secret-gate")
    assert (piece.order, piece.doc, piece.settings) == (50, "", {})
    assert piece.registration() == {"event": "pre-commit", "target": "pkg.mod:fn",
                                    "fail": "closed", "order": "50"}
    full = dict(GOOD, order=0, doc="One line.", settings={"tag-pattern": "multi",
                                                          "x2": "one"})
    assert pieces.check("demo.gate", full).order == 0
    assert pieces.check("demo.gate", dict(GOOD, order=99)).order == 99


@pytest.mark.parametrize("entry", ["demo", "Demo.gate", "demo.Gate", "demo.gate_x",
                                   "demo.-gate", "demo.gate-", "de-mo.gate", ".gate", "demo."])
def test_bad_entry_names(entry):
    assert "is not <module>.<name>" in refused(entry, GOOD)


@pytest.mark.parametrize("value, says", [
    (["event"], "the declaration is a list, not a table"),
    (dict(GOOD, when="now"), "unknown key 'when'"),
    (dict(GOOD, event="pre-receive"), "event 'pre-receive' is not a client-side git event"),
    ({k: v for k, v in GOOD.items() if k != "event"}, "event None"),
    (dict(GOOD, target="pkg.mod.fn"), "is not package.module:function"),
    (dict(GOOD, target="pkg.mod:fn()"), "is not package.module:function"),
    (dict(GOOD, target=3), "is not package.module:function"),
    (dict(GOOD, fail="soft"), "fail 'soft' is not"),
    ({k: v for k, v in GOOD.items() if k != "fail"}, "fail None is not"),
    (dict(GOOD, order=-1), "order -1 is not a whole number from 0 to 99"),
    (dict(GOOD, order=100), "order 100 is not"),
    (dict(GOOD, order=True), "order True is not"),
    (dict(GOOD, order="5"), "order '5' is not"),
    (dict(GOOD, order=5.0), "order 5.0 is not"),
    (dict(GOOD, doc="two\nlines"), "doc is not one line"),
    (dict(GOOD, settings=["a"]), "settings is not a table"),
    (dict(GOOD, settings={"a": "many"}), "setting 'a': 'many' is not"),
    (dict(GOOD, settings={"tag_pattern": "one"}), "setting 'tag_pattern': a setting's name"),
    (dict(GOOD, settings={"9lives": "one"}), "setting '9lives'"),
    (dict(GOOD, settings={"order": "one"}), "is a key of the registration itself"),
    (dict(GOOD, settings={"recorded": "one"}), "is a key of the registration itself"),
    (dict(GOOD, settings={"enabled": "one"}), "is a key of the registration itself"),
    (dict(GOOD, settings={"tagPattern": "one"}), "setting 'tagPattern': a setting's name"),
])
def test_bad_declarations_name_the_key(value, says):
    assert says in refused("demo.gate", value)


def test_message_path_is_the_message_events_absolute_first_argument():
    ctx = HookContext("commit-msg", (".git/COMMIT_EDITMSG",))
    assert ctx.message_path == os.path.abspath(".git/COMMIT_EDITMSG")
    assert HookContext("prepare-commit-msg", ("/m", "message")).message_path == "/m"
    assert HookContext("commit-msg").message_path is None
    assert HookContext("pre-commit", ("x",)).message_path is None
    assert HookContext("post-merge", ("0",)).message_path is None


def test_updates_are_pre_pushs_ref_lines():
    a, z = "a" * 40, "0" * 40
    stdin = (f"refs/heads/main {a} refs/heads/main {z}\n"
             f"(delete) {z} refs/heads/old {a}\n"
             "a short line\n").encode()
    ctx = HookContext("pre-push", ("origin", "/r.git"), stdin)
    assert ctx.updates == (PushUpdate("refs/heads/main", a, "refs/heads/main", z),
                           PushUpdate("(delete)", z, "refs/heads/old", a))
    assert HookContext("pre-push").updates == ()
    assert HookContext("post-rewrite", (), stdin).updates == ()


def test_outcomes():
    assert pieces.Outcome.ok() == pieces.Outcome("ok")
    refuse = pieces.Outcome.refuse("no", "because")
    assert (refuse.kind, refuse.lines) == ("refuse", ("no", "because"))
    assert pieces.Outcome.notice(3).lines == ("3",)
    assert pieces.Outcome.record("r") != pieces.Outcome.notice("r")


def _piece(entry: str, settings: dict) -> pieces.Piece:
    return pieces.check(entry, dict(GOOD, settings=settings))


def test_plan_sets_edges():
    gate = _piece("demo.gate", {"refused-name": "multi", "label": "one"})
    other = _piece("demo.other", {"label": "one"})
    assert pieces.plan_sets([gate], ["LABEL=x", "refused-name=a", "Refused-Name=b"]) == {
        "demo/gate": {"label": ["x"], "refused-name": ["a", "b"]}}
    assert pieces.plan_sets([gate], ["label="]) == {"demo/gate": {"label": None}}
    assert pieces.plan_sets([gate], ["label=a=b"]) == {"demo/gate": {"label": ["a=b"]}}
    assert pieces.plan_sets([gate], []) == {}
    for sets, says in [
        (["label"], "write it KEY=VALUE"),
        (["=x"], "write it KEY=VALUE"),
        (["colour=red"], "demo/gate declares no setting named colour"),
        (["label=a", "label=b"], "takes one value for label, not 2"),
        (["refused-name=", "refused-name=a"], "goes alone"),
    ]:
        with pytest.raises(pieces.SetError, match=says):
            pieces.plan_sets([gate], sets)
    with pytest.raises(pieces.SetError, match="both demo/gate and demo/other declare it"):
        pieces.plan_sets([gate, other], ["label=x"])


class _Entry:
    def __init__(self, name: str, value):
        self.name, self.value = name, value

    def load(self):
        return self.value


@pytest.mark.parametrize("copies", [2, 3, 4])
def test_an_id_two_or_more_distributions_declare_is_refused(monkeypatch, copies):
    entries = [_Entry("demo.gate", GOOD) for _ in range(copies)] + [_Entry("demo.lint", GOOD)]
    monkeypatch.setattr(pieces, "extensions", lambda: entries)
    good, bad = pieces.declared()
    assert set(good) == {"demo/lint"}
    assert bad == {"demo/gate": "two distributions declare demo/gate"}


def test_an_id_declared_twice_is_refused_even_when_one_declaration_is_bad(monkeypatch):
    entries = [_Entry("demo.gate", dict(GOOD, fail="soft")), _Entry("demo.gate", GOOD)]
    monkeypatch.setattr(pieces, "extensions", lambda: entries)
    good, bad = pieces.declared()
    assert good == {} and bad == {"demo/gate": "two distributions declare demo/gate"}

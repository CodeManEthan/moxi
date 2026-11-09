"""`moxi statusline`'s verbs."""

import io
import json
import sys

import pytest
from statusline_kit import write_config

from moxi import paths
from moxi_statusline import switch

from moxi_statusline.cli import VERBS, main


def test_preview_prints_the_line_and_its_time(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_config('show = ["model", "effort", "cost"]\n')
    assert main(["preview"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Sample │ high │ $1.24"
    assert lines[1].startswith("rendered in ") and lines[1].endswith(" ms")


def test_preview_show_replaces_the_configs_items(capsys):
    write_config('show = ["model"]\n')
    assert main(["preview", "--show", "effort,row,context", "--no-color"]) == 0
    assert capsys.readouterr().out.splitlines()[:2] == ["high", "ctx:42%"]


@pytest.mark.parametrize("show", ["row,dir", "dir,row,model,row,cost", "Bad-Name"])
def test_preview_refuses_a_show_the_config_would_refuse(show, capsys):
    assert main(["preview", "--show", show]) == 2
    assert capsys.readouterr().err.startswith("moxi statusline: --show ")


def test_each_verb_has_a_parser_and_a_runner():
    assert [v.name for v in VERBS] == ["use", "restore", "status", "preview"]
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2


def _settings(data):
    path = paths.claude_home() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_use_then_restore_then_restore_refused(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    path = _settings({"theme": "dark"})
    assert main(["use"]) == 0
    assert capsys.readouterr().out.startswith("Claude Code now uses Moxi's status line.")
    assert main(["restore"]) == 0
    assert json.loads(path.read_text()) == {"theme": "dark"}
    assert main(["restore"]) == 3
    assert "no longer Moxi's (it has none)" in capsys.readouterr().err
    assert main(["restore", "--force"]) == 0


def test_status_text_and_json(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        '{"statusLine": {"type": "command", "command": "./mine.sh"}}')
    write_config('show = ["dir", "nosuchmodule"]\n')
    _settings({"statusLine": {"type": "command", "command": "/x/line.sh"}})
    assert main(["status"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Claude Code's status line: your own"
    assert out[1] == "  command: /x/line.sh"
    assert "Saved line: none kept" in out
    assert "Shows: dir, nosuchmodule" in out
    assert "Shows nothing: nosuchmodule (not installed)" in out
    assert out[-1] == (f"This folder's {tmp_path / '.claude' / 'settings.json'} sets its "
                       "own status line (./mine.sh), which Claude Code uses here")
    assert switch.switch(None, by="test").ok
    assert main(["status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["moxis"] and data["here"]
    assert data["saved"]["statusLine"] == {"type": "command", "command": "/x/line.sh"}
    assert data["show"] == ["dir", "nosuchmodule"]
    assert data["nothing_behind"] == ["nosuchmodule (not installed)"]
    assert data["project"][0]["command"] == "./mine.sh"
    assert main(["status"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Claude Code's status line: Moxi's, this environment's render"
    assert out[3].startswith("Saved line: /x/line.sh (kept ")

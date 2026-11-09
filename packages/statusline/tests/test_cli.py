"""`moxi statusline`'s verbs."""

import pytest
from statusline_kit import write_config

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
    assert [v.name for v in VERBS] == ["preview"]
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2

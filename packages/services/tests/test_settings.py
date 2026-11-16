"""services' settings schema with its page fields, as `moxi config services show --json`
prints it."""

from __future__ import annotations

import json

from moxi import cli
from moxi.config import Schema


def test_the_schema_passes_the_cores_check_with_its_page_fields():
    Schema.from_resource("moxi_services", "settings.toml")


def test_show_json_gives_the_switch_table_a_label_and_a_line(capsys):
    capsys.readouterr()
    assert cli.main(["config", "services", "show", "--json"]) == 0
    (key,) = json.loads(capsys.readouterr().out)
    assert (key["name"], key["label"]) == ("adopted", "Adopted timers")
    assert key["line"] and key["common"] is False and key["hurts"] is None

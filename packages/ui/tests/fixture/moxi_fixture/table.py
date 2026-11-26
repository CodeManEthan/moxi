"""A table of rows the fixture keeps in a file of its own, `defaults.json` in its config
folder, for the Settings page's tests of a module's table. The fixture names it in
`CONFIG_TABLES` only when a test installs it so (`fixture_kit.install(..., table=True)`).

Two rows: `mode`, a pick of `fast` or `slow`, and `count`, an int. Any other key the file
holds can only be cleared. A `mode` the list doesn't offer reads with a warning. The file
reads as unreadable while it holds the text `broken`.
"""

from __future__ import annotations

import json

from moxi.config import ConfigError
from moxi.paths import Layout

TITLE = "Fixture defaults"
LINE = "Every read starts from these."
APPLIES = "Applies to reads made after this."
#: What `mode` says while the file holds a value its list doesn't offer.
OFF_LIST = "This mode is not offered; reads take slow."
#: What a key no row has says.
NO_ROW = "is not a fixture row; clear it."


class _Table:
    title = TITLE
    line = LINE
    applies = APPLIES

    def file(self, env):
        return Layout("fixture").config_dir(env) / "defaults.json"

    def _read(self, env):
        path = self.file(env)
        if not path.exists():
            return {}
        text = path.read_text()
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ConfigError(path, None, "is not valid JSON", "correct the file")
        return data

    def keys(self, env):
        data = self._read(env)
        mode = data.get("mode", "slow")
        out = [{"name": "mode", "type": "enum", "values": ["fast", "slow"], "default": "slow",
                "label": "Mode", "doc": "How a read runs.", "line": "How a read runs.",
                "group": "reads", "value": mode, "source": "file" if "mode" in data else "default",
                "warning": OFF_LIST if mode not in ("fast", "slow") else None},
               {"name": "count", "type": "int", "default": 1, "label": "Count",
                "doc": "How many reads.", "line": "How many reads.", "group": "reads",
                "value": data.get("count", 1),
                "source": "file" if "count" in data else "default"}]
        held = [{"name": name, "type": "int" if isinstance(value, int) else "string",
                 "label": name, "doc": "", "group": "needs a fix", "value": value,
                 "source": "file", "clear_only": True, "refused": f"{name} {NO_ROW}"}
                for name, value in data.items() if name not in ("mode", "count")]
        return held + out

    def change(self, row, op, value, *, by, env):
        data = self._read(env)
        path = self.file(env)
        if op == "unset":
            if row not in data:
                return False, []
            del data[row]
        elif row == "mode":
            if value not in ("fast", "slow"):
                raise ConfigError(path, row, f"cannot be {value!r}", "give fast or slow")
            data[row] = value
        elif row == "count":
            try:
                data[row] = int(value)
            except ValueError:
                raise ConfigError(path, row, f"cannot be {value!r}", "give a whole number") from None
        else:
            raise ConfigError(path, row, NO_ROW, "clear it")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return True, []


TABLE = _Table()

"""peer's page server: no route, only its component's script in `web/`."""

from moxi import modules

_ui = modules.load("ui", interface=3)
if not _ui.ok:
    raise ImportError(f"peer's page needs the ui module: {_ui.reason}")

PAGE = _ui.api.loopback.Page(routes=(), assets="web")

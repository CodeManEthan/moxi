# moxi-ui

The browser shell: one origin for every module's page. It runs one process, the shell,
which listens on `127.0.0.1` and `[::1]` on one port (8460 by default), signs the browser
in with a one-use ticket from `moxi ui open`, and serves each module's page under
`/m/<module>/`.

## Testing a module's pages

`moxi_ui.testing` is a pytest plugin, installed with moxi-ui and loaded in every pytest run
beside it. Its `ui_*` fixtures start a real shell over the installed modules
(`ui_shell`), open signed-in browser contexts (`ui_page`), and run the contract check
(`ui_component_check`) and the 360 px width check (`ui_width_check`) on a module's own
components. The browser fixtures need the `testing` extra and a browser:
`pip install 'moxi-ui[testing]'`, then `playwright install chromium`. The plugin's
docstring lists every fixture.

A page module whose UI is optional:

- declares an extra `ui = ["moxi-ui>=V,<W"]` and an `OPTIONAL` sentence for it;
- reaches loopback in `page.py` and its handlers only through
  `moxi.modules.load("loopback", interface=1)`, and raises `ImportError` when that fails;
- imports neither from its announcement, API, CLI or console scripts;
- gets the contract check in its suite through the plugin's fixtures, by name, and never
  imports `moxi_ui`.

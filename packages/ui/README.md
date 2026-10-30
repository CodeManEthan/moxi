# moxi-ui

The browser shell: one origin for every module's page. It runs one process, the shell,
which listens on `127.0.0.1` and `[::1]` on one port (8460 by default), signs the browser
in with a one-use ticket from `moxi ui open`, and serves each module's page under
`/m/<module>/`.

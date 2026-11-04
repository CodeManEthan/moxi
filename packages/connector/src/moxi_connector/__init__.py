"""Moxi's connector: the public MCP door for the Claude apps.

- `moxi_connector.provider`: what a module gives to serve its tools (the one part of the
  connector a provider imports);
- `moxi_connector.origin`: the public origin and every value derived from it;
- `moxi_connector.app`: the door's ASGI app, built whole from one origin;
- `moxi_connector.daemon`: `moxi-connector-server`, the daemon behind the `enabled` switch;
- `moxi_connector.control`: the daemon's control socket;
- `moxi_connector.calllog`: the call log, a file per UTC day;
- `moxi_connector.cli` and `moxi_connector.doctor`: `moxi connector` and the doctor's rows.
"""

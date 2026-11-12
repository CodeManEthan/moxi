"""The announcement of peer: one row on Home, ordered above the launcher's Sessions row,
that says `quiet` and needs nothing."""

INTERFACES = frozenset({1})
SUMMARY = "A module the launcher's browser tests install"
PAGE = "moxi_peer.page:PAGE"
COMPONENTS = {
    "row": {"script": "row.js", "title": "Peer", "doc": "A row for Home that needs nothing",
            "contract": 1, "place": "block", "home": True, "home_as": "row", "order": 50},
}

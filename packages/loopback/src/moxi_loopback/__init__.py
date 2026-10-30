"""moxi-loopback: the base every Moxi page server is built on.

It holds the request check, the runtime file, the route table and the page-server base.
The threat model: module pages are trusted code like their Python half; Moxi defends
against the network and other users on the machine, not a module you chose to install.

This file imports nothing, so a process started without `python -P` can refuse before it
loads anything outside the standard library.
"""

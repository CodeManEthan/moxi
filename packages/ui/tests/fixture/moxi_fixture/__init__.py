"""fixture: the page module the ui module's tests install as a real distribution.

The tests copy this package into a site directory of their own, write its `.dist-info`
beside it and put the directory on the shell's `PYTHONPATH`, so `moxi modules --json`,
the export command and the page server, which all run as children, find it.
"""

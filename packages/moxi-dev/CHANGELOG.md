# Changelog

## 0.1.1 (2026-11-08)

The first public release.

- The README explains the tag check in plain words: what it refuses, what
  `.residue-allow` is, and what a finding, its `finding` mark and `--finding` are.
- `moxi-dev new` writes a core requirement of `moxi>=0.1.6,<0.2`, the oldest core a
  module has been run against from wheels.
- The contract check's names rule and `moxi-dev new` refuse `test` and say why each kept
  name is refused, reading `moxi.names.reserved_why`.
- The contract check gains check 9: a module reached by `moxi.modules.load` or
  `available` with a literal or constant name must come with the package, a required
  dependency, an extra, or a module an extra requires.
- The test fence answers the unit plan's listener connect with "nothing listens" in
  every test, so what listens on the machine's ports never decides a test. A test marked
  `honest_ports` (a new marker), or `live`, makes the real connect.
- moxi-dev needs moxi 0.1.6 or later, and its dependency now says so.

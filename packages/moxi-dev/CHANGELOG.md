# Changelog

## 0.1.1 (2026-11-08)

- `moxi-dev rename` can apply a real table: it is fast; reads tests, conftest and
  scripts; resolves submodule imports, lazy `__getattr__` re-export tables and imports
  inside functions; renames dataclass field keywords; makes `kwonly` rows work with an
  existing star; rewrites `__all__` only in its list and backticked cites only where
  they mean the renamed definition; refuses to write a file that would not parse. A
  `use` row's old text may be longer than the name and changes every match on its line;
  a `keep` row also guards a cite on its line; a keyword on a later line of a call is
  covered by a row on that line; a keep row on a line a use row changed stays applied.
  A relative import in a package's `__init__` resolves from the package; a parameter row
  leaves `self.<name>` alone and reads its state from the signature; a parameter's cite
  left unrewritten is listed only in its own file.
- `moxi-dev rename` refuses, before writing, a row whose edit would change what a token
  means (its new name already in the scope, a nested scope with its own old name, a
  module-level name that is also a function local), naming the table line and the code
  line. A row may rename a function's local out of a parameter's way.
- `moxi-dev check`'s naming check names the house suffix in each unit hit, reads a mark
  on a decorated function's def line, and reports an API module that does not import;
  the refs check refuses a repeated `.refs-allow` line.
- `renames.tsv` holds this release's renames.

The first public release.

- `moxi-dev check` now runs whole-tree comment-reference and public-name steps, reports
  `refs` and `names` in its summary, and can run either step alone with `--only`.
- Contract check 7 holds daemon and timer console scripts to their announcement keys.
- `moxi-dev rename` applies the tab-separated identifier rename table, preserves
  resolved imports and calls, and reports unresolved or moved old uses.
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
- `moxi-dev check` runs a release step over every package: the catalogue lists exactly
  the module packages, every requirement between packages admits the other's version,
  and each package has `readme`, a licence, `requires-python`, keywords, classifiers,
  `[project.urls]`, a CHANGELOG whose first entry is its version and which its sdist
  includes, and a README with no relative link. moxi-dev now depends on `packaging`,
  which reads the requirements.
- `moxi-dev new` writes `readme`, keywords, Linux-only classifiers and a `CHANGELOG.md`,
  so a new package passes the release step once the catalogue lists it.

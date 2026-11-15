# Changelog

## Unreleased

- Every setting carries page fields, which `moxi config secrets show --json` prints
  and the settings page draws: a plain label and a line, and on the three lists of
  private names a can-hurt mark on taking one away. secrets needs moxi 0.1.9 or later.
- `Allowlist.from_text(public_text, private)`: the public allowlist from text a caller
  read (from git's index or a commit), the private file read as `Allowlist.read` reads
  it.
- The private allowlist's place is checked only when the file exists, and `read` and
  `from_text` take `git_dir`, a repo's common git dir, as a place for it whatever that
  directory is named.

## 0.1.0 (2026-11-08)

The first public release.

- Four named pattern sets: `credentials` (gitleaks v8.30.1's default rules, pinned, plus
  a local list), `broad`, `private-names` (your own list, in secrets' config file) and
  `personal-data` (card numbers, IBANs and US SSNs, checked by python-stdnum's validators,
  in the `personal-data` extra).
- `scan`, `redact`, `scan_lines`, `is_secret_path`, `private_names` and `describe`, and a
  tab-separated allowlist read from a public and a private file.
- Two engines: RE2 through google-re2 (the `re2` extra), and a fallback on Python's `re`
  that runs the same rules and reports text it can't finish as unscanned.
- `moxi doctor secrets` rows for the rules, the engine, the private names, where they
  live, and the personal-data extra.
- Linux; macOS and Windows are untested.
- Requires moxi 0.1.6 or later.
- The README installs from PyPI, and its links work on PyPI.

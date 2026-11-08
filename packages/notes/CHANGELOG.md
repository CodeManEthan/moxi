# Changelog

## 0.1.1 (2026-11-08)

The first public release.

- `moxi notes dir` says why a folder has no notes directory: an excluded folder, a
  computed folder inside the topics or inbox folder, or a folder outside your home.
- The package metadata gains the README as its long description, keywords and
  classifiers.
- The README installs from PyPI and says Linux is supported and macOS and Windows
  untested. The README's links work on PyPI.
- `moxi doctor notes` no longer fails a missing notes root before notes' setup has run
  in this home. It warns that setup hasn't run and names `moxi install notes`. A root
  that goes missing after setup still fails.
- notes needs moxi 0.1.6 or later.

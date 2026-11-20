# moxi-secrets

Moxi's secrets module holds named pattern sets and one way to look for them in text. A
caller hands it a text, or the added lines of a change, and names the sets to look for. It
gets back hits, or the text with its hits redacted. What to do about a hit is the caller's
call. secrets never refuses, drops a file or exits for a caller, and it has no command,
daemon, timer or store of its own.

It doesn't spawn git or read a repository, and it doesn't resolve keys. Finding a stored
credential is a different job.

## The sets

A caller always names the sets it wants. There is no default set.

| set | what it finds | where its patterns come from |
|---|---|---|
| `credentials` | keys and tokens of known shapes | gitleaks' default rules, pinned to one tag, minus its generic rule, plus a local list |
| `broad` | unknown secrets, by context or by how random they look | gitleaks' generic rule and two local rules |
| `private-names` | what identifies you: names, logins, private package and organisation names, emails | your own list, in secrets' config file |
| `personal-data` | payment card numbers, IBANs, US SSNs | candidate patterns checked by python-stdnum's validators |

- **credentials** follows gitleaks: a rule runs only when one of its keywords is in the
  text, its secret must pass the rule's entropy floor, and global and per-rule allowlists
  drop allowed findings. In `scan`, a line holding `gitleaks:allow` keeps its hits out of
  credentials and broad, never out of private names or personal data, and so does a line
  that a rule's line allowlist names (broad's generic rule has two: a
  `--mount=type=secret,` or JavaScript import line, and a BitBake recipe's license and
  source lines). `redact` honours neither: text beside a secret never keeps it out of a
  redacted copy, since whoever wrote that text may not be the one who reads the copy.
  The local list adds rules for
  Bitwarden access tokens, Google OAuth tokens and client secrets, bare Discord and
  Telegram bot tokens, URL credentials, private key headers, ntfy tokens and
  `Authorization: Bearer` values. gitleaks' decoding passes (base64, hex, percent-encoding)
  aren't run in-process.
- **broad** finds more and pays for it in false positives. Pick it where a false positive
  is cheap.
- **private-names** matches plain text, never a regex. A `words` entry hits unless a letter
  or digit stands right next to one of its letter-or-digit ends, so `alexa` hits
  `alexa-quill` and `Alexa's`, not `alexa2000`. Case is ignored: each letter matches the
  letters Python's `re` treats as its case variants on Unicode 16.0, from a table shipped
  with the package, so RE2 and the fallback match an entry at the same places on every
  Python. On a Python with a newer Unicode, a letter that version first gives a case pair
  matches only itself until a moxi-secrets release regenerates the table. A space in an entry
  matches any run of whitespace, `-` and `_`, and `-` and `_` match each other. Whether an
  entry matches never depends on the other entries. An `anywhere` entry or an email hits
  even glued to other text. An `allowed` entry, and the author name and email, are never
  hits, and neither is a match inside one of them.
- **personal-data** needs the `personal-data` extra. Without it, asking for the set raises
  `SetUnavailable` naming the install line. A card is a bare run of 15 or 16 digits, or 13
  to 19 digits written in groups, with no digit next to it. It must pass Luhn and start
  with an issuer prefix. About 3% of random bare 15- and 16-digit runs pass, and no other
  bare length is ever a candidate, so timestamps and 17-19 digit ids stay quiet.

## Use

```python
from moxi_secrets import scan, redact, scan_lines, describe

hits = scan(text, ("credentials", "private-names"), path="docs/setup.md")
clean = redact(text, ("credentials", "private-names"))
found = scan_lines(added_lines, ("credentials",))   # (path, line, text) triples
```

- `scan(text, sets, *, path=None, allow=None, known=())` returns a list of `Hit`, sorted
  by start. A `Hit` has `set`, `rule`, `description`, `start`, `end` (the secret's span in
  the text), `line` and `fingerprint` (`sha256:` and the first 16 hex digits of the
  secret's hash). It holds no secret text, and its repr prints only the set, rule, line and
  fingerprint.
- `path`, when given, is repo-relative with `/`. It turns on the path parts of the rules:
  allowlisted paths skip the text, and a path-only rule (such as `pkcs12-file`) hits on
  the path alone, spanning the whole text.
- `known` holds literal values the caller already holds, such as the token it is using.
  They are found first, as set `known`.
- Both read past invisible characters. A token split by a zero-width space, a soft
  hyphen or a like character is a hit, spanning the characters inside it. The characters
  read past are the ones a renderer may show as nothing: C0 controls other than tab and
  newline, DEL and C1 controls; format characters (category `Cf`, as the running
  Python's `unicodedata` reads it), such as zero-width spaces and joiners, bidi controls
  and the byte order mark; and every code point Unicode 17.0 names default-ignorable,
  assigned or not, such as variation selectors and Hangul fillers. Tab, newline and the
  Unicode spaces aren't among them. A text holding any is scanned twice: as given, and
  as a copy without them, whose hits are mapped back. A text that also holds one a rule
  may read as whitespace (a vertical tab, form feed, carriage return with no newline
  after it, U+001C to U+001F or NEL) is scanned a third time, as a copy with a space for
  each of those and without the rest, so the boundary one gave a token survives. `allow`
  and `known` judge a split value as they judge it whole.
- `redact(text, sets, ...)` replaces each hit with `[REDACTED:<rule id>]`, a split token
  with the characters inside it; a split token at the very start or end of the text
  takes in the invisible characters before or after it. Its result scans clean with the
  same arguments, under `scan` and under `redact`'s own rules, and redacting it again
  changes nothing. Text outside hits comes back character for character,
  `surrogateescape` bytes and invisible characters included.
- `REDACT_VERSION` says which `redact` this is (2). It rises with a release in which
  `redact`, with its default keywords, redacts in some text a span the release before
  returned whole because of how it reads text: a suppression it stops applying,
  characters it newly reads past. A change of rule data alone (a rule, regex, keyword,
  allowlist or stopword, a gitleaks bump among them) leaves it as it is. A store of
  redacted text can record it: moxi memory does, and its doctor asks for a `moxi memory
  reindex` once the version rises. Text stored earlier meets a rise, or a new rule, only
  when someone runs that reindex.
- `seen_through(c)` says whether `scan` and `redact` read past the character `c`: the
  invisible characters above. A store that trims stored text by counting characters can
  count past the same ones.
- `scan_lines(triples, sets, *, allow=None)` takes the added lines of a whole change as
  `(path, line number, text)`, scans each path's lines as one text, and returns
  `LineHit(path, line, hit)` values. It joins a path's lines with `\n` whatever their
  numbers, so a pattern that can span lines, such as a private name with a space, can
  match across lines 3 and 40 when no line between them was added.
- `is_secret_path(path)` says a file must not be read at all: `.env*`, `auth.json`,
  `*_history`, anything under `.ssh/`, `*.pem`, `credentials`, `.netrc`, `.pgpass`,
  `*.p12`, `*.pfx`, `*.jks`, `*.keystore`. It matches names and paths case-insensitively,
  with `\` read as `/`, and never opens the file.
- `private_names()` returns your list, frozen, for a local caller such as a publishing
  step that needs the author name.
- `describe()` returns each set's rule ids, source, pin, dropped rules and whether it can
  run here.
- `Hit`, `LineHit`, `SetInfo` and `PrivateNames` are plain frozen classes, not
  dataclasses: `dataclasses.replace` and `asdict` don't work on them, and a `match`
  statement names their fields by keyword (`case Hit(rule="github-pat")`), not by
  position.

Any `str` a caller made by decoding bytes with UTF-8 and `surrogateescape` scans, redacts
and fingerprints without raising. A private-names scan that can't read the list raises
`SetUnavailable` and never returns zero hits for a list it didn't read.

## The engine

With the `re2` extra, every pattern runs on RE2 through `google-re2`, the gitleaks
patterns verbatim, in time linear in the text. google-re2 ships wheels for CPython 3.12 to
3.14 on Linux with glibc 2.28 or later (x86_64, aarch64), macOS 13 and later, and
Windows. `moxi install` offers the extra only where a wheel fits, since anywhere else an
install would try to build the C++ library.

Without it, a fallback runs the same rules on Python's `re`, each pattern translated by
the generator. A translation folds case exactly as Go does, written out letter by letter
rather than left to Python's wider case folding, so it matches the same text on every
Python version. A rule that ignores case on a character outside ASCII isn't translated and
is left out on both engines. Credentials and broad run in a worker child process with a deadline of 1 s
plus 2 s per MB. A scan that reaches it, or whose worker fails in any way, reports the
text as `unscanned`: one hit per set, spanning the whole text, which a caller treats like
any other hit, never as clean. Large or unusual texts can come back unscanned on the
fallback. Private names and personal data run in the calling process on either engine.

## Settings

The private names list lives in secrets' config file, read at each scan that asks for it,
so an edit takes effect at the next scan.

| key | what it holds |
|---|---|
| `private_names.words` | names that identify you: your names, logins, agent names, private package and organisation names |
| `private_names.anywhere` | names that identify you even glued to other text, such as an organisation prefix |
| `private_names.emails` | email addresses that identify you |
| `private_names.allowed` | text that holds a private name but is public on purpose, such as a public handle |
| `author.name`, `author.email` | the identity your public commits are authored as; never a hit |

```sh
moxi config set secrets private_names.words --add NAME
moxi config set secrets private_names.words --remove NAME
moxi config secrets path
```

The config file lives in the config root, outside every repo unless you put it in one. The
doctor warns when it is inside a git work tree. secrets writes a name nowhere else, and no
error, doctor row or log line it writes carries one: each names the key and the entry's
position.

## The allowlist

A hit someone has judged and kept is written as one tab-separated line:

```text
# path <TAB> set/rule <TAB> match <TAB> YYYY-MM-DD <TAB> why
docs/setup.md	credentials/jwt	sha256:3f9c2a1b7d4e5f60	2026-11-02	the sample token in the guide
tests/	credentials/*	*	2026-11-02	fixtures
@author	private-names/private-email	text:someone@example.com	2026-11-02	the release author
```

- `path` is `*`, a repo-relative path, a directory ending in `/`, or `@author` (commit
  author and committer headers).
- `set/rule` is `<set>/<rule id>`, `<set>/*` or `*`.
- `match` is `*`, `sha256:` with exactly 16 hex digits (the hit's fingerprint), or
  `text:<text>`. A `text:` value and a hit's text are both normalised the same way before
  they are compared: `str.casefold()`, then every run of whitespace, `-` and `_` that holds
  a whitespace character becomes one space, `_` becomes `-`, and whitespace, `-` and `_`
  are trimmed from both ends. So `text:Alexa-Quill` keeps the hits `alexa_quill` and
  `ALEXA-QUILL`, not `alexa quill`.

There are two files, read with `Allowlist.read(public, private)`:

- `.secrets-allow` at a repo's root may be public. It takes credentials lines only, each
  about a value that is already in the public text it allows.
- The private file takes lines of every set, and `text:` lines for private names. It must
  lie inside a directory named `.git` (a repo's own is `<git common dir>/secrets-allow-private`)
  or in secrets' config directory, checked after every link is resolved, so git never
  tracks it. A caller judging a repo passes its common git dir as `git_dir`, which counts
  whatever it is named (a bare clone's, say). A private file that doesn't exist adds no
  line, wherever it would be.

`Allowlist.from_text(public_text, private)` takes the public file's content as text
instead, for a caller that judges a snapshot rather than the work tree: a git hook reads
`.secrets-allow` from the index, a push check from the commit it judges.

A malformed line raises `AllowlistError` naming the file and the line number, never the
line's text. A line that allows a whole set everywhere is refused: its path and match are
`*` and its set/rule is `*` or `<set>/*`.

## Doctor

`moxi doctor secrets` shows five rows:

| row | ok | otherwise |
|---|---|---|
| `rules` | the rule counts and the gitleaks tag | `fail` when the generated sets won't load or don't match the pinned file; `warn` when a rule is dropped |
| `engine` | RE2 and its version, or the fallback where no google-re2 wheel fits | `warn` on the fallback where a wheel fits, with the install line; `fail` when the fallback's worker won't start, exits, or sends a reply that doesn't parse |
| `private-names` | how many entries each key holds | `off` when the list is empty; `warn` for an entry shorter than three characters or empty once trimmed; `fail` when the list won't load |
| `private-names-location` | the config file, and a private allowlist beside it, are outside every git work tree | `warn` when the config directory, or the directory a link resolves it to, is inside one |
| `personal-data` | python-stdnum imports | `off` without the extra; `fail` when it's installed but won't import |

## Keeping the rules current

The rules change only through a moxi-secrets release. `rules/upstream/` holds gitleaks'
`config/gitleaks.toml` byte for byte, its MIT licence, and a `PIN` file (tag, commit,
sha256, date). `rules/credentials.toml` extends it, `rules/broad.toml` holds the broad
set, and `_generate.py` resolves both into `rules/sets.json`, the file the scanner loads,
with each pattern's fallback translation beside it. A rule that uses a gitleaks field the
scanner doesn't implement, or that `rules/drop.txt` lists, is left out with its reason. A
test regenerates `sets.json` and refuses any difference.

To move to a new gitleaks tag, from the repo root, with the network and Go:

```sh
uv run packages/secrets/tools/bump_gitleaks.py v8.31.0
```

It fetches the tag's rules and licence and writes the pin, then regenerates `sets.json` and
prints the rules added, removed, changed and dropped. Next it has Go generate samples of
every pattern and compares Go's matches and secrets with RE2's and the fallback's, then
the in-process hit lists of both engines. After that it times RE2 on large adversarial
texts, runs the gitleaks binary of the tag over the package's own files expecting no hit,
checks the private names' case table against the running Python, and runs the package's
tests. When that Python folds a letter the table lacks, the bump regenerates the table with
`tools/name_folds.py` (on a Python with a newer Unicode than the table's) and stops for a
review. Run it on the newest CPython. Last it prints google-re2's newest wheels, so the `re2`
extra's bound and the wheel check move with them. A rule that fails the comparison or the
time budget is fixed in the translator or added to `rules/drop.txt`, and the bump runs
again. `tools/` is not in the wheel or the sdist.

No timer and no user step fetches rules: your rules are the ones your installed version
pinned.

## Install

Linux, Python 3.12 or later, and [uv](https://docs.astral.sh/uv/). The repository's CI
also runs secrets' tests on macOS and Windows, without gating, but nobody runs secrets
there yet: treat macOS and Windows as untested.

```sh
uv tool install moxi
moxi install secrets
```

`moxi install secrets` offers the `re2` extra where a google-re2 wheel fits, and the
`personal-data` extra everywhere.

The first line installs `moxi`, the core, whose README covers the `moxi` command. The
repository, [CodeManEthan/moxi](https://github.com/CodeManEthan/moxi), lists the other
modules.

## Dependencies

`moxi`, Moxi's core. Two optional extras:

- `re2`: `google-re2`, the fast engine (BSD licence).
- `personal-data`: `python-stdnum`, the personal-data set's validators (LGPL).

## License

MIT. See [LICENSE](https://github.com/CodeManEthan/moxi/blob/moxi-secrets-v0.1.0/packages/secrets/LICENSE). The vendored gitleaks rules are gitleaks' own, under its MIT
licence in `src/moxi_secrets/rules/upstream/LICENSE`.

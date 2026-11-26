# Changelog

## Unreleased

- credentials gains `sk-api-key`: `sk-` and 16 or more letters, digits, `_` or `-`,
  anywhere in text, with entropy above 2.5. It finds short keys, hex keys and
  OpenRouter's `sk-or-v1-` that no vendor rule knows. OpenSSH's `sk-ecdsa-...` and
  `sk-ssh-...` type names are allowed. Callers that judge with credentials (notes,
  publishing) now refuse such keys.
- `url-credential` allows a placeholder only as the whole password (letters and
  underscores holding `pass`, `changeme` or `xxx`, optionally bracketed) or one holding
  `${`. A real password that merely contains one of those words is now a hit.
- Text stored redacted before this release gets these rules only when redacted again.
- `chat_redact(text, between=...)`: a caller's own change to the text (a read tool
  cutting local paths) runs after the strip and before the second redaction, so the last
  redaction judges the text exactly as sent and a change that glues a word onto a secret
  can't break its boundary. `between=None` is the redaction as before.
- `chat_redact(text)` and `CHAT_SETS` (`credentials`, `broad`, `personal-data`, fixed in
  code): the one redaction for text bound for a reader outside the machine. It redacts
  with the chat sets in strict mode, removes C0 controls other than newline and tab, DEL,
  C1 controls and format characters, and redacts again; it returns that last text, or
  `[REDACTED:unscanned]` alone when either pass could not scan the text. It raises
  `SetUnavailable` when a chat set can't run, so a caller without python-stdnum refuses
  rather than reads.
- `scan` and `redact` take `strict=False`. With `strict`, nothing beside a value keeps it
  whole: no `gitleaks:allow` mark and no allowlist of any kind (line, match, secret,
  stopword, path) is read; each rule that ends with gitleaks' end condition runs its
  regex without it (`strictRegex` in `sets.json`, written by the generator for both
  engines); only `high-entropy-run` and `high-entropy-slash-run` keep an entropy floor,
  met when the value or any stretch of it from 32 to 512 characters passes, so padding
  glued on can't pull a value under; private names drop `allowed` and the author's name
  and email. That search does at most 2 million steps per text, and a text that needs
  more comes back unscanned. Strict reaches every copy a scan reads past see-through
  characters, and the fallback worker's requests for each. The defaults are unchanged.
- `secret_path(value)`: the first path in a tool call's input that `is_secret_path`
  names, or None. Every whitespace-separated word of every string counts, split at shell
  joins, inside lists and nested objects, and inside strings that hold JSON. It is the
  walker memory's ingest used, moved here unchanged.
- broad gains `high-entropy-slash-run`: a run of 40 or more base64 characters, up to two
  `=` after it, holding a `/` or `+`, an upper-case letter, a lower-case letter and a
  digit, no `//`, and entropy above 4.5, starting at the text's start or right after
  whitespace, a quote, a backtick, `=`, `:`, `(`, `[`, `{`, `,`, `<` or `>`. It finds a
  bare secret holding `/`, such as an AWS secret access key with no name before it,
  which `high-entropy-run` leaves out, and leaves URLs, paths and page hashes alone,
  since their runs start after a `.` or a `-`. Its
  conditions are a rule field of Moxi's own, `requires`, which only `broad.toml` may use.
  Text stored redacted before this release gets the rule only when redacted again.
- `redact` no longer lets text beside a secret keep it: a `gitleaks:allow` mark, or a
  line a rule's line allowlist names, leaves the secret on that line redacted. `scan`
  still honours both.
- `scan` and `redact` read past invisible characters (controls other than tab and
  newline, format characters, and Unicode's default-ignorable code points), so a token
  split by a zero-width space or a soft hyphen is found. `redact` replaces it in place
  and leaves every other character as it was. A text holding such a character is
  scanned twice; on the fallback engine that is a second worker request. One that also
  holds a vertical tab, form feed, lone carriage return, U+001C to U+001F or NEL is
  scanned a third time, with each of those as a space, so a token that one of them
  sets apart from the word before it is found when split. So is one where a run of the
  others stands just before a rule's keyword, with the run as one space, so a split
  Stripe key after `abc` and a zero-width space is found. A split token is still missed
  where such a run glues a word onto it and its rule has no keyword at its start, or,
  rarely, its split falls just before text that starts another rule's keyword.
- `scan` and `redact` read past the backslashes that escape a quote. A key whose rule
  ends on gitleaks' boundary group (150 of credentials' 231 rules and broad's
  generic-api-key) went unfound when the quote after it was escaped, as in a JSON tool
  call holding `python -c '... {\"key\": \"sk_live_...\"}'`, so memory stored such a key
  whole. A text holding a backslash, or a run of them, directly before a quote is now
  also scanned as a copy without those backslashes, the same copy that leaves out the
  invisible characters; a text holding both is also scanned without the invisible
  characters alone, so no hit found before is lost. The hit is the key alone; the
  backslashes and quotes stay outside the placeholder. The gates now refuse a commit
  holding such a key. A set the fallback left unscanned in the text is not scanned again
  in a copy.
- Cost: on memory's ingest, the redaction of one real Claude Code transcript (1,746
  records, 250 tool calls holding an escaped quote) took 0.30 s against 0.23 s before,
  1.3 times, and 0.10 s against 0.08 s on another (516 records). In the cost test, RE2
  on a megabyte of JSON lines of curl commands, with an escaped quote on every line,
  took 0.91 s for credentials against 0.30 s (its budget is 2.40 s), since every line
  is scanned twice and each escaped header now gives curl-auth-header's hit as well;
  broad took 0.23 s against 0.11 s. No other shape changed.
- `REDACT_VERSION`, now 3: which `redact` this is, for a store of redacted text to
  record. It is 3 because `redact` reads past an escaped quote.
- `seen_through(c)`: whether `scan` and `redact` read past a character.
- Every setting carries page fields, which `moxi config secrets show --json` prints
  and the settings page draws: a plain label and a line, a can-hurt mark on taking a
  name away from the three lists of private names, and one on adding text to the list
  that is public on purpose. secrets needs moxi 0.1.9 or later.
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

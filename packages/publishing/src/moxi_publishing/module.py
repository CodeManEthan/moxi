"""The announcement of Moxi's publishing module, which moxi.modules reads.

publishing keeps private text out of a public repo and releases that repo's packages. It
declares four git hook pieces, which repo runs (`moxi_publishing.message`, `.content`,
`.push` and `.approval`), and reads secrets' pattern sets and lists. No daemon, timer,
store or page.
"""

INTERFACES = frozenset({1})
API = None
#: What `moxi publishing` runs, in process, as `main(argv, prog="moxi publishing")`.
CLI = "moxi_publishing.cli:main"
SUMMARY = "Keep private text out of a public git repo, and release its packages."
OPTIONAL = {}
GATED = {}
INTERNAL_ENV = {
    "MOXI_PUBLISHING_PUSH_WORD": "set by whoever pushes, per push: where the push was "
                                 "approved, which the approval piece checks against "
                                 "push.word_pattern for an agent's push of a guarded "
                                 "branch or a tag, and which the push log keeps",
    "MOXI_PUBLISHING_GITHUB_API_URL": "where the tag gate reads GitHub's API, when not "
                                      "api.github.com: a test's fake, or a GitHub "
                                      "Enterprise host",
    "MOXI_PUBLISHING_PYPI_URL": "where the tag gate reads the package index's JSON API, "
                                "when not pypi.org: a test's fake, or a mirror; the "
                                "release test then installs from its simple index",
    "MOXI_PUBLISHING_SITE_CHECK_NAMES": "set by the release test for its own pytest run: "
                                        "the import names of the wheel under test, which "
                                        "must import from the venv's site-packages",
}
SETTINGS = "settings.toml"
# The schema can't tell a rule name from a typo, nor a pattern that won't compile.
CONFIG_CHECK = "moxi_publishing.settings:check_text"
SETUP = None
SEGMENT = None
#: The doctor's rows for each repo carrying the public flag.
DOCTOR = "moxi_publishing.doctor:rows"
ANNOUNCES = 1

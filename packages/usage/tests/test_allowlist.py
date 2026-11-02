"""The post-ceiling allowlist (`moxi_usage.allowlist`), as a table of accepted and refused
calls. Every command is data: nothing here runs a shell.

The tree is built in the test's tmp with a home of its own: the agent's working directory
is `~/code/app`, builds sit under the work root `~/tmp`, hand-off files under the hand-off
root `~/notes`. The build's worktree is outside the working directory, so every allowed
git call reaches it only through the work root. `make handoff` is the one command prefix
the user added.
"""

import os

from moxi_usage.allowlist import allowed

COMMANDS = (("make", "handoff"),)


def build_tree(t):
    t = os.path.realpath(t)
    home = os.path.join(t, "home")
    p = {
        "T": t,
        "HOME": home,
        "CWD": os.path.join(home, "code", "app"),
        "SUB": os.path.join(home, "code", "app", "sub"),
        "OTHER": os.path.join(home, "code", "other"),
        "WORK": os.path.join(home, "tmp"),
        "REPO": os.path.join(home, "tmp", "builds", "branch"),
        "NOTES": os.path.join(home, "notes"),
        "REPORT": os.path.join(home, "notes", "designs", "r", "build", "build-report.md"),
        "ELSE": os.path.join(t, "elsewhere"),
    }
    for k in ("CWD", "SUB", "OTHER", "REPO", "ELSE"):
        os.makedirs(p[k])
    os.makedirs(os.path.dirname(p["REPORT"]))
    open(p["REPORT"], "w").close()
    open(os.path.join(p["ELSE"], "outside.md"), "w").close()
    # A link under the work root that leads outside it, and a .md link under the hand-off
    # root that leads outside it.
    os.symlink(p["ELSE"], os.path.join(p["WORK"], "link-out"))
    os.symlink(os.path.join(p["ELSE"], "outside.md"), os.path.join(p["NOTES"], "link.md"))
    # Under the hand-off root: a .md name leading to source, and a plain name leading to a
    # report.
    open(os.path.join(p["NOTES"], "x.py"), "w").close()
    os.symlink(os.path.join(p["NOTES"], "x.py"), os.path.join(p["NOTES"], "src.md"))
    os.symlink(p["REPORT"], os.path.join(p["NOTES"], "alias"))
    # The path rule: links whose `..` reads one way lexically and another resolved.
    # ELSE/inlink sits outside every root and leads into the work root; WORK/inlink stays
    # inside.
    os.makedirs(os.path.join(p["REPO"], "a", "b"))
    os.symlink(os.path.join(p["REPO"], "a", "b"), os.path.join(p["ELSE"], "inlink"))
    os.symlink(os.path.join(p["REPO"], "a", "b"), os.path.join(p["WORK"], "inlink"))
    # REPO/S is a repository subdirectory with S/L -> S/a/b; REPO/up leads above the
    # repository.
    os.makedirs(os.path.join(p["REPO"], "S", "a", "b"))
    os.symlink(os.path.join(p["REPO"], "S", "a", "b"), os.path.join(p["REPO"], "S", "L"))
    os.symlink(p["HOME"], os.path.join(p["REPO"], "up"))
    # The hand-off root: ELSE/notelink leads into it, NOTES/dlink stays inside it,
    # NOTES/uplink leads out of it into ELSE/sub.
    os.makedirs(os.path.join(p["ELSE"], "sub"))
    os.symlink(os.path.dirname(p["REPORT"]), os.path.join(p["ELSE"], "notelink"))
    os.symlink(os.path.dirname(p["REPORT"]), os.path.join(p["NOTES"], "dlink"))
    os.symlink(os.path.join(p["ELSE"], "sub"), os.path.join(p["NOTES"], "uplink"))
    # A work root named through a link: linkroot -> WORK.
    os.symlink(p["WORK"], os.path.join(p["T"], "linkroot"))
    return p


def table(P):
    """Every case as (label, passed, reason)."""
    CWD, REPO, WORK, NOTES = P["CWD"], P["REPO"], P["WORK"], P["NOTES"]
    WORK_ROOTS = [WORK]
    REPO_TILDE = "~/tmp/builds/branch"
    REPO_REL = "../../tmp/builds/branch"  # relative to CWD
    RESULTS = []

    def ask(name, inp, cwd, work_roots, handoff_roots, commands=COMMANDS):
        return allowed(name, inp, cwd, handoff_roots=handoff_roots, work_roots=work_roots,
                       commands=commands, home=P["HOME"])

    def check(label, got_ok, want_ok, reason=""):
        RESULTS.append((label, got_ok == want_ok, reason))

    def bash(cmd, cwd=CWD):
        return ask("Bash", {"command": cmd, "description": "x"}, cwd, WORK_ROOTS, NOTES)

    def expect_bash(label, cmd, want, cwd=CWD):
        ok, reason = bash(cmd, cwd)
        check("bash %s: %s" % ("allowed" if want else "refused", label), ok, want, reason)
        if not ok and not reason:
            RESULTS.append(("bash refusal has a reason: " + label, False, ""))

    def shapes(form):
        """The repository shapes for one git form, cwd outside the worktree. The -C shape
        is given absolute, ~/ and relative to cwd."""
        return [
            ("bare", "git " + form, REPO),
            ("bare, cwd is the agent's", "git " + form, CWD),
            ("-C absolute", "git -C %s %s" % (REPO, form), CWD),
            ("-C ~/", "git -C %s %s" % (REPO_TILDE, form), CWD),
            ("-C relative", "git -C %s %s" % (REPO_REL, form), CWD),
            ("cd absolute", "cd %s && git %s" % (REPO, form), CWD),
            ("cd ~/", "cd %s && git %s" % (REPO_TILDE, form), CWD),
        ]

    # Every allowed git form, in every shape ------------------------------------------------------
    ALLOWED_FORMS = [
        "status --short", "status --porcelain",
        "diff --stat", "diff --cached --stat", "diff --check", "diff --cached --check",
        "diff", "diff --cached",
        "add -A", "add --all", "add -u", "add --update",
        "add -- hooks/x.py", "add -- hooks/x.py tests/y.py", "add -- .", "add -- 'name with space.md'",
        "commit -m 'one line'", "commit -m 'subject' -m 'body'", "commit -m \"double quoted\"",
        "rev-parse HEAD", "log -1 --oneline", "branch --show-current",
    ]
    for form in ALLOWED_FORMS:
        for shape, cmd, cwd in shapes(form):
            if form.startswith("add -- ") and cwd == CWD and shape.startswith("bare"):
                continue  # bare add paths resolve in cwd; covered below
            expect_bash("%s [%s]" % (form, shape), cmd, True, cwd)
    expect_bash("bare add -- path inside cwd", "git add -- sub/x.py", True)
    expect_bash("-C a directory inside cwd", "git -C sub status --short", True)
    expect_bash("-C the work root itself", "git -C %s status --short" % WORK, True)
    expect_bash("extra spaces and tabs", "  cd %s\t&&  git  status --short  " % REPO, True)
    expect_bash("&& with no spaces", "cd %s&&git status --short" % REPO, True)

    # The commit message: one quoted -m carries subject, body and trailer block -----------------
    MSG = ("app: the post-ceiling allowlist\n\nBody line one.\nBody line two.\n\n"
           "Signed-off-by: A Person <person@example.com>")
    expect_bash("single-quoted -m with embedded newlines", "git -C %s commit -m '%s'" % (REPO, MSG), True)
    expect_bash("single-quoted -m with newlines, cd shape", "cd %s && git commit -m '%s'" % (REPO, MSG), True)
    expect_bash("double-quoted -m with embedded newlines", "git -C %s commit -m \"a\nb\"" % REPO, True)
    expect_bash("$ and backtick inside single quotes", "git -C %s commit -m 'costs $5, see `x`'" % REPO, True)
    expect_bash("escaped quote inside double quotes", "git -C %s commit -m \"say \\\"hi\\\"\"" % REPO, True)
    expect_bash("message that looks like an option", "git -C %s commit -m '--amend'" % REPO, True)

    # The user's command prefixes (ceiling.allow_commands) ----------------------------------------
    expect_bash("a listed prefix, bare", "make handoff", True)
    expect_bash("a listed prefix with more words", "make handoff report --quiet", True)
    expect_bash("a listed prefix in the cd shape", "cd %s && make handoff" % REPO, True)
    expect_bash("a listed prefix in the cd shape, ~/", "cd %s && make handoff" % REPO_TILDE, True)
    expect_bash("a quoted word that equals the prefix", "'make' handoff", True)
    expect_bash("the prefix's first word alone", "make", False)
    expect_bash("another word after the first", "make clean", False)
    expect_bash("a prefix word with $", "make $TARGET", False)
    expect_bash("a prefix then ;", "make handoff; ls", False)
    expect_bash("a prefix, cd outside every root", "cd %s && make handoff" % P["ELSE"], False)
    expect_bash("a prefix, cd to a sibling of cwd", "cd %s && make handoff" % P["OTHER"], False)
    check("no prefix listed refuses it",
          ask("Bash", {"command": "make handoff"}, CWD, WORK_ROOTS, NOTES, commands=())[0], False)
    check("an empty prefix is ignored",
          ask("Bash", {"command": "ls"}, CWD, WORK_ROOTS, NOTES, commands=([],))[0], False)

    # Refused git subcommands and options, in every shape -----------------------------------------
    REFUSED_FORMS = [
        "checkout main", "checkout -- x", "restore x", "reset --hard", "reset", "clean -fd", "merge main",
        "rebase main", "push", "push origin main", "pull", "fetch", "stash", "show", "show HEAD",
        "grep foo", "log", "log -p", "log -1", "log --oneline", "status", "status -s", "diff HEAD",
        "diff --stat HEAD", "diff --name-only", "diff -- x", "rev-parse --show-toplevel", "branch",
        "branch -D x", "tag v1", "config core.fsmonitor x", "worktree add x", "apply x.patch",
        "cherry-pick abc", "am x", "submodule update", "init", "help",
        "add .", "add x", "add -i", "add -p", "add --interactive", "add -A x", "add --", "add -N x",
        "commit", "commit -m", "commit -m a -m", "commit --amend -m x", "commit -m x --amend",
        "commit --fixup abc", "commit --no-verify -m x", "commit -m x --no-verify", "commit -am x",
        "commit -F msg.txt", "commit --message x", "commit -mx", "commit -m x -n",
        "-c core.fsmonitor=x status --short", "--git-dir=x status --short", "--work-tree=x status --short",
        "-p log -1 --oneline", "--no-pager diff", "--exec-path=x status --short",
    ]
    for form in REFUSED_FORMS:
        for shape, cmd, cwd in shapes(form):
            if shape.startswith("-C") and form.startswith("-"):
                pass  # `git -C <dir> -c ...` is refused too; keep it
            expect_bash("%s [%s]" % (form, shape), cmd, False, cwd)
    expect_bash("git alone", "git", False)
    expect_bash("git -C with no directory", "git -C", False)
    expect_bash("git -C with no subcommand", "git -C %s" % REPO, False)
    expect_bash("two -C options", "git -C %s -C %s status --short" % (REPO, REPO), False)
    expect_bash("git by full path", "/usr/bin/git -C %s status --short" % REPO, False)
    expect_bash("command git", "command git -C %s status --short" % REPO, False)
    expect_bash("env prefix", "GIT_DIR=x git -C %s status --short" % REPO, False)

    # Repository directory outside cwd and every work root ----------------------------------------
    for d in (P["ELSE"], P["OTHER"], "../other", "/", os.path.dirname(WORK), P["T"]):
        expect_bash("-C outside every root: %s" % d, "git -C %s status --short" % d, False)
        expect_bash("cd outside every root: %s" % d, "cd %s && git status --short" % d, False)
    expect_bash("-C through a link out of the work root", "git -C %s/link-out status --short" % WORK, False)
    expect_bash("cd through a link out of the work root", "cd %s/link-out && git status --short" % WORK, False)
    expect_bash("-C with .. out of the work root", "git -C %s/../code/other status --short" % WORK, False)
    expect_bash("-C a sibling with the root as name prefix", "git -C %s2 status --short" % WORK, False)
    expect_bash("-C bare ~", "git -C ~ status --short", False)
    expect_bash("-C ~user", "git -C ~root/tmp status --short", False)
    expect_bash("-C a quoted ~ is literal, relative to cwd", "git -C '~/tmp' status --short", True)
    expect_bash("cd -", "cd - && git status --short", False)
    expect_bash("cd with an option", "cd -P %s && git status --short" % REPO, False)
    expect_bash("cd with no directory", "cd && git status --short", False)
    expect_bash("cd alone", "cd %s" % REPO, False)
    expect_bash("cd with nothing after &&", "cd %s &&" % REPO, False)
    expect_bash("cd then another cd", "cd %s && cd x" % REPO, False)
    expect_bash("bare git with cwd outside every root? cwd always passes", "git status --short", True, P["ELSE"])

    # add -- paths must resolve inside the repository directory -----------------------------------
    for path in ("../x.py", "../../../code/app/x.py", "/etc/passwd", P["ELSE"] + "/x",
                 "%s/link-out/x" % WORK, ":/x", ":(top)x", "':!x'", "ok.py ../x.py", "''"):
        expect_bash("add -- outside the repository: %s [-C]" % path, "git -C %s add -- %s" % (REPO, path), False)
        expect_bash("add -- outside the repository: %s [cd]" % path, "cd %s && git add -- %s" % (REPO, path), False)
    expect_bash("add -- absolute path inside the repository", "git -C %s add -- %s/hooks/x.py" % (REPO, REPO), True)
    expect_bash("add -- ~/ path inside the repository", "git -C %s add -- %s/x.py" % (REPO, REPO_TILDE), True)
    expect_bash("add -- bare, path outside cwd", "git add -- ../other/x.py", False)
    expect_bash("add -- with a .. that stays inside", "git -C %s add -- hooks/../tests/y.py" % REPO, True)

    # The shell text -------------------------------------------------------------------------------
    R = REPO
    for label, cmd, why in [
        ("newline outside quotes", "git -C %s status --short\ngit -C %s diff" % (R, R), 'newline outside quotes'),
        ("trailing newline outside quotes", "git -C %s status --short\n" % R, 'newline outside quotes'),
        ("backslash-newline outside quotes", "git -C %s status \\\n--short" % R, 'backslash outside quotes'),
        (";", "git -C %s status --short; git -C %s diff" % (R, R), "control operator ';'"),
        (";, no space", "git -C %s status --short;ls" % R, "control operator ';'"),
        ("||", "git -C %s status --short || ls" % R, "control operator '|'"),
        ("|", "git -C %s diff | head" % R, "control operator '|'"),
        ("&", "git -C %s status --short &" % R, "control operator '&'"),
        ("& in the middle", "git -C %s status --short & ls" % R, "control operator '&'"),
        ("a second &&", "cd %s && git add -A && git commit -m x" % R, "a second '&&'"),
        ("&& not after cd", "git -C %s add -A && git -C %s commit -m x" % (R, R), 'only directly after cd'),
        ("&& after cd with two words", "cd %s x && git status --short" % R, 'only directly after cd'),
        ("redirection >", "git -C %s diff > /tmp/x" % R, 'redirection'),
        ("redirection >>", "git -C %s diff >> /tmp/x" % R, 'redirection'),
        ("redirection <", "git -C %s commit -m x < /dev/null" % R, 'redirection'),
        ("redirection 2>&1", "git -C %s status --short 2>&1" % R, 'redirection'),
        ("redirection &>", "git -C %s status --short &>/dev/null" % R, 'redirection'),
        ("here-string", "git -C %s commit -m x <<< y" % R, 'redirection'),
        ("$ unquoted", "git -C $HOME/tmp/builds/branch status --short", "'$' outside single quotes"),
        ("$ in double quotes", "git -C %s commit -m \"$(cat x)\"" % R, "'$' outside single quotes"),
        ("escaped $ in double quotes", "git -C %s commit -m \"\\$x\"" % R, "'$' outside single quotes"),
        ("${}", "git -C %s commit -m ${x}" % R, "'$' outside single quotes"),
        ("$'...'", "git -C %s commit -m $'a\\nb'" % R, "'$' outside single quotes"),
        ("backtick unquoted", "git -C %s commit -m `cat x`" % R, 'backtick outside single quotes'),
        ("backtick in double quotes", "git -C %s commit -m \"`cat x`\"" % R, 'backtick outside single quotes'),
        ("git -C after cd", "cd %s && git -C %s status --short" % (R, R), 'git -C after cd'),
        ("git -C after cd, same dir", "cd %s && git -C . status --short" % R, 'git -C after cd'),
        ("subshell", "(git -C %s status --short)" % R, 'subshell'),
        ("process substitution", "git -C %s commit -F <(echo x)" % R, 'redirection'),
        ("glob", "git -C %s add -- *.py" % R, "glob '*'"),
        ("glob ?", "git -C %s add -- x?.py" % R, "glob '?'"),
        ("glob [", "git -C %s add -- [ab].py" % R, "glob '['"),
        ("brace", "git -C %s add -- {a,b}.py" % R, "brace '{'"),
        ("brace in the directory", "git -C %s/{a,b} status --short" % WORK, "brace '{'"),
        ("comment", "git -C %s status --short #x" % R, 'comment'),
        ("backslash outside quotes", "git -C %s commit -m a\\ b" % R, 'backslash outside quotes'),
        ("unterminated single quote", "git -C %s commit -m 'x" % R, 'unterminated single quote'),
        ("unterminated double quote", "git -C %s commit -m \"x" % R, 'unterminated double quote'),
        ("carriage return outside quotes", "git -C %s status --short\r" % R, 'control character'),
        ("tilde mid-word", "git -C %s add -- a~/b" % R, 'tilde'),
        ("not a wrap-up command", "pytest -q", 'pytest is not a wrap-up command'),
        ("cat a source file", "cat %s/hooks/x.py" % R, 'cat is not a wrap-up command'),
        ("bash -c", "bash -c 'git status --short'", 'bash is not a wrap-up command'),
        ("empty command", "   ", 'no command'),
    ]:
        ok, reason = bash(cmd)
        check("bash refused: %s (reason: %s)" % (label, why), (ok, why in reason), (False, True), reason)

    # Write and Edit: Markdown under the hand-off root ---------------------------------------------
    def tool(name, inp, cwd=CWD, roots=NOTES):
        return ask(name, inp, cwd, WORK_ROOTS, roots)


    def expect_tool(label, name, inp, want, cwd=CWD, roots=NOTES):
        ok, reason = tool(name, inp, cwd, roots)
        check("%s %s: %s" % (name, "allowed" if want else "refused", label), ok, want, reason)


    REPORT = P["REPORT"]
    for name in ("Write", "Edit"):
        extra = {"content": "x"} if name == "Write" else {"old_string": "a", "new_string": "b"}
        expect_tool("report under the hand-off root", name, dict(extra, file_path=REPORT), True)
        expect_tool("new report under the hand-off root", name, dict(extra, file_path=NOTES + "/new/dir/r.md"), True)
        expect_tool("report path relative to cwd", name, dict(extra, file_path="../../notes/r.md"), True)
        expect_tool("hand-off roots as a list", name, dict(extra, file_path=REPORT), True, roots=[P["ELSE"], NOTES])
        expect_tool("not Markdown under the root", name, dict(extra, file_path=NOTES + "/r.py"), False)
        expect_tool("Markdown suffix in the middle", name, dict(extra, file_path=NOTES + "/r.md.py"), False)
        expect_tool("Markdown outside the root", name, dict(extra, file_path=P["ELSE"] + "/r.md"), False)
        expect_tool("Markdown in the repository", name, dict(extra, file_path=REPO + "/README.md"), False)
        expect_tool("Markdown in cwd", name, dict(extra, file_path=CWD + "/README.md"), False)
        expect_tool(".. out of the root", name, dict(extra, file_path=NOTES + "/../code/app/x.md"), False)
        expect_tool("a .md link under the root leading outside", name, dict(extra, file_path=NOTES + "/link.md"), False)
        expect_tool("a .md name leading to source under the root", name, dict(extra, file_path=NOTES + "/src.md"), False)
        expect_tool("a plain name leading to a report", name, dict(extra, file_path=NOTES + "/alias"), False)
        expect_tool("a sibling with the root as name prefix", name, dict(extra, file_path=NOTES + "2/r.md"), False)
        expect_tool("no file_path", name, dict(extra), False)
        expect_tool("file_path not a string", name, dict(extra, file_path=7), False)
        expect_tool("no hand-off root", name, dict(extra, file_path=REPORT), False, roots=[])
        expect_tool("relative hand-off root ignored", name, dict(extra, file_path=REPORT), False, roots="notes")

    # Read: bounded reads of the same Markdown paths -----------------------------------------------
    for label, inp, want in [
        ("limit 200, no offset", {"limit": 200}, True),
        ("limit 1, no offset", {"limit": 1}, True),
        ("limit 50, offset 0", {"limit": 50, "offset": 0}, True),
        ("limit 50, offset 400", {"limit": 50, "offset": 400}, True),
        ("limit 50, offset null", {"limit": 50, "offset": None}, True),
        ("no limit", {}, False),
        ("no limit, offset 0", {"offset": 0}, False),
        ("limit 0", {"limit": 0}, False),
        ("limit 201", {"limit": 201}, False),
        ("limit -1", {"limit": -1}, False),
        ("limit as a string", {"limit": "50"}, False),
        ("limit as a float", {"limit": 50.0}, False),
        ("limit true", {"limit": True}, False),
        ("offset -1", {"limit": 50, "offset": -1}, False),
        ("offset as a string", {"limit": 50, "offset": "3"}, False),
        ("offset as a float", {"limit": 50, "offset": 3.0}, False),
    ]:
        expect_tool(label, "Read", dict(inp, file_path=REPORT), want)
    expect_tool("bounded read outside the root", "Read", {"file_path": P["ELSE"] + "/outside.md", "limit": 50}, False)
    expect_tool("bounded read of source in the repository", "Read", {"file_path": REPO + "/hooks/x.py", "limit": 50}, False)
    expect_tool("bounded read of non-Markdown under the root", "Read", {"file_path": NOTES + "/x.txt", "limit": 50}, False)

    # The path rule: inside the root both lexically and resolved ----------------------
    E = P["ELSE"]
    S = REPO + "/S"
    for shape, fmt in (("cd", "cd %s && git status --short"), ("-C", "git -C %s status --short")):
        expect_bash("%s: a link from outside every root, then .. (shell lands outside)" % shape,
                    fmt % (E + "/inlink/.."), False)
        expect_bash("%s: a link from outside every root into the work root" % shape, fmt % (E + "/inlink"), False)
        expect_bash("%s: a link inside the work root, then .. (stays inside)" % shape,
                    fmt % (WORK + "/inlink/.."), True)
        expect_bash("%s: a link inside the work root" % shape, fmt % (WORK + "/inlink"), True)
        expect_bash("%s: a link in the repository leading above it" % shape, fmt % (REPO + "/up"), False)
    check("work root named through a link: its realpath is inside",
          ask("Bash", {"command": "git -C %s status --short" % REPO}, CWD,
                                [P["T"] + "/linkroot"], NOTES)[0], True)
    check("work root named through a link: the link path is inside",
          ask("Bash", {"command": "git -C %s/linkroot/builds/branch status --short"
                                         % P["T"]}, CWD, [P["T"] + "/linkroot"], NOTES)[0], True)
    for shape, fmt in (("-C", "git -C %s add -- %%s" % S), ("cd", "cd %s && git add -- %%s" % S)):
        expect_bash("add -- through an in-dir link, .. lexically above <dir> [%s]" % shape,
                    fmt % "L/../../top.txt", False)
        expect_bash("add -- through an in-dir link, .. lexically inside <dir> [%s]" % shape, fmt % "L/../x", True)
        expect_bash("add -- an in-dir link [%s]" % shape, fmt % "L/x", True)
    expect_bash("add -- a link in the repository leading above it", "git -C %s add -- up/x" % REPO, False)
    expect_bash("add -- a link leading above the repository, then ..", "git -C %s add -- up/.." % REPO, False)
    expect_bash("add -- a link leading above the repository, cd shape", "cd %s && git add -- up/x" % REPO, False)
    for name in ("Write", "Edit"):
        expect_tool("a link from outside the hand-off root into it", name, {"file_path": E + "/notelink/r.md"}, False)
        expect_tool("a link inside the hand-off root, then .. (stays inside)", name,
                    {"file_path": NOTES + "/dlink/../x.md"}, True)
        expect_tool("a link out of the hand-off root, then .. (lexically inside)", name,
                    {"file_path": NOTES + "/uplink/../outside.md"}, False)
    expect_tool("bounded read through a link from outside into the root", "Read",
                {"file_path": E + "/notelink/build-report.md", "limit": 50}, False)

    # Codex's shell tool: Bash with `command` alone, and its own `workdir` --------------------
    def codex_bash(label, inp, want):
        ok, reason = ask("Bash", inp, CWD, WORK_ROOTS, NOTES)
        check("codex Bash %s: %s" % ("allowed" if want else "refused", label), ok, want, reason)

    codex_bash("git -C status", {"command": "git -C %s status --short" % REPO}, True)
    codex_bash("cd && commit", {"command": "cd %s && git commit -m 'hand off'" % REPO}, True)
    codex_bash("cd && add -A", {"command": "cd %s && git add -A" % REPO}, True)
    codex_bash("a user's command prefix", {"command": "make handoff"}, True)
    codex_bash("push", {"command": "git -C %s push" % REPO}, False)
    codex_bash("workdir in the work root", {"command": "git status --short", "workdir": REPO}, True)
    codex_bash("workdir under cwd", {"command": "git add -A", "workdir": "sub"}, True)
    codex_bash("workdir relative to cwd, into the work root",
               {"command": "git commit -m x", "workdir": REPO_REL}, True)
    codex_bash("workdir outside every root", {"command": "git status --short", "workdir": P["ELSE"]}, False)
    codex_bash("workdir through a link out of the work root",
               {"command": "git status --short", "workdir": WORK + "/link-out"}, False)
    codex_bash("workdir not a string", {"command": "git status --short", "workdir": 7}, False)
    codex_bash("workdir empty", {"command": "git status --short", "workdir": ""}, False)
    codex_bash("cd from the workdir to a place outside every root",
               {"command": "cd %s && git status --short" % P["OTHER"], "workdir": REPO}, False)
    codex_bash("cd from the workdir back under cwd",
               {"command": "cd %s && git status --short" % P["SUB"], "workdir": REPO}, True)
    codex_bash("a relative cd joins the workdir",
               {"command": "cd .. && git status --short", "workdir": REPO}, True)
    codex_bash("a relative -C joins the workdir",
               {"command": "git -C S add -- L/x", "workdir": REPO}, True)
    codex_bash("add -- reads its paths from the workdir",
               {"command": "git add -- up/x", "workdir": REPO}, False)
    codex_bash("command as an argv list", {"command": ["git", "status", "--short"]}, False)

    # Codex's apply_patch: adds and updates of Markdown under the hand-off root -------------
    def patch(*sections, begin="*** Begin Patch\n", end="*** End Patch\n"):
        return begin + "".join(sections) + end

    def add(path, *lines):
        return "*** Add File: %s\n" % path + "".join("+%s\n" % line for line in lines)

    def update(path, *lines):
        return "*** Update File: %s\n" % path + "".join("%s\n" % line for line in lines)

    def expect_patch(label, text, want, cwd=CWD, key="command"):
        ok, reason = ask("apply_patch", {key: text}, cwd, WORK_ROOTS, NOTES)
        check("apply_patch %s: %s" % ("allowed" if want else "refused", label), ok, want, reason)
        if not ok and not reason:
            RESULTS.append(("apply_patch refusal has a reason: " + label, False, ""))

    NEW = NOTES + "/designs/r/build/next.md"
    CHANGE = ("@@", "-old line", "+new line")
    expect_patch("add a report under the root", patch(add(NEW, "# Hand-off", "", "done")), True)
    expect_patch("add an empty report", patch(add(NEW)), True)
    expect_patch("add in a new directory", patch(add(NOTES + "/new/dir/r.md", "x")), True)
    expect_patch("add relative to cwd", patch(add("../../notes/r.md", "x")), True)
    expect_patch("update the report", patch(update(REPORT, *CHANGE)), True)
    expect_patch("update with no @@ first", patch(update(REPORT, " context", "+added")), True)
    expect_patch("update with two chunks and End of File",
                 patch(update(REPORT, "@@ ## Done", "-a", "+b", "@@", "+c", "*** End of File")), True)
    expect_patch("update whose context line is a Markdown rule", patch(update(REPORT, " ***", "+x")), True)
    expect_patch("add and update, both under the root", patch(add(NEW, "x"), update(REPORT, *CHANGE)), True)
    expect_patch("CRLF line ends", patch(add(NEW, "x")).replace("\n", "\r\n"), True)
    expect_patch("blank lines around the patch", "\n\n" + patch(add(NEW, "x")) + "\n  \n", True)
    expect_patch("padded markers", patch(add(NEW, "x"), begin="  *** Begin Patch \n",
                                         end=" *** End Patch"), True)
    expect_patch("a link inside the root, then ..", patch(add(NOTES + "/dlink/../x.md", "x")), True)
    expect_patch(".. that stays inside the root", patch(add(NOTES + "/designs/../x.md", "x")), True)
    expect_patch("not Markdown", patch(add(NOTES + "/r.py", "x")), False)
    expect_patch("one Markdown, one not", patch(add(NEW, "x"), add(NOTES + "/r.py", "x")), False)
    expect_patch("update of a source file", patch(update(REPO + "/hooks/x.py", *CHANGE)), False)
    expect_patch("Markdown outside the root", patch(add(P["ELSE"] + "/r.md", "x")), False)
    expect_patch("Markdown in the repository", patch(add(REPO + "/README.md", "x")), False)
    expect_patch("relative Markdown in cwd", patch(add("README.md", "x")), False)
    expect_patch("~/ is not expanded: a path under cwd", patch(add("~/notes/r.md", "x")), False)
    expect_patch(".. out of the root", patch(add(NOTES + "/../code/app/x.md", "x")), False)
    expect_patch("relative .. out of the root", patch(add("../../notes/../x.md", "x")), False)
    expect_patch("a .md link under the root leading outside", patch(update(NOTES + "/link.md", *CHANGE)), False)
    expect_patch("a directory link under the root leading outside",
                 patch(add(NOTES + "/uplink/new.md", "x")), False)
    expect_patch("a link out of the root, then .. (lexically inside)",
                 patch(add(NOTES + "/uplink/../outside.md", "x")), False)
    expect_patch("a link from outside into the root", patch(add(E + "/notelink/r.md", "x")), False)
    expect_patch("a .md name leading to source", patch(update(NOTES + "/src.md", *CHANGE)), False)
    expect_patch("a sibling with the root as name prefix", patch(add(NOTES + "2/r.md", "x")), False)
    for label, text, why in [
        ("delete a report", patch("*** Delete File: %s\n" % REPORT), "deletes"),
        ("delete after an add", patch(add(NEW, "x"), "*** Delete File: %s\n" % REPORT), "deletes"),
        ("an indented delete after an add", patch(add(NEW, "x"), "   *** Delete File: %s\n" % REPORT),
         "deletes"),
        ("move within the root", patch(update(REPORT, "*** Move to: %s" % NEW, *CHANGE)), "moves"),
        ("move out of the root", patch(update(REPORT, "*** Move to: %s/r.md" % P["ELSE"], *CHANGE)),
         "moves"),
        ("move after a change", patch(update(REPORT, *CHANGE), "*** Move to: %s\n" % NEW), "moves"),
        ("no begin marker", patch(add(NEW, "x"), begin=""), "Begin Patch"),
        ("no end marker", patch(add(NEW, "x"), end=""), "Begin Patch"),
        ("a heredoc wrapper", "<<'EOF'\n" + patch(add(NEW, "x")) + "EOF\n", "Begin Patch"),
        ("a shell call around the patch", "apply_patch <<'EOF'\n" + patch(add(NEW, "x")) + "EOF\n",
         "Begin Patch"),
        ("nothing between the markers", patch(), "changes no file"),
        ("a line before the first section", patch("+x\n", add(NEW, "x")), "not a line of a file section"),
        ("an add line without +", patch(add(NEW, "x"), "y\n"), "not a line of a file section"),
        ("a blank line in an add", patch(add(NEW, "x"), "\n"), "not a line of a file section"),
        ("an unknown header", patch("*** Copy File: %s\n" % NEW), "not a line of a file section"),
        ("a header with no space", patch("*** Add File:%s\n+x\n" % NEW), "not a line of a file section"),
        ("an add with no path", patch("*** Add File: \n+x\n"), "not a line of a file section"),
        ("an update with no change", patch(update(REPORT)), "no change"),
        ("an update with no change before the next", patch(update(REPORT), add(NEW, "x")), "no change"),
        ("End of File with no change", patch(update(REPORT, "*** End of File")), "no change"),
        ("a stray line in an update", patch(update(REPORT, "@@", "x")), "not a change line"),
        ("a tab-led line in an update", patch(update(REPORT, "@@", "\t+x")), "not a change line"),
        ("only whitespace", "  \n ", "no patch text"),
        ("empty", "", "no patch text"),
    ]:
        ok, reason = ask("apply_patch", {"command": text}, CWD, WORK_ROOTS, NOTES)
        check("apply_patch refused: %s (reason: %s)" % (label, why), (ok, why in reason),
              (False, True), reason)
    expect_patch("the text under another key", patch(add(NEW, "x")), False, key="input")
    expect_patch("the text under patch", patch(add(NEW, "x")), False, key="patch")
    expect_patch("command not a string", ["*** Begin Patch"], False)
    expect_patch("cwd relative", patch(add("../../notes/r.md", "x")), False, cwd="code/app")
    check("apply_patch with no hand-off root",
          ask("apply_patch", {"command": patch(add(NEW, "x"))}, CWD, WORK_ROOTS, [])[0], False)
    check("apply_patch input not an object",
          ask("apply_patch", patch(add(NEW, "x")), CWD, WORK_ROOTS, NOTES)[0], False)


    # Every other tool is refused -----------------------------------------------------------------
    for name, inp in [
        ("Glob", {"pattern": "**/*.md"}), ("Grep", {"pattern": "x"}), ("Agent", {"prompt": "x"}),
        ("Task", {"prompt": "x"}), ("Skill", {"skill": "commit"}), ("WebFetch", {"url": "x"}),
        ("WebSearch", {"query": "x"}), ("NotebookEdit", {"notebook_path": NOTES + "/x.md"}),
        ("MultiEdit", {"file_path": REPORT, "edits": []}), ("mcp__x__y", {}), ("ToolSearch", {"query": "x"}),
        ("", {}), (None, {}),
    ]:
        expect_tool("%s is not a wrap-up operation" % name, name, inp, False)

    # Malformed arguments never allow -------------------------------------------------------------
    check("tool_input not an object", ask("Bash", "git status --short", CWD, WORK_ROOTS, NOTES)[0], False)
    check("tool_input None", ask("Write", None, CWD, WORK_ROOTS, NOTES)[0], False)
    check("command not a string", bash(["git", "status", "--short"])[0], False)
    check("cwd relative", bash("git status --short", "code/app")[0], False)
    check("cwd None", bash("git -C %s status --short" % REPO, None)[0], False)
    check("work_roots None still allows cwd", ask("Bash", {"command": "git status --short"}, CWD, None, NOTES)[0], True)
    check("work_roots None refuses the worktree", ask("Bash", {"command": "git -C %s status --short" % REPO}, CWD, None, NOTES)[0], False)
    check("relative work root ignored", ask("Bash", {"command": "git -C %s status --short" % REPO}, CWD, ["home/tmp"], NOTES)[0], False)
    ok, reason = bash("git -C %s push" % REPO)
    check("the refusal names the rule", (ok, "push" in reason), (False, True), reason)
    check("the allow is (True, \"\")", bash("git -C %s status --short" % REPO), (True, ""))
    return RESULTS


def test_the_allowlist_table(tmp_path):
    results = table(build_tree(tmp_path))
    failed = [f"{label} ({reason})" if reason else label
              for label, passed, reason in results if not passed]
    assert len(results) > 600
    assert not failed, "\n".join(failed)


def test_the_process_home_expands_a_leading_tilde_when_none_is_given(tmp_path, monkeypatch):
    p = build_tree(tmp_path)
    monkeypatch.setenv("HOME", p["HOME"])
    command = {"command": "git -C ~/tmp/builds/branch status --short"}
    assert allowed("Bash", command, p["CWD"], work_roots=[p["WORK"]]) == (True, "")

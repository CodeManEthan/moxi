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

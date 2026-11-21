"""The post-ceiling allowlist (`moxi_usage.allowlist`), as a table of accepted and refused
calls. Every command is data: nothing here runs a shell.

The tree is built in the test's tmp with a home of its own: the agent's working directory
is `~/code/app`, builds sit under the work root `~/tmp`, hand-off files under the hand-off
root `~/notes`. The build's worktree is outside the working directory, so every allowed
git call reaches it only through the work root. `make handoff` is the one command prefix
the user added.
"""

import os

from moxi_usage.allowlist import allowed, patch_files

COMMANDS = (("make", "handoff"), ("moxi", "repo", "rules"),
            ("make", "trailers", "<key>"))


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


def table(p):
    """Every case as (label, passed, reason)."""
    current_dir, repo, work, notes = p["CWD"], p["REPO"], p["WORK"], p["NOTES"]
    work_dirs = [work]
    repo_tilde = "~/tmp/builds/branch"
    repo_rel = "../../tmp/builds/branch"  # relative to CWD
    results = []

    def ask(name, inp, cwd, work_roots, handoff_roots, commands=COMMANDS):
        return allowed(name, inp, cwd, handoff_roots=handoff_roots, work_roots=work_roots,
                       commands=commands, home=p["HOME"])

    def check(label, got_ok, want_ok, reason=""):
        results.append((label, got_ok == want_ok, reason))

    def bash(cmd, cwd=current_dir):
        return ask("Bash", {"command": cmd, "description": "x"}, cwd, work_dirs, notes)

    def expect_bash(label, cmd, want, cwd=current_dir):
        ok, reason = bash(cmd, cwd)
        check("bash %s: %s" % ("allowed" if want else "refused", label), ok, want, reason)
        if not ok and not reason:
            results.append(("bash refusal has a reason: " + label, False, ""))

    def shapes(form):
        """The repository shapes for one git form, cwd outside the worktree. The -C shape
        is given absolute, ~/ and relative to cwd."""
        return [
            ("bare", "git " + form, repo),
            ("bare, cwd is the agent's", "git " + form, current_dir),
            ("-C absolute", "git -C %s %s" % (repo, form), current_dir),
            ("-C ~/", "git -C %s %s" % (repo_tilde, form), current_dir),
            ("-C relative", "git -C %s %s" % (repo_rel, form), current_dir),
            ("cd absolute", "cd %s && git %s" % (repo, form), current_dir),
            ("cd ~/", "cd %s && git %s" % (repo_tilde, form), current_dir),
        ]

    # Every allowed git form, in every shape ------------------------------------------------------
    allowed_forms = [
        "status --short", "status --porcelain",
        "diff --stat", "diff --cached --stat", "diff --check", "diff --cached --check",
        "diff", "diff --cached",
        "add -A", "add --all", "add -u", "add --update",
        "add -- hooks/x.py", "add -- hooks/x.py tests/y.py", "add -- .", "add -- 'name with space.md'",
        "commit -m 'one line'", "commit -m 'subject' -m 'body'", "commit -m \"double quoted\"",
        "rev-parse HEAD", "log -1 --oneline", "branch --show-current",
    ]
    for form in allowed_forms:
        for shape, cmd, cwd in shapes(form):
            if form.startswith("add -- ") and cwd == current_dir and shape.startswith("bare"):
                continue  # bare add paths resolve in cwd; covered below
            expect_bash("%s [%s]" % (form, shape), cmd, want=True, cwd=cwd)
    expect_bash("bare add -- path inside cwd", "git add -- sub/x.py", want=True)
    expect_bash("-C a directory inside cwd", "git -C sub status --short", want=True)
    expect_bash("-C the work root itself", "git -C %s status --short" % work, want=True)
    expect_bash("extra spaces and tabs", "  cd %s\t&&  git  status --short  " % repo, want=True)
    expect_bash("&& with no spaces", "cd %s&&git status --short" % repo, want=True)

    # The commit message: one quoted -m carries subject, body and trailer block -----------------
    msg = ("app: the post-ceiling allowlist\n\nBody line one.\nBody line two.\n\n"
           "Signed-off-by: A Person <person@example.com>")
    expect_bash("single-quoted -m with embedded newlines", "git -C %s commit -m '%s'" % (repo, msg), want=True)
    expect_bash("single-quoted -m with newlines, cd shape", "cd %s && git commit -m '%s'" % (repo, msg), want=True)
    expect_bash("double-quoted -m with embedded newlines", "git -C %s commit -m \"a\nb\"" % repo, want=True)
    expect_bash("$ and backtick inside single quotes", "git -C %s commit -m 'costs $5, see `x`'" % repo, want=True)
    expect_bash("escaped quote inside double quotes", "git -C %s commit -m \"say \\\"hi\\\"\"" % repo, want=True)
    expect_bash("message that looks like an option", "git -C %s commit -m '--amend'" % repo, want=True)

    # The user's commands (ceiling.allow_commands), each allowed only whole ------------
    expect_bash("a listed prefix, bare", "make handoff", want=True)
    expect_bash("a listed command with more words", "make handoff report --quiet", want=False)
    expect_bash("a listed command with one word more", "make handoff x", want=False)
    expect_bash("moxi repo rules with no argument", "moxi repo rules", want=True)
    expect_bash("moxi repo rules in the cd shape", "cd %s && moxi repo rules" % repo, want=True)
    expect_bash("moxi repo rules with a key", "moxi repo rules moxi", want=False)
    expect_bash("moxi repo rules with a path", "moxi repo rules %s" % repo, want=False)
    expect_bash("a <key> word filled by one key", "make trailers my-repo.2", want=True)
    expect_bash("a <key> word left empty", "make trailers", want=False)
    expect_bash("a <key> word and a word more", "make trailers my-repo extra", want=False)
    expect_bash("a <key> word filled by an option", "make trailers --all", want=False)
    expect_bash("a <key> word filled by a path", "make trailers ../x", want=False)
    expect_bash("a <key> word filled by the placeholder", "make trailers '<key>'", want=False)
    expect_bash("a listed prefix in the cd shape", "cd %s && make handoff" % repo, want=True)
    expect_bash("a listed prefix in the cd shape, ~/", "cd %s && make handoff" % repo_tilde, want=True)
    expect_bash("a quoted word that equals the prefix", "'make' handoff", want=True)
    expect_bash("the prefix's first word alone", "make", want=False)
    expect_bash("another word after the first", "make clean", want=False)
    expect_bash("a prefix word with $", "make $TARGET", want=False)
    expect_bash("a prefix then ;", "make handoff; ls", want=False)
    expect_bash("a prefix, cd outside every root", "cd %s && make handoff" % p["ELSE"], want=False)
    expect_bash("a prefix, cd to a sibling of cwd", "cd %s && make handoff" % p["OTHER"], want=False)
    check("no prefix listed refuses it",
          ask("Bash", {"command": "make handoff"}, current_dir, work_dirs, notes, commands=())[0], want_ok=False)
    check("an empty prefix is ignored",
          ask("Bash", {"command": "ls"}, current_dir, work_dirs, notes, commands=([],))[0], want_ok=False)

    # Refused git subcommands and options, in every shape -----------------------------------------
    refused_forms = [
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
    for form in refused_forms:
        for shape, cmd, cwd in shapes(form):
            if shape.startswith("-C") and form.startswith("-"):
                pass  # `git -C <dir> -c ...` is refused too; keep it
            expect_bash("%s [%s]" % (form, shape), cmd, want=False, cwd=cwd)
    expect_bash("git alone", "git", want=False)
    expect_bash("git -C with no directory", "git -C", want=False)
    expect_bash("git -C with no subcommand", "git -C %s" % repo, want=False)
    expect_bash("two -C options", "git -C %s -C %s status --short" % (repo, repo), want=False)
    expect_bash("git by full path", "/usr/bin/git -C %s status --short" % repo, want=False)
    expect_bash("command git", "command git -C %s status --short" % repo, want=False)
    expect_bash("env prefix", "GIT_DIR=x git -C %s status --short" % repo, want=False)

    # Repository directory outside cwd and every work root ----------------------------------------
    for d in (p["ELSE"], p["OTHER"], "../other", "/", os.path.dirname(work), p["T"]):
        expect_bash("-C outside every root: %s" % d, "git -C %s status --short" % d, want=False)
        expect_bash("cd outside every root: %s" % d, "cd %s && git status --short" % d, want=False)
    expect_bash("-C through a link out of the work root", "git -C %s/link-out status --short" % work, want=False)
    expect_bash("cd through a link out of the work root", "cd %s/link-out && git status --short" % work, want=False)
    expect_bash("-C with .. out of the work root", "git -C %s/../code/other status --short" % work, want=False)
    expect_bash("-C a sibling with the root as name prefix", "git -C %s2 status --short" % work, want=False)
    expect_bash("-C bare ~", "git -C ~ status --short", want=False)
    expect_bash("-C ~user", "git -C ~root/tmp status --short", want=False)
    expect_bash("-C a quoted ~ is literal, relative to cwd", "git -C '~/tmp' status --short", want=True)
    expect_bash("cd -", "cd - && git status --short", want=False)
    expect_bash("cd with an option", "cd -P %s && git status --short" % repo, want=False)
    expect_bash("cd with no directory", "cd && git status --short", want=False)
    expect_bash("cd alone", "cd %s" % repo, want=False)
    expect_bash("cd with nothing after &&", "cd %s &&" % repo, want=False)
    expect_bash("cd then another cd", "cd %s && cd x" % repo, want=False)
    expect_bash("bare git with cwd outside every root? cwd always passes", "git status --short", want=True, cwd=p["ELSE"])

    # add -- paths must resolve inside the repository directory -----------------------------------
    for path in ("../x.py", "../../../code/app/x.py", "/etc/passwd", p["ELSE"] + "/x",
                 "%s/link-out/x" % work, ":/x", ":(top)x", "':!x'", "ok.py ../x.py", "''"):
        expect_bash("add -- outside the repository: %s [-C]" % path, "git -C %s add -- %s" % (repo, path), want=False)
        expect_bash("add -- outside the repository: %s [cd]" % path, "cd %s && git add -- %s" % (repo, path), want=False)
    expect_bash("add -- absolute path inside the repository", "git -C %s add -- %s/hooks/x.py" % (repo, repo), want=True)
    expect_bash("add -- ~/ path inside the repository", "git -C %s add -- %s/x.py" % (repo, repo_tilde), want=True)
    expect_bash("add -- bare, path outside cwd", "git add -- ../other/x.py", want=False)
    expect_bash("add -- with a .. that stays inside", "git -C %s add -- hooks/../tests/y.py" % repo, want=True)

    # The shell text -------------------------------------------------------------------------------
    r = repo
    for label, cmd, why in [
        ("newline outside quotes", "git -C %s status --short\ngit -C %s diff" % (r, r), 'newline outside quotes'),
        ("trailing newline outside quotes", "git -C %s status --short\n" % r, 'newline outside quotes'),
        ("backslash-newline outside quotes", "git -C %s status \\\n--short" % r, 'backslash outside quotes'),
        (";", "git -C %s status --short; git -C %s diff" % (r, r), "control operator ';'"),
        (";, no space", "git -C %s status --short;ls" % r, "control operator ';'"),
        ("||", "git -C %s status --short || ls" % r, "control operator '|'"),
        ("|", "git -C %s diff | head" % r, "control operator '|'"),
        ("&", "git -C %s status --short &" % r, "control operator '&'"),
        ("& in the middle", "git -C %s status --short & ls" % r, "control operator '&'"),
        ("a second &&", "cd %s && git add -A && git commit -m x" % r, "a second '&&'"),
        ("&& not after cd", "git -C %s add -A && git -C %s commit -m x" % (r, r), 'only directly after cd'),
        ("&& after cd with two words", "cd %s x && git status --short" % r, 'only directly after cd'),
        ("redirection >", "git -C %s diff > /tmp/x" % r, 'redirection'),
        ("redirection >>", "git -C %s diff >> /tmp/x" % r, 'redirection'),
        ("redirection <", "git -C %s commit -m x < /dev/null" % r, 'redirection'),
        ("redirection 2>&1", "git -C %s status --short 2>&1" % r, 'redirection'),
        ("redirection &>", "git -C %s status --short &>/dev/null" % r, 'redirection'),
        ("here-string", "git -C %s commit -m x <<< y" % r, 'redirection'),
        ("$ unquoted", "git -C $HOME/tmp/builds/branch status --short", "'$' outside single quotes"),
        ("$ in double quotes", "git -C %s commit -m \"$(cat x)\"" % r, "'$' outside single quotes"),
        ("escaped $ in double quotes", "git -C %s commit -m \"\\$x\"" % r, "'$' outside single quotes"),
        ("${}", "git -C %s commit -m ${x}" % r, "'$' outside single quotes"),
        ("$'...'", "git -C %s commit -m $'a\\nb'" % r, "'$' outside single quotes"),
        ("backtick unquoted", "git -C %s commit -m `cat x`" % r, 'backtick outside single quotes'),
        ("backtick in double quotes", "git -C %s commit -m \"`cat x`\"" % r, 'backtick outside single quotes'),
        ("git -C after cd", "cd %s && git -C %s status --short" % (r, r), 'git -C after cd'),
        ("git -C after cd, same dir", "cd %s && git -C . status --short" % r, 'git -C after cd'),
        ("subshell", "(git -C %s status --short)" % r, 'subshell'),
        ("process substitution", "git -C %s commit -F <(echo x)" % r, 'redirection'),
        ("glob", "git -C %s add -- *.py" % r, "glob '*'"),
        ("glob ?", "git -C %s add -- x?.py" % r, "glob '?'"),
        ("glob [", "git -C %s add -- [ab].py" % r, "glob '['"),
        ("brace", "git -C %s add -- {a,b}.py" % r, "brace '{'"),
        ("brace in the directory", "git -C %s/{a,b} status --short" % work, "brace '{'"),
        ("comment", "git -C %s status --short #x" % r, 'comment'),
        ("backslash outside quotes", "git -C %s commit -m a\\ b" % r, 'backslash outside quotes'),
        ("unterminated single quote", "git -C %s commit -m 'x" % r, 'unterminated single quote'),
        ("unterminated double quote", "git -C %s commit -m \"x" % r, 'unterminated double quote'),
        ("carriage return outside quotes", "git -C %s status --short\r" % r, 'control character'),
        ("tilde mid-word", "git -C %s add -- a~/b" % r, 'tilde'),
        ("not a wrap-up command", "pytest -q", 'pytest is not a wrap-up command'),
        ("cat a source file", "cat %s/hooks/x.py" % r, 'cat is not a wrap-up command'),
        ("bash -c", "bash -c 'git status --short'", 'bash is not a wrap-up command'),
        ("empty command", "   ", 'no command'),
    ]:
        ok, reason = bash(cmd)
        check("bash refused: %s (reason: %s)" % (label, why), (ok, why in reason), (False, True), reason)

    # Write and Edit: Markdown under the hand-off root ---------------------------------------------
    def tool(name, inp, cwd=current_dir, roots=notes):
        return ask(name, inp, cwd, work_dirs, roots)


    def expect_tool(label, name, inp, want, cwd=current_dir, roots=notes):
        ok, reason = tool(name, inp, cwd, roots)
        check("%s %s: %s" % (name, "allowed" if want else "refused", label), ok, want, reason)


    report = p["REPORT"]
    for name in ("Write", "Edit"):
        extra = {"content": "x"} if name == "Write" else {"old_string": "a", "new_string": "b"}
        expect_tool("report under the hand-off root", name, dict(extra, file_path=report), want=True)
        expect_tool("new report under the hand-off root", name, dict(extra, file_path=notes + "/new/dir/r.md"), want=True)
        expect_tool("report path relative to cwd", name, dict(extra, file_path="../../notes/r.md"), want=True)
        expect_tool("hand-off roots as a list", name, dict(extra, file_path=report), want=True, roots=[p["ELSE"], notes])
        expect_tool("not Markdown under the root", name, dict(extra, file_path=notes + "/r.py"), want=False)
        expect_tool("Markdown suffix in the middle", name, dict(extra, file_path=notes + "/r.md.py"), want=False)
        expect_tool("Markdown outside the root", name, dict(extra, file_path=p["ELSE"] + "/r.md"), want=False)
        expect_tool("Markdown in the repository", name, dict(extra, file_path=repo + "/README.md"), want=False)
        expect_tool("Markdown in cwd", name, dict(extra, file_path=current_dir + "/README.md"), want=False)
        expect_tool(".. out of the root", name, dict(extra, file_path=notes + "/../code/app/x.md"), want=False)
        expect_tool("a .md link under the root leading outside", name, dict(extra, file_path=notes + "/link.md"), want=False)
        expect_tool("a .md name leading to source under the root", name, dict(extra, file_path=notes + "/src.md"), want=False)
        expect_tool("a plain name leading to a report", name, dict(extra, file_path=notes + "/alias"), want=False)
        expect_tool("a sibling with the root as name prefix", name, dict(extra, file_path=notes + "2/r.md"), want=False)
        expect_tool("no file_path", name, dict(extra), want=False)
        expect_tool("file_path not a string", name, dict(extra, file_path=7), want=False)
        expect_tool("no hand-off root", name, dict(extra, file_path=report), want=False, roots=[])
        expect_tool("relative hand-off root ignored", name, dict(extra, file_path=report), want=False, roots="notes")

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
        expect_tool(label, "Read", dict(inp, file_path=report), want)
    expect_tool("bounded read outside the root", "Read", {"file_path": p["ELSE"] + "/outside.md", "limit": 50}, want=False)
    expect_tool("bounded read of source in the repository", "Read", {"file_path": repo + "/hooks/x.py", "limit": 50}, want=False)
    expect_tool("bounded read of non-Markdown under the root", "Read", {"file_path": notes + "/x.txt", "limit": 50}, want=False)

    # The path rule: inside the root both lexically and resolved ----------------------
    e = p["ELSE"]
    s = repo + "/S"
    for shape, fmt in (("cd", "cd %s && git status --short"), ("-C", "git -C %s status --short")):
        expect_bash("%s: a link from outside every root, then .. (shell lands outside)" % shape,
                    fmt % (e + "/inlink/.."), want=False)
        expect_bash("%s: a link from outside every root into the work root" % shape, fmt % (e + "/inlink"), want=False)
        expect_bash("%s: a link inside the work root, then .. (stays inside)" % shape,
                    fmt % (work + "/inlink/.."), want=True)
        expect_bash("%s: a link inside the work root" % shape, fmt % (work + "/inlink"), want=True)
        expect_bash("%s: a link in the repository leading above it" % shape, fmt % (repo + "/up"), want=False)
    check("work root named through a link: its realpath is inside",
          ask("Bash", {"command": "git -C %s status --short" % repo}, current_dir,
                                [p["T"] + "/linkroot"], notes)[0], want_ok=True)
    check("work root named through a link: the link path is inside",
          ask("Bash", {"command": "git -C %s/linkroot/builds/branch status --short"
                                         % p["T"]}, current_dir, [p["T"] + "/linkroot"], notes)[0], want_ok=True)
    for shape, fmt in (("-C", "git -C %s add -- %%s" % s), ("cd", "cd %s && git add -- %%s" % s)):
        expect_bash("add -- through an in-dir link, .. lexically above <dir> [%s]" % shape,
                    fmt % "L/../../top.txt", want=False)
        expect_bash("add -- through an in-dir link, .. lexically inside <dir> [%s]" % shape, fmt % "L/../x", want=True)
        expect_bash("add -- an in-dir link [%s]" % shape, fmt % "L/x", want=True)
    expect_bash("add -- a link in the repository leading above it", "git -C %s add -- up/x" % repo, want=False)
    expect_bash("add -- a link leading above the repository, then ..", "git -C %s add -- up/.." % repo, want=False)
    expect_bash("add -- a link leading above the repository, cd shape", "cd %s && git add -- up/x" % repo, want=False)
    for name in ("Write", "Edit"):
        expect_tool("a link from outside the hand-off root into it", name, {"file_path": e + "/notelink/r.md"}, want=False)
        expect_tool("a link inside the hand-off root, then .. (stays inside)", name,
                    {"file_path": notes + "/dlink/../x.md"}, want=True)
        expect_tool("a link out of the hand-off root, then .. (lexically inside)", name,
                    {"file_path": notes + "/uplink/../outside.md"}, want=False)
    expect_tool("bounded read through a link from outside into the root", "Read",
                {"file_path": e + "/notelink/build-report.md", "limit": 50}, want=False)

    # Codex's shell tool: Bash with `command` alone, and a `workdir` a later Codex may send --
    def codex_bash(label, inp, want):
        ok, reason = ask("Bash", inp, current_dir, work_dirs, notes)
        check("codex Bash %s: %s" % ("allowed" if want else "refused", label), ok, want, reason)

    codex_bash("git -C status", {"command": "git -C %s status --short" % repo}, want=True)
    codex_bash("cd && commit", {"command": "cd %s && git commit -m 'hand off'" % repo}, want=True)
    codex_bash("cd && add -A", {"command": "cd %s && git add -A" % repo}, want=True)
    codex_bash("a user's command prefix", {"command": "make handoff"}, want=True)
    codex_bash("push", {"command": "git -C %s push" % repo}, want=False)
    codex_bash("workdir in the work root", {"command": "git status --short", "workdir": repo}, want=True)
    codex_bash("workdir under cwd", {"command": "git add -A", "workdir": "sub"}, want=True)
    codex_bash("workdir relative to cwd, into the work root",
               {"command": "git commit -m x", "workdir": repo_rel}, want=True)
    codex_bash("workdir outside every root", {"command": "git status --short", "workdir": p["ELSE"]}, want=False)
    codex_bash("workdir through a link out of the work root",
               {"command": "git status --short", "workdir": work + "/link-out"}, want=False)
    codex_bash("workdir not a string", {"command": "git status --short", "workdir": 7}, want=False)
    codex_bash("workdir empty", {"command": "git status --short", "workdir": ""}, want=False)
    codex_bash("cd from the workdir to a place outside every root",
               {"command": "cd %s && git status --short" % p["OTHER"], "workdir": repo}, want=False)
    codex_bash("cd from the workdir back under cwd",
               {"command": "cd %s && git status --short" % p["SUB"], "workdir": repo}, want=True)
    codex_bash("a relative cd joins the workdir",
               {"command": "cd .. && git status --short", "workdir": repo}, want=True)
    codex_bash("a relative -C joins the workdir",
               {"command": "git -C S add -- L/x", "workdir": repo}, want=True)
    codex_bash("add -- reads its paths from the workdir",
               {"command": "git add -- up/x", "workdir": repo}, want=False)
    codex_bash("command as an argv list", {"command": ["git", "status", "--short"]}, want=False)

    # Codex's apply_patch: adds and updates of Markdown under the hand-off root -------------
    def patch(*sections, begin="*** Begin Patch\n", end="*** End Patch\n"):
        return begin + "".join(sections) + end

    def add(path, *lines):
        return "*** Add File: %s\n" % path + "".join("+%s\n" % line for line in lines)

    def update(path, *lines):
        return "*** Update File: %s\n" % path + "".join("%s\n" % line for line in lines)

    def expect_patch(label, text, want, cwd=current_dir, key="command"):
        ok, reason = ask("apply_patch", {key: text}, cwd, work_dirs, notes)
        check("apply_patch %s: %s" % ("allowed" if want else "refused", label), ok, want, reason)
        if not ok and not reason:
            results.append(("apply_patch refusal has a reason: " + label, False, ""))

    new = notes + "/designs/r/build/next.md"
    change = ("@@", "-old line", "+new line")
    expect_patch("add a report under the root", patch(add(new, "# Hand-off", "", "done")), want=True)
    expect_patch("add an empty report", patch(add(new)), want=True)
    expect_patch("add in a new directory", patch(add(notes + "/new/dir/r.md", "x")), want=True)
    expect_patch("add relative to cwd", patch(add("../../notes/r.md", "x")), want=True)
    expect_patch("update the report", patch(update(report, *change)), want=True)
    expect_patch("update with no @@ first", patch(update(report, " context", "+added")), want=True)
    expect_patch("update with two chunks and End of File",
                 patch(update(report, "@@ ## Done", "-a", "+b", "@@", "+c", "*** End of File")), want=True)
    expect_patch("update whose context line is a Markdown rule", patch(update(report, " ***", "+x")), want=True)
    expect_patch("add and update, both under the root", patch(add(new, "x"), update(report, *change)), want=True)
    expect_patch("CRLF line ends", patch(add(new, "x")).replace("\n", "\r\n"), want=True)
    expect_patch("blank lines around the patch", "\n\n" + patch(add(new, "x")) + "\n  \n", want=True)
    expect_patch("padded markers", patch(add(new, "x"), begin="  *** Begin Patch \n",
                                         end=" *** End Patch"), want=True)
    expect_patch("a link inside the root, then ..", patch(add(notes + "/dlink/../x.md", "x")), want=True)
    expect_patch(".. that stays inside the root", patch(add(notes + "/designs/../x.md", "x")), want=True)
    expect_patch("not Markdown", patch(add(notes + "/r.py", "x")), want=False)
    expect_patch("one Markdown, one not", patch(add(new, "x"), add(notes + "/r.py", "x")), want=False)
    expect_patch("update of a source file", patch(update(repo + "/hooks/x.py", *change)), want=False)
    expect_patch("Markdown outside the root", patch(add(p["ELSE"] + "/r.md", "x")), want=False)
    expect_patch("Markdown in the repository", patch(add(repo + "/README.md", "x")), want=False)
    expect_patch("relative Markdown in cwd", patch(add("README.md", "x")), want=False)
    expect_patch("~/ is not expanded: a path under cwd",
                 patch(add("~/" + "notes/r.md", "x")), want=False)
    expect_patch(".. out of the root", patch(add(notes + "/../code/app/x.md", "x")), want=False)
    expect_patch("relative .. out of the root", patch(add("../../notes/../x.md", "x")), want=False)
    expect_patch("a .md link under the root leading outside", patch(update(notes + "/link.md", *change)), want=False)
    expect_patch("a directory link under the root leading outside",
                 patch(add(notes + "/uplink/new.md", "x")), want=False)
    expect_patch("a link out of the root, then .. (lexically inside)",
                 patch(add(notes + "/uplink/../outside.md", "x")), want=False)
    expect_patch("a link from outside into the root", patch(add(e + "/notelink/r.md", "x")), want=False)
    expect_patch("a .md name leading to source", patch(update(notes + "/src.md", *change)), want=False)
    expect_patch("a sibling with the root as name prefix", patch(add(notes + "2/r.md", "x")), want=False)
    for label, text, why in [
        ("delete a report", patch("*** Delete File: %s\n" % report), "deletes"),
        ("delete after an add", patch(add(new, "x"), "*** Delete File: %s\n" % report), "deletes"),
        ("an indented delete after an add", patch(add(new, "x"), "   *** Delete File: %s\n" % report),
         "deletes"),
        ("move within the root", patch(update(report, "*** Move to: %s" % new, *change)), "moves"),
        ("move out of the root", patch(update(report, "*** Move to: %s/r.md" % p["ELSE"], *change)),
         "moves"),
        ("move after a change", patch(update(report, *change), "*** Move to: %s\n" % new), "moves"),
        ("no begin marker", patch(add(new, "x"), begin=""), "Begin Patch"),
        ("no end marker", patch(add(new, "x"), end=""), "Begin Patch"),
        ("a heredoc wrapper", "<<'EOF'\n" + patch(add(new, "x")) + "EOF\n", "Begin Patch"),
        ("a shell call around the patch", "apply_patch <<'EOF'\n" + patch(add(new, "x")) + "EOF\n",
         "Begin Patch"),
        ("nothing between the markers", patch(), "changes no file"),
        ("a line before the first section", patch("+x\n", add(new, "x")), "not a line of a file section"),
        ("an add line without +", patch(add(new, "x"), "y\n"), "not a line of a file section"),
        ("a blank line in an add", patch(add(new, "x"), "\n"), "not a line of a file section"),
        ("an unknown header", patch("*** Copy File: %s\n" % new), "not a line of a file section"),
        ("a header with no space", patch("*** Add File:%s\n+x\n" % new), "not a line of a file section"),
        ("an add with no path", patch("*** Add File: \n+x\n"), "not a line of a file section"),
        ("an update with no change", patch(update(report)), "no change"),
        ("an update with no change before the next", patch(update(report), add(new, "x")), "no change"),
        ("End of File with no change", patch(update(report, "*** End of File")), "no change"),
        ("a stray line in an update", patch(update(report, "@@", "x")), "not a change line"),
        ("a tab-led line in an update", patch(update(report, "@@", "\t+x")), "not a change line"),
        ("only whitespace", "  \n ", "no patch text"),
        ("empty", "", "no patch text"),
    ]:
        ok, reason = ask("apply_patch", {"command": text}, current_dir, work_dirs, notes)
        check("apply_patch refused: %s (reason: %s)" % (label, why), (ok, why in reason),
              (False, True), reason)
    expect_patch("the text under another key", patch(add(new, "x")), want=False, key="input")
    expect_patch("the text under patch", patch(add(new, "x")), want=False, key="patch")
    expect_patch("command not a string", ["*** Begin Patch"], want=False)
    check("apply_patch with a workdir is refused, its paths resolving elsewhere",
          ask("apply_patch", {"command": patch(add("../../notes/r.md", "x")), "workdir": current_dir},
              current_dir, work_dirs, notes)[0], want_ok=False)
    expect_patch("cwd relative", patch(add("../../notes/r.md", "x")), want=False, cwd="code/app")
    check("apply_patch with no hand-off root",
          ask("apply_patch", {"command": patch(add(new, "x"))}, current_dir, work_dirs, [])[0], want_ok=False)
    check("apply_patch input not an object",
          ask("apply_patch", patch(add(new, "x")), current_dir, work_dirs, notes)[0], want_ok=False)


    # Every other tool is refused -----------------------------------------------------------------
    for name, inp in [
        ("Glob", {"pattern": "**/*.md"}), ("Grep", {"pattern": "x"}), ("Agent", {"prompt": "x"}),
        ("Task", {"prompt": "x"}), ("Skill", {"skill": "commit"}), ("WebFetch", {"url": "x"}),
        ("WebSearch", {"query": "x"}), ("NotebookEdit", {"notebook_path": notes + "/x.md"}),
        ("MultiEdit", {"file_path": report, "edits": []}), ("mcp__x__y", {}), ("ToolSearch", {"query": "x"}),
        ("", {}), (None, {}),
    ]:
        expect_tool("%s is not a wrap-up operation" % name, name, inp, want=False)

    # Malformed arguments never allow -------------------------------------------------------------
    check("tool_input not an object", ask("Bash", "git status --short", current_dir, work_dirs, notes)[0], want_ok=False)
    check("tool_input None", ask("Write", None, current_dir, work_dirs, notes)[0], want_ok=False)
    check("command not a string", bash(["git", "status", "--short"])[0], want_ok=False)
    check("cwd relative", bash("git status --short", "code/app")[0], want_ok=False)
    check("cwd None", bash("git -C %s status --short" % repo, None)[0], want_ok=False)
    check("work_roots None still allows cwd", ask("Bash", {"command": "git status --short"}, current_dir, None, notes)[0], want_ok=True)
    check("work_roots None refuses the worktree", ask("Bash", {"command": "git -C %s status --short" % repo}, current_dir, None, notes)[0], want_ok=False)
    check("relative work root ignored", ask("Bash", {"command": "git -C %s status --short" % repo}, current_dir, ["home/tmp"], notes)[0], want_ok=False)
    ok, reason = bash("git -C %s push" % repo)
    check("the refusal names the rule", (ok, "push" in reason), (False, True), reason)
    check("the allow is (True, \"\")", bash("git -C %s status --short" % repo), (True, ""))
    return results


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


def test_a_patch_path_loses_only_unicode_white_space_at_its_ends():
    # A header line is trimmed as Rust's `trim` does: the wide spaces after the path go,
    # and no letter, digit or backslash the path ends in.
    padded = "notes/a.conf\u205f\u202f\u2029\u2028\u3000"
    text = ("*** Begin Patch\n"
            f"*** Add File: {padded}\n+x\n"
            "*** Add File: notes/2025\\u\n+x\n"
            "*** End Patch\u3000\n")
    assert patch_files(text) == ["notes/a.conf", "notes/2025\\u"]

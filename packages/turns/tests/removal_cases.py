"""The removal guard's cases: data only, read by `test_shipped_guards.py`.

Each case is `(label, command, expect)`. `expect` is "pass", or "T" when the guard
refuses with rule T's reason. No command is ever run.

The rows are those of the shell hook the guard was ported from, run against Claude Code
2.1.289: what Claude Code's own removal check stops for approval, and what the guard
refuses. The guard keeps rule T alone, so a row Claude Code stops for another reason (a
path it can't read, the working directory, a shell's -c script) passes here: those stops
are left to a guard at the permission ask step.

The heredoc rows are the later hook's, which cuts a file-writing heredoc's body before its
rule reads the command; the guard makes the same cut.
"""

CWD = "/home/user/proj/agents"
HOME = "/home/user"
X = "/home/user/tmp/x"
# A cwd like Claude Code's own scratchpad: under /tmp, with a
# part led by `-`, its last parts a/b/c.
SP = "/tmp/claude-1000/-home-user-proj/session/scratchpad"
SPC = SP + "/w-p/a/b/c"
CASES = []


def case(label, cmd, expect):
    CASES.append((label, cmd, expect))


def subs(opener, closer, n):
    return "echo " + " ".join(opener + "true" + closer for _ in range(n))


# Claude Code stops, the guard refuses (rows 1 to 22).
case("row 1", "mkdir -p s && cd s && rm -rf * && for r in x y; do mkdir $r; done", "T")
case("row 2", "cd t && rm -rf *", "T")
case("row 3", "cd t; rm -rf *", "T")
case("row 4", "(cd t && rm -rf *)", "T")
# Rows 5a and 5b run the same text in a shell's -c script.
case("row 5a", "bash -c 'cd t && rm -rf *'", "T")
case("row 5b", "bash -lc 'cd t && rm -rf *'", "T")
case("row 6", "pushd t && rm -rf *", "T")
case("row 7a", "cd t && rm -rf ./*", "T")
case("row 7b", "cd t && rm -rf sub/*", "T")
case("row 7c", "cd t && rm *", "T")
case("row 7d", "cd t && rmdir *", "T")
case("row 8", "rm -rf t/sub/* && cd t", "T")
case("row 9", 'cd t && rm -rf "$(pwd)"/*', "T")
case("row 10", "cd /full/path/t && rm -rf *", "T")
case("row 11a", "cd t && rm -rf sub/*/.", "T")
case("row 11b", "cd t && rm -rf sub/*/x/..", "T")
case("row 12a", "builtin cd t && rm -rf *", "T")
case("row 12b", "nice cd t && rm -rf sub/*", "T")
case("row 13a", "cd t && env rm -rf *", "T")
case("row 13b", "cd t && /usr/bin/env rm -rf sub/*", "T")
case("row 13c", "cd t && timeout 5 rm -rf sub/*", "T")
case("row 14", "x=`cd t`; rm -rf sub/*", "T")
case("row 15", 'x="$(cd t && pwd)"; rm -rf sub/*', "T")
case("row 16", 'cd t && echo "$(rm -rf sub/*)"', "T")
case("row 17", "cat <<EOF\n$(cd t && rm -rf sub/*)\nEOF", "T")
case("row 18", "x=$((1<<2))\ncd t\nrm -rf sub/*", "T")
case("row 19", r"echo $'it\'s'; cd t && rm -rf sub/*", "T")
case("row 20", "[ $# -eq 0 ] && cd t && rm -rf *", "T")
case("row 21 (second scan)", "c''d t && rm -rf sub/*", "T")
case("row 22 (second scan)", r"cd t && r\m -rf sub/*", "T")

# Rule T refuses more than Claude Code stops (rows 23 to 31): Claude Code runs these.
# Row 23's body is a quoted heredoc fed to cat, which the heredoc cut takes out.
case("row 23", "cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case("row 24", 'git commit -m "cd x && rm *"', "T")
case("row 25a", "cd t && rm -f *.txt", "T")
case("row 25b", "cd t && rm -rf s*", "T")
case("row 25c", "cd t && rm -rf sub/**", "T")
case("row 26a", "cd /full/path/t && rm -rf sub/*", "T")
case("row 26b", "cd ./t && rm -rf sub/*", "T")
case("row 27", "cd t && cd sub && rm -rf x/*", "T")
case("row 28", 'cd t && rm -rf "$FOO"/x/*', "T")
case("row 29", "cd t && rm -rf build && ls *.py", "T")
case("row 30", "eval 'cd t && rm -rf *'", "T")
case("row 31", "for d in t; do (cd $d && rm -rf *); done", "T")

# Claude Code runs, the guard passes (rows 32 to 37).
case("row 32a", "cd t && touch f", "pass")
case("row 32b", "cd t && echo hi > f", "pass")
case("row 32c", "cd t && mkdir x && cp a x/ && mv b.txt c.txt", "pass")
case("row 33", "cd t && rm -rf sub", "pass")
case("row 34", "cd t && ls | xargs rm -rf", "pass")
case("row 35", "rm -rf t/*", "pass")
case("row 36", "cd t && find . -mindepth 1 -delete", "pass")
case("row 37", r"cd t && $'\x72m' -rf sub/*", "pass")

# Claude Code stops, the guard passes: no cd (rows 38 to 53m).
case("row 38", "rm -rf *", "pass")
case("row 39", "rm -rf ./*", "pass")
case("row 40a", "rmdir .", "pass")
case("row 40b", "rmdir ..", "pass")
case("row 41", "rm -rf " + CWD + "/*", "pass")
case("row 42a", "rm -rf ../..", "pass")
case("row 42b", "rm -rf t/..", "pass")
case("row 42c", "rm -rf ./*/..", "pass")
case("row 42d", "rm -rf $HOME/..", "pass")
for i, w in enumerate(["**", "***", "./**", "*/*", "./*/*", "./*/*/*", "*//*", "./*/./*", "./**/*"]):
    case("row 43." + str(i + 1), "rm -rf " + w, "pass")
case("row 44a", "rm -rf " + CWD + "/**", "pass")
case("row 44b", "rmdir //home/user/proj", "pass")
case("row 45", "rm -rf ~", "pass")
case("row 46a", "rmdir /zzz-not-there", "pass")
case("row 46b", "rm -rf /tmp/*", "pass")
for i, w in enumerate(["../*", "../x/*", "./*/", "*/", "*//", "t/../*"]):
    case("row 47." + str(i + 1), "rm -rf " + w, "pass")
case("row 48a", "rm -rf /x/sub/../*", "pass")
case("row 48b", "rm -rf //x/sub/*", "pass")
case("row 48c", "rmdir -p /x/sub/*", "pass")
case("row 49", "rm -rf ${PWD}/*", "pass")
case("row 50a", "rm -rf t/*/*", "pass")
case("row 50b", "rm -rf s*/*", "pass")
case("row 50c", "rm -rf s1/x/*/*", "pass")
case("row 51a", "rm -rf /x/sub/*/*", "pass")
case("row 51b", "rm -rf /x/s?/*", "pass")
case("row 52a", 'rm -rf "$FOO"/tmp', "pass")
case("row 52b", "rm -rf ${X}/usr/*", "pass")
case("row 53a", r"rm -f \\", "pass")
case("row 53b", r"rm -f '\'", "pass")
case("row 53a.1", "rm -rf ~+/t/sub/*", "pass")
case("row 53a.2", "rm -rf ~zzznouser/x/*", "pass")
case("row 53a.3", "rm -rf ~zzznouser/*", "pass")
case("row 53b.1", "rm -rf '" + CWD + "\\*'", "pass")
case("row 53b.2", "rm -rf '" + CWD + "/t\\..\\*'", "pass")
case("row 53c", 'rm -rf "../my dir/*"', "pass")
case("row 53d", "rm -rf /elsewhere-zz/*", "pass")
case("row 53e.1", "rm -rf $A$B/tmp", "pass")
case("row 53e.2", "rm -rf $FOO//tmp/**", "pass")
case("row 53e.3", "rmdir -p sub/*", "pass")
# Rows 53f to 53j: Claude Code compares paths case-folded, with /private mapped.
case("row 53f.1", "rmdir ../C", "pass")
case("row 53f.2", "rmdir " + SP + "/w-p/a/b/C", "pass")
case("row 53f.3", "rmdir " + SP + "/w-p/a/B", "pass")
case("row 53f.4", "rmdir " + SPC.upper(), "pass")
case("row 53f.5", "rm -rf ../C/x/..", "pass")
case("row 53g", "rmdir " + SP.replace("/scratchpad", "/\u017fcratchpad") + "/w-p/a/b", "pass")
case("row 53h.1", "rmdir /private" + SP + "/w-p/a/b", "pass")
case("row 53h.2", "rmdir /private/tmp", "pass")
case("row 53i.1", "rmdir /home/USER", "pass")
case("row 53i.2", "rmdir /HOME/user", "pass")
case("row 53j.1", "rm -rf " + SP + "/w-p/a/B/*", "pass")
case("row 53j.2 (plain path)", "rm -rf /var/tmp/w/a/B/*", "pass")
# Rows 53k to 53m (non-ASCII and the split class): Claude Code trims the target with
# JavaScript's whitespace set and U+FEFF.
PARENT = SP + "/w-p/a/b"
case("row 53k.1 (<parent> U+FEFF)", "rmdir " + PARENT + "\ufeff", "pass")
case("row 53k.2 (.. U+FEFF)", "rmdir ..\ufeff", "pass")
case("row 53k.3 (<cwd> U+FEFF)", "rmdir " + SPC + "\ufeff", "pass")
case("row 53k.4 (<parent>/../b U+FEFF)", "rmdir " + PARENT + "/../b\ufeff", "pass")
case("row 53k.5 (/tmp/claude-1000/ U+FEFF)", "rmdir /tmp/claude-1000/\ufeff", "pass")
case("row 53l (<parent> U+3000)", "rmdir " + PARENT + "\u3000", "pass")
case("row 53m.1", "rmdir ..;ls", "pass")
case("row 53m.2", "rm -rf ..&&ls", "pass")

# Claude Code stops, the guard passes: more than 64 substitutions (rows 54, 55).
case("row 54", subs("$(", ")", 65) + "; rm -f nofile.txt", "pass")
case("row 55", subs("$(", ")", 65) + " && rm -f nofile.txt", "pass")

# Claude Code runs, the guard passes (rows 56 to 59d).
case("row 56", "rm -rf /home/user/tmp/x/*", "pass")
case("row 57a", "mkdir -p ~/tmp/x/b && rm -rf ~/tmp/x/*", "pass")
case("row 57b", 'rm -rf "$HOME"/tmp/x/*', "pass")
case("row 58", "rm -rf /x/sub/**", "pass")
case("row 59a", "rm -f /x/a.txt", "pass")
case("row 59b", "rm -rf " + CWD + "/sub", "pass")
case("row 59a.1", "rm -rf sub/*", "pass")
case("row 59a.2", "rm -rf t/x/*", "pass")
case("row 59b.1", "rm -rf /elsewhere-zz/a/b/*", "pass")
case("row 59b.2", "rm -rf ~/.cache/x/*", "pass")
case("row 59b.3", "rmdir {.,..}", "pass")
case("row 59c.1 (scratchpad glob)", "rm -rf " + SP + "/x/*", "pass")
case("row 59c.2 (another scratchpad glob)", "rm -rf " + SP + "/w-q/x/*", "pass")
case("row 59d.1", "rmdir C:/", "pass")
case("row 59d.2", "rmdir C:/x", "pass")

# Claude Code runs, the guard passes (rows 61 to 71).
case("row 61", "rm -rf ./sub/*", "pass")
case("row 62", "rm -f *.txt", "pass")
case("row 63", "find . -name '*.o' | xargs rm", "pass")
case("row 64", "git rm -r --cached .", "pass")
case("row 65", "git -C " + CWD + " log && rm -f tmp.txt", "pass")
case("row 66", "cd .. && rm -f x.txt", "pass")
case("row 67", "rm -rf /home/user/tmp/x/**/*.o", "pass")
case("row 68a", 'rm -f "$TMPFILE"', "pass")
case("row 68b", 'for f in $(ls); do rm "$f"; done', "pass")
case("row 69a", "rm -rf 'sub/*'", "pass")
case("row 69b", "rm -rf {a,b}/*", "pass")
case("row 69a (<parent> U+200B, Claude Code runs it)", "rmdir " + PARENT + "\u200b", "pass")
case("row 69b.1 (non-ASCII filename)", "rm -f caf\u00e9.txt", "pass")
case("row 69b.2 (an em dash in another word)", "git commit -m 'fix \u2014 dash' && rm -f x.txt", "pass")
case("row 70", subs("$(", ")", 10) + "; rm -f nofile.txt", "pass")
case("row 71", "rm -rf tmp/x/*", "pass")

# Rule T's words and its two scans.
case("T: chdir", "chdir t && rm -rf sub/*", "T")
case("T: popd", "popd && rm -rf sub/*", "T")
case("T: /bin/rm counts", "cd t && /bin/rm -rf sub/*", "T")
case("T: quoted rm counts", 'cd t && "rm" -rf sub/*', "T")
case("T: --rm counts", "docker run --rm img; cd t; ls sub/*", "T")
case("T: cdx is not cd", "cdx t && rm -rf sub/*", "pass")
case("T: cd_x is not cd", "cd_x t && rm -rf sub/*", "pass")
case("T: rmx is not rm", "cd t && rmx sub/*", "pass")
case("T: arm is not rm", "cd t && arm sub/*", "pass")
case("T: no star", "cd t && rm -rf sub", "pass")
case("T: all three must be in one scan", "c''d t && rm'x' -rf sub/*", "pass")
case("T: backslash-split cd", r"c\d t && rm -rf sub/*", "T")

# A shell's -c script: rule T reads it as any other text, so only a cd, an rm and a * in
# it refuse.
# A long command that once stalled a session, its paths changed.
STALLED = r'''cd ~/tmp/verify && P9=~/tmp/scratch/py/cpython-3.9.25-linux-x86_64-gnu/bin/python3.9; P10=~/tmp/scratch/py/cpython-3.10.20-linux-x86_64-gnu/bin/python3.10; SHIM=/home/user/tmp/work/server/shim.py
IN='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"resources/list"}'
for P in $P9 $P10; do
 echo "== $P inert, no mode"; printf '%s\n' "$IN" | timeout 10 env -i PATH=/usr/bin:/bin HOME=$HOME/tmp/verify $P $SHIM; echo "exit $?"
 echo "== $P mode plugin, wrong parent"; printf '%s\n' "$IN" | timeout 10 env -i PATH=/usr/bin:/bin HOME=$HOME/tmp/verify SHIM_MODE=plugin SHIM_LAUNCH_PID=1 $P $SHIM 2>&1 | head -3; echo "exit ${PIPESTATUS[1]}"
 echo "== $P mode plugin, launched parent"; timeout 10 env -i PATH=/usr/bin:/bin HOME=$HOME/tmp/verify SHIM_MODE=plugin bash -c 'export SHIM_LAUNCH_PID=$$; "$0" "$1" </dev/null; echo "child exit $?"' $P $SHIM 2>&1
done
echo "== ast 3.9 parse"; $P9 -c "import ast,sys; ast.parse(open('$SHIM').read()); print('parses on', sys.version.split()[0])"'''

# Claude Code stops (rows 1 to 15b).
case("shell-c row 1 (the stalled command)", STALLED, "pass")
case("shell-c row 2", "cd . && X=/bin/echo; bash -c 'true; \"$0\" hi' $X", "pass")
case("shell-c row 3", "bash -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 1", "timeout 10 bash -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 2", "env FOO=1 bash -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 3", "nice sh -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 4", "bash -lc '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 5", "bash -ec '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 6", "/bin/bash -c '\"$X\" \"$1\"' _ a", "pass")
case("shell-c row 5", "bash -c 'mkdir -p " + CWD + "/m/{a,b}; rm -f zz'", "pass")
case("shell-c row 6", "bash -c 'mkdir -p " + CWD + "/m/{a,b}; r\"\"m -f zz'", "pass")
case("shell-c row 7", "bash --foo -c 'rm -f zz'", "pass")
case("shell-c row 8", "bash -c 'r{m,}dir zz'", "pass")
case("shell-c row 9", r"bash -c $'echo \x68i'", "pass")
case("shell-c row 11", "bash -c 'cd t && rm -rf *'", "T")
case("shell-c row 12", "bash -c \"bash -c \\\"bash -c 'rm -f zz'\\\"\"", "pass")
case("shell-c row 13", "bash -c 'rm -rf \"$1\"/x' _ \"$D\"", "pass")
case("shell-c row 14 (a script over 2000 characters with rm)", "bash -c 'echo " + "x" * 2000 + "; rm -f zz'", "pass")
case("shell-c row 15a", 'Q=r; bash --foo -c "/${Q}m zz"', "pass")
case("shell-c row 15b", "cd . && Z=1; for i in 1\ndo\nX=\"a b\" $Y\nbash -c 'echo hi' $Z\ndone", "pass")
# Claude Code stops (row 10).
case("shell-c row 10 (nine groups)", "( ( ( ( ( ( ( ( ( echo {a,b} ) ) ) ) ) ) ) ) )", "pass")
# Claude Code runs (rows 16 to 23b).
case("shell-c row 16", "bash -c 'rm -f " + CWD + "/zz'", "pass")
case("shell-c row 17, form 1", "bash -c 'echo {a,b}'", "pass")
case("shell-c row 17, form 2", "bash -c 'mkdir -p " + CWD + "/m/{a,b}'", "pass")
case("shell-c row 17 (jq in a script)", "bash -c \"jq -c '{a,b}' f.json\"", "pass")
case("shell-c row 18, form 1", "bash -c '\"$0\" hi' /bin/echo", "pass")
case("shell-c row 18, form 2", "X=/bin/echo; bash -c 'true; \"$0\" hi' $X", "pass")
case("shell-c row 18, form 3", "X=/bin/echo; bash -c 'X=/bin/echo; $X hi'", "pass")
case("shell-c row 19, form 1", "for P in /bin/echo; do bash -c '\"$0\" hi' $P; done", "pass")
case("shell-c row 19, form 2", "for f in a; do bash -c 'echo hi'; rm -f zz; done", "pass")
case("shell-c row 19, form 3", "for P in /bin/echo; do $P hi; bash -c 'echo x'; done", "pass")
case("shell-c row 20", "cd . && X=/bin/echo; bash -c 'true; echo hi' $X", "pass")
case("shell-c row 21 ($HOME)", "sh -c 'echo $HOME'", "pass")
case("shell-c row 21 (loop)", "bash -c 'for f in a b; do echo $f; done'", "pass")
case("shell-c row 21 (find -exec)", "find . -name '*.txt' -exec bash -c 'echo \"$1\"' _ {} \\;", "pass")
case("shell-c row 21 (exec $@)", "sh -c 'exec \"$@\"' sh ls", "pass")
case("shell-c row 22", "echo a | xargs bash -c '$X \"$1\"' _", "pass")
case("shell-c row 23 (rm in another command)", "bash -c 'echo hi' && rm -f /home/user/tmp/x/a.txt", "pass")
case("shell-c row 23 (rm in a string)", "git commit -m 'note: bash -c with rm inside'", "pass")
case("shell-c row 23a (tail -c)", "bash tests/hooks.sh 2>&1 | tail -c 2000; echo \"exit ${PIPESTATUS[0]}\"", "pass")
case("shell-c row 23a (wc -c)", "bash x.sh | wc -c; echo $?", "pass")
case("shell-c row 23b (a heredoc that writes a test)", "cat > t.sh <<'EOF'\nbash -c 'echo \"$1\"' _ hi\nEOF", "pass")
# Claude Code runs (rows 24 to 29).
case("shell-c row 24", "bash -c 'echo hi'", "pass")
case("shell-c row 24 (timeout)", "timeout 10 bash -c 'make test'", "pass")
case("shell-c row 24 (-lc)", "bash -lc 'git status'", "pass")
case("shell-c row 24 (-xc)", "bash -xc 'echo hi' 2>&1 | head", "pass")
case("shell-c row 25", "bash " + CWD + "/script.sh", "pass")
case("shell-c row 26", "python3 -c \"print({1,2}, {'a': 1, 'b': 2})\"", "pass")
case("shell-c row 26 (uv run)", "uv run python -c \"print({1,2})\"", "pass")
case("shell-c row 26 (.sh is not a shell name)", "python3 -c \"print({1,2})\" && ./build.sh", "pass")
case("shell-c row 26 (multi-line python3 -c)", "python3 -c \"\nimport os\nfor k in {'a', 'b'}:\n    print(os.environ.get(k, '$'))\n\"", "pass")
case("shell-c row 26 (multi-line uv run python -c)", "uv run python -c \"\nx = {1, 2}\nprint(x)\n\"", "pass")
case("shell-c row 26 (multi-line node -e)", "node -e \"\nconst o = {a: 1, b: 2};\nconsole.log(`${o.a}`);\n\"", "pass")
case("shell-c row 26 (a heredoc that writes Python)", "cat > x.py <<'EOF'\nimport sys\nprint({1, 2}, f'{sys.argv}')\nEOF", "pass")
case("shell-c row 26 (a heredoc that writes Markdown)", "cat > n.md <<'EOF'\n# Notes\nThe cost is $5 for {a,b}.\nEOF", "pass")
case("shell-c row 26 (python3's -c beside a shell name counts)", "python3 -c \"print({1,2})\" && bash x.sh", "pass")
case("shell-c row 27", "$SHELL -c '$X \"$1\"' _ a", "pass")
case("shell-c row 28 (xargs -I{})", "ls | xargs -I{} bash -c 'echo {}'", "pass")
case("shell-c row 28 (jq -c)", "jq -c '{a,b}' x.json", "pass")
case("shell-c row 28 (bash -n)", 'for f in *.sh; do bash -n "$f"; done', "pass")
case("shell-c row 29 (eight groups)", "( ( ( ( ( ( ( ( echo {a,b} ) ) ) ) ) ) ) )", "pass")
# A shell name or -c built from a variable.
case("shell-c built name (shell name built)", "S=bas; Z=1 ${S}h -c '$X \"$1\"' _ a", "pass")
case("shell-c built name (sh after } is a shell name)", "S=ba; Z=1 ${S}sh -c '$X \"$1\"' _ a", "pass")
case("shell-c built name (-c built)", "F=c; Z=1 bash -$F '$X \"$1\"' _ a", "pass")
case("shell-c built name (S=bash; $S -c)", "S=bash; $S -c 'echo hi'", "pass")

# The heredoc cut: the body of a quoted heredoc fed to cat, tee, python3 - or node - is cut
# before the rule reads the command. These pass; the "heredoc kept" rows below are still
# read whole, and refuse as they did before the cut.
case("heredoc: cat > f <<'EOF'", "cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: cat > f <<"EOF"', 'cat > s.sh <<"EOF"\ncd x\nrm -rf *\nEOF', "pass")
case('heredoc: cat >> f', "cat >> s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case("heredoc: cat <<'EOF' > f", "cat <<'EOF' > s.sh\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: a space before the delimiter', "cat > s.sh << 'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: \\EOF is quoted', 'cat > s.sh <<\\EOF\ncd x\nrm -rf *\nEOF', "pass")
case('heredoc: tee', "tee s.sh <<'EOF' >/dev/null\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: tee -a', "tee -a s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: /usr/bin/cat', "/usr/bin/cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: an assignment before cat', "LC_ALL=C cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: python3 -', "python3 - <<'PY'\nimport os\nos.chdir('t')\nos.system('rm -rf *')\nPY", "pass")
case('heredoc: python3 -I - with an argument', "python3 -I - /home/user/proj/agents <<'PY'\nos.chdir('t'); os.system('rm -rf *')\nPY", "pass")
case('heredoc: node -', "node - <<'JS'\nprocess.chdir('t'); run('rm -rf *')\nJS", "pass")
case('heredoc: <<- with tab-indented closing line', "cat > s.sh <<-'EOF'\n\tcd x\n\trm -rf *\n\tEOF", "pass")
case('heredoc: after a cd elsewhere on the line, no rm or star outside', "cd t && cat > s.sh <<'EOF'\nrm -rf *\nEOF", "pass")
case('heredoc: two file bodies', "cat > a <<'A'\ncd x && rm -rf *\nA\ncat > b <<'B'\nrm *\nB", "pass")
case('heredoc: two on one line', "cat > a <<'A'; cat > b <<'B'\ncd x\nA\nrm -rf *\nB", "pass")
case('heredoc: inside $( )', "x=$(cat <<'EOF'\ncd x\nrm -rf *\nEOF\n)", "pass")
case("heredoc: after $'...' with an escaped quote", "echo $'it\\'s'; cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: after a comment with an apostrophe', "# don't\ncat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: an unquoted one before it holds an apostrophe', "cat > a <<A\ndon't\nA\ncat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: then && bash on the same line', "cat > s.sh <<'EOF' && bash s.sh\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: { before cat', "{ cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF\n}", "pass")
case('heredoc: ! before cat', "! cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: do before cat', "for f in a; do cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF\ndone", "pass")
case('heredoc: then before cat', "if true; then cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF\nfi", "pass")
case('heredoc: else before cat', "if false; then :; else cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF\nfi", "pass")
case('heredoc: time before cat', "time cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc: leading words and an assignment together', "! time LC_ALL=C cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "pass")
case('heredoc kept: unquoted delimiter', 'cat > s.sh <<EOF\ncd x\nrm -rf *\nEOF', "T")
case("heredoc kept: bash <<'EOF'", "bash <<'EOF'\ncd x\nrm -rf *\nEOF", "T")
case("heredoc kept: sh -s <<'EOF'", "sh -s <<'EOF'\ncd x\nrm -rf *\nEOF", "T")
case('heredoc kept: python3 with no - argument', "python3 <<'PY'\nos.chdir('t'); os.system('rm -rf *')\nPY", "T")
case('heredoc kept: git commit -F -', "git commit -F - <<'EOF'\ncd x && rm -rf *\nEOF", "T")
case('heredoc kept: cat piped to bash', "cat <<'EOF' | bash\ncd x\nrm -rf *\nEOF", "T")
case('heredoc kept: cat piped on with /&', "cat <<'EOF' |& bash\ncd x\nrm -rf *\nEOF", "T")
case('heredoc kept: no closing line', "cat > s.sh <<'EOF'\ncd x\nrm -rf *\n", "T")
case('heredoc kept: closing line indented without <<-', "cat > s.sh <<'EOF'\ncd x\nrm -rf *\n  EOF", "T")
case('heredoc kept: operator inside double quotes', 'cat "<<\'E\'"\ncd t && rm -rf *\nE', "T")
case('heredoc kept: operator inside single quotes', 'bash -c \'cat > f <<"E"\ncd t; rm -rf *\nE\n\'', "T")
case('heredoc kept: a live line after an operator inside single quotes', 'echo \'; cat > f <<"E"\'\ncd t && rm -rf *\nE', "T")
case('heredoc kept: sudo is not a leading word', "sudo cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "T")
case('heredoc kept: operator in a comment', "# cat > f <<'E'\ncd t && rm -rf *\nE", "T")
case('heredoc kept: <<< is a here-string', "cat <<<'E'\ncd t && rm -rf *\nE", "T")
case('heredoc kept: a cd glob removal after the body', "cat > s.sh <<'EOF'\nx\nEOF\ncd t && rm -rf *", "T")
case("heredoc kept: a cd glob removal on the operator's line", "cd t && rm -rf * && cat > s.sh <<'EOF'\nx\nEOF", "T")
case('heredoc kept: the second of two, fed to bash', "cat > a <<'A'\ncd x\nA\nbash <<'B'\ncd t; rm -rf *\nB", "T")
case("heredoc kept: row 17's unquoted body", 'cat > s.sh <<EOF\n$(cd t && rm -rf sub/*)\nEOF', "T")
case('extra: CLAUDE.md heredoc exemption, python3 -', "python3 - <<'EOF'\nimport os; os.chdir('/x'); os.system('rm -rf *')\nEOF", "pass")

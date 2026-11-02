"""The removal guard's cases: data only, read by `test_shipped_guards.py`.

Each case is `(label, command, expect, cwd, home)`. `expect` is "pass", or the letters of
the rules whose reasons the refusal holds, in the order T, W, S, C. No command is ever run.
`cwd` is the call's working directory and `home` the launch's `HOME` (`UNSET` for none).

The rows are those of the shell hook the guard was ported from, run against Claude Code
2.1.289: what Claude Code's own removal check stops for approval, and what the guard
refuses. Rule C keeps only its removal arm here, so a shell with -c and no removal word
passes where the hook refused it.
"""

CWD = "/home/user/proj/agents"
HOME = "/home/user"
X = "/home/user/tmp/x"
# A cwd like Claude Code's own scratchpad: under /tmp, with a
# part led by `-`, its last parts a/b/c.
SP = "/tmp/claude-1000/-home-user-proj/session/scratchpad"
SPC = SP + "/w-p/a/b/c"
UNSET = object()
CASES = []


def case(label, cmd, expect, cwd=CWD, home=HOME):
    CASES.append((label, cmd, expect, cwd, home))


def subs(opener, closer, n):
    return "echo " + " ".join(opener + "true" + closer for _ in range(n))


# Claude Code stops, the guard refuses (rows 1 to 22).
case("row 1", "mkdir -p s && cd s && rm -rf * && for r in x y; do mkdir $r; done", "TW")
case("row 2", "cd t && rm -rf *", "TW")
case("row 3", "cd t; rm -rf *", "TW")
case("row 4", "(cd t && rm -rf *)", "TW")
# Rows 5a and 5b also break rule C.
case("row 5a", "bash -c 'cd t && rm -rf *'", "TWC")
case("row 5b", "bash -lc 'cd t && rm -rf *'", "TWC")
case("row 6", "pushd t && rm -rf *", "TW")
case("row 7a", "cd t && rm -rf ./*", "TW")
case("row 7b", "cd t && rm -rf sub/*", "T")
case("row 7c", "cd t && rm *", "TW")
case("row 7d", "cd t && rmdir *", "TW")
case("row 8", "rm -rf t/sub/* && cd t", "T")
case("row 9", 'cd t && rm -rf "$(pwd)"/*', "TW")
case("row 10", "cd /full/path/t && rm -rf *", "TW")
case("row 11a", "cd t && rm -rf sub/*/.", "TW")
case("row 11b", "cd t && rm -rf sub/*/x/..", "TW")
case("row 12a", "builtin cd t && rm -rf *", "TW")
case("row 12b", "nice cd t && rm -rf sub/*", "T")
case("row 13a", "cd t && env rm -rf *", "TW")
case("row 13b", "cd t && /usr/bin/env rm -rf sub/*", "T")
case("row 13c", "cd t && timeout 5 rm -rf sub/*", "T")
case("row 14", "x=`cd t`; rm -rf sub/*", "T")
case("row 15", 'x="$(cd t && pwd)"; rm -rf sub/*', "TW")
case("row 16", 'cd t && echo "$(rm -rf sub/*)"', "TW")
case("row 17", "cat <<EOF\n$(cd t && rm -rf sub/*)\nEOF", "TW")
case("row 18", "x=$((1<<2))\ncd t\nrm -rf sub/*", "TW")
case("row 19", r"echo $'it\'s'; cd t && rm -rf sub/*", "TW")
case("row 20", "[ $# -eq 0 ] && cd t && rm -rf *", "TW")
case("row 21 (second scan)", "c''d t && rm -rf sub/*", "T")
case("row 22 (second scan)", r"cd t && r\m -rf sub/*", "TW")

# Rule T refuses more than Claude Code stops (rows 23 to 31): Claude Code runs these.
case("row 23", "cat > s.sh <<'EOF'\ncd x\nrm -rf *\nEOF", "TW")
case("row 24", 'git commit -m "cd x && rm *"', "TW")
case("row 25a", "cd t && rm -f *.txt", "TW")
case("row 25b", "cd t && rm -rf s*", "TW")
case("row 25c", "cd t && rm -rf sub/**", "TW")
case("row 26a", "cd /full/path/t && rm -rf sub/*", "T")
case("row 26b", "cd ./t && rm -rf sub/*", "T")
case("row 27", "cd t && cd sub && rm -rf x/*", "T")
case("row 28", 'cd t && rm -rf "$FOO"/x/*', "TW")
case("row 29", "cd t && rm -rf build && ls *.py", "TW")
case("row 30", "eval 'cd t && rm -rf *'", "TW")
case("row 31", "for d in t; do (cd $d && rm -rf *); done", "TW")

# Claude Code runs, the guard passes (rows 32 to 37).
case("row 32a", "cd t && touch f", "pass")
case("row 32b", "cd t && echo hi > f", "pass")
case("row 32c", "cd t && mkdir x && cp a x/ && mv b.txt c.txt", "pass")
case("row 33", "cd t && rm -rf sub", "pass")
case("row 34", "cd t && ls | xargs rm -rf", "pass")
case("row 35", "rm -rf t/*", "pass")
case("row 36", "cd t && find . -mindepth 1 -delete", "pass")
case("row 37", r"cd t && $'\x72m' -rf sub/*", "pass")

# Rule W: Claude Code stops, the guard refuses (rows 38 to 53m, no cd).
case("row 38", "rm -rf *", "W")
case("row 39", "rm -rf ./*", "W")
case("row 40a", "rmdir .", "W")
case("row 40b", "rmdir ..", "W")
case("row 41", "rm -rf " + CWD + "/*", "W")
case("row 42a", "rm -rf ../..", "W")
case("row 42b", "rm -rf t/..", "W")
case("row 42c", "rm -rf ./*/..", "W")
case("row 42d", "rm -rf $HOME/..", "W")
for i, w in enumerate(["**", "***", "./**", "*/*", "./*/*", "./*/*/*", "*//*", "./*/./*", "./**/*"]):
    case("row 43." + str(i + 1), "rm -rf " + w, "W")
case("row 44a", "rm -rf " + CWD + "/**", "W")
case("row 44b", "rmdir //home/user/proj", "W")
case("row 45", "rm -rf ~", "W")
case("row 46a", "rmdir /zzz-not-there", "W")
case("row 46b", "rm -rf /tmp/*", "W")
for i, w in enumerate(["../*", "../x/*", "./*/", "*/", "*//", "t/../*"]):
    case("row 47." + str(i + 1), "rm -rf " + w, "W")
case("row 48a", "rm -rf /x/sub/../*", "W")
case("row 48b", "rm -rf //x/sub/*", "W")
case("row 48c", "rmdir -p /x/sub/*", "W")
case("row 49", "rm -rf ${PWD}/*", "W")
case("row 50a", "rm -rf t/*/*", "W")
case("row 50b", "rm -rf s*/*", "W")
case("row 50c", "rm -rf s1/x/*/*", "W")
case("row 51a", "rm -rf /x/sub/*/*", "W")
case("row 51b", "rm -rf /x/s?/*", "W")
case("row 52a", 'rm -rf "$FOO"/tmp', "W")
case("row 52b", "rm -rf ${X}/usr/*", "W")
case("row 53a", r"rm -f \\", "W")
case("row 53b", r"rm -f '\'", "W")
case("row 53a.1", "rm -rf ~+/t/sub/*", "W")
case("row 53a.2", "rm -rf ~zzznouser/x/*", "W")
case("row 53a.3", "rm -rf ~zzznouser/*", "W")
case("row 53b.1", "rm -rf '" + CWD + "\\*'", "W")
case("row 53b.2", "rm -rf '" + CWD + "/t\\..\\*'", "W")
case("row 53c", 'rm -rf "../my dir/*"', "W")
case("row 53d", "rm -rf /elsewhere-zz/*", "W")
case("row 53e.1", "rm -rf $A$B/tmp", "W")
case("row 53e.2", "rm -rf $FOO//tmp/**", "W")
case("row 53e.3", "rmdir -p sub/*", "W")
# Rows 53f to 53j: Claude Code compares paths case-folded, with /private mapped.
case("row 53f.1", "rmdir ../C", "W", cwd=SPC)
case("row 53f.2", "rmdir " + SP + "/w-p/a/b/C", "W", cwd=SPC)
case("row 53f.3", "rmdir " + SP + "/w-p/a/B", "W", cwd=SPC)
case("row 53f.4", "rmdir " + SPC.upper(), "W", cwd=SPC)
case("row 53f.5", "rm -rf ../C/x/..", "W", cwd=SPC)
case("row 53g", "rmdir " + SP.replace("/scratchpad", "/\u017fcratchpad") + "/w-p/a/b", "W", cwd=SPC)
case("row 53h.1", "rmdir /private" + SP + "/w-p/a/b", "W", cwd=SPC)
case("row 53h.2", "rmdir /private/tmp", "W", cwd=SPC)
case("row 53i.1", "rmdir /home/USER", "W", cwd=SPC)
case("row 53i.2", "rmdir /HOME/user", "W", cwd=SPC)
case("row 53j.1", "rm -rf " + SP + "/w-p/a/B/*", "W", cwd=SPC)
case("row 53j.2 (plain path)", "rm -rf /var/tmp/w/a/B/*", "W", cwd="/var/tmp/w/a/b/c")
# Rows 53k to 53m (non-ASCII and the split class): Claude Code trims the target with
# JavaScript's whitespace set and U+FEFF; the guard refuses any non-ASCII character.
PARENT = SP + "/w-p/a/b"
case("row 53k.1 (<parent> U+FEFF)", "rmdir " + PARENT + "\ufeff", "W", cwd=SPC)
case("row 53k.2 (.. U+FEFF)", "rmdir ..\ufeff", "W", cwd=SPC)
case("row 53k.3 (<cwd> U+FEFF)", "rmdir " + SPC + "\ufeff", "W", cwd=SPC)
case("row 53k.4 (<parent>/../b U+FEFF)", "rmdir " + PARENT + "/../b\ufeff", "W", cwd=SPC)
case("row 53k.5 (/tmp/claude-1000/ U+FEFF)", "rmdir /tmp/claude-1000/\ufeff", "W", cwd=SPC)
case("row 53l (<parent> U+3000)", "rmdir " + PARENT + "\u3000", "W", cwd=SPC)
case("row 53m.1", "rmdir ..;ls", "W", cwd=SPC)
case("row 53m.2", "rm -rf ..&&ls", "W", cwd=SPC)

# Rule S: Claude Code stops, the guard refuses (rows 54, 55). Each $( also leaves a `$`
# word, which rule W refuses.
case("row 54", subs("$(", ")", 65) + "; rm -f nofile.txt", "WS")
case("row 55", subs("$(", ")", 65) + " && rm -f nofile.txt", "WS")

# Rules W and S: Claude Code runs, the guard passes (rows 56 to 59d).
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
case("row 59c.2 (scratchpad glob, from a scratchpad cwd)", "rm -rf " + SP + "/w-q/x/*", "pass", cwd=SPC)
case("row 59d.1", "rmdir C:/", "pass")
case("row 59d.2", "rmdir C:/x", "pass")

# Rule W refuses more than Claude Code stops (rows 61 to 69).
case("row 61", "rm -rf ./sub/*", "W")
case("row 62", "rm -f *.txt", "W")
case("row 63", "find . -name '*.o' | xargs rm", "W")
case("row 64", "git rm -r --cached .", "W")
case("row 65", "git -C " + CWD + " log && rm -f tmp.txt", "W")
case("row 66", "cd .. && rm -f x.txt", "W")
case("row 67", "rm -rf /home/user/tmp/x/**/*.o", "W")
case("row 68a", 'rm -f "$TMPFILE"', "W")
case("row 68b", 'for f in $(ls); do rm "$f"; done', "W")
case("row 69a", "rm -rf 'sub/*'", "W")
case("row 69b", "rm -rf {a,b}/*", "W")
case("row 69a (<parent> U+200B, Claude Code runs it)", "rmdir " + PARENT + "\u200b", "W", cwd=SPC)
case("row 69b.1 (non-ASCII filename)", "rm -f caf\u00e9.txt", "W")
case("row 69b.2 (an em dash in another word)", "git commit -m 'fix \u2014 dash' && rm -f x.txt", "W")
# Row 70: each `$(true)` leaves a `$` word, which rule W refuses; rule S does not.
case("row 70", subs("$(", ")", 10) + "; rm -f nofile.txt", "W")
# Row 71: from cwd /, the command name itself (`rm`) resolves to /rm, directly under /.
case("row 71", "rm -rf tmp/x/*", "W", cwd="/")

# Rule S refuses more: openers Claude Code counts as fewer substitutions.
case("S over: 33 backtick pairs", subs("`", "`", 33) + "; rm -f nofile.txt", "S")
case("S over: 65 single-quoted $( openers", "echo " + "'$(x)' " * 65 + "; rm -f nofile.txt", "WS")
case("S over: 65 $(( openers", "echo " + "$((1)) " * 65 + "; rm -f nofile.txt", "WS")

# Rule S at the boundary. <( leaves no word behind, so these isolate rule S.
case("S: 64 <( openers", "cat " + " ".join("<(true)" for _ in range(64)) + "; rm -f nofile.txt", "pass")
case("S: 65 <( openers", "cat " + " ".join("<(true)" for _ in range(65)) + "; rm -f nofile.txt", "S")
case("S: 64 >( openers", "tee " + " ".join(">(true)" for _ in range(64)) + "; rm -f nofile.txt", "pass")
case("S: 65 >( openers", "tee " + " ".join(">(true)" for _ in range(65)) + "; rm -f nofile.txt", "S")
case("S: 64 $( openers", subs("$(", ")", 64) + "; rm -f nofile.txt", "W")
case("S: 65 $( openers", subs("$(", ")", 65) + "; rm -f nofile.txt", "WS")
case("S: 64 backticks", subs("`", "`", 32) + "; rm -f nofile.txt", "pass")
case("S: 65 backticks", subs("`", "`", 32) + " `true; rm -f nofile.txt", "S")
case("S: 65 ${ openers (space)", "echo " + "${ true;} " * 65 + "; rm -f nofile.txt", "WS")
case("S: 65 ${| openers", "echo " + "${|true;} " * 65 + "; rm -f nofile.txt", "WS")
case("S: 65 ${NAME} are not openers", "echo " + "${x} " * 65 + "; rm -f nofile.txt", "W")
case("S: 65 openers, rmdir", "cat " + " ".join("<(true)" for _ in range(65)) + "; rmdir empty", "S")
case("S: 65 openers, no removal word", "cat " + " ".join("<(true)" for _ in range(65)), "pass")
case("S: 65 openers, rm only in second scan", "cat " + " ".join("<(true)" for _ in range(65)) + r"; r\m -f nofile.txt", "WS")

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
case("T: backslash-split cd", r"c\d t && rm -rf sub/*", "TW")

# Rule W, each branch passing and failing.
case("W (i) pass: name/*", "rm -rf build/*", "pass")
case("W (i) pass: a/b.c/*", "rm -rf a/b.c/*", "pass")
case("W (i) fail: leading .", "rm -rf .git/*", "W")
case("W (i) fail: leading -", "rm -rf -x/*", "W")
case("W (i) fail: leading ~", "rm -rf ~x/*", "W")
case("W (i) fail: last part not *", "rm -rf sub/*x", "W")
case("W (i) fail: lands on a top-level dir from cwd /", "rm -rf tmp/*", "W", cwd="/")
case("W (i) fail: lands on home from cwd /home", "rm -rf user/*", "W", cwd="/home")
case("W (ii) pass: plain file", "rm -f a.txt", "pass")
case("W (ii) pass: absolute file", "rm -f /x/y/a.txt", "pass")
case("W (ii) pass: ~ path", "rm -f ~/tmp/a.txt", "pass")
case("W (ii) pass: redirect target", "rm -f a.txt 2>/dev/null", "pass")
case("W (ii) fail: .", "rm -rf .", "W")
case("W (ii) fail: /", "rm -rf /", "W")
case("W (ii) fail: top-level dir", "rm -rf /usr", "W")
case("W (ii) fail: home", "rm -rf /home/user", "W")
case("W (ii) fail: home with trailing slash", "rm -rf /home/user/", "W")
case("W (ii) fail: ~ from a cwd outside home", "rm -rf ~", "W", cwd="/var/tmp/w")
case("W (iii) fail: ~/* from a cwd outside home", "rm -rf ~/*", "W", cwd="/var/tmp/w")
case("W (iii) pass: ~/tmp/* from a cwd outside home", "rm -rf ~/tmp/*", "pass", cwd="/var/tmp/w")
case("W (ii) fail: cwd", "rm -rf " + CWD, "W")
case("W (ii) fail: parent of cwd", "rm -rf /home/user/proj", "W")
case("W (ii) fail: quoted ..", 'rm -rf ".."', "W")
case("W (ii) fail: quoted .. for rmdir", "rmdir '..'", "W")
case("W (ii) fail: leading // collapses", "rmdir ///home", "W")
case("W (ii) fail: empty quotes are cwd", 'rm -f ""', "W")
case("W (iii) pass: absolute name/*", "rm -rf /x/y/*", "pass")
case("W (iii) pass: ~/ glob", "rm -rf ~/tmp/x/*", "pass")
case("W (iii) pass: $HOME/ glob", "rm -rf $HOME/tmp/x/*", "pass")
case("W (iii) pass: ${HOME}/ glob", "rm -rf ${HOME}/tmp/x/*", "pass")
case("W (iii) pass: quoted ${HOME}", 'rm -rf "${HOME}"/tmp/x/*', "pass")
case("W (iii) pass: hidden dir part", "rm -rf /x/.cache/*", "pass")
case("W (iii) pass: run of stars", "rm -rf /x/y/***", "pass")
case("W (iii) fail: top-level glob", "rm -rf /usr/*", "W")
case("W (iii) fail: home glob", "rm -rf ~/*", "W")
case("W (iii) fail: $HOME glob", "rm -rf $HOME/*", "W")
case("W (iii) fail: /home glob", "rm -rf /home/*", "W")
case("W (iii) fail: cwd glob", "rm -rf " + CWD + "/*", "W")
case("W (iii) fail: parent glob", "rm -rf /home/user/proj/*", "W")
case("W (iii) fail: another $", "rm -rf /x/$Y/*", "W")
case("W (iii) fail: $HOME then another $", "rm -rf $HOME/$Y/*", "W")
case("W (iii) fail: backslash", r"rm -rf /x/y\z/*", "W")
case("W (iii) fail: . part", "rm -rf /x/./y/*", "W")
case("W (iii) fail: glob before last part", "rm -rf /x/*/y", "W")
case("W (iii) fail: last part mixed glob", "rm -rf /x/y/*.o", "W")
case("W (iii) fail: $HOME alone", "rm -rf $HOME", "W")
case("W (iii) pass: a part led by -", "rm -rf /x/-y/*", "pass")
case("W (iii) fail: a part led by - beside ..", "rm -rf /x/../-y/*", "W")
case("W (iii) fail: a part that is only -- then a glob part", "rm -rf /x/--/*/*", "W")
case("W (c) fold: /private/var alias", "rmdir /private/var/tmp/w", "W", cwd="/var/tmp/w/a")
case("W (c) fold: /Private/Var alias, any case", "rmdir /Private/Var/tmp", "W", cwd="/var/tmp/w/a")
case("W (c) fold: /PRIVATE/TMP", "rmdir /PRIVATE/TMP", "W", cwd=SPC)
case("W (c) fold: dotless i", "rmdir /var/tmp/w\u0131", "W", cwd="/var/tmp/wi/a")
case("W (c) fold: upper-case home from outside home", "rm -rf /Home/User", "W", cwd="/var/tmp/w")
case("W (c) fold: upper-case home glob", "rm -rf /HOME/USER/*", "W", cwd="/var/tmp/w")
case("W (c) fold: HOME itself in mixed case", "rmdir /home/user", "W", cwd="/var/tmp/w", home="/home/User")
case("W (c) fold: upper-case top-level", "rmdir /TMP", "W", cwd="/var/tmp/w")
case("W (c) fold pass: other case, not a parent", "rmdir /var/tmp/W2", "pass", cwd="/var/tmp/w/a")
case("W (c) fold pass: /private/tmp below a top-level dir", "rm -rf /private/tmp/x/y", "pass")
case("W (c) fold pass: /private/var below a top-level dir", "rm -rf /private/var/x/*", "pass")
case("W (c) fold pass: /privatetmp is not the alias", "rmdir /privatetmp/x", "pass", cwd="/tmp/x/a")
# Non-ASCII words and the split class.
case("non-ASCII: U+00A0 inside a word", "rm -f a\u00a0b", "W")
case("non-ASCII: U+2003 inside a word", "rm -f a\u2003b", "W")
case("non-ASCII: U+0085 inside a word", "rm -f a\u0085b", "W")
case("non-ASCII: a control character", "rm -f a\x01b", "W")
case("non-ASCII: DEL", "rm -f a\x7fb", "W")
case("non-ASCII: printable ASCII edges pass", "rm -f a!~b", "pass")
case("non-ASCII: non-ASCII with no removal word passes", "echo caf\u00e9 \ufeff", "pass")
case("non-ASCII: non-ASCII with rm only in the second scan", "r\\m -f caf\u00e9", "W")
case("split: tab separates", "rm\t-f\ta.txt", "pass")
case("split: VT separates", "rm\x0b-f\x0ba.txt", "pass")
case("split: FF separates", "rm\x0c-f\x0ca.txt", "pass")
case("split: CR separates", "rm -f a.txt\r", "pass")
case("split: VT before ..", "rmdir\x0b..", "W")
case("split: FF before ..", "rmdir\x0c..", "W")
case("split: U+3000 does not separate", "rm\u3000-f a.txt", "W")
case("split: | separates", "rm -f a.txt|ls ..", "W")
case("split: ( ) separate", "(rm -f a.txt)", "pass")
# More forms (cwd under the scratchpad).
case("more forms: /PRIVATE/TMP/claude-1000, a parent", "rmdir /PRIVATE/TMP/claude-1000", "W", cwd=SPC)
case("more forms: /tmp///claude-1000/, a parent", "rmdir /tmp///claude-1000/", "W", cwd=SPC)
case("more forms: <parent> with dotless i", "rmdir /var/tmp/w\u0131/a/b", "W", cwd="/var/tmp/wi/a/b/c")
case("more forms: /-q/* is directly under /", "rm -rf /-q/*", "W", cwd=SPC)
case("more forms pass: <parent>. names a sibling", "rmdir " + PARENT + ".", "pass", cwd=SPC)
case("more forms pass: ../b. names a sibling", "rmdir ../b.", "pass", cwd=SPC)
case("more forms pass: /tmp/-q/*", "rm -rf /tmp/-q/*", "pass", cwd=SPC)
case("more forms pass: ~/-q/*", "rm -rf ~/-q/*", "pass", cwd=SPC)
case("more forms pass: /tmp/claude-1000/-x/*", "rm -rf /tmp/claude-1000/-x/*", "pass", cwd=SPC)
case("W rmdir pass: plain dir", "rmdir /x/sub/empty", "pass")
case("W rmdir pass: relative dir", "rmdir empty", "pass")
case("W rmdir fail: relative name/*", "rmdir sub/*", "W")
case("W rmdir fail: absolute glob", "rmdir /x/sub/*", "W")
case("W rmdir fail: rm name/* beside rmdir", "rm -rf sub/* && rmdir x", "W")
case("W rmdir fail: rmdir only in second scan", r"rm -rf sub/* && rmd\ir x", "W")
case("W: no removal word, no refusal", "ls ~ * .. $X", "pass")

# Scratchpad forms (cwd under the scratchpad): 11 stops, 3 passes.
SCRATCH_STOPS = ["rmdir " + SP + "/w-p/a/b/C", "rmdir " + SP + "/w-p/a/B", "rmdir ../C", "rmdir " + SPC.upper(),
    "rm -rf ../C/x/..", "rmdir " + SP.replace("/scratchpad", "/\u017fcratchpad") + "/w-p/a/b",
    "rmdir /private" + SP + "/w-p/a/b", "rmdir /private/tmp", "rmdir /home/USER", "rmdir /HOME/user",
    "rm -rf " + SP + "/w-p/a/B/*"]
SCRATCH_PASSES = ["rm -rf " + SP + "/w-q/x/*", "rmdir C:/", "rmdir C:/x"]
assert (len(SCRATCH_STOPS), len(SCRATCH_PASSES)) == (11, 3)
for i, c in enumerate(SCRATCH_STOPS):
    case("scratchpad form stop " + str(i + 1), c, "W", cwd=SPC)
for i, c in enumerate(SCRATCH_PASSES):
    case("scratchpad form pass " + str(i + 1), c, "pass", cwd=SPC)

# Non-ASCII forms (cwd under the scratchpad): 8 stops, 3 over-refusals, 2 passes.
NONASCII_STOPS = ["rmdir " + PARENT + "\ufeff", "rmdir ..\ufeff", "rmdir " + SPC + "\ufeff",
    "rmdir " + PARENT + "/../b\ufeff", "rmdir /tmp/claude-1000/\ufeff", "rmdir " + PARENT + "\u3000",
    "rmdir ..;ls", "rm -rf ..&&ls"]
NONASCII_OVER = ["rmdir " + PARENT + "\u200b", "rm -f caf\u00e9.txt", "git commit -m 'fix \u2014 dash' && rm -f x.txt"]
NONASCII_PASSES = ["rm -f a.txt 2>/dev/null", "rm -rf " + SP + "/w-q/x/*"]
assert (len(NONASCII_STOPS), len(NONASCII_OVER), len(NONASCII_PASSES)) == (8, 3, 2)
for i, c in enumerate(NONASCII_STOPS):
    case("non-ASCII form stop " + str(i + 1), c, "W", cwd=SPC)
for i, c in enumerate(NONASCII_OVER):
    case("non-ASCII form over " + str(i + 1), c, "W", cwd=SPC)
for i, c in enumerate(NONASCII_PASSES):
    case("non-ASCII form pass " + str(i + 1), c, "pass", cwd=SPC)

# Desk forms: 59 stops, 12 passes and 7 over-refusals.
DESK_STOPS = ["rm -rf **", "rm -rf ***", "rm -rf ./**", "rm -rf " + CWD + "/**", "rm -rf */*", "rm -rf ./*/*",
    "rm -rf ./*/*/*", "rm -rf *//*", "rm -rf ./*/./*", "rm -rf ./**/*", "rmdir //home/user/proj",
    "rm -rf ../x/*", "rm -rf ./*/", "rm -rf */", "rm -rf *//", "rm -rf t/../*", "rm -rf t/*/*",
    "rm -rf s*/*", "rm -rf s1/x/*/*", "rm -rf ${PWD}/*", "rm -rf $PWD/*", "rm -rf *", "rm -rf ./*",
    "rmdir .", "rmdir ..", "rm -rf " + CWD + "/*", "rm -rf ../*", "rm -rf ../..", "rm -rf ~", "rm -rf t/..",
    "rm -rf ./*/..", "rmdir /tmp", "rmdir /zzz-not-there", "rm -rf " + X + "/s?/*", "rm -rf " + X + "/sub/*/*",
    "rm -rf " + X + "/$UNSETXYZ/*", "rm -rf /" + X + "/sub/*", "rmdir -p " + X + "/sub/*", "rm -rf " + X + "/sub/../*",
    r"rm -f \\", 'rm -rf "$FOO"/tmp', "rm -rf ${X}/usr/*", "rm -rf /tmp/*", "rm -rf ~/proj",
    "rm -rf $HOME/..", "rm -rf ~/.",
    "rm -rf ~+/t/sub/*", "rm -rf ~zzznouser/x/*", "rm -rf ~zzznouser/*", "rm -rf '" + CWD + "\\*'",
    "rm -rf '" + CWD + "/t\\..\\*'", r"rm -f '\'", 'rm -rf "../my dir/*"', "rm -rf /elsewhere-zz/*",
    "rm -rf $A$B/tmp", "rm -rf $FOO//tmp/**", "rmdir -p sub/*", "rmdir '..'", 'rm -rf ".."']
DESK_PASSES = ["rm -rf " + X + "/*", "mkdir -p ~/tmp/x/b && rm -rf ~/tmp/x/*", 'rm -rf "$HOME"/tmp/x/*',
    "rm -f " + X + "/a.txt", "rm -rf " + CWD + "/sub", "rm -rf sub/*", "rm -rf t/x/*", "rm -rf /elsewhere-zz/a/b/*",
    "rm -rf " + X + "/sub/**", "rmdir {.,..}", "rm -rf ~/.cache/x/*", "rm -f a.txt 2>/dev/null"]
DESK_OVER = ["rm -f *.txt", "rm -rf ./build/*", 'rm -f "$TMPFILE"', "git add . && rm x",
    "rm -rf 'sub/*'", "rm -rf {a,b}/*", 'for f in $(ls); do rm "$f"; done']
assert (len(DESK_STOPS), len(DESK_PASSES), len(DESK_OVER)) == (59, 12, 7)
for i, c in enumerate(DESK_STOPS):
    case("desk form stop " + str(i + 1), c, "W")
for i, c in enumerate(DESK_PASSES):
    case("desk form pass " + str(i + 1), c, "pass")
for i, c in enumerate(DESK_OVER):
    case("desk form over " + str(i + 1), c, "W")

# cwd: none in the input, and odd forms of it.
case("no cwd: */* refuses", "rm -rf */*", "W", cwd=None)
case("no cwd: * refuses", "rm -rf *", "W", cwd=None)
case("no cwd: .. refuses", "rm -rf ..", "W", cwd=None)
case("no cwd: ./*/.. refuses", "rm -rf ./*/..", "W", cwd=None)
case("no cwd: /x/a/b passes", "rm -rf /x/a/b", "pass", cwd=None)
case("no cwd: sub/* passes", "rm -rf sub/*", "pass", cwd=None)
case("no cwd: a.txt passes", "rm -f a.txt", "pass", cwd=None)
case("no cwd: /tmp/* still refuses", "rm -rf /tmp/*", "W", cwd=None)
case("no cwd: rule T still refuses", "cd t && rm -rf sub/*", "T", cwd=None)
case("non-string cwd counts as none", "rm -rf /x/a/b", "pass", cwd=5)
case("relative cwd counts as none", "rm -rf ..", "W", cwd="rel/dir")
case("cwd with a trailing slash", "rm -rf " + CWD + "/*", "W", cwd=CWD + "/")
case("cwd /: no removal word passes", "ls a/b /tmp", "pass", cwd="/")
case("cwd /: tmp refuses", "rm -rf tmp", "W", cwd="/")

# HOME unset: a word that needs it fails.
case("HOME unset: ~/tmp/x/* refuses", "rm -rf ~/tmp/x/*", "W", home=UNSET)
case("HOME unset: ~/tmp/a.txt refuses", "rm -f ~/tmp/a.txt", "W", home=UNSET)
case("HOME unset: ~ refuses", "rm -rf ~", "W", home=UNSET)
case("HOME unset: $HOME/tmp/x/* refuses", "rm -rf $HOME/tmp/x/*", "W", home=UNSET)
case("HOME unset: ${HOME}/tmp/x/* refuses", "rm -rf ${HOME}/tmp/x/*", "W", home=UNSET)
case("HOME unset: absolute path passes", "rm -rf /home/user/tmp/x/*", "pass", home=UNSET)
case("HOME unset: name/* passes", "rm -rf sub/*", "pass", home=UNSET)
case("HOME unset: rule T still refuses", "cd t && rm -rf *", "TW", home=UNSET)
case("HOME unset: no removal word passes", "ls ~/tmp", "pass", home=UNSET)
case("HOME empty counts as unset", "rm -rf ~/tmp/x/*", "W", home="")

# Rule C. Labels lead with "shell-c" so the HOME-unset rerun of the "row" cases
# doesn't repeat them: rule C reads neither cwd nor HOME. Only its removal arm is
# ported, so a shell with -c and no rm or rmdir passes.
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

# Claude Code stops (rows 1 to 15b); the guard refuses those with a removal word.
case("shell-c row 1 (the stalled command)", STALLED, "pass")
case("shell-c row 2", "cd . && X=/bin/echo; bash -c 'true; \"$0\" hi' $X", "pass")
case("shell-c row 3", "bash -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 1", "timeout 10 bash -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 2", "env FOO=1 bash -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 3", "nice sh -c '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 4", "bash -lc '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 5", "bash -ec '$X \"$1\"' _ a", "pass")
case("shell-c row 4, form 6", "/bin/bash -c '\"$X\" \"$1\"' _ a", "pass")
case("shell-c row 5", "bash -c 'mkdir -p " + CWD + "/m/{a,b}; rm -f zz'", "C")
case("shell-c row 6", "bash -c 'mkdir -p " + CWD + "/m/{a,b}; r\"\"m -f zz'", "C")
case("shell-c row 7", "bash --foo -c 'rm -f zz'", "C")
case("shell-c row 8", "bash -c 'r{m,}dir zz'", "C")
case("shell-c row 9", r"bash -c $'echo \x68i'", "pass")
case("shell-c row 11", "bash -c 'cd t && rm -rf *'", "TWC")
case("shell-c row 12", "bash -c \"bash -c \\\"bash -c 'rm -f zz'\\\"\"", "WC")
case("shell-c row 13", "bash -c 'rm -rf \"$1\"/x' _ \"$D\"", "WC")
case("shell-c row 14 (a script over 2000 characters with rm)", "bash -c 'echo " + "x" * 2000 + "; rm -f zz'", "C")
case("shell-c row 15a", 'Q=r; bash --foo -c "/${Q}m zz"', "pass")
case("shell-c row 15b", "cd . && Z=1; for i in 1\ndo\nX=\"a b\" $Y\nbash -c 'echo hi' $Z\ndone", "pass")
# Claude Code stops, the guard passes: the known miss.
case("shell-c row 10 (nine groups)", "( ( ( ( ( ( ( ( ( echo {a,b} ) ) ) ) ) ) ) ) )", "pass")
# Claude Code runs, the guard refuses: a removal word with a shell -c (rows 16 to 23b).
case("shell-c row 16", "bash -c 'rm -f " + CWD + "/zz'", "C")
case("shell-c row 17, form 1", "bash -c 'echo {a,b}'", "pass")
case("shell-c row 17, form 2", "bash -c 'mkdir -p " + CWD + "/m/{a,b}'", "pass")
case("shell-c row 17 (jq in a script)", "bash -c \"jq -c '{a,b}' f.json\"", "pass")
case("shell-c row 18, form 1", "bash -c '\"$0\" hi' /bin/echo", "pass")
case("shell-c row 18, form 2", "X=/bin/echo; bash -c 'true; \"$0\" hi' $X", "pass")
case("shell-c row 18, form 3", "X=/bin/echo; bash -c 'X=/bin/echo; $X hi'", "pass")
case("shell-c row 19, form 1", "for P in /bin/echo; do bash -c '\"$0\" hi' $P; done", "pass")
case("shell-c row 19, form 2", "for f in a; do bash -c 'echo hi'; rm -f zz; done", "C")
case("shell-c row 19, form 3", "for P in /bin/echo; do $P hi; bash -c 'echo x'; done", "pass")
case("shell-c row 20", "cd . && X=/bin/echo; bash -c 'true; echo hi' $X", "pass")
case("shell-c row 21 ($HOME)", "sh -c 'echo $HOME'", "pass")
case("shell-c row 21 (loop)", "bash -c 'for f in a b; do echo $f; done'", "pass")
case("shell-c row 21 (find -exec)", "find . -name '*.txt' -exec bash -c 'echo \"$1\"' _ {} \\;", "pass")
case("shell-c row 21 (exec $@)", "sh -c 'exec \"$@\"' sh ls", "pass")
case("shell-c row 22", "echo a | xargs bash -c '$X \"$1\"' _", "pass")
case("shell-c row 23 (rm in another command)", "bash -c 'echo hi' && rm -f /home/user/tmp/x/a.txt", "C")
case("shell-c row 23 (rm in a string)", "git commit -m 'note: bash -c with rm inside'", "C")
case("shell-c row 23a (tail -c)", "bash tests/hooks.sh 2>&1 | tail -c 2000; echo \"exit ${PIPESTATUS[0]}\"", "pass")
case("shell-c row 23a (wc -c)", "bash x.sh | wc -c; echo $?", "pass")
case("shell-c row 23b (a heredoc that writes a test)", "cat > t.sh <<'EOF'\nbash -c 'echo \"$1\"' _ hi\nEOF", "pass")
# Claude Code runs, the guard passes (rows 24 to 29).
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

# Rule C's parts, each passing and failing. Every gate case has a removal word, so only
# the part named decides it.
# The shell name and its boundaries.
case("C shell name: /bin/bash counts", "/bin/bash -c 'rm zz'", "C")
case("C shell name: BASH counts", "BASH -c 'rm zz'", "C")
case("C shell name: bash-5.2 counts", "bash-5.2 -c 'rm zz'", "C")
case("C shell name: S=bash counts", "S=bash; run -c \"rm zz\"", "C")
for n in ["sh", "zsh", "dash", "ksh", "mksh", "ash", "hush", "rbash"]:
    case("C shell name: " + n + " counts", n + " -c 'rm zz'", "C")
case("C shell name: run.sh does not count", "./run.sh -c 'rm zz'", "pass")
case("C shell name: ssh does not count", "ssh host -c aes 'rm zz'", "pass")
case("C shell name: bash5 does not count", "bash5 -c 'rm zz'", "pass")
case("C shell name: _bash does not count", "_bash -c 'rm zz'", "pass")
case("C shell name: bash_x does not count", "bash_x -c 'rm zz'", "pass")
case("C shell name: in the quote-stripped text only (ba\"s\"h)", "ba\"s\"h -c 'rm zz'", "C")
# The -c word.
case("C -c word: -c", "bash -c 'rm zz'", "C")
case("C -c word: -lc", "bash -lc 'rm zz'", "C")
case("C -c word: -xec", "bash -xec 'rm zz'", "C")
case("C -c word: \"-c\" (in the quote-stripped text only)", "bash \"-c\" 'rm zz'", "C")
case("C -c word: FLAGS=-c", "FLAGS=-c; bash $FLAGS 'rm zz'", "WC")
case("C -c word: after a ;", "bash x.sh;-c rm", "C")
case("C -c word: --norc does not count", "bash --norc x.sh rm", "pass")
case("C -c word: -x does not count", "bash -x x.sh rm", "pass")
case("C -c word: a - inside a word does not count", "bash x-c.sh rm", "pass")
case("C -c word: -C does not count", "bash x.sh -C rm", "pass")
# Arm (a), removal.
case("C arm (a): rm", "bash -c 'rm zz'", "C")
case("C arm (a): rmdir", "bash -c 'rmdir zz'", "C")
case("C arm (a): RM in any case", "bash -c 'RM zz'", "C")
case("C arm (a): rm2 (a digit after counts)", "bash -c 'rm2 zz'", "C")
case("C arm (a): /bin/rm", "bash -c '/bin/rm zz'", "C")
case("C arm (a): in the quote-stripped text only (r\"\"m)", "bash -c 'r\"\"m zz'", "C")
case("C arm (a): in the quote-stripped text only (r\\m; W refuses the backslash word)", "bash -c 'r\\m zz'", "WC")
case("C arm (a): in the quote-stripped text with braces deleted (r{m})", "bash -c 'r{m} zz'", "C")
case("C arm (a): arm does not count", "bash -c 'arm zz'", "pass")
case("C arm (a): rmx does not count", "bash -c 'rmx zz'", "pass")
case("C arm (a): rm_x does not count", "bash -c 'rm_x zz'", "pass")
case("C arm (a): 2rm does not count", "bash -c 'echo 2rm'", "pass")
# Arm (b), a brace expansion: not ported, so each passes.
case("C arm (b): {a,b}", "bash -c 'echo {a,b}'", "pass")
case("C arm (b): {1..3}", "bash -c 'echo {1..3}'", "pass")
case("C arm (b): in the quote-stripped text only ({1.\\.3})", "bash -c 'echo {1.\\.3}'", "pass")
case("C arm (b): {a} does not count", "bash -c 'echo {a}'", "pass")
case("C arm (b): { a,b } does not count", "bash -c 'echo { a,b }'", "pass")
case("C arm (b): {} does not count", "bash -c 'echo {}'", "pass")
# Arm (c), any $: not ported.
case("C arm (c): $X", "bash -c 'echo $X'", "pass")
case("C arm (c): a $ that doesn't start a word (/${Q}m)", 'bash -c "/${Q}m zz"', "pass")
case("C arm (c): $ inside a word", "bash -c 'echo a$b'", "pass")
case("C arm (c): \\$X", "bash -c \\$X", "pass")
case("C arm (c): $\"x\" (in the raw text only)", 'bash -c $"x"', "pass")
case("C arm (c): $ outside the script", "bash -c 'echo hi' && echo $HOME", "pass")
# Arm (d), $': not ported.
case("C arm (d): $'", r"bash -c $'echo \x68i'", "pass")
case("C arm (d): $' behind a \"", "bash -c $\"'echo hi'\"", "pass")
# No arm.
case("C: shell -c with no arm passes", "bash -c 'echo hi; ls -la | wc -l'", "pass")
case("C: no -c passes", "bash x.sh $X {a,b} $'x'", "pass")
case("C: no shell name passes", "python3 -c 'print(1)' $X {a,b}", "pass")



# With HOME unset a ~ word refuses (57, 59b.2), and an upper-case home (53i) cannot be
# known as home; from a cwd outside home it then passes. Every other "row" case reads the
# same with HOME unset.
HOME_UNSET = {"row 57a": "W", "row 57b": "W", "row 59b.2": "W", "row 53i.1": "pass",
              "row 53i.2": "pass"}

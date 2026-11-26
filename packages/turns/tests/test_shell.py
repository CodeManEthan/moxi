"""The shell reader: one row per line of its grammar in each dialect, the command word
past wrappers, the scripts handed to a shell, and one row per class of what it doesn't
read."""

from __future__ import annotations

import dataclasses
import random

import pytest

import moxi_turns as turns
from moxi_turns.guards.shell import (DIALECTS, MARK_KINDS, SCRIPT_SOURCES, ShellCommand,
                                     ShellMark, ShellRedirect, ShellScript, ShellText,
                                     ShellWord, command_word_index, read_shell,
                                     shell_scripts)


def words(text, dialect="sh"):
    """Each command's word texts."""
    return [[w.text for w in c.words] for c in read_shell(text, dialect=dialect).commands]


def ended(text, dialect="sh"):
    return [c.ended_by for c in read_shell(text, dialect=dialect).commands]


def kinds(text, dialect="sh"):
    return [m.kind for m in read_shell(text, dialect=dialect).marks]


def runs(text, dialect="sh"):
    """The word each command runs, or None."""
    out = []
    for c in read_shell(text, dialect=dialect).commands:
        k = command_word_index(c, dialect=dialect)
        out.append(None if k is None else c.words[k].text)
    return out


def scripts(text, dialect="sh"):
    return [(s.text, s.dialect, s.command_index, s.source)
            for s in shell_scripts(read_shell(text, dialect=dialect), dialect=dialect)]


def word(text, index=0, command=0, dialect="sh"):
    return read_shell(text, dialect=dialect).commands[command].words[index]


# --- the API -------------------------------------------------------------------------------

def test_the_names_reach_the_package():
    for name in ("DIALECTS", "read_shell", "shell_scripts", "command_word_index",
                 "ShellText", "ShellCommand", "ShellWord", "ShellRedirect", "ShellScript",
                 "ShellMark"):
        assert getattr(turns, name) is globals()[name]
        assert name in turns.__all__
    assert DIALECTS == ("sh", "powershell", "cmd")
    assert SCRIPT_SOURCES == ("argument", "heredoc", "here-string", "pipe")


def test_the_mark_kinds_and_script_sources_reach_the_package():
    for name in ("MARK_KINDS", "SCRIPT_SOURCES"):
        assert getattr(turns, name) is globals()[name]
        assert name in turns.__all__


def test_is_sub_is_refused_by_position():
    with pytest.raises(TypeError):
        ShellWord("x", 0, (), True)  # noqa: FBT003 -- the refusal under test
    assert ShellWord("x", 0, (), is_sub=True).is_sub
    assert all(f.kw_only for f in dataclasses.fields(ShellWord) if f.name == "is_sub")


def test_text_that_is_not_a_string_reads_as_no_commands():
    assert read_shell(None) == ShellText((), ())
    assert read_shell(b"kill 211") == ShellText((), ())
    assert read_shell("") == ShellText((), ())


def test_an_unknown_dialect_is_a_value_error():
    with pytest.raises(ValueError):
        read_shell("ls", dialect="fish")


def test_it_never_raises_on_any_text():
    rng = random.Random(7)
    alphabet = list("ab $()`'\"\\|&;<>{}[]*?~#\n\t-=@^%!") + ["<<", "$((", "<<'E'\nE\n",
                                                              "@'\n", "\n'@", "$'", "((", "case", " in ", ";;", "esac"]
    for dialect in DIALECTS:
        for _ in range(3000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 30)))
            shell = read_shell(text, dialect=dialect)
            for c in shell.commands:
                command_word_index(c, dialect=dialect)
            shell_scripts(shell, dialect=dialect)
            assert all(m.kind in MARK_KINDS for m in shell.marks)
            assert [m.position for m in shell.marks] == sorted(m.position for m in shell.marks)


def test_text_nested_too_deep_reads_as_unclosed():
    deep = "$(" * 5000 + "kill 211" + ")" * 5000
    shell = read_shell(deep)
    assert shell.commands == () and shell.marks == (ShellMark("unclosed", 0),)


# --- sh: words, quoting, escapes ------------------------------------------------------------

def test_sh_words_split_at_spaces_and_tabs_and_a_backslash_newline_joins():
    assert words("ls  -l\tx") == [["ls", "-l", "x"]]
    assert words("ec\\\nho hi") == [["echo", "hi"]]
    assert kinds("ec\\\nho") == ["backslash"]


def test_sh_quoting_forms():
    assert words("echo 'a \"b\" $x' \"c 'd' $y\" $'e\\'f'") == \
        [["echo", 'a "b" $x', "c 'd' $y", "e'f"]]
    # A command after $'...' is read, not swallowed.
    assert words("echo $'a\\'b' ; kill 211") == [["echo", "a'b"], ["kill", "211"]]


def test_sh_escapes():
    assert words('echo \\$x "\\$y \\a \\" \\\\"') == [["echo", "$x", '$y \\a " \\']]
    # In $'...' only \\, \' and \" are taken off; every other escape is kept as written.
    assert word("echo $'kill 211\\n\\\\'", 1).text == "kill 211\\n\\"


def test_sh_substitution_forms():
    w = word("echo $(a) `b` \"$(c)\" x$(d)y", 1)
    assert w.is_sub and w.subs == ("a",)
    w = word("echo $(a) `b` \"$(c)\" x$(d)y", 2)
    assert w.is_sub and w.text == "\0" and w.subs == ("b",)
    w = word("echo $(a) `b` \"$(c)\" x$(d)y", 3)
    assert w.is_sub and w.subs == ("c",)
    w = word("echo $(a) `b` \"$(c)\" x$(d)y", 4)
    assert not w.is_sub and w.text == "x\0y" and w.subs == ("d",)
    # A mixed word keeps its substitution's text.
    w = word("x=$(cat T)")
    assert w.text == "x=\0" and w.subs == ("cat T",) and not w.is_sub
    assert word('kill "$(pgrep python3)"', 1).is_sub
    assert not word('kill ""$(pgrep x)', 1).is_sub


def test_sh_substitutions_nest_and_close_at_their_own_paren():
    w = word("echo $(a $(b) 'c)' \"d)\" # e)\n)", 1)
    assert w.subs == ("a $(b) 'c)' \"d)\" # e)\n",)
    assert word("echo `a \\`b\\` c`", 1).subs == ("a `b` c",)


def test_sh_a_substitution_inside_a_value_is_read():
    w = word("echo ${x:-$(kill 211)}", 1)
    assert w.text == "${x:-\0}" and w.subs == ("kill 211",)


def test_sh_double_quotes_inside_a_value_are_read_as_double_quotes():
    # A ' or } inside them is an ordinary character, so the value ends at its own brace.
    assert words("${x:-\"it's\"}; next") == [["${x:-\"it's\"}"], ["next"]]
    assert words('${HOME:-"}"}; next') == [['${HOME:-"}"}'], ["next"]]
    assert words("echo \"${x:-\"it's\"}\"; next") == [["echo", "${x:-\"it's\"}"], ["next"]]
    w = word('echo ${x:-"$(kill 2) `kill 3` \\" }"}', 1)
    assert w.subs == ("kill 2", "kill 3")


@pytest.mark.parametrize("operator", ["-", ":-", "+", ":+", "=", ":="])
def test_sh_a_default_or_alternate_outside_quotes_gives_the_words_bash_splits_it_into(
        operator):
    v = "${x" + operator + " /p q }"
    assert word(f"cat {v}", 1).fields == ("/p", "q")
    assert word(f"cat a{v}b", 1).fields == ("a", "/p", "q", "b")
    assert word(f"cat {v}cmd", 1) == ShellWord(v + "cmd", 4, (), is_sub=False,
                                               fields=("/p", "q", "cmd"))
    assert word("cat ${x" + operator + "/p}/x", 1).fields == ("/p/x",)


def test_sh_a_default_s_quotes_keep_its_words_together_unless_it_is_assigned():
    assert word("cat ${x:- \"/p q\" 'r s'\\ t}", 1).fields == ("/p q", "r s t")
    assert word("cat ${x:= \"/p q\" r}", 1).fields == ("/p", "q", "r")


def test_sh_a_default_in_double_quotes_or_a_check_gives_no_words():
    for text in ('cat "${x:- /p q }"', 'cat a"${x:- /p }"b', "cat ${x:? /p q }",
                 "cat ${x? /p q }", "cat ${x#a b}", "cat ${x/a/ b c}", "cat ${x:- /p",
                 "cat $x"):
        assert word(text, 1).fields == (), text
    assert word('cat "${x:- /p }"${y:+ /q r }', 1).fields == ("${x:- /p }", "/q", "r")


def test_sh_a_default_nested_in_a_default_gives_its_own_words():
    assert word("cat ${x:- a ${y:- /t } }", 1).fields == ("a", "/t")
    assert word("cat ${x:- ${y:-/t}}", 1).fields == ("/t",)
    assert word("cat ${x:- ${y:- ${z:- /t q } } }", 1).fields == ("/t", "q")
    assert word("cat ${x:= a ${y:- /t q } }", 1).fields == ("a", "/t", "q")
    # In double quotes inside the default it is one piece, as bash keeps it.
    assert word('cat ${x:- "${y:- /t q }" r }', 1).fields == (" /t q ", "r")
    assert word('cat ${x:- "a ${y:-/t}" }', 1).fields == ("a /t",)
    # A value bash would have to look up stays as written.
    assert word("cat ${x:- ${y#a} /t }", 1).fields == ("${y#a}", "/t")


def test_sh_a_substitution_in_a_default_stays_a_nul_in_its_words():
    w = word("cat ${x:- $(kill 211)/p q }", 1)
    assert w.fields == ("\0/p", "q") and w.subs == ("kill 211",)


def test_sh_an_assignment_bash_makes_without_splitting_gives_no_words():
    assert [w.fields for w in read_shell("X=${x:- /p q } cat Y=${x:- /r }").commands[0]
            .words] == [(), (), ("Y=", "/r")]
    for text in ("export E=${x:- /p q }", "local L=${x:- /p q }"):
        assert word(text, 1).fields == (), text
    assert word("command export E=${x:- /p q }", 2).fields == ("E=", "/p", "q")
    assert read_shell("cat < ${x:- /p }").commands[0].redirects[0].target.fields == ("/p",)


def test_sh_arithmetic():
    assert words("echo $((1<<2)); kill 211") == [["echo", "$((1<<2))"], ["kill", "211"]]
    assert words("echo $[1<<2]") == [["echo", "$[1<<2]"]]
    assert words("(( x = 1 << 3 )); kill 211") == [["(( x = 1 << 3 ))"], ["kill", "211"]]
    assert runs("(( x = 1 << 3 )); kill 211") == [None, "kill"]
    assert words("while (( n << 1 )); do\nkill 211\ndone") == \
        [["while", "(( n << 1 ))"], ["do"], ["kill", "211"], ["done"]]
    assert runs("while (( n << 1 )); do\nkill 211\ndone") == [None, None, "kill", None]
    assert words("for ((i=0; i<(1<<3); i++)); do kill 1; done") == \
        [["for", "((i=0; i<(1<<3); i++))"], ["do", "kill", "1"], ["done"]]
    w = word("echo $(( $(cat f) + 1 ))", 1)
    assert w.subs == ("cat f",) and w.text == "$(( \0 + 1 ))"
    assert "arithmetic" in kinds("echo $((1))") and "arithmetic" in kinds("echo $[1]")
    assert kinds("((x))") == ["arithmetic"]
    # No redirect or heredoc inside arithmetic.
    assert read_shell("echo $((1<<2))").commands[0].redirects == ()


def test_sh_the_named_misread():
    shell = read_shell("echo $((true); (kill 211))")
    assert [[w.text for w in c.words] for c in shell.commands] == \
        [["echo", "$((true); (kill 211))"]]
    assert [m.kind for m in shell.marks] == ["dollar", "arithmetic"]


# --- sh: reserved words and case ------------------------------------------------------------

def test_sh_reserved_words_that_start_a_command():
    text = ("! kill 1; { kill 2; }; if a; then kill 3; elif b; then kill 4; else kill 5; fi; "
            "while c; do kill 6; done; until d; do kill 7; done; time -p kill 8")
    assert runs(text) == ["kill", "kill", None, "a", "kill", "b", "kill", "kill", None,
                          "c", "kill", None, "d", "kill", None, "kill"]


def test_sh_reserved_words_stay_words_and_only_count_in_command_position():
    assert words("if true; then echo then; fi") == [["if", "true"], ["then", "echo", "then"],
                                                    ["fi"]]
    assert runs("echo do") == ["echo"]


def test_sh_function_and_coproc_with_a_name():
    assert runs("function f { kill 211; }") == ["kill", None]
    assert runs("function f() { kill 211; }") == [None, None, "kill", None]
    assert runs("coproc w { kill 211; }") == ["kill", None]
    assert runs("coproc kill 211") == ["kill"]


def test_sh_words_that_run_nothing():
    assert runs("for x in a b; do kill $x; done") == [None, "kill", None]
    assert runs("select x in a; do kill 1; done") == [None, "kill", None]
    assert runs("[[ a < b ]]") == [None]
    shell = read_shell("[[ a < b && c ]]; kill 1")
    assert [w.text for w in shell.commands[0].words] == ["[[", "a", "&&", "c", "]]"]
    assert shell.commands[0].redirects[0].operator == "<"
    assert runs("[[ a < b && c ]]; kill 1") == [None, "kill"]


def test_sh_a_case_pattern_paren_ends_a_command_and_no_substitution():
    assert words("case $x in a|b) kill 1;; (c) kill 2;& *) kill 3;;& esac") == [
        ["case", "$x", "in", "a|b"], ["kill", "1"], ["c"], ["kill", "2"], ["*"],
        ["kill", "3"], ["esac"]]
    assert ended("case x in a) kill 1;; esac") == [")", ";", ""]
    w = word('echo "$(case x in x) kill 211;; esac)"', 1)
    assert w.is_sub and w.subs == ("case x in x) kill 211;; esac",)
    assert runs(w.subs[0]) == [None, "kill", None]
    assert words("case x in\n  a) kill 1\n  ;;\nesac\nkill 2")[-1] == ["kill", "2"]


# --- sh: operators, redirects, heredocs ------------------------------------------------------

def test_sh_each_operator_ends_a_command():
    assert ended("a | b |& c && d || e ; f & g\nh") == ["|", "|", "&&", "||", ";", "&", "\n",
                                                        ""]
    assert ended("(a)") == ["(", ")", ""]
    assert ended("case x in a) b;; c) d;& e) f;;& esac") == [")", ";", ")", ";", ")", ";",
                                                            ""]
    assert kinds("a|b") == ["operator"]


def test_sh_empty_commands_are_kept():
    assert words("| grep x") == [[], ["grep", "x"]]
    assert words("grep x |") == [["grep", "x"], []]
    assert words("a ;; b") == [["a"], ["b"]]
    assert words("") == [] and words("   ") == [] and words("# c") == []


@pytest.mark.parametrize("text, operator, fd, target", [
    ("cat < f", "<", None, "f"), ("ls > f", ">", None, "f"), ("ls >> f", ">>", None, "f"),
    ("ls >| f", ">|", None, "f"), ("cat <> f", "<>", None, "f"), ("cat <&3", "<&", None, "3"),
    ("ls 2>&1", ">&", "2", "1"), ("ls &> f", "&>", None, "f"), ("ls &>> f", "&>>", None, "f"),
    ("ls 2>/dev/null", ">", "2", "/dev/null"), ("ls {fd}>&-", ">&", "{fd}", "-"),
    ("cat <<< word", "<<<", None, "word"),
])
def test_sh_each_redirect_is_kept(text, operator, fd, target):
    shell = read_shell(text)
    (command,) = shell.commands
    (r,) = command.redirects
    assert (r.operator, r.fd, r.target.text) == (operator, fd, target)
    assert len(command.words) == 1


def test_sh_a_quoted_or_spaced_number_is_no_fd():
    assert words("kill 211 2 >/dev/null") == [["kill", "211", "2"]]
    assert words("kill 211 '2'>/dev/null") == [["kill", "211", "2"]]


def test_sh_a_process_substitution_is_a_redirect_with_its_target():
    (r,) = read_shell("diff <(ls a) f").commands[0].redirects
    assert r.operator == "<(" and r.target.is_sub and r.target.subs == ("ls a",)
    (r,) = read_shell("tee >(kill 211)").commands[0].redirects
    assert r.operator == ">(" and r.target.subs == ("kill 211",)


def test_sh_a_heredoc_body_is_kept_and_read_as_no_command():
    shell = read_shell("cat <<'EOF'\nkill 211\nEOF\nls")
    assert [[w.text for w in c.words] for c in shell.commands] == [["cat"], ["ls"]]
    r = shell.commands[0].redirects[0]
    assert (r.operator, r.target.text, r.body, r.subs) == ("<<", "EOF", "kill 211\n", ())
    assert "heredoc" in [m.kind for m in shell.marks]


def test_sh_heredoc_forms():
    r = read_shell("cat <<-EOF\n\tbody\n\tEOF\nls").commands[0].redirects[0]
    assert r.operator == "<<-" and r.body == "\tbody\n"
    assert words("cat <<-EOF\n\tbody\n\tEOF\nls") == [["cat"], ["ls"]]
    shell = read_shell("cat <<A <<'B'\none\nA\ntwo\nB\nkill 3")
    assert [r.body for r in shell.commands[0].redirects] == ["one\n", "two\n"]
    assert [[w.text for w in c.words] for c in shell.commands] == [["cat"], ["kill", "3"]]
    shell = read_shell("cat <<EOF\nnever closed\nkill 1")
    assert shell.commands[0].redirects[0].body == "never closed\nkill 1"
    assert [[w.text for w in c.words] for c in shell.commands] == [["cat"], []]
    assert "unclosed" in [m.kind for m in shell.marks]


def test_sh_an_unquoted_heredoc_runs_its_substitutions():
    r = read_shell("cat <<EOF\nsay \"hi\" it's $(kill 211) `kill 212` \\$(no)\nEOF").commands[
        0].redirects[0]
    assert r.subs == ("kill 211", "kill 212")
    r = read_shell("cat <<'EOF'\n$(kill 211)\nEOF").commands[0].redirects[0]
    assert r.subs == ()
    r = read_shell("cat <<\\EOF\n$(kill 211)\nEOF").commands[0].redirects[0]
    assert r.subs == ()
    r = read_shell("cat <<EOF\n$(( $(kill 2) + 1 )) ${x:-$(kill 3)}\nEOF").commands[
        0].redirects[0]
    assert r.subs == ("kill 2", "kill 3")


def test_sh_a_heredoc_inside_a_substitution_closes_at_its_own_paren():
    text = "git commit -m \"$(cat <<'EOF'\nit's a ) test, (really\nEOF\n)\" && kill 211"
    shell = read_shell(text)
    assert [[w.text for w in c.words] for c in shell.commands] == \
        [["git", "commit", "-m", "\0"], ["kill", "211"]]
    sub = shell.commands[0].words[3].subs[0]
    assert words(sub) == [["cat"], []]


def test_sh_a_here_string_is_the_commands_input():
    (r,) = read_shell("bash <<< 'kill 211'").commands[0].redirects
    assert r.operator == "<<<" and r.target.text == "kill 211"


def test_sh_comments():
    assert words("ls # kill 211\nkill 3") == [["ls"], ["kill", "3"]]
    assert words("echo a#b '#c'") == [["echo", "a#b", "#c"]]
    assert kinds("ls #x") == ["comment"]


def test_sh_values_are_kept_as_written():
    assert words("echo $x ${y} * {a,b} ~ ~/z") == [["echo", "$x", "${y}", "*", "{a,b}", "~",
                                                     "~/z"]]
    assert kinds("echo ~/z") == [] and kinds("echo ~") == ["tilde"]


@pytest.mark.parametrize("text, kind", [
    ("a;b", "operator"), ("a >f", "redirect"), ("cat <<E\nE", "heredoc"), ("$x", "dollar"),
    ('"\\$x"', "dollar"), ("`a`", "backtick"), ("a\\ b", "backslash"), ("*.py", "glob"),
    ("x?", "glob"), ("[ab]", "glob"), ("{a,b}", "brace"), ("a~/b", "tilde"),
    ("~root", "tilde"), ("a #c", "comment"), ("$'x'", "ansi-c"), ("$((1))", "arithmetic"),
    ("a\rb", "control"), ("'x", "unclosed"), ('"x', "unclosed"), ("$(x", "unclosed"),
    ("`x", "unclosed"), ("cat <<E", "unclosed"),
])
def test_one_mark_per_kind(text, kind):
    assert kind in kinds(text)


def test_nothing_quoted_is_marked():
    assert kinds("echo 'a;b|c $x `y` * {z} ~ #'") == []
    assert kinds('echo "a;b|c * {z} ~ #"') == []


# --- PowerShell ----------------------------------------------------------------------------

def test_ps_words_quotes_and_escapes():
    assert words("Write-Output 'it''s' \"say \"\"hi\"\"\" a`$b", "powershell") == \
        [["Write-Output", "it's", 'say "hi"', "a$b"]]
    w = word("echo \"kill 211`n\"", 1, dialect="powershell")
    assert w.text == "kill 211`n"


def test_ps_substitutions():
    w = word('echo "$(Stop-Process 1)" @(Stop-Process 2) (Stop-Process 3)', 1,
             dialect="powershell")
    assert w.is_sub and w.subs == ("Stop-Process 1",)
    assert word("echo @(a)", 1, dialect="powershell").subs == ("a",)
    assert word("echo (Get-Content p.txt)", 1, dialect="powershell").is_sub
    w = word("Stop-Process -Id (Get-Content pid.txt) -Force", 2, dialect="powershell")
    assert w.subs == ("Get-Content pid.txt",)


def test_ps_statement_words_and_blocks_end_commands():
    text = "if ($x) { Stop-Process 5 } else { kill 6 }; foreach ($p in $l) { spps $p }"
    assert ended(text, "powershell") == ["{", "}", "{", "}", ";", "{", "}", ""]
    assert runs(text, "powershell") == [None, "Stop-Process", None, "kill", None, None,
                                        "spps", None]


def test_ps_the_call_operators_and_ampersand_after_a_pipeline():
    assert runs("& 'C:\\t\\x.exe' 1; . ./a.ps1", "powershell") == ["C:\\t\\x.exe", "./a.ps1"]
    assert ended("Start-Job { a } & b", "powershell") == ["{", "}", "&", ""]
    assert ended("a && b || c | d", "powershell") == ["&&", "||", "|", ""]


def test_ps_here_strings():
    text = "$s = @'\nit's \"x\" $(no)\n'@\n$t = @\"\n$(Stop-Process 1) `$y\n\"@\nls"
    shell = read_shell(text, dialect="powershell")
    assert [[w.text for w in c.words] for c in shell.commands] == \
        [["$s", "=", "it's \"x\" $(no)"], ["$t", "=", "\0 $y"], ["ls"]]
    assert shell.commands[1].words[2].subs == ("Stop-Process 1",)
    assert "unclosed" in kinds("$s = @'\nnever", "powershell")


def test_ps_redirects_and_comments():
    shell = read_shell("Stop-Process 211 2>&1 *> out.txt >> 2 # x\n<# y\nz #> ls",
                       dialect="powershell")
    c = shell.commands[0]
    assert [w.text for w in c.words] == ["Stop-Process", "211"]
    assert [(r.operator, r.fd, r.target and r.target.text) for r in c.redirects] == [
        (">&1", "2", None), (">", "*", "out.txt"), (">>", None, "2")]
    assert [[w.text for w in c.words] for c in shell.commands][-1] == ["ls"]
    assert kinds("a # x", "powershell") == ["comment"]


def test_ps_values_are_kept_as_written():
    assert words("echo $x ${y z} $env:PATH", "powershell") == [["echo", "$x", "${y z}",
                                                                 "$env:PATH"]]


# --- cmd ----------------------------------------------------------------------------------

def test_cmd_quotes_escapes_and_operators():
    assert words('echo "a & b" c^&d', "cmd") == [["echo", "a & b", "c&d"]]
    assert ended("a & b && c || d | e\nf", "cmd") == ["&", "&&", "||", "|", "\n", ""]


def test_cmd_at_redirects_and_blocks():
    assert runs("@echo off", "cmd") == ["echo"]
    shell = read_shell("dir > out.txt 2>&1 < in.txt", dialect="cmd")
    assert [(r.operator, r.fd, r.target and r.target.text)
            for r in shell.commands[0].redirects] == [
        (">", None, "out.txt"), (">&1", "2", None), ("<", None, "in.txt")]
    assert runs("if exist x (taskkill /PID 211)", "cmd") == [None, "taskkill", None]


def test_cmd_values_and_control_words():
    assert words("echo %PATH% !X!", "cmd") == [["echo", "%PATH%", "!X!"]]
    for control in ("if", "for", "call", "start", "else", "IF"):
        assert runs(f"{control} x taskkill /PID 211", "cmd") == [None]


# --- the command word -----------------------------------------------------------------------

@pytest.mark.parametrize("text, name", [
    ("FOO=1 BAR=2 kill 1", "kill"),
    ("sudo kill 1", "kill"), ("sudo -n kill 1", "kill"), ("sudo -u root kill 1", "kill"),
    ("sudo -uroot kill 1", "kill"), ("sudo --user=root kill 1", "kill"),
    ("sudo --preserve-env=PATH kill 1", "kill"), ("sudo -nE kill 1", "kill"),
    ("sudo -nu root kill 1", "kill"), ("sudo -- kill 1", "kill"),
    ("/usr/bin/sudo kill 1", "kill"),
    ("doas -u root kill 1", "kill"), ("env -i A=1 kill 1", "kill"), ("env - kill 1", "kill"),
    ("env -u X kill 1", "kill"), ("nohup kill 1", "kill"), ("builtin kill 1", "kill"),
    ("exec -a x kill 1", "kill"), ("command -p kill 1", "kill"), ("nice -n 5 kill 1", "kill"),
    ("nice -5 kill 1", "kill"), ("timeout 5 kill 1", "kill"),
    ("timeout -s KILL -k 2 5 kill 1", "kill"), ("timeout --foreground 5 kill 1", "kill"),
    ("setsid -f kill 1", "kill"), ("stdbuf -oL kill 1", "kill"),
    ("sudo nohup env A=1 kill 1", "kill"),
])
def test_command_word_past_each_wrapper(text, name):
    assert runs(text) == [name]


#: Each long form a wrapper's tool takes for a short option the table names.
LONG_PLAIN = [("sudo", o) for o in ("--askpass", "--background", "--preserve-env",
                                    "--set-home", "--reset-timestamp", "--non-interactive",
                                    "--preserve-groups", "--stdin")] + [
    ("env", "--ignore-environment"), ("env", "--null"), ("timeout 5", "--verbose"),
    ("setsid", "--ctty"), ("setsid", "--fork"), ("setsid", "--wait")]
LONG_VALUED = [("sudo", o) for o in ("--close-from", "--chdir", "--group", "--host",
                                     "--prompt", "--role", "--chroot", "--type", "--user",
                                     "--other-user")] + [
    ("env", "--unset"), ("env", "--chdir"), ("nice", "--adjustment"),
    ("timeout 5", "--signal"), ("timeout 5", "--kill-after"), ("stdbuf", "--input"),
    ("stdbuf", "--output"), ("stdbuf", "--error")]


def _long(wrapper: str, option: str, value: str | None) -> str:
    """`wrapper option [value] kill 1`, with timeout's duration after its options."""
    name, _, duration = wrapper.partition(" ")
    words = [name, option] + ([value] if value else []) + ([duration] if duration else [])
    return " ".join(words + ["kill", "1"])


@pytest.mark.parametrize("wrapper, option", LONG_PLAIN)
def test_command_word_past_a_long_option_without_a_value(wrapper, option):
    assert runs(_long(wrapper, option, None)) == ["kill"]


@pytest.mark.parametrize("wrapper, option", LONG_VALUED)
def test_command_word_past_a_long_option_and_its_value_in_either_form(wrapper, option):
    assert runs(_long(wrapper, option, "X")) == ["kill"]
    assert runs(_long(wrapper, option + "=X", None)) == ["kill"]


@pytest.mark.parametrize("text", [
    "sudo --login kill 1", "sudo -i kill 1", "env -S 'kill 1'", "timeout --frob 5 kill",
    "sudo --non-interactiv kill 1", "env --split-string='kill 1'",
    "command -v kill", "A=1", "sudo", "for x in y", "case x in", "}", "fi", "done", "esac",
    "((x))",
])
def test_command_word_is_none(text):
    assert runs(text)[0] is None


def test_command_word_in_powershell_and_cmd():
    assert runs("& kill 1", "powershell") == ["kill"]
    assert runs(". kill 1", "powershell") == ["kill"]
    for statement in ("if", "while", "foreach", "switch", "try", "function", "param"):
        assert runs(f"{statement} x", "powershell") == [None]
    assert runs("@ taskkill /PID 1", "cmd") == ["taskkill"]


@pytest.mark.parametrize("text", [
    "$x = kill 1", "$null = kill 1", "$x= kill 1", "[int]$x = kill 1", "[int] $x = kill 1",
    "[void]$x = kill 1", "$x += kill 1", "$x -= kill 1", "$x *= kill 1", "$x /= kill 1",
    "$x %= kill 1", "$x ??= kill 1", "$x+= kill 1", "$a, $b = kill 1", "$a,$b = kill 1",
    "$x.y = kill 1", "$a[0] = kill 1", "$env:X = kill 1", "${x} = kill 1",
    "$script:x = kill 1", "$x = $y = kill 1", "$x = . kill 1",
])
def test_ps_an_assignment_s_pipeline_is_a_command(text):
    assert runs(text, "powershell") == ["kill"]


def test_ps_what_is_not_an_assignment_keeps_its_first_word():
    assert runs("$x", "powershell") == ["$x"]
    assert runs("$x -eq 1", "powershell") == ["$x"]
    assert runs("$x.Kill()", "powershell") == ["$x.Kill\x00"]
    assert runs("$x = 1 | kill 2", "powershell") == ["1", "kill"]
    assert runs("$x = if ($a) { kill 1 }", "powershell") == [None, "kill", None]
    # An operator joined to the command word leaves one word, which isn't split.
    assert runs("$x=kill 1", "powershell") == ["$x=kill"]
    assert runs("$x =kill 1", "powershell") == ["$x"]
    # In `sh` an assignment word is `NAME=value`, as before.
    assert runs("$x = kill 1") == ["$x"]


def test_ps_an_assignment_s_shell_gives_its_script():
    assert scripts("$out = pwsh -Command 'kill 1'", "powershell") == [
        ("kill 1", "powershell", 0, "argument")]
    assert scripts("$null = bash -c 'kill 1'", "powershell") == [
        ("kill 1", "sh", 0, "argument")]


def test_xargs_is_no_wrapper():
    assert runs("pgrep x | xargs kill") == ["pgrep", "xargs"]


# --- scripts handed to a shell --------------------------------------------------------------

@pytest.mark.parametrize("text, script", [
    ("bash -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -lc 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -o pipefail -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash --rcfile f -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash --login --norc -e -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("/bin/sh -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("zsh -c x; dash -c y; ksh -c z", ("x", "sh", 0, "argument")),
    ("mksh -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("ash -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -eo pipefail -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -co pipefail 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -oOc pipefail extglob 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash +O extglob -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -Zc 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash +c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("bash -c -x 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("sudo -u root bash -c 'kill 211'", ("kill 211", "sh", 0, "argument")),
    ("pwsh -NoProfile -Command Stop-Process -Id 211",
     ("Stop-Process -Id 211", "powershell", 0, "argument")),
    ("pwsh -com 'Stop-Process 211'", ("Stop-Process 211", "powershell", 0, "argument")),
    ("pwsh -ep Bypass -c 'Stop-Process 211'", ("Stop-Process 211", "powershell", 0, "argument")),
    ("powershell.exe Stop-Process 211", ("Stop-Process 211", "powershell", 0, "argument")),
    ("PowerShell -NoLogo Stop-Process 211", ("Stop-Process 211", "powershell", 0, "argument")),
    ("cmd /c taskkill /PID 211", ("taskkill /PID 211", "cmd", 0, "argument")),
    ("cmd //k taskkill /PID 211", ("taskkill /PID 211", "cmd", 0, "argument")),
    ("cmd /q /v:on /c taskkill /PID 211", ("taskkill /PID 211", "cmd", 0, "argument")),
    ("bash <<'EOF'\nkill 211\nEOF", ("kill 211\n", "sh", 0, "heredoc")),
    ("bash -s <<< 'kill 211'", ("kill 211", "sh", 0, "here-string")),
    ("pwsh -Command - <<'EOF'\nStop-Process 211\nEOF",
     ("Stop-Process 211\n", "powershell", 0, "heredoc")),
    ("pwsh -File - <<< 'Stop-Process 211'", ("Stop-Process 211", "powershell", 0,
                                             "here-string")),
    ("echo kill 211 | bash", ("kill 211", "sh", 1, "pipe")),
    ("echo -n 'kill 211' | sh", ("kill 211", "sh", 1, "pipe")),
    ("cat <<'EOF' | bash\nkill 211\nEOF", ("kill 211\n", "sh", 1, "pipe")),
    ("cat - <<< 'kill 211' | bash", ("kill 211", "sh", 1, "pipe")),
    ("echo kill 211 |& sh", ("kill 211", "sh", 1, "pipe")),
])
def test_each_shell_and_source_gives_its_script(text, script):
    assert scripts(text)[0] == script


def test_scripts_in_powershell_and_cmd_text():
    assert scripts("Write-Output 'Stop-Process 211' 'kill 5' | pwsh -Command -",
                   "powershell") == [("Stop-Process 211\nkill 5", "powershell", 1, "pipe")]
    assert scripts("echo 'Stop-Process 211' | powershell", "powershell") == [
        ("Stop-Process 211", "powershell", 1, "pipe")]
    assert scripts("bash -c 'kill 211'", "powershell") == [("kill 211", "sh", 0, "argument")]
    assert scripts("C:\\Windows\\System32\\CMD.EXE /C taskkill /PID 211", "powershell") == [
        ("taskkill /PID 211", "cmd", 0, "argument")]
    # An escape kept as written gives no script.
    assert scripts('pwsh -c "Stop-Process 211`n"', "powershell") == []
    assert scripts('cmd /c "taskkill /PID 211"', "cmd") == [
        ("taskkill /PID 211", "cmd", 0, "argument")]
    assert scripts("if exist x (cmd /c taskkill /PID 211)", "cmd") == [
        ("taskkill /PID 211", "cmd", 1, "argument")]


def test_an_unquoted_heredoc_fed_to_a_shell_is_unescaped_first():
    (script,) = scripts('bash <<EOF\necho "\\$(kill 211)" \\`x\\` \\\\ a\\\nb\nEOF')
    assert script[0] == 'echo "$(kill 211)" `x` \\ ab\n'
    assert words(script[0])[0][:1] == ["echo"]
    assert read_shell(script[0]).commands[0].words[1].subs == ("kill 211",)
    (script,) = scripts("bash <<'EOF'\necho \"\\$(kill 211)\"\nEOF")
    assert script[0] == 'echo "\\$(kill 211)"\n'


def test_a_tab_stripped_heredoc_fed_to_a_shell_loses_each_line_s_tabs():
    (script,) = scripts("bash <<-EOF\n\tcat <<X\n\tX\n\tkill 211\n\tEOF")
    assert script[0] == "cat <<X\nX\nkill 211\n"
    assert words(script[0]) == [["cat"], ["kill", "211"], []]


@pytest.mark.parametrize("text", [
    "bash --frob -c 'kill 211'",            # an option the shell's row doesn't name
    "bash -1 -c 'kill 211'",                # a cluster of anything but letters
    "bash -co pipefail",                    # `o` took the only word left
    "bash script.sh <<'EOF'\nkill 211\nEOF",  # a script file: the heredoc is its input
    "pwsh script.ps1",                      # pwsh's bare operand is -File
    "pwsh -File x.ps1",
    "pwsh -EncodedCommand AAAA",
    "pwsh -e AAAA",
    "pwsh -Frob -c x",
    "printf 'kill 211\\n' | sh",            # escapes a program processes
    "echo -e 'kill 211' | sh",
    "echo -E 'kill 211' | sh",
    "echo 'kill 211\\n' | sh",
    "bash -c $'kill 211\\n'",               # an escape kept as written
    "cat pids.txt | sh",                    # text from outside the command
    "sh < f",
    "curl x | sh",
    "echo kill 211 > f | sh",
    "cmd /x taskkill",
    "bash -c",
    "eval 'kill 211'",
    "ssh host kill 211",
    "python -c 'import os'",
])
def test_no_script_for_what_the_finder_does_not_read(text):
    assert scripts(text) == []


def test_a_script_names_the_command_words_it_was_made_from():
    def indexes(text, dialect="sh"):
        shell = read_shell(text, dialect=dialect)
        return [(s.word_indexes, [shell.commands[s.command_index].words[i].text
                                  for i in s.word_indexes])
                for s in shell_scripts(shell, dialect=dialect)]

    assert indexes("bash -lc 'kill 211' x") == [((2,), ["kill 211"])]
    assert indexes("FOO=1 sudo -u root bash -co pipefail 'kill 211'") == [
        ((7,), ["kill 211"])]
    assert indexes("pwsh -NoProfile -Command Stop-Process -Id 211") == [
        ((3, 4, 5), ["Stop-Process", "-Id", "211"])]
    assert indexes("& powershell Stop-Process 211", "powershell") == [
        ((2, 3), ["Stop-Process", "211"])]
    assert indexes("@cmd /q /c taskkill /PID 211", "cmd") == [
        ((4, 5, 6), ["taskkill", "/PID", "211"])]
    assert indexes("bash <<'EOF'\nkill 211\nEOF") == [((), [])]
    assert indexes("echo kill 211 | bash -s") == [((), [])]
    assert indexes("pwsh -Command - <<< 'Stop-Process 211'") == [((), [])]


@pytest.mark.parametrize("text, dialect, script", [
    ("& 'C:\\Program Files\\Git\\bin\\bash.exe' -c 'kill 211'", "powershell",
     ("kill 211", "sh", 0, "argument")),
    ("'C:/Program Files/Git/usr/bin/sh.EXE' -c 'kill 211'", "sh",
     ("kill 211", "sh", 0, "argument")),
    ("& 'C:\\Program Files\\PowerShell\\7\\PWSH.EXE' -Command Stop-Process 211",
     "powershell", ("Stop-Process 211", "powershell", 0, "argument")),
    ("'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\PowerShell.Exe' Stop-Process 211",
     "sh", ("Stop-Process 211", "powershell", 0, "argument")),
    ("C:/Windows/System32/Cmd.Exe /C taskkill /PID 211", "cmd",
     ("taskkill /PID 211", "cmd", 0, "argument")),
    ("PWSH -c 'Stop-Process 211'", "sh", ("Stop-Process 211", "powershell", 0, "argument")),
    ("BASH.EXE -c 'kill 211'", "sh", ("kill 211", "sh", 0, "argument")),
    ("& 'C:\\Git\\bin\\Bash.exe' -c 'kill 211'", "powershell",
     ("kill 211", "sh", 0, "argument")),
    ("C:\\Git\\bin\\Bash.exe -c \"kill 211\"", "cmd", ("kill 211", "sh", 0, "argument")),
    ("ZSH -c 'kill 211'", "sh", ("kill 211", "sh", 0, "argument")),
])
def test_a_shell_is_named_by_its_last_part_across_either_slash_without_exe(
        text, dialect, script):
    assert scripts(text, dialect) == [script]


def test_a_shell_with_no_input_source_gives_none():
    assert scripts("bash") == [] and scripts("ls; bash") == []


# --- what isn't read ------------------------------------------------------------------------

def test_each_class_that_is_not_read_runs_nothing_the_reader_names():
    # 1. values
    assert words("k=kill; $k 211") == [["k=kill"], ["$k", "211"]]
    # 2. escapes a program processes
    assert scripts("printf 'kill 211\\n' | sh") == []
    # 3. strings run as code
    assert runs("eval 'kill 211'") == ["eval"] and scripts("eval 'kill 211'") == []
    assert runs("iex 'Stop-Process 211'", "powershell") == ["iex"]
    # 4. cmd's control words
    assert scripts("cmd /c start /b taskkill /PID 211") == [
        ("start /b taskkill /PID 211", "cmd", 0, "argument")]
    assert runs("start /b taskkill /PID 211", "cmd") == [None]
    # 5. options the tables don't name
    assert runs("env -S 'kill 211'") == [None]
    # 6. programs the tables don't name
    assert runs("ssh host kill 211") == ["ssh"] and scripts("ssh host kill 211") == []
    # 7. text from outside the command
    assert scripts("cat pids.txt | sh") == [] and scripts("bash f.sh") == []


# --- rows from the copies it replaces -------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("echo ok; sudo kill -9 211", [["echo", "ok"], ["sudo", "kill", "-9", "211"]]),
    ("FOO=1 env kill 211 && ls", [["FOO=1", "env", "kill", "211"], ["ls"]]),
    ("sleep 1\nkill 211", [["sleep", "1"], ["kill", "211"]]),
    ("echo 'kill 211'", [["echo", "kill 211"]]),
    ("# kill 211", []),
    ("kill 211&>x; kill 111", [["kill", "211"], ["kill", "111"]]),
    ("kill 211 >| out 111", [["kill", "211", "111"]]),
    ("kill 211 <<< 2", [["kill", "211"]]),
    ("cat 'a file' | wc -l", [["cat", "a file"], ["wc", "-l"]]),
    ("grep a'|'b f | wc -l", [["grep", "a|b", "f"], ["wc", "-l"]]),
    ("cd /x&&git status --short", [["cd", "/x"], ["git", "status", "--short"]]),
    ("git commit -m 'costs $5, see `x`'", [["git", "commit", "-m", "costs $5, see `x`"]]),
    ('git commit -m "say \\"hi\\""', [["git", "commit", "-m", 'say "hi"']]),
    ("wc -l < T", [["wc", "-l"]]),
])
def test_rows_ported_from_the_copies(text, expected):
    assert words(text) == expected


def test_a_redirect_target_and_every_substitution_stay_readable():
    shell = read_shell("moxi sessions find $(cat T) `cat U` < V")
    (command,) = shell.commands
    assert [w.subs for w in command.words if w.subs] == [("cat T",), ("cat U",)]
    assert command.redirects[0].target.text == "V"


def test_the_records_are_values():
    assert ShellRedirect(">", None, None) == ShellRedirect(">", None, None, None, ())
    assert ShellCommand((), (), "") == ShellCommand((), (), "")
    assert ShellScript("x", "sh", 0, "argument").source in SCRIPT_SOURCES

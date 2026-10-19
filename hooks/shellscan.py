"""A quote-, comment- and heredoc-aware shell scanner, shared by the PreToolUse(Bash) hooks.

Why not shlex: shlex has no notion of heredocs, comments-after-words, command substitution or
separators, so `cat <<'EOF'\\nnpm run dev\\nEOF` and `echo "rm -rf x"` look like commands to it.
A hook that matches command TEXT fires on documentation, heredocs and grep patterns; a hook that
asks this scanner for the PROGRAM of each segment does not.

In this package its one consumer is instinct-recall.py, which asks it whether a Bash command
only prints existing content (cat, grep, git log, ...) so a symptom quoted in a file is not
mistaken for one that just happened. Without this module the hook still runs, just noisier.

It is a hand-written scanner for catching agent MISTAKES, not a sandbox: a script FILE run by
`bash x.sh` and interpreter code strings are opaque to it.
"""
import re
from dataclasses import dataclass, field

# The closers (fi done esac) only ever stand alone as a segment; without them `fi` reads as a program.
KEYWORDS = {"{", "}", "!", "then", "do", "else", "elif", "if", "while", "until", "time", "coproc", "fi", "done", "esac"}


@dataclass
class Word:
    text: str = ""
    quoted: bool = False
    glob: bool = False  # an unquoted * ? [ { or }
    dollar: bool = False  # an unquoted or double-quoted $
    redirect: bool = False  # target of a < or > redirection, not an argument
    redirect_out: bool = False  # ...and that redirection writes: > >> >| <> &>
    first_quoted: bool = False  # its first character came from a quote or escape (so `NAME=` is not an assignment)


@dataclass
class Segment:
    words: list = field(default_factory=list)
    heredocs: list = field(default_factory=list)  # bodies fed to this segment's stdin
    end: str = ""  # separator that ended it: ; && || | & ( ) or newline
    depth: int = 0  # subshell depth it ran at


def scan(s, i=0, nested=0, until_paren=False):
    """Tokenise a shell command into segments; raises ValueError on unterminated quotes.

    Command substitutions ($(…), `…`, <(…), >(…)) are scanned recursively and their segments
    returned too (depth > 0), so a command inside a substitution is checked like any other; the
    word that contained it is marked `dollar`. With nested > 0 the scan stops at the `)` that
    closes the substitution and returns its index."""
    segs, cur, w = [], Segment(depth=nested), None
    pending, depth, redirect_next, redirect_out = [], 0, False, False

    def end_word():
        nonlocal w, redirect_next
        if w is not None:
            w.redirect = redirect_next
            w.redirect_out = redirect_next and redirect_out
            redirect_next = False
            cur.words.append(w)
        w = None

    def end_seg(sep):
        nonlocal cur
        end_word()
        cur.end = sep
        if cur.words or cur.heredocs:
            segs.append(cur)
        cur = Segment(depth=nested + depth)

    def word(quoting=False):
        nonlocal w
        if w is None:
            w = Word(first_quoted=quoting)
        return w

    def substitution(at):
        """Scan the substitution starting at s[at]; return the index of its last char."""
        if s.startswith("$((", at):  # arithmetic: no commands inside
            close = s.find("))", at)
            if close == -1:
                raise ValueError("unterminated $((")
            return close + 1
        if s[at] == "`":
            close = at + 1
            while close < len(s) and s[close] != "`":
                close += 2 if s[close] == "\\" else 1
            if close >= len(s):
                raise ValueError("unterminated backtick")
            segs.extend(scan(s[at + 1:close], 0, nested + 1)[0])  # runs to the end of the backtick text
            return close
        inner, close = scan(s, at + 2, nested + 1, until_paren=True)  # $( <( >(
        if close >= len(s):
            raise ValueError("unterminated substitution")
        segs.extend(inner)
        return close

    while i < len(s):
        c = s[i]
        if c in " \t":
            end_word()
        elif c == "\n":
            end_seg("\n")
            for delim, strip_tabs in pending:  # consume heredoc bodies
                body = []
                j = s.find("\n", i + 1)
                while True:
                    line_end = len(s) if j == -1 else j
                    line = s[i + 1:line_end]
                    i = line_end
                    if (line.lstrip("\t") if strip_tabs else line) == delim or j == -1:
                        break
                    body.append(line)
                    j = s.find("\n", i + 1)
                (segs[-1] if segs else cur).heredocs.append("\n".join(body))
            pending = []
        elif c == ")" and until_paren and depth == 0:
            end_seg(")")
            return segs, i
        elif c in ";&|()":
            two = s[i:i + 2]
            if c == "(":
                depth += 1
            sep = two if two in ("&&", "||", ";;") else c
            end_seg(sep)
            if c == ")":
                depth -= 1
            i += len(sep) - 1
        elif c == "#" and w is None:
            while i + 1 < len(s) and s[i + 1] != "\n":
                i += 1
        elif c == "\\" and s.startswith("\n", i + 1):
            i += 1  # line continuation: `r\<newline>m` is the word `rm`
        elif c == "\\":
            word(True).text += s[i + 1] if i + 1 < len(s) else ""
            w.quoted = True
            i += 1
        elif c == "'":
            end = s.find("'", i + 1)
            if end == -1:
                raise ValueError("unterminated single quote")
            word(True).text += s[i + 1:end]
            w.quoted = True
            i = end
        elif c == '"':
            word(True).quoted = True
            i += 1
            while i < len(s) and s[i] != '"':
                if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in '$`"\\\n':
                    w.text += s[i + 1]
                    i += 2
                    continue
                if s[i] == "`" or s.startswith("$(", i):
                    i = substitution(i) + 1
                    w.dollar = True
                    w.text += "$(…)"
                    continue
                if s[i] == "$":
                    w.dollar = True
                w.text += s[i]
                i += 1
            if i >= len(s):
                raise ValueError("unterminated double quote")
        elif c == "<" and s.startswith("<<", i) and not s.startswith("<<<", i):
            end_word()
            i += 2
            strip_tabs = i < len(s) and s[i] == "-"
            i += 1 if strip_tabs else 0
            while i < len(s) and s[i] in " \t":
                i += 1
            delim = ""
            while i < len(s) and s[i] not in " \t\n;&|()<>":
                if s[i] not in "'\"\\":
                    delim += s[i]
                i += 1
            if not delim:
                raise ValueError("heredoc without a delimiter")
            pending.append((delim, strip_tabs))
            continue
        elif c in "<>" and s.startswith("(", i + 1):  # process substitution
            end_word()
            i = substitution(i)
            word().dollar = True
            w.text = "/dev/fd/N"
        elif c in "<>":
            if w is not None and w.text.isdigit() and not w.quoted:
                w = None  # an fd number like the 2 in 2>&1
            end_word()
            op = c
            while i + 1 < len(s) and s[i + 1] in "<>|":
                i += 1
                op += s[i]
            if i + 1 < len(s) and s[i + 1] == "&":
                i += 1
                while i + 1 < len(s) and (s[i + 1].isdigit() or s[i + 1] == "-"):
                    i += 1
            else:
                redirect_next = True
                redirect_out = ">" in op
        elif s.startswith("${", i):  # one word, even with zsh flags `${(f)x}`; nested $( ) inside still scanned
            close, nest = i + 2, 1
            while close < len(s) and nest:
                nest += {"{": 1, "}": -1}.get(s[close], 0)
                close += 1
            if nest:
                raise ValueError("unterminated ${")
            inner = s[i + 2:close - 1]
            if "$(" in inner or "`" in inner:
                segs.extend(scan(inner, 0, nested + 1)[0])
            word().dollar = True
            w.text += "${…}"
            i = close - 1
        elif c == "`" or s.startswith("$(", i):
            i = substitution(i)
            word().dollar = True
            w.text += "$(…)"
        else:
            if c == "$":
                word().dollar = True
            if c in "*?[{}":
                word().glob = True
            word().text += c
        i += 1
    if until_paren:
        raise ValueError("unterminated substitution")
    end_seg("")
    return segs, i


def strip_prefix(words):
    """Drop env assignments and shell keywords. Returns (words, env_names)."""
    env, i = [], 0
    while i < len(words):
        w = words[i]
        if w.redirect:
            pass
        elif not w.first_quoted and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w.text):
            env.append(w.text.split("=", 1)[0])
        elif not w.quoted and w.text in KEYWORDS:
            pass
        else:
            break
        i += 1
    return [w for w in words[i:] if not w.redirect], env

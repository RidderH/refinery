#!/usr/bin/env python3
"""PostToolUse / PostToolUseFailure hook: surface an instinct when tool output matches its trigger.

Pull-only lookup ("search by symptom") rarely happens mid-failure, so this hook does the
search: it splits each instinct's `trigger:` on `|`, keeps the phrases long enough to be
literal symptoms, and substring-matches them against the tool's output. Each instinct is
surfaced at most once per session. Warn-only: always exits 0, never blocks a tool call.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

INSTINCT_DIR = Path(os.environ.get("REFINERY_INSTINCT_DIR",
                                   Path.home() / ".claude/homunculus/instincts/personal"))
STATE_DIR = Path(os.environ.get("INSTINCT_RECALL_STATE", "/tmp/claude-instinct-recall"))
LOG = os.environ.get("INSTINCT_RECALL_LOG", str(Path.home() / ".claude/instinct-recall.log"))
MIN_PHRASE = 16
MAX_TEXT = 20_000
LIMIT = 2
# A trigger phrase is only a symptom if it reads like an error message. The lookbehind
# rejects code tokens that merely contain the word (console.error, e2e_preexisting_failures).
# Without this filter the hook fires on a sizeable share of all tool results, mostly on topic
# words and identifiers in ordinary file reads.
ERROR_WORD = re.compile(
    r"(?<![.\w])(error|errors|not found|no such|cannot|can't|could not|couldn't|failed|failure"
    r"|denied|refused|not allowed|invalid|unexpected|exit code|exited|no matches|warning"
    r"|missing|undefined|does not exist|unknown (?:revision|option|command|flag|key)|timed out"
    r"|blocked|aborted|fatal|exception|traceback|panic|eaddrinuse|econnrefused|enoent"
    r"|ts\d{4}|violates|violation|conflict|rejected|unable|exceeded|too many)(?!\w)")
WATCHED_TOOLS = {"Bash"}
# Programs that only print what is already on disk. A command made of nothing else shows file
# content — a transcript, a log, a digest, a patch — where a lesson's symptom is quoted, not
# happening; unfiltered, such reads are a large share of all fires. Interpreters are
# deliberately absent: what they print is the program's own output.
READERS = {"cat", "head", "tail", "sed", "grep", "egrep", "fgrep", "rg", "ugrep", "less", "more", "wc", "cut",
           "awk", "jq", "nl", "sort", "uniq", "tr", "column", "strings", "bat", "ls", "echo", "printf", "cd",
           "true", "find", "diff", "cmp", "file", "stat", "basename", "dirname", "realpath", "xxd", "od",
           "fold", "paste", "tac", "rev"}
GIT_READERS = {"show", "diff", "log", "grep", "blame", "cat-file"}
# What the shell itself prints when it aborts a command, or the harness when it refuses one —
# real even when the command only reads.
SHELL_ORIGIN = re.compile(r"^(\(eval\):\d+:|zsh:|bash:|sh:|permission to use \w+ with command )")


def norm(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def is_usable_phrase(p):
    return len(p) >= MIN_PHRASE and bool(ERROR_WORD.search(p))


def frontmatter(text):
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def field(fm, name):
    m = re.search(rf'^{name}:\s*"(.*?)"\s*$', fm, re.S | re.M)
    if m:
        return m.group(1)
    m = re.search(rf"^{name}:\s*(.*)$", fm, re.M)
    return m.group(1).strip() if m else ""


def build_index(directory):
    index = []
    for f in sorted(Path(directory).glob("*.md")):
        if f.name.endswith(".evidence.md"):
            continue
        fm = frontmatter(f.read_text(errors="ignore"))
        trigger = field(fm, "trigger")
        if not trigger:
            continue
        phrases = [norm(p).strip("`'\" ") for p in trigger.replace('\\"', '"').split("|")]
        try:
            conf = float(field(fm, "confidence") or 0)
        except ValueError:
            conf = 0.0
        index.append({"id": field(fm, "id") or f.stem, "path": str(f), "confidence": conf,
                      "action": re.sub(r"\s+", " ", field(fm, "action")).strip(),
                      "phrases": [p for p in phrases if is_usable_phrase(p)]})
    return index


def quoted(prefix):
    # Docs and source quote a symptom (`test timed out in 10000ms`); real output does not.
    return prefix.count("`") % 2 == 1 or prefix.count('"') % 2 == 1


def occurs_unquoted(phrase, line):
    # "…" in a trigger stands for the variable part ("command … has been denied").
    first, *rest = [f.strip() for f in phrase.split("…")]
    i = line.find(first)
    while i >= 0:
        if not quoted(line[:i]) and follows_in_order(rest, line, i + len(first)):
            return True
        i = line.find(first, i + 1)
    return False


def follows_in_order(fragments, line, pos):
    for f in fragments:
        pos = line.find(f, pos)
        if pos < 0:
            return False
        pos += len(f)
    return True


def match(text, index, limit=LIMIT):
    lines = [norm(l) for l in text.splitlines()]
    hits = []
    for e in index:
        found = [p for p in e["phrases"] if any(occurs_unquoted(p, l) for l in lines)]
        if found:
            best = max(found, key=len)
            hits.append({**e, "matched": best, "rank": e["phrases"].index(best)})
    # Ties: a phrase listed earlier in the trigger is the instinct's primary symptom.
    hits.sort(key=lambda h: (-h["confidence"], -len(h["matched"]), h["rank"]))
    return hits[:limit]


def flatten(raw):
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return "\n".join(flatten(v) for v in raw.values() if isinstance(v, (str, list, dict)))
    if isinstance(raw, list):
        return "\n".join(flatten(v) for v in raw)
    return ""


def output_text(payload):
    raw = payload.get("error") or payload.get("tool_response") or payload.get("tool_result") \
        or payload.get("tool_output") or ""
    return flatten(raw)[:MAX_TEXT]


def only_reads(command):
    """True when every program in the command just prints existing content (see READERS)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from shellscan import scan, strip_prefix
        segs, _ = scan(command)
    except Exception:  # unparseable, or the scanner missing: not provably a read
        return False
    programs = 0
    for seg in segs:
        words, _ = strip_prefix(seg.words)
        if not words:
            continue
        programs += 1
        prog, args = words[0].text.rsplit("/", 1)[-1], [w.text for w in words[1:]]
        if prog == "sed" and any(a.startswith("-i") or a == "--in-place" for a in args):
            return False
        if prog == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in args):
            return False
        if prog == "git":
            # `-C <path>` and `-c <key=val>` take a value that is not the subcommand.
            sub, i = "", 0
            while i < len(args):
                if args[i] in ("-C", "-c"):
                    i += 2
                elif args[i].startswith("-"):
                    i += 1
                else:
                    sub = args[i]
                    break
            if sub not in GIT_READERS:
                return False
        elif prog not in READERS:
            return False
    return programs > 0


def shell_origin_lines(text):
    return "\n".join(l for l in text.splitlines() if SHELL_ORIGIN.match(l.strip().lower()))


def touches_corpus(payload):
    inp = json.dumps(payload.get("tool_input") or {})
    return "homunculus/instincts" in inp or str(INSTINCT_DIR) in inp or "INSTINCT_DIR" in inp


def reads_past_sessions(command):
    """True when the command names this hook's log or the transcripts under ~/.claude/projects.
    Whatever program digs through them prints quoted symptoms, so it is handled like a reader."""
    return "instinct-recall.log" in command or ".claude/projects/" in command


def seen_file(session):
    return STATE_DIR / re.sub(r"[^\w-]", "_", session or "nosession")


def render(hits):
    lines = ["[instinct-recall] This tool output matches a past lesson. Read it before acting on the output:"]
    for h in hits:
        action = h["action"] if len(h["action"]) <= 400 else h["action"][:400] + "…"
        lines.append(f'- {h["id"]} (confidence {h["confidence"]}) matched "{h["matched"]}"\n'
                     f'  action: {action}\n  full: cat {h["path"]}')
    return "\n".join(lines)


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return
    if not isinstance(payload, dict) or touches_corpus(payload) or not INSTINCT_DIR.is_dir():
        return
    # Successful Read/Grep output is file content, not a symptom; failures of any tool are.
    if payload.get("hook_event_name") != "PostToolUseFailure" \
            and payload.get("tool_name") not in WATCHED_TOOLS:
        return
    text = output_text(payload)
    command = (payload.get("tool_input") or {}).get("command") or ""
    if payload.get("tool_name") == "Bash" and (only_reads(command) or reads_past_sessions(command)):
        text = shell_origin_lines(text)
    if not text:
        return
    sf = seen_file(payload.get("session_id"))
    seen = set(sf.read_text().split()) if sf.exists() else set()
    hits = [h for h in match(text, build_index(INSTINCT_DIR), limit=50) if h["id"] not in seen][:LIMIT]
    if not hits:
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with sf.open("a") as fh:
        fh.write("".join(h["id"] + "\n" for h in hits))
    event = payload.get("hook_event_name", "PostToolUse")
    try:
        with open(LOG, "a") as fh:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for h in hits:
                fh.write(f'{ts}\t{payload.get("session_id")}\t{event}\t{payload.get("tool_name")}\t{h["id"]}\t{h["matched"]}\n')
    except OSError:
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": render(hits)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

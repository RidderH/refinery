#!/usr/bin/env python3
"""Replay a candidate `trigger:` through the real instinct-recall hook before it is written.

The recall hook (~/.claude/hooks/instinct-recall.py) is how most instincts get read: it
surfaces one when a tool's output contains a trigger phrase. This probe imports that hook and
answers, with its own logic:
  reachable — does the hook index any phrase of this trigger at all?
  session   — would it have fired on this session's tool output, at the moment of need?
  recent    — on how many recent sessions would it fire, and on what output (noise check)?

Usage:
    trigger_probe.py "<trigger line>" | <instinct.md>  [--recent N] [--session FILE]
    trigger_probe.py "<trigger line>" | <instinct.md>  --no-replay
    trigger_probe.py --selftest

Exit: 0 fires in this session · 1 reachable but silent in this session · 2 unreachable.
With --no-replay: 0 reachable · 2 unreachable.
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

# The installed hook is the one that runs, so it wins; a checkout's copy (skills/ is linked
# into the agent's skills root, and resolve() follows the link) covers a host without one.
HOOK_CANDIDATES = [Path.home() / ".claude/hooks/instinct-recall.py",
                   Path(__file__).resolve().parents[3] / "hooks/instinct-recall.py"]
HOOK = next((p for p in HOOK_CANDIDATES if p.is_file()), None)
if HOOK is None:
    sys.exit("trigger_probe: instinct-recall.py not found; looked in "
             + ", ".join(str(p) for p in HOOK_CANDIDATES))
PROJECTS = Path.home() / ".claude/projects"
spec = importlib.util.spec_from_file_location("recall", HOOK)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def split_phrases(trigger):
    raw = [R.norm(p).strip("`'\" ") for p in trigger.replace('\\"', '"').split("|")]
    return [p for p in raw if R.is_usable_phrase(p)], [p for p in raw if p and not R.is_usable_phrase(p)]


def current_session():
    # Claude Code stores a project's transcripts under its cwd with every non-alphanumeric as '-'.
    project = PROJECTS / re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
    found = sorted(project.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def recent_sessions(n, exclude):
    found = sorted(PROJECTS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
    return [p for p in found if p != exclude][-n:] if n > 0 else []


def tool_outputs(path):
    """Yield (command, text) for every tool result the hook would have scanned."""
    uses = {}
    for line in open(path, errors="ignore"):
        try:
            content = (json.loads(line).get("message") or {}).get("content")
        except (ValueError, AttributeError):
            continue
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use":
                uses[c.get("id")] = (c.get("name"), c.get("input") or {})
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in uses:
                name, tool_input = uses[c["tool_use_id"]]
                command = tool_input.get("command") or ""
                # Mirror the hook: Bash always, other tools only on failure; never corpus work
                # or this probe's own output, which quotes the phrases it tests.
                if name != "Bash" and not c.get("is_error"):
                    continue
                if R.touches_corpus({"tool_input": tool_input}) or "trigger_probe" in command:
                    continue
                text = R.flatten(c.get("content"))[:R.MAX_TEXT]
                if name == "Bash" and R.only_reads(command):
                    text = R.shell_origin_lines(text)
                if text:
                    yield command or name, text


def fires(path, entry):
    return [(cmd, R.match(text, entry)[0]["matched"]) for cmd, text in tool_outputs(path)
            if R.match(text, entry)]


def report(label, path, entry):
    hits = fires(path, entry)
    if hits:
        cmd, phrase = hits[0]
        print(f"  {label} {path.name}: {len(hits)}x — first on {cmd[:80]!r} via {phrase!r}")
    return bool(hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trigger")
    ap.add_argument("--recent", type=int, default=40)
    ap.add_argument("--session", type=Path)
    ap.add_argument("--no-replay", action="store_true",
                    help="reachability only: exit 0 reachable, 2 unreachable")
    args = ap.parse_args()

    trigger = args.trigger
    if trigger.endswith(".md") and Path(trigger).is_file():
        trigger = R.field(R.frontmatter(Path(trigger).read_text(errors="ignore")), "trigger")
    usable, ignored = split_phrases(trigger)
    print(f"usable phrases ({len(usable)}): {usable}")
    print(f"ignored by the hook ({len(ignored)}): {ignored}")
    if not usable:
        print("UNREACHABLE: the recall hook can never surface this trigger. A usable phrase is a "
              f"literal symptom of >= {R.MIN_PHRASE} characters containing an error word "
              "(failed, denied, not found, no such, cannot, exit code, ...).")
        return 2
    if args.no_replay:
        print("REACHABLE: the recall hook indexes this trigger.")
        return 0

    entry = [{"id": "candidate", "path": "-", "confidence": 1.0, "action": "", "phrases": usable}]
    session = args.session or current_session()
    in_session = bool(session) and report("SESSION", session, entry)
    if session and not in_session:
        print(f"  SESSION {session.name}: silent — no tool output here contains a usable phrase")

    recent = recent_sessions(args.recent, session)
    noisy = sum(report("RECENT ", p, entry) for p in recent)
    print(f"session: {'FIRES' if in_session else 'SILENT'} · recent: fires in {noisy}/{len(recent)}")
    return 0 if in_session else 1


def selftest():
    """Assert every exit path on fixtures, so a green corpus scan means something."""
    import subprocess
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    session = tmp / "session.jsonl"
    rows = [
        {"message": {"content": [{"type": "tool_use", "id": "a", "name": "Bash",
                                  "input": {"command": "git checkout feature/x"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "a",
                                  "content": "fatal: not a git repository (or any of the parent directories): .git"}]}},
        {"message": {"content": [{"type": "tool_use", "id": "b", "name": "Bash",
                                  "input": {"command": "cat notes.md"}}]}},
        {"message": {"content": [{"type": "tool_result", "tool_use_id": "b",
                                  "content": "quoted: permission denied for table orders"}]}},
    ]
    session.write_text("\n".join(json.dumps(r) for r in rows))
    lesson = tmp / "lesson.md"
    lesson.write_text('---\nid: lesson\ntrigger: "checking out a branch |\n'
                      '          fatal: not a git repository"\n---\n')

    def run(*args):
        return subprocess.run([sys.executable, __file__, *args], capture_output=True,
                              text=True).returncode

    zero = subprocess.run([sys.executable, __file__, str(lesson), "--session", str(session),
                           "--recent", "0"], capture_output=True, text=True).stdout

    cases = [
        ("fires on the literal symptom", run("fatal: not a git repository",
                                             "--session", str(session), "--recent", "0"), 0),
        ("silent on a symptom the session never printed",
         run("relation does not exist in schema", "--session", str(session), "--recent", "0"), 1),
        ("a read-only command's output is not a symptom",
         run("permission denied for table", "--session", str(session), "--recent", "0"), 1),
        ("unreachable when no phrase is error-shaped", run("when checking out a branch"), 2),
        ("a multi-line trigger is parsed from the file",
         run(str(lesson), "--session", str(session), "--recent", "0"), 0),
        ("--no-replay: reachable is 0", run(str(lesson), "--no-replay"), 0),
        ("--no-replay: unreachable is 2", run("when checking out a branch", "--no-replay"), 2),
        ("--recent 0 replays no recent sessions", "fires in 0/0" in zero, True),
    ]
    ok = True
    for label, got, want in cases:
        print(f"  {'PASS' if got == want else 'FAIL'}  {label} (exit {got}, want {want})")
        ok = ok and got == want
    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else main())

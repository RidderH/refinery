#!/usr/bin/env python3
"""Find trigger phrases broken across a YAML line wrap — they are unreachable by grep.

Retrieval is `grep`, and `grep` matches within ONE line. A trigger phrase split across two
physical lines can never be found by someone typing that phrase; the file looks findable and
isn't. A break is only safe at a `|` boundary, so every phrase stays whole on its line.

    # BROKEN — "touch-based framer drag" spans the wrap
    trigger: "framer-motion drag | you need to test touch-based
              framer drag | Input.dispatchTouchEvent"

    # CORRECT — the break lands on a pipe
    trigger: "framer-motion drag |
              you need to test touch-based framer drag |
              Input.dispatchTouchEvent"

Usage: python3 ~/.claude/skills/instinct-format/scripts/trigger_wrap.py [DIR]
       python3 ~/.claude/skills/instinct-format/scripts/trigger_wrap.py --selftest

Exit 1 if any file is flagged, 2 if the corpus directory is missing (a check whose body never
ran must not report success). Flags are file:line of the offending break.

`--selftest` builds four fixtures in a temp dir and asserts the detector flags the broken one
and only the broken one. Run it before believing a green corpus scan: green-on-current-code is
not evidence a guard works, since the bad condition may simply be absent.
"""
import pathlib
import sys
import tempfile

DEFAULT_ROOT = pathlib.Path.home() / ".claude/homunculus/instincts/personal"


def trigger_block(lines):
    """Physical lines of the `trigger:` value, as (lineno, text). Empty if no trigger."""
    for i, line in enumerate(lines):
        if not line.startswith("trigger:"):
            continue
        value = line[len("trigger:"):].strip()
        block = [(i + 1, value)]
        # Single-line forms: a closed quote, or an unquoted value that is the whole line.
        if value.startswith('"'):
            if len(value) > 1 and value.endswith('"'):
                return block
        elif value.startswith("'"):
            if len(value) > 1 and value.endswith("'"):
                return block
        elif not value.startswith((">", "|")):
            return block  # unquoted scalar, ends at the newline
        # Multi-line: consume until the quote closes, the indent ends, or the next key starts.
        quote = value[0] if value[:1] in ('"', "'") else ""
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if nxt.startswith("---") or (nxt[:1].isalpha() and ":" in nxt.split(" ")[0]):
                break  # next frontmatter key or the closing fence
            block.append((j + 1, nxt.strip()))
            if quote and nxt.rstrip().endswith(quote):
                break
        return block
    return []


def bad_breaks(block):
    """Line numbers where a phrase continues onto the next line without a pipe boundary."""
    bad = []
    for (lineno, text), (_, nxt) in zip(block, block[1:]):
        here = text.rstrip().rstrip('"').rstrip("'").rstrip()
        if here.endswith("|") or nxt.lstrip().startswith("|"):
            continue
        if not here or not nxt.strip():
            continue
        bad.append((lineno, here, nxt.strip()))
    return bad


FIXTURES = {
    # The BROKEN example from SKILL.md — "touch-based framer drag" spans the wrap.
    "broken-wrap.md": '---\nid: broken-wrap\n'
                      'trigger: "framer-motion drag | you need to test touch-based\n'
                      '          framer drag | Input.dispatchTouchEvent"\n'
                      'domain: testing\n---\n# body\n',
    # Same phrases, broken at the pipes — every phrase whole on its line.
    "correct-wrap.md": '---\nid: correct-wrap\n'
                       'trigger: "framer-motion drag |\n'
                       '          you need to test touch-based framer drag |\n'
                       '          Input.dispatchTouchEvent"\n'
                       'domain: testing\n---\n# body\n',
    # Pipe leading the continuation line is equally safe.
    "leading-pipe.md": '---\nid: leading-pipe\n'
                       'trigger: "framer-motion drag\n'
                       '          | you need to test touch-based framer drag"\n'
                       'domain: testing\n---\n# body\n',
    # The common single-line form must never be flagged.
    "single-line.md": '---\nid: single-line\n'
                      'trigger: "when a codebase-wide fix touches data access patterns in production"\n'
                      'domain: workflow\n---\n# body\n',
}


def selftest():
    """Prove the detector goes red on a known-broken trigger before trusting its green."""
    with tempfile.TemporaryDirectory() as tmp:
        d = pathlib.Path(tmp)
        for name, text in FIXTURES.items():
            (d / name).write_text(text)
        hits = {}
        for p in sorted(d.glob("*.md")):
            bad = bad_breaks(trigger_block(p.read_text().splitlines()))
            if bad:
                hits[p.name] = bad
        ok = True
        if "broken-wrap.md" not in hits:
            print("FAIL: broken-wrap.md was NOT flagged — the detector cannot go red")
            ok = False
        else:
            print("ok: broken-wrap.md flagged (detector can fail)")
        for name in ("correct-wrap.md", "leading-pipe.md", "single-line.md"):
            if name in hits:
                print(f"FAIL: {name} flagged — false positive on a valid trigger")
                ok = False
            else:
                print(f"ok: {name} not flagged")
        print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
        return 0 if ok else 1


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest()
    root = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        print(f"FATAL: {root} is not a directory", file=sys.stderr)
        return 2

    files = [p for p in sorted(root.glob("*.md")) if not p.name.endswith(".evidence.md")]
    if not files:
        print(f"FATAL: no lesson files under {root}", file=sys.stderr)
        return 2

    flagged = notrigger = multiline = 0
    for p in files:
        block = trigger_block(p.read_text().splitlines())
        if not block:
            notrigger += 1
            continue
        if len(block) > 1:
            multiline += 1
        for lineno, here, nxt in bad_breaks(block):
            flagged += 1
            print(f"{p.name}:{lineno}")
            print(f"    ...{here[-58:]}")
            print(f"    {nxt[:58]}...   <- phrase spans this break")

    print(
        f"\nscanned {len(files)} files "
        f"({multiline} multi-line triggers, {notrigger} with no trigger:) "
        f"— {flagged} broken wrap(s)"
    )
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())

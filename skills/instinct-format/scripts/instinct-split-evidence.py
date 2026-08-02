#!/usr/bin/env python3
"""Mechanically split an instinct's evidence pile into <id>.evidence.md.

Moving evidence text is a deterministic text operation, so no model should pay
to read it, reason about it and write it back. Measured on this corpus, prose
below "## Evidence" is 39% of 393k tokens — the single largest slice of the
conversion cost, and the slice with zero judgement in it.

What this does NOT do: write `action:`, restructure the body into
Symptom / What's actually happening / Do this, or compress the retained
entries. Those need judgement and stay with the model.

Safety: dry-run by default. Refuses to touch a file whose evidence section it
cannot parse cleanly, rather than guessing. Everything it edits is committed to
git first, so `git checkout HEAD -- <path>` is the undo.

Usage:
  instinct-split-evidence.py <id> [<id>...]      # dry run, prints a plan
  instinct-split-evidence.py --apply <id> ...    # writes
  instinct-split-evidence.py --all               # dry run over the corpus
"""
import re
import sys
from pathlib import Path

DIR = Path.home() / ".claude/homunculus/instincts/personal"
# A corpus id is a bare filename stem, never a path. Anything else ("../x", "/tmp/x")
# would make the writes at the bottom of main() land outside the corpus dir.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
KEEP = 2          # entries retained inline in the lesson file
MIN_TO_SPLIT = 3  # files with fewer entries keep everything inline

DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse(text):
    """-> (before, entries, after) or None when the shape is unclear."""
    m = re.search(r"^## Evidence\b.*$", text, re.M)
    if not m:
        return None
    before = text[: m.start()]
    rest = text[m.end():]

    # The evidence section ends at the next top-level heading, so trailing
    # sections such as "## Related" survive untouched.
    nxt = re.search(r"^## ", rest, re.M)
    body, after = (rest[: nxt.start()], rest[nxt.start():]) if nxt else (rest, "")

    # Entries are top-level "- " bullets; continuation lines are indented or blank.
    entries, cur = [], None
    for line in body.splitlines(keepends=True):
        if line.startswith("- "):
            if cur is not None:
                entries.append(cur)
            cur = line
        elif cur is not None:
            cur += line
        elif line.strip():
            return None  # non-bullet prose inside the section: hand to a human
    if cur is not None:
        entries.append(cur)
    return (before, [e.rstrip() + "\n" for e in entries], after) if entries else None


def entry_date(e):
    m = DATE.search(e)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def plan(path):
    text = path.read_text(encoding="utf-8")
    parsed = parse(text)
    if not parsed:
        return None, "unparseable evidence section"
    before, entries, after = parsed
    if len(entries) < MIN_TO_SPLIT:
        return None, f"{len(entries)} entries — below split threshold"

    # Newest first. Python's sort is stable, so undated entries hold their order.
    ordered = sorted(entries, key=entry_date, reverse=True)
    kept, archived = ordered[:KEEP], ordered

    ident = path.stem
    archive = (
        f"# Evidence archive — {ident}\n\n"
        f"Full history. The lesson lives in `{ident}.md`; this file exists so the pattern\n"
        f"*across* entries stays visible.\n\n"
        + "".join(archived)
    )
    lesson = (
        before
        + f"## Evidence (n={len(entries)}, latest {len(kept)})\n\n"
        + "".join(kept)
        + f"\n→ full history: `{ident}.evidence.md`\n"
        + (("\n" + after.lstrip("\n")) if after.strip() else "")
    )
    return (lesson, archive, len(entries), len(text), len(lesson)), None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    if "--all" in sys.argv:
        args = sorted(p.stem for p in DIR.glob("*.md") if not p.name.endswith(".evidence.md"))
    if not args:
        print(__doc__)
        return 1

    split = skipped = 0
    saved = 0
    for ident in args:
        if not ID_RE.fullmatch(ident) or ".." in ident:
            print(f"  INVALID  {ident!r} — id must be a bare corpus filename, not a path")
            continue
        path = DIR / f"{ident}.md"
        if not path.exists():
            print(f"  MISSING  {ident}")
            continue
        result, why = plan(path)
        if not result:
            print(f"  skip     {ident:<52} {why}")
            skipped += 1
            continue
        lesson, archive, n, old, new = result
        saved += old - new
        split += 1
        print(f"  split    {ident:<52} {n} entries, {old // 4} -> {new // 4} tok")
        if apply_:
            (DIR / f"{ident}.evidence.md").write_text(archive, encoding="utf-8")
            path.write_text(lesson, encoding="utf-8")

    print(f"\n{split} splittable, {skipped} skipped, ~{saved // 4} tokens removed from lesson files")
    print("dry run — pass --apply to write" if not apply_ else "APPLIED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

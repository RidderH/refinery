#!/usr/bin/env python3
"""Find [[wiki-links]] in the instinct corpus that no longer resolve to a lesson file.

Run this as part of retiring merged originals — not afterwards. Merging removes ids,
and surviving files link to them; a batch of merges silently breaks every inbound link.

Four buckets, because "does not resolve to a lesson" hides four different situations:

  RETIRED   the target sits in the archive and nothing declares that deliberate —
            crash debt or an unfinished repoint. These are the actionable rows.
  INTENT    the target sits in the archive AND its archive's MANIFEST declares it
            retired without successor (an ALL_DEAD retirement, spec §6b-5): a
            `## → (no successor)` section names the id. Deliberate, fix by hand at
            leisure. The marker lives in the MANIFEST — written at MANIFEST_UPDATED —
            so a crash BEFORE that step has no marker and correctly reads as RETIRED
            debt: the marker's absence is the crash signal.
  SUCCESSOR the target names a rule or a skill. A RULE_DUP retirement repoints citations
            at the covering rule (2026-07-30 ruling), so these are CORRECT, not debt.
            Before this bucket existed they landed in NEVER and read as junk, which made
            a wrong successor pointer indistinguishable from a typo.
  NEVER     resolves to nothing anywhere — project-memory names, literal bracket syntax.

Usage: python3 ~/.claude/skills/instinct-format/scripts/dangling_links.py [--selftest]
"""
import re
import sys
import pathlib
from collections import defaultdict

HOME = pathlib.Path.home() / ".claude"
ROOT = HOME / "homunculus/instincts/personal"


def archived_ids(instincts_dir):
    """Ids sitting in the archive, across BOTH layouts.

    Current: `Archive/<YYYY-MM-DD>/` (ruled 2026-07-30).
    Legacy:  `.pruned-<date>/`, which lived at two levels — most beside `personal/`,
             but .pruned-2026-07-19 INSIDE it. Scanning only the parent silently
             misclassified those ids as "never resolved": the opposite advice.
    Both are scanned so a half-finished migration cannot silently reclassify 282 ids.
    """
    ids = set()
    archive = instincts_dir / "Archive"
    dirs = [d for d in archive.iterdir() if d.is_dir()] if archive.is_dir() else []
    for root in (instincts_dir, instincts_dir / "personal"):
        if root.is_dir():
            dirs += [d for d in root.glob(".pruned-*") if d.is_dir()]
    for d in dirs:
        ids |= {p.stem for p in d.glob("*.md") if p.stem != "MANIFEST"}
    return ids


NO_SUCCESSOR_HEADING = re.compile(r"^##\s*→\s*\(no successor\)\s*$")
MANIFEST_ROW_ID = re.compile(r"^\|\s*`?([A-Za-z0-9][A-Za-z0-9._-]*?)(?:\.md)?`?\s*\|")


def no_successor_ids(instincts_dir):
    """Ids each archive's MANIFEST declares retired WITHOUT a successor (§6b-5).

    The marker is a `## → (no successor)` section; ids are taken from its table
    rows, first column, up to the next `## ` heading. Only ids under that exact
    heading count — a row under an ordinary `## → <covering-artifact>` section
    is a merge source, not a no-successor retirement.
    """
    ids = set()
    archive = instincts_dir / "Archive"
    dirs = [d for d in archive.iterdir() if d.is_dir()] if archive.is_dir() else []
    for root in (instincts_dir, instincts_dir / "personal"):
        if root.is_dir():
            dirs += [d for d in root.glob(".pruned-*") if d.is_dir()]
    for d in dirs:
        mf = d / "MANIFEST.md"
        if not mf.is_file():
            continue
        inside = False
        for line in mf.read_text().splitlines():
            if line.startswith("## "):
                inside = bool(NO_SUCCESSOR_HEADING.match(line))
                continue
            if inside:
                m = MANIFEST_ROW_ID.match(line)
                if m and m.group(1).lower() not in ("retired file", "---"):
                    ids.add(m.group(1))
    return ids


def successor_ids(home):
    """Targets that name a rule or a skill — a legitimate repoint destination."""
    ids = set()
    rules = home / "rules"
    if rules.is_dir():
        ids |= {p.stem for p in rules.glob("*.md")}
    skills = home / "skills"
    if skills.is_dir():
        ids |= {d.name for d in skills.iterdir() if d.is_dir()}
    return ids


def scan(home):
    root = home / "homunculus/instincts/personal"
    instincts = root.parent
    lessons = {p.stem for p in root.glob("*.md") if not p.name.endswith(".evidence.md")}
    archived = archived_ids(instincts)
    successors = successor_ids(home)

    dangling = defaultdict(list)
    for p in sorted(root.glob("*.md")):
        for target in re.findall(r"\[\[([^\]]+)\]\]", p.read_text()):
            if target not in lessons:
                dangling[target].append(p.name)

    declared = no_successor_ids(instincts)

    retired, intent, successor, never = [], [], [], []
    for target, srcs in sorted(dangling.items()):
        # Archive wins over successor: a retired id that happens to share a name with a
        # skill is still a retirement. Within the archive, a MANIFEST no-successor
        # declaration wins over debt — that is the whole point of the marker.
        if target in archived:
            bucket = intent if target in declared else retired
        else:
            bucket = successor if target in successors else never
        bucket.append((target, srcs))
    return lessons, archived, retired, intent, successor, never


def main():
    lessons, archived, retired, intent, successor, never = scan(HOME)
    print(f"lesson files: {len(lessons)}   archived ids on disk: {len(archived)}")
    for title, rows in (
        (f"links to RETIRED ids ({len(retired)} distinct) — repoint these", retired),
        (f"declared no-successor retirements ({len(intent)} distinct) — deliberate, fix by hand", intent),
        (f"links to a RULE or SKILL ({len(successor)} distinct) — successor pointers, expected", successor),
        (f"links that never resolved ({len(never)} distinct) — usually leave alone", never),
    ):
        print(f"\n=== {title} ===")
        for t, srcs in rows:
            print(f"  [[{t}]]  <- {', '.join(sorted(set(srcs)))}")


def selftest():
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp)
        root = home / "homunculus/instincts/personal"
        root.mkdir(parents=True)
        (home / "rules").mkdir()
        (home / "rules/testing.md").write_text("x")
        (home / "skills/using-git-worktrees").mkdir(parents=True)

        # one live lesson citing one of each kind
        (root / "live.md").write_text(
            "[[gone-new]] [[gone-legacy]] [[gone-nested]] [[gone-intent]] "
            "[[testing]] [[using-git-worktrees]] [[nowhere]]\n")

        # current archive layout — MANIFEST declares gone-intent retired without
        # successor (§6b-5 marker); gone-new sits under an ordinary merge heading
        (home / "homunculus/instincts/Archive/2026-07-30").mkdir(parents=True)
        (home / "homunculus/instincts/Archive/2026-07-30/gone-new.md").write_text("x")
        (home / "homunculus/instincts/Archive/2026-07-30/gone-intent.md").write_text("x")
        (home / "homunculus/instincts/Archive/2026-07-30/MANIFEST.md").write_text(
            "# Pruned 2026-07-30\n\n"
            "## → some-topic\n\n"
            "| retired file | lines | contributed |\n|---|---|---|\n"
            "| `gone-new` | 10 | stuff |\n\n"
            "## → (no successor)\n\n"
            "| retired file | lines | why retired |\n|---|---|---|\n"
            "| `gone-intent.md` | 12 | ALL_DEAD — every cited path gone |\n")
        # legacy layouts, both levels
        (home / "homunculus/instincts/.pruned-2026-06-09").mkdir(parents=True)
        (home / "homunculus/instincts/.pruned-2026-06-09/gone-legacy.md").write_text("x")
        (root / ".pruned-2026-07-19").mkdir()
        (root / ".pruned-2026-07-19/gone-nested.md").write_text("x")

        lessons, archived, retired, intent, successor, never = scan(home)
        r, i, s, n = ({t for t, _ in retired}, {t for t, _ in intent},
                      {t for t, _ in successor}, {t for t, _ in never})

        check("MANIFEST is not counted as an archived id", "MANIFEST" not in archived)
        check("archive/<date> ids are RETIRED", "gone-new" in r)
        check("legacy .pruned-* beside personal/ still RETIRED", "gone-legacy" in r)
        check("legacy .pruned-* nested in personal/ still RETIRED", "gone-nested" in r)
        check("a declared no-successor retirement is INTENT, not debt",
              "gone-intent" in i and "gone-intent" not in r)
        check("an id under an ordinary merge heading is NOT intent",
              "gone-new" not in i)
        check("crash debt (archived, MANIFEST silent) stays RETIRED",
              "gone-legacy" in r and "gone-legacy" not in i)
        check("a rule name is a SUCCESSOR, not never-resolved",
              "testing" in s and "testing" not in n)
        check("a skill name is a SUCCESSOR, not never-resolved",
              "using-git-worktrees" in s and "using-git-worktrees" not in n)
        check("an unknown target is still NEVER", "nowhere" in n)
        check("buckets are disjoint",
              not (r & i) and not (r & s) and not (r & n)
              and not (i & s) and not (i & n) and not (s & n))

        # archive must win over successor on a name collision
        (home / "skills/gone-new").mkdir()
        _, _, retired2, _, successor2, _ = scan(home)
        check("archived id beats a same-named skill",
              "gone-new" in {t for t, _ in retired2}
              and "gone-new" not in {t for t, _ in successor2})

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else (main() or 0))

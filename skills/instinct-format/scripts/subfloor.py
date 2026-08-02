#!/usr/bin/env python3
"""List sub-floor (<35 line) instincts grouped by domain — the merge-candidate worklist.

Usage: python3 ~/.claude/skills/instinct-format/scripts/subfloor.py
"""
import re
import pathlib
from collections import defaultdict

ROOT = pathlib.Path.home() / ".claude/homunculus/instincts/personal"
rows = defaultdict(list)

for p in sorted(ROOT.glob("*.md")):
    if p.name.endswith(".evidence.md"):
        continue
    text = p.read_text()
    n = len(text.splitlines())
    if n >= 35:
        continue
    d = re.search(r"^domain: *(.+)$", text, re.M)
    tr = re.search(r'^trigger: *"?(.*?)"?$', text, re.M)
    rows[d.group(1).strip() if d else "?"].append(
        (n, p.stem, tr.group(1)[:78] if tr else "")
    )

total = 0
for dom in sorted(rows, key=lambda k: -len(rows[k])):
    print(f"\n### {dom} ({len(rows[dom])})")
    total += len(rows[dom])
    for n, pid, tr in sorted(rows[dom]):
        print(f"  {n:3d} {pid}\n       {tr}")
print(f"\nTOTAL sub-floor: {total}")

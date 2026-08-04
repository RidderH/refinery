#!/usr/bin/env python3
"""Fail-loud gate: no evidence entry may be invisible to the parser.

The corpus and the parser drifted apart once already — 23 files held 38 entries in shapes
`build_index.py` could not see, so files reported "0 repeated_failure" that in fact held
several. The user ruling was: keep the PARSER strict, fix the FILES with
`migrate_evidence.py`, and stand a gate behind it so the drift cannot come back. This is
that gate — the `fix:` / `lint:` / CI pair, where `migrate_evidence.py` is the fixer.

It reimplements NOTHING. Both violation classes are answered by the two scripts that
already own those questions:

  * `migrate_evidence.migrate_text` decides what a conforming shape is. If the codemod
    would change a file, the file is nonconforming — by construction, the lint and the fix
    can never disagree about what a violation is.
  * `build_index.record` decides what the parser can see. A file whose frontmatter
    declares more entries than the parser counts is hiding evidence.

Usage:
    python3 lint_evidence.py                 # gate the corpus, exit 1 if anything hides
    python3 lint_evidence.py <dir>           # gate another root
    python3 lint_evidence.py --selftest      # prove it goes red on a violating fixture

Writes nothing, ever.
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_index as B          # noqa: E402  (path must be set first)
import migrate_evidence as M     # noqa: E402
from instinct_record import InstinctFormatError, parse_file, parse_text  # noqa: E402

DEFAULT_ROOT = M.DEFAULT_ROOT


# The migrated entry shape, counted directly off the text the codemod WOULD write.
# BYTE-IDENTICAL to `build_index.ENTRY`, not merely a prefix of it: stopping at the
# opening backtick accepted `**2026-01-02** `Repeated_Failure`` — an outcome token the
# index REJECTS — so the gate would have counted an entry nothing downstream reads. The
# agreement is asserted over probes in `--selftest`, including that one.
ENTRY_LINE = B.ENTRY


def declared_count(root, p):
    """The `evidence_count` governing this file. For an archive, its parent lesson's.

    An archive has no frontmatter of its own, but it is the record its parent declares a
    count for (`build_index.record` reads the archive EXCLUSIVELY when one exists), so the
    parent's number is the one an archive must reconcile against.
    """
    owner = (pathlib.Path(root) / (p.name[:-len(".evidence.md")] + ".md")
             if p.name.endswith(".evidence.md") else p)
    if not owner.exists():
        return None
    try:
        record = parse_file(owner)
    except (OSError, InstinctFormatError):
        return None
    value = record.values.get("evidence_count")
    return value if isinstance(value, int) and value >= 0 else None


def entries_after(text):
    """How many entries the migrated text holds, counted off the migrated bytes."""
    return sum(1 for line in text.split("\n") if ENTRY_LINE.match(line))


def entries_before(text, name):
    """What the index sees in the file TODAY — every shape, not just the migrated one.

    Counting only migrated-shape lines here reported `before=0` for every un-migrated file,
    so a file holding N legacy bullets and declaring N-1 looked like the codemod had
    invented an entry it merely rewrote. `before` has to mean "what the parser already
    reads", which is exactly `build_index`'s total.
    """
    if name.endswith(".evidence.md"):
        return B._total(B.evidence_stats(text, whole=True))
    try:
        record = parse_text(text)
    except InstinctFormatError:
        return 0
    return B._total(B.evidence_stats(record.body)) if record.kind != "legacy" else 0


def lint_file(root, p):
    """Violations for one file, as reason strings. Empty list = clean.

    A file can violate both ways at once and both are reported: a nonconforming shape is
    the CAUSE and the invisible-entry count is the CONSEQUENCE, and printing only one of
    them sends the reader to fix a symptom or to a cause with no measure of its cost.
    """
    reasons, invisible = [], 0
    try:
        new, st = M.migrate_text(p.read_text(), p.name)
    except M.Malformed as e:
        # Unreadable is worse than nonconforming, and nothing downstream can judge it.
        return [f"MALFORMED — {e}"], 0
    if new != p.read_text():
        reasons.append(
            f"nonconforming evidence shape — the codemod would rewrite "
            f"{st['normalised']} entr{'y' if st['normalised'] == 1 else 'ies'}"
            + (f" ({st['heading_dated']} dated from a heading, needing review)"
               if st["heading_dated"] else ""))
    # `undatable` is a NOTE, never a violation on its own. Every `<id>.evidence.md` opens
    # with an undated boilerplate preamble ("Full history. The lesson lives in …"), which
    # the codemod correctly declines to date — so failing on `undatable` alone would paint
    # every archive in the corpus permanently red with nothing anyone could do about it,
    # and a gate that is always red is a gate nobody runs. It is still worth printing on a
    # file that is failing anyway: those entries are the part the codemod cannot fix.
    note = (f"note: {st['undatable']} undated column-0 paragraph(s) the codemod will not "
            f"touch — check whether any is a real entry needing a date by hand"
            if st["undatable"] else None)
    # RECONCILIATION — the only check that looks at the WHOLE file rather than one block.
    # Every shape check above can pass while the file as a whole gains entries that were
    # never observed: fabrication is a property of the total, not of any single block. A
    # codemod emitting MORE entries than the file declares has invented evidence, and per
    # SKILL.md § What counts as one evidence entry the fix is never to lower the declared
    # number to match. Counted off the migrated bytes, not off a stat the codemod keeps
    # about itself — a miscounting codemod would report its own miscount as correct.
    dec, after = declared_count(root, p), entries_after(new)
    before = entries_before(p.read_text(), p.name)
    if dec is not None and after > dec:
        # Two different failures, deliberately labelled apart. Only the first is this
        # script's fault; calling a stale frontmatter number "fabrication" sends someone
        # to fix a codemod that is behaving, and per SKILL.md § What counts as one
        # evidence entry the second is never fixed by folding entries to match.
        reasons.append(
            f"FABRICATION: migrating yields {after} entries but the file declares {dec}"
            f" — the codemod INVENTED {after - dec} (it held {before} before)"
            if before <= dec else
            f"STALE COUNT: migrating yields {after} entries against a declared {dec}, but "
            f"the file already held {before} before migrating — the declared number is "
            f"stale. Raise it or split folded bullets; never fold entries to match it")
    # Declared-vs-parsed, on lesson files only: an archive has no frontmatter to declare
    # anything, and `record()` already folds the archive into its owner's counts.
    if not p.name.endswith(".evidence.md"):
        r = B.record(root, p.stem)
        if r.get("malformed") or r.get("missing"):
            return reasons + [f"unreadable by build_index ({r})"], invisible
        invisible = max(0, r["evidence_count"] - B._total(r["evidence"]))
        if invisible:
            reasons.append(
                f"frontmatter declares {r['evidence_count']} entries, the parser sees "
                f"{B._total(r['evidence'])} — {invisible} INVISIBLE")
    return (reasons + [note] if reasons and note else reasons), invisible


def lint(root):
    """(rows, scanned, invisible). `rows` is one (name, reasons) per violating file."""
    rows, scanned, invisible = [], 0, 0
    for p in sorted(pathlib.Path(root).glob("*.md")):
        scanned += 1
        reasons, inv = lint_file(root, p)
        invisible += inv
        if reasons:
            rows.append((p.name, reasons))
    return rows, scanned, invisible


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    named = [a for a in args if not a.startswith("--")]
    root = pathlib.Path(named[0]) if named else DEFAULT_ROOT
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    rows, scanned, invisible = lint(root)
    for name, reasons in rows:
        for r in reasons:
            print(f"{name}: {r}")
    print(f"\nscanned={scanned}  violating={len(rows)}  invisible-entries={invisible}")
    if rows:
        # The whole point of the gate. A warning nobody's exit code reads is a comment.
        print(f"FAIL: {len(rows)} file(s) hold evidence the parser cannot see.\n"
              f"  Most are mechanical: python3 migrate_evidence.py <file> …\n"
              f"  NOT all. A STALE COUNT needs the declared number raised by hand, and a "
              f"file declaring evidence it never recorded cannot be fixed at all.\n"
              f"  Dry-run first: python3 migrate_evidence.py --report <file> (writes "
              f"nothing).", file=sys.stderr)
        return 1
    print("OK: every declared evidence entry is visible to the parser.")
    return 0


# -------------------------------------------------------------------- selftest
def selftest():
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    def said(rows, text):
        """Did any violation reason mention `text`? Flattened, never indexed.

        `rows[0][1]` raises IndexError the moment a check stops firing — which is exactly
        when the assertion is supposed to go RED. A selftest that crashes instead of
        failing looks like "the check never ran", and that is one of the three things a
        neuter staying green can mean.
        """
        return any(text in r for _, rs in rows for r in rs)

    conforming = (
        "---\nschema_version: 2\nid: clean\ndomain: harness\nevidence_count: 2\n---\n\n"
        "## Evidence\n"
        "- **2026-01-02** `repeated_failure` — one\n"
        "- **2026-03-04** `repeated_failure` — two\n")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "clean.md").write_text(conforming)
        rows, scanned, invisible = lint(root)
        check("GREEN: a conforming corpus reports no violations", rows == [])
        check("GREEN: counts the file it scanned", scanned == 1 and invisible == 0)
        argv = sys.argv
        try:
            sys.argv = ["lint_evidence.py", str(root)]
            check("GREEN: exit code 0 on a clean corpus", main() == 0)
        finally:
            sys.argv = argv        # a selftest must not leave the process reconfigured

    # RED 1 — the paragraph shape. Declares 2, the parser sees 0: the exact 38-entry
    # failure this gate exists to catch.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "para.md").write_text(
            "---\nschema_version: 2\nid: para\nevidence_count: 2\n---\n\n## Evidence\n\n"
            "2026-01-02 the first observation, written as a bare paragraph.\n\n"
            "2026-03-04 the second one, same shape.\n")
        rows, scanned, invisible = lint(root)
        check("RED: paragraph-shaped entries flagged", len(rows) == 1)
        check("RED: named as a nonconforming shape, with the rewrite count",
              said(rows, "nonconforming evidence shape")
              and said(rows, "would rewrite 2 entries"))
        check("RED: the invisible entries are counted and named",
              invisible == 2 and said(rows, "2 INVISIBLE"))
        argv = sys.argv
        try:
            sys.argv = ["lint_evidence.py", str(root)]
            check("RED: exit code 1 — this is a gate, not a warning", main() == 1)
        finally:
            sys.argv = argv

    # RED 2 — a shape the codemod cannot fix on its own: no date anywhere. It is still a
    # violation, via the declared-vs-parsed count, and the reason must say the codemod
    # cannot fix it — otherwise someone runs the fixer, sees no change, and moves on.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "undated.md").write_text(
            "---\nid: undated\nevidence_count: 1\n---\n\n## Evidence\n\n"
            "A paragraph entry with no date anywhere in it.\n")
        rows, _, invisible = lint(root)
        check("RED: an entry with no date anywhere is still a violation",
              len(rows) == 1 and said(rows, "1 INVISIBLE"))
        check("RED: and the reason says the codemod will not fix it",
              said(rows, "the codemod will not touch"))
        check("RED: undatable entry is ALSO counted as invisible", invisible == 1)

    # An archive's undated boilerplate preamble must NOT make it a violation. Every
    # archive has one, so failing on it would paint the whole corpus permanently red.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "pre.md").write_text(conforming.replace("clean", "pre"))
        (root / "pre.evidence.md").write_text(
            "# Evidence archive — pre\n\nFull history. The lesson lives in `pre.md`; this\n"
            "file exists so the pattern stays visible.\n\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-03-04** `repeated_failure` — two\n")
        rows, _, _ = lint(root)
        check("GREEN: an archive's undated preamble alone is a note, not a violation",
              rows == [])

    # RECONCILIATION — a file whose migration would yield MORE entries than it declares.
    # The shape checks cannot see this: each looks at one block, and fabrication is a
    # property of the total. Fixture: a 4-paragraph incident under one dated heading in a
    # file declaring 1 — the `green-guards-…` defect in miniature.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "fab.md").write_text(
            "---\nid: fab\nevidence_count: 1\n---\n\n"
            "## Evidence — 2026-07-29 (one incident)\n\n"
            "First paragraph of the one incident.\n\n"
            "Second paragraph of the same incident.\n\n"
            "Third paragraph of the same incident.\n\n"
            "Merged from: session observation, 2026-07-29.\n")
        rows, _, _ = lint(root)
        after = entries_after(M.migrate_text((root / "fab.md").read_text(), "fab.md")[0])
        check("RECONCILE: one incident under one dated heading yields ONE entry",
              after == 1)
        check("RECONCILE: parsed_after never exceeds the declared evidence_count",
              after <= declared_count(root, root / "fab.md"))
        check("RECONCILE: so this file is NOT reported as fabrication",
              not said(rows, "FABRICATION"))

        # And the gate fires when the count really would exceed the declaration.
        (root / "fab.md").write_text(
            "---\nid: fab\nevidence_count: 1\n---\n\n## Evidence\n\n"
            "2026-01-02 first incident, leading with its own date.\n\n"
            "2026-03-04 second incident, also leading with its own date.\n")
        rows, _, _ = lint(root)
        check("RECONCILE: a file yielding MORE entries than declared is a violation",
              said(rows, "FABRICATION"))
        check("RECONCILE: the violation names both numbers and blames the codemod",
              said(rows, "yields 2 entries but the file declares 1")
              and said(rows, "INVENTED 1"))

    # A file that already over-counted BEFORE migration must be named as such — the ruling
    # says a shortfall is never fixed by lowering the declared number, so the gate must not
    # let a stale count read as the codemod's fault. Its OWN root: the fabricating fixture
    # above lives in the other one, and `said()` is corpus-wide, so sharing a directory
    # made this assertion pass on the neighbour's message.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "stale.md").write_text(
            "---\nid: stale\nevidence_count: 1\n---\n\n## Evidence\n"
            "- **2026-01-02** `?` — one\n- **2026-03-04** `?` — two\n")
        rows, _, _ = lint(root)
        check("RECONCILE: a pre-existing over-count is not blamed on the codemod",
              said(rows, "STALE COUNT") and not said(rows, "FABRICATION"))

    # LEGACY-shape entries: the case that distinguishes `entries_before` from a naive count
    # of migrated-shape lines. These two bullets are entries the index ALREADY reads, so a
    # declared 1 is stale — but counting only `- **DATE** \`x\`` lines scores `before=0` and
    # blames the codemod for entries it merely rewrote. Corpus-wide that mislabelled all 7
    # reconciliation failures as fabrication.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "legacy.md").write_text(
            "---\nid: legacy\nevidence_count: 1\n---\n\n## Evidence\n"
            "- **2026-01-02** — a legacy entry the parser already counts\n"
            "- **2026-03-04** — and a second one\n")
        rows, _, _ = lint(root)
        check("RECONCILE: legacy entries count toward `before`, so rewriting them is "
              "not fabrication",
              said(rows, "STALE COUNT") and said(rows, "already held 2 before")
              and not said(rows, "FABRICATION"))

    # The gate counts entries with the same shape `build_index` parses. If these two ever
    # disagree, the gate is guarding a shape nothing downstream reads.
    probes = ["- **2026-01-02** `?` — untyped", "* **2026-01-02** `confirmation` — typed",
              "- **2026-01-02** — legacy, no outcome", "- no date at all",
              "  - **2026-01-02** `?` — indented continuation", "",
              # The probe whose absence made the old claim overstate itself: an outcome
              # token with capitals. `build_index.ENTRY` requires `[a-z_?]+`, so a gate
              # regex that stopped at the opening backtick counted it and the index did not.
              "- **2026-01-02** `Repeated_Failure` — capitalised, rejected by the index",
              "- **2026-01-02** `unterminated — no closing backtick"]
    check("RECONCILE: the gate's entry shape agrees with build_index's on every probe",
          all(bool(ENTRY_LINE.match(s)) == bool(B.ENTRY.match(s)) for s in probes))

    # RED 3 — declared-vs-parsed drift with a perfectly conforming shape. The codemod
    # would change nothing, so shape-checking alone reports this file clean.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "overcount.md").write_text(
            "---\nschema_version: 2\nid: overcount\nevidence_count: 5\n---\n\n"
            "## Evidence\n- **2026-01-02** `repeated_failure` — the only one\n")
        rows, _, invisible = lint(root)
        _, st = M.migrate_text((root / "overcount.md").read_text(), "overcount.md")
        check("RED: the shape check alone would call this file clean",
              st["normalised"] == 0 and st["undatable"] == 0)
        check("RED: declared-vs-parsed drift still caught",
              len(rows) == 1 and said(rows, "4 INVISIBLE"))

    # RED 4 — a malformed file must be a violation, not a silent skip. A file nobody can
    # read is the one most likely to be hiding something.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "broken.md").write_text("no frontmatter at all\n")
        rows, _, _ = lint(root)
        check("RED: malformed file is a violation, named as malformed",
              len(rows) == 1 and said(rows, "MALFORMED"))

    # RED 5 — a paragraph-shaped ARCHIVE. `record()` reads the archive EXCLUSIVELY when
    # one exists, so an unparseable archive silences its owner's evidence entirely — and
    # the owner's own frontmatter can still look perfectly consistent.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "arch.md").write_text(conforming.replace("clean", "arch"))
        (root / "arch.evidence.md").write_text(
            "# Evidence archive — arch\n\n"
            "2026-07-21 `successful_recall` — the newest entry, in paragraph shape.\n")
        rows, _, _ = lint(root)
        names = {n for n, _ in rows}
        check("RED: paragraph-shaped archive flagged", "arch.evidence.md" in names)
        check("RED: its owner flagged too — the archive is what record() reads",
              "arch.md" in names)

    # The gate and the fixer must agree by construction: running the codemod on a
    # violating corpus must make the gate go green. If it does not, one of them has its
    # own private idea of what a conforming file is.
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        f = root / "para.md"
        f.write_text(
            "---\nschema_version: 2\nid: para\nevidence_count: 2\n---\n\n## Evidence\n\n"
            "2026-01-02 the first observation, written as a bare paragraph.\n\n"
            "2026-03-04 the second one, same shape.\n")
        check("gate is RED before the fixer runs", lint(root)[0] != [])
        M.run([f], write=True)
        check("gate is GREEN after the fixer runs — lint and fix cannot disagree",
              lint(root)[0] == [])

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

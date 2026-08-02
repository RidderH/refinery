#!/usr/bin/env python3
"""Phase D of `instinct-prune` (spec §5): perform one retirement transaction.

ARCHIVE-FIRST (decided-by-dependency 2026-07-31), on PruneTransaction:

    CLAIMED → ARCHIVED → CITATIONS_REPOINTED → MANIFEST_UPDATED
            → COMMITTING → COMMITTED

Intent is recorded BEFORE each effect (ledger contract), every effect is
idempotent, so `--resume` replays the last recorded step after a crash and
`--rollback` undoes any non-terminal transaction. Dry-run by default.

Two modes, two different MANIFEST shapes (spec §4/§5):
  --successor <name>   RULE_DUP retirement: citations repoint to the covering
                       rule; MANIFEST gets an ordinary `## → <name>` section.
                       <name> is the BARE rule stem or skill dir name
                       ('agents', 'claude-code-hooks-config') — never a path.
  --no-successor       ALL_DEAD retirement: citations are deliberately LEFT;
                       MANIFEST gets the `## → (no successor)` section — the
                       intent-marker dangling_links.py reads — including the
                       inbound-link hand-fix worklist.

This driver moves files and edits citers. It does NOT decide: run it only on
candidates Phase C has verified, and never on rank-1 candidates before ruling
R3 (evidence lineage) is made.

Usage:
  retire.py <id>... (--successor <rule> | --no-successor) [--why "<text>"]
            [--date YYYY-MM-DD] [--root <personal-dir>] [--apply]
  retire.py --resume <cluster_id> [--root ...] [--apply]
  retire.py --rollback <cluster_id> [--root ...] [--apply]
  retire.py --selftest
"""
import datetime
import pathlib
import re
import sys

DISTILL_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent \
    / "instinct-distill/scripts"
sys.path.insert(0, str(DISTILL_SCRIPTS))
from ledger import (PruneTransaction, Store, manifest_append,  # noqa: E402
                    recover)
FORMAT_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent \
    / "instinct-format/scripts"
sys.path.insert(0, str(FORMAT_SCRIPTS))
from dangling_links import successor_ids  # noqa: E402

CORPUS = pathlib.Path.home() / ".claude/homunculus/instincts/personal"


def inbound_citers(root, sid, exclude):
    """Live lessons whose body links [[sid]] — the hand-fix worklist."""
    pat = re.compile(r"\[\[" + re.escape(sid) + r"\]\]")
    out = []
    for p in sorted(root.glob("*.md")):
        if p.stem in exclude or p.name.endswith(".evidence.md"):
            continue
        if pat.search(p.read_text()):
            out.append(p.name)
    return out


def build_manifest_block(root, sources, successor, why):
    """The section this transaction appends — spec §5 format, §4 for no-successor."""
    rows = []
    for sid in sources:
        p = root / f"{sid}.md"
        lines = len(p.read_text().splitlines()) if p.exists() else "?"
        rows.append(f"| `{sid}.md` | {lines} | {why or '—'} |")
    if successor:
        head = f"## → {successor}"
        cols = "| retired file | lines | contributed |"
    else:
        head = "## → (no successor)"
        cols = "| retired file | lines | why retired |"
    block = [head, "", cols, "|---|---|---|", *rows, ""]
    if not successor:
        for sid in sources:
            citers = inbound_citers(root, sid, exclude=set(sources))
            if citers:
                block.append(f"Inbound links deliberately left (fix by hand): "
                             f"{', '.join(f'`{c}`' for c in citers)} → `[[{sid}]]`")
        block.append("")
    block += ["## Restore", "```"]
    for sid in sources:
        block.append(f"mv <archive>/{sid}.md {root}/")
        block.append(f"mv <archive>/{sid}.evidence.md {root}/  # if present")
    block += ["```",
              "Restore restores source files only; it does not un-repoint "
              "citations — after COMMITTED that is a manual recovery."]
    return "\n".join(block)


# Each step: (re)record intent, then perform the idempotent effect.
def step_archive(tx, root, apply_):
    adir = pathlib.Path(tx.d["archive_dir"])
    if apply_:
        adir.mkdir(parents=True, exist_ok=True)
    for sid in tx.d["sources"]:
        for name in (f"{sid}.md", f"{sid}.evidence.md"):
            src, dst = root / name, adir / name
            print(f"  mv {name} -> {adir.name}/" + ("" if src.exists() else "  (absent)"))
            if apply_ and src.exists() and not dst.exists():
                src.rename(dst)


def step_repoint(tx, apply_):
    for f, old, new in tx.d.get("citations") or []:
        fp = pathlib.Path(f)
        print(f"  repoint {fp.name}: {old} -> {new}")
        if apply_ and fp.exists() and old in fp.read_text():
            fp.write_text(fp.read_text().replace(old, new))


def step_manifest(tx, apply_):
    mp = tx.d["manifest_path"]
    print(f"  MANIFEST block -> {mp}")
    if apply_:
        manifest_append(mp, tx.cluster_id, tx.d["manifest_rows"][0])


def run(store, root, sources, successor, why, date, apply_):
    cluster = f"prune-{date}-{sources[0]}"
    adir = root.parent / "Archive" / date
    mpath = adir / "MANIFEST.md"

    # A successor must be a bare rule stem or skill dir name — the exact
    # vocabulary dangling_links classifies as SUCCESSOR. A path form like
    # "rules/agents.md" commits fine and then every repointed link lands in
    # "never resolved" (found by the 2026-08-01 rehearsal). Fail closed.
    if successor:
        home = root.parent.parent.parent
        vocab = successor_ids(home)
        if not vocab:
            print(f"retire: no rules/ or skills/ under {home} — root must live "
                  f"at <home>/homunculus/instincts/personal", file=sys.stderr)
            return 4
        if successor not in vocab:
            print(f"retire: successor {successor!r} is not a rule stem or "
                  f"skill dir name (e.g. 'agents', not 'rules/agents.md') — "
                  f"refusing", file=sys.stderr)
            return 4

    missing = [s for s in sources if not (root / f"{s}.md").exists()]
    if missing:
        print(f"retire: not in corpus: {missing} — refusing", file=sys.stderr)
        return 3
    claimed = store.claimed_ids() & set(sources)
    if claimed:
        print(f"retire: already claimed: {sorted(claimed)} — refusing", file=sys.stderr)
        return 3

    # Citations plan + manifest block are computed BEFORE anything moves, and
    # recorded in the ledger, so --resume needs no recomputation.
    triples = []
    if successor:
        for sid in sources:
            for citer in inbound_citers(root, sid, exclude=set(sources)):
                triples.append([str(root / citer), f"[[{sid}]]", f"[[{successor}]]"])
    block = build_manifest_block(root, sources, successor, why)

    print(f"{'APPLY' if apply_ else 'DRY RUN'}  cluster={cluster}")
    if not apply_:
        print(f"  would claim {sources}, archive to {adir}, "
              f"{'repoint ' + str(len(triples)) + ' citation(s)' if successor else 'leave citations (no successor)'},"
              f" append MANIFEST block, commit")
        print("\n--- MANIFEST block ---\n" + block)
        return 0

    tx = PruneTransaction.begin(store, cluster, sources)
    tx.claim()
    tx.record_plan(adir, triples, mpath, [block])
    tx.archived(str(adir))
    step_archive(tx, root, apply_)
    tx.citations_repointed(triples)
    step_repoint(tx, apply_)
    tx.manifest_updated([block], manifest_path=mpath)
    step_manifest(tx, apply_)
    tx.commit(datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"COMMITTED {cluster}")
    return 0


def resume(store, root, cluster, apply_):
    tx = PruneTransaction.load(store, cluster)
    if tx is None:
        print(f"retire: no transaction {cluster!r}", file=sys.stderr)
        return 3
    step = tx.next_action()
    if step is None:
        print(f"{cluster}: already terminal ({tx.d['state']}) — nothing to do")
        return 0
    print(f"{'APPLY' if apply_ else 'DRY RUN'}  resume {cluster} from {step}")
    if not apply_:
        return 0
    if step == "ROLLING_BACK":
        tx.rollback("resumed rollback")
        print(f"ROLLED_BACK {cluster}")
        return 0
    # Replay the recorded step, then run the remainder in sequence order.
    order = ["CLAIMED", "ARCHIVED", "CITATIONS_REPOINTED", "MANIFEST_UPDATED",
             "COMMITTING"]
    if step == "DISCOVERED":
        step = "CLAIMED"  # nothing after begin() landed; replay from the top
    if step not in order:
        print(f"retire: cannot resume from {step!r}", file=sys.stderr)
        return 3
    for s in order[order.index(step):]:
        if s == "CLAIMED":
            tx.claim()
        elif s == "ARCHIVED":
            tx.archived(tx.d["archive_dir"])
            step_archive(tx, root, True)
        elif s == "CITATIONS_REPOINTED":
            tx.citations_repointed(tx.d.get("citations") or [])
            step_repoint(tx, True)
        elif s == "MANIFEST_UPDATED":
            tx.manifest_updated(tx.d["manifest_rows"],
                                manifest_path=tx.d["manifest_path"])
            step_manifest(tx, True)
        elif s == "COMMITTING":
            tx.commit(tx.d.get("watermark_value")
                      or datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"COMMITTED {cluster}")
    return 0


def main(argv):
    args = list(argv)
    sources, successor, why, date = [], None, "", None
    mode, cluster, apply_, root = "run", None, False, CORPUS
    no_succ = False
    while args:
        a = args.pop(0)
        if a == "--successor":
            successor = args.pop(0)
        elif a == "--no-successor":
            no_succ = True
        elif a == "--why":
            why = args.pop(0)
        elif a == "--date":
            date = args.pop(0)
        elif a == "--root":
            root = pathlib.Path(args.pop(0))
        elif a == "--apply":
            apply_ = True
        elif a in ("--resume", "--rollback"):
            mode, cluster = a.lstrip("-"), args.pop(0)
        elif a.startswith("-"):
            print(__doc__)
            return 2
        else:
            sources.append(a)
    store = Store(root)
    if mode == "resume":
        return resume(store, root, cluster, apply_)
    if mode == "rollback":
        tx = PruneTransaction.load(store, cluster)
        if tx is None:
            print(f"retire: no transaction {cluster!r}", file=sys.stderr)
            return 3
        print(f"{'APPLY' if apply_ else 'DRY RUN'}  rollback {cluster} "
              f"from {tx.d['state']}")
        if apply_:
            print(tx.rollback("operator rollback"))
        return 0
    if not sources or (successor is None) == (not no_succ):
        print(__doc__)
        return 2
    return run(store, root, sources, successor, why,
               date or datetime.date.today().isoformat(), apply_)


# ---------------------------------------------------------------- selftest
def _home(tmp):
    home = pathlib.Path(tmp)
    root = home / "homunculus/instincts/personal"
    root.mkdir(parents=True)
    (home / "rules").mkdir()
    (home / "rules/testing.md").write_text("the covering rule\n")
    (root / "dup.md").write_text("# dup lesson\nbody\n")
    (root / "dup.evidence.md").write_text("# Evidence archive — dup\n")
    (root / "dead.md").write_text("# dead lesson\ncites /nonexistent\n")
    (root / "citer.md").write_text("see [[dup]] and [[dead]]\n")
    return home, root


def selftest():
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent
                           / "instinct-format/scripts"))
    import dangling_links as DL

    # --- successor retirement, end to end --------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        rc = run(st, root, ["dup"], "testing", "carried by rules/testing.md",
                 "2026-07-31", apply_=False)
        check("dry run exits 0", rc == 0)
        check("dry run moves nothing", (root / "dup.md").exists())
        rc = run(st, root, ["dup"], "testing", "carried by rules/testing.md",
                 "2026-07-31", apply_=True)
        adir = root.parent / "Archive/2026-07-31"
        check("apply exits 0 and archives lesson + evidence sibling",
              rc == 0 and (adir / "dup.md").exists()
              and (adir / "dup.evidence.md").exists()
              and not (root / "dup.md").exists())
        check("citer repointed to the covering rule",
              "[[testing]]" in (root / "citer.md").read_text())
        mf = (adir / "MANIFEST.md").read_text()
        check("MANIFEST block is transaction-marked",
              "<!-- tx:prune-2026-07-31-dup -->" in mf and "## → testing" in mf)
        check("claims released, watermark advanced, nothing open",
              st.claimed_ids() == set() and st.read_watermark() and
              recover(st) == [])
        _l, _a, retired, intent, succ, _n = DL.scan(home)
        check("dangling_links: repointed citer lands in SUCCESSOR bucket",
              "testing" in {t for t, _ in succ}
              and "dup" not in {t for t, _ in retired})
        rc = run(st, root, ["dead"], "rules/testing.md", "path form",
                 "2026-07-31", apply_=True)
        check("path-form successor refused (exit 4), nothing moved",
              rc == 4 and (root / "dead.md").exists())
        rc = run(st, root, ["dead"], "no-such-rule", "bogus successor",
                 "2026-07-31", apply_=True)
        check("unknown successor refused (exit 4), nothing moved",
              rc == 4 and (root / "dead.md").exists())

    # --- no-successor retirement: marker end to end ----------------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        rc = run(st, root, ["dead"], None, "every cited path gone",
                 "2026-07-31", apply_=True)
        adir = root.parent / "Archive/2026-07-31"
        mf = (adir / "MANIFEST.md").read_text()
        check("no-successor MANIFEST carries the intent-marker heading",
              rc == 0 and "## → (no successor)" in mf)
        check("inbound hand-fix worklist recorded",
              "citer.md" in mf and "[[dead]]" in mf)
        check("citations deliberately left",
              "[[dead]]" in (root / "citer.md").read_text())
        _l, _a, retired, intent, _s, _n = DL.scan(home)
        check("dangling_links: left link reads INTENT, not debt",
              "dead" in {t for t, _ in intent}
              and "dead" not in {t for t, _ in retired})

    # --- crash + resume, crash + rollback --------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        before = {p.name for p in root.glob("*.md")}
        adir = root.parent / "Archive/2026-07-31"
        # crash at the EARLIEST recoverable point: plan recorded, no effect yet
        tx = PruneTransaction.begin(st, "prune-2026-07-31-dup", ["dup"])
        tx.claim()
        tx.record_plan(adir,
                       [[str(root / "citer.md"), "[[dup]]", "[[testing]]"]],
                       adir / "MANIFEST.md",
                       [build_manifest_block(root, ["dup"], "testing", "x")])
        # crash here — resume must archive, repoint, append MANIFEST, commit
        st2 = Store(root)
        rc = resume(st2, root, "prune-2026-07-31-dup", apply_=True)
        check("resume finishes a transaction crashed right after the plan",
              rc == 0 and st2.claimed_ids() == set()
              and PruneTransaction.load(st2, "prune-2026-07-31-dup").d["state"]
              == "COMMITTED"
              and "[[testing]]" in (root / "citer.md").read_text()
              and "<!-- tx:prune-2026-07-31-dup -->"
              in (adir / "MANIFEST.md").read_text())

        # rollback a second, crashed-early transaction
        tx2 = PruneTransaction.begin(st2, "prune-2026-07-31-dead", ["dead"])
        tx2.claim()
        tx2.archived(str(adir))
        step_archive(tx2, root, True)
        check("fixture: dead.md is archived pre-rollback",
              not (root / "dead.md").exists())
        tx2.rollback("phase C rejected")
        check("rollback restores the archived source",
              (root / "dead.md").exists() and st2.claimed_ids() == set())
        check("corpus file set == before, minus the committed retirement",
              {p.name for p in root.glob("*.md")}
              == before - {"dup.md", "dup.evidence.md"})

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Phase B of `instinct-prune` (spec §3): turn Phase A's candidate flags into a
ranked, reviewable worklist. Reads `instinct-buckets.sh --tsv` output; writes a
shortlist with a stated reason per row. Touches no file.

Ranking, strongest signal first (spec §3; SKILL_* added 2026-08-01 — gate 1
scans skills/*/SKILL.md as covering artifacts too):
  1  RULE_DUP|SKILL_DUP + CONVERTED   carried by ONE rule/skill AND current.
                            Gate-2 exclusions deliberately DO NOT apply — the
                            _DUP flags are content judgments, UNCITED/
                            UNVERIFIABLE are location artifacts. A SKILL
                            successor is trigger-loaded, not always-on: Phase C
                            must additionally verify trigger coverage.
  2  ALL_DEAD               every cited path gone. Staleness rank: excluded when
                            the flag is UNVERIFIABLE or UNCITED by construction.
  3  RULE_NEAR + SOME_DEAD  two weak signals agreeing.

Load-bearing exclusions:
  - claimed files are INVISIBLE (not skipped-with-a-warning): Store.claimed_ids()
  - UNVERIFIABLE is never shortlisted by a staleness rank (56-bogus-ALL_DEAD lesson)
  - UNCITED cannot fail gate 2 and must not be read as passing it
  - CITES_NONE is `cites: []` — the author DECLARED the lesson path-independent,
    so no staleness rank can apply. It reaches no rank by falling through the
    ALL_DEAD/SOME_DEAD tests rather than by a named exclusion; the selftest pins
    that, because a future gate-2 label added to those tests would silently
    start ranking declared-path-independent files.

Every row is a CANDIDATE FLAG, not a verdict — Phase C verifies each one, and a
rank-1 retirement additionally waits on ruling R3 (evidence lineage): the row
carries evidence_count so the reviewer sees what a rule would lose.

Fail-closed: an empty or malformed TSV is a failed run (exit 3), never a clean
empty shortlist — a tool that cannot see its input must not report "no work".

Usage:
  bash ~/.claude/skills/instinct-prune/scripts/instinct-buckets.sh --tsv | python3 shortlist.py
  python3 shortlist.py --tsv-file <path> [--root <corpus-dir>]
  python3 shortlist.py --selftest
"""
import pathlib
import sys

DISTILL_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent \
    / "instinct-distill/scripts"
sys.path.insert(0, str(DISTILL_SCRIPTS))
from ledger import Store  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ruling  # noqa: E402  — sibling module: the ruling ledger (Phase C verdicts)

HEADER = ["file", "lines", "conf", "evid", "gate0", "gate1", "gate2"]
CORPUS = pathlib.Path.home() / ".claude/homunculus/instincts/personal"


def classify(row):
    """Return (rank, reason) or None. Pure — claim filtering happens outside."""
    g0, g1, g2 = row["gate0"], row["gate1"], row["gate2"]
    if g1 in ("RULE_DUP", "SKILL_DUP") and g0 == "CONVERTED":
        kind = "rule" if g1 == "RULE_DUP" else "skill (successor-mode; verify trigger coverage)"
        return (1, f"{g1}+CONVERTED — {kind} carries it and file is current; "
                   f"R3: {row['evid']} evidence entries at stake")
    if g2 == "ALL_DEAD":
        return (2, "ALL_DEAD — every cited path gone")
    if g1 in ("RULE_NEAR", "SKILL_NEAR") and g2 == "SOME_DEAD":
        return (3, f"{g1}+SOME_DEAD — two weak signals agreeing")
    return None


def parse_tsv(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise SystemExit(3)  # message printed by caller-facing wrapper below
    head = lines[0].rstrip("\n").split("\t")
    if head != HEADER:
        print(f"shortlist: unexpected TSV header {head!r} — want {HEADER!r}; "
              "refusing to guess column meanings", file=sys.stderr)
        raise SystemExit(3)
    rows = []
    for ln in lines[1:]:
        cols = ln.rstrip("\n").split("\t")
        if len(cols) != len(HEADER):
            print(f"shortlist: malformed row ({len(cols)} cols): {ln!r}",
                  file=sys.stderr)
            raise SystemExit(3)
    # a data-less TSV is a failed Phase A run, not an empty corpus
        rows.append(dict(zip(HEADER, cols)))
    if not rows:
        print("shortlist: TSV has a header and zero data rows — Phase A did not "
              "run; refusing to report an empty worklist as clean", file=sys.stderr)
        raise SystemExit(3)
    return rows


def shortlist(rows, claimed):
    out = []
    for row in rows:
        stem = row["file"].removesuffix(".md")
        if stem in claimed or row["file"] in claimed:
            continue  # invisible, by design — not a warning line
        hit = classify(row)
        if hit:
            out.append((hit[0], row["file"], hit[1]))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def ruling_base_for(root):
    """The home base whose CORPUS_REL resolves to `root`, so a --root run
    scopes standing rulings to the corpus under test instead of always the
    live personal ledger (BUG 1: an unrelated test corpus was inheriting the
    home ledger's suppressions). root=None means "default corpus" -> the
    live ruling.BASE, unchanged. A non-standard-shaped root (retire.py's
    root.parent.parent.parent != root) has no ledger we can validate subject
    paths against -> None, meaning "suppress nothing" (ruling.py's own
    fail-open direction), never a guess at the wrong ledger."""
    if root is None:
        return ruling.BASE
    root_p = pathlib.Path(root).resolve()
    base = root_p.parent.parent.parent
    if base / ruling.CORPUS_REL != root_p:
        return None
    return base


def main(argv):
    tsv_file = root = None
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--tsv-file":
            tsv_file = args.pop(0)
        elif a == "--root":
            root = args.pop(0)
        else:
            print(__doc__)
            return 2
    text = pathlib.Path(tsv_file).read_text() if tsv_file else sys.stdin.read()
    if not text.strip():
        print("shortlist: empty input — Phase A did not run or the pipe is "
              "broken; refusing to report an empty worklist as clean",
              file=sys.stderr)
        return 3
    rows = parse_tsv(text)
    claimed = Store(pathlib.Path(root) if root else CORPUS).claimed_ids()
    picked = shortlist(rows, claimed)
    # Ruling ledger (ruling.py): subtract adjudicated candidates, VISIBLY —
    # no silent caps; an unreadable ledger subtracts nothing (fail-open).
    # Scoped to the corpus being shortlisted (BUG 1) — a --root run must not
    # inherit the live personal ledger's suppressions.
    rbase = ruling_base_for(root)
    if rbase is None:
        print("shortlist: custom root: standing rulings not applied (no "
              "ledger scoped to this corpus)", file=sys.stderr)
    rulings = ruling.load_rulings(rbase) if rbase is not None else None
    kept, suppressed = ruling.apply_rulings(picked, rulings, rbase or ruling.BASE)
    print(f"shortlist: {len(kept)} candidates from {len(rows)} files "
          f"({len(claimed)} claimed, invisible; "
          f"{len(suppressed)} suppressed by standing rulings)")
    for rank, fname, reason in kept:
        print(f"  rank {rank}  {fname:<55} {reason}")
    for (rank, fname, _), r in suppressed:
        print(f"  suppressed by ruling {r['ruled']} (DROP): {fname} — {r['why']}")
    # Latest DEFER per id only (T3). The ledger stays append-only; a re-ruled
    # DEFER used to print every superseded record too, each carrying a stale
    # `[subjects changed]` marker — 10 lines for 3 files.
    dbase = rbase or ruling.BASE
    defers = sorted(ruling.latest_defers(rulings),
                    key=lambda r: (r["ruled"], r["id"]))
    if defers:
        print("\nStanding DEFERs — promises to revisit, shown every run:")
        for r in defers:
            state = "" if ruling.subjects_valid(r, dbase) \
                else "  [subjects changed — re-adjudicate]"
            print(f"  {r['ruled']}  {r['id']} — {r['why']}{state}")
            # Announce-only. The exit condition is inactivity, evaluated
            # against the lesson's CURRENT evidence_count; a legacy DEFER
            # without an accrual block, or an unreadable count, stays manual
            # and says nothing. This tool never retires.
            if ruling.defer_condition_met(r, dbase):
                acc = r["accrual"]
                print(f"      DEFER condition MET — take to retirement review "
                      f"(evidence_count still {acc['evidence_count']}, "
                      f"no accrual for {acc['no_accrual_days']}+ days since "
                      f"{r['ruled']})")
    print("\nCandidate flags, not verdicts. Phase C verifies each; rank 1 also "
          "waits on ruling R3 (evidence lineage).")
    return 0


# ---------------------------------------------------------------- selftest
def selftest():
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    def tsv(*rows):
        return "\t".join(HEADER) + "\n" + "".join(
            "\t".join(r) + "\n" for r in rows)

    R = {
        "rank1":      ("dup-conv.md", "40", "0.9", "9", "CONVERTED", "RULE_DUP", "UNCITED"),
        "dup-part":   ("dup-part.md", "40", "0.9", "2", "PARTIAL", "RULE_DUP", "CITES_OK"),
        "dead":       ("dead.md", "30", "0.5", "1", "PARTIAL", "-", "ALL_DEAD"),
        "claimed":    ("claimed.md", "30", "0.5", "1", "PARTIAL", "-", "ALL_DEAD"),
        "unverif":    ("unverif.md", "30", "0.5", "1", "PARTIAL", "-", "UNVERIFIABLE"),
        "near-some":  ("near-some.md", "30", "0.5", "1", "PARTIAL", "RULE_NEAR", "SOME_DEAD"),
        "near-ok":    ("near-ok.md", "30", "0.5", "1", "PARTIAL", "RULE_NEAR", "CITES_OK"),
        "near-uncit": ("near-uncit.md", "30", "0.5", "1", "CONVERTED", "RULE_NEAR", "UNCITED"),
        "plain":      ("plain.md", "30", "0.5", "1", "CONVERTED", "-", "CITES_OK"),
        "skill-conv": ("skill-conv.md", "40", "0.7", "3", "CONVERTED", "SKILL_DUP", "UNCITED"),
        "skill-part": ("skill-part.md", "40", "0.7", "3", "PARTIAL", "SKILL_DUP", "CITES_OK"),
        "snear-some": ("snear-some.md", "30", "0.5", "1", "PARTIAL", "SKILL_NEAR", "SOME_DEAD"),
        "near-none":  ("near-none.md", "30", "0.5", "1", "CONVERTED", "RULE_NEAR", "CITES_NONE"),
        "dup-none":   ("dup-none.md", "40", "0.9", "9", "CONVERTED", "RULE_DUP", "CITES_NONE"),
    }
    rows = parse_tsv(tsv(*R.values()))
    picked = {f: (rk, why) for rk, f, why in shortlist(rows, claimed={"claimed"})}

    check("RULE_DUP+CONVERTED is rank 1", picked.get("dup-conv.md", (0,))[0] == 1)
    check("rank 1 survives a gate-2 flag (UNCITED must NOT exclude it)",
          "dup-conv.md" in picked)
    check("rank 1 reason carries the R3 evidence count",
          "9 evidence entries" in picked.get("dup-conv.md", (0, ""))[1])
    check("RULE_DUP without CONVERTED is not rank 1 (not shortlisted)",
          "dup-part.md" not in picked)
    check("ALL_DEAD is rank 2", picked.get("dead.md", (0,))[0] == 2)
    check("a claimed file is INVISIBLE, not warned about",
          "claimed.md" not in picked)
    check("UNVERIFIABLE is never shortlisted by a staleness rank",
          "unverif.md" not in picked)
    check("RULE_NEAR+SOME_DEAD is rank 3", picked.get("near-some.md", (0,))[0] == 3)
    check("RULE_NEAR alone is not shortlisted", "near-ok.md" not in picked)
    check("UNCITED does not read as passing gate 2 (RULE_NEAR+UNCITED is out)",
          "near-uncit.md" not in picked)
    check("unflagged files are not shortlisted", "plain.md" not in picked)
    check("CITES_NONE never shortlists by a staleness rank (declared path-independent)",
          "near-none.md" not in picked)
    check("CITES_NONE does NOT exclude rank 1 — like UNCITED, it is not a content judgment",
          picked.get("dup-none.md", (0,))[0] == 1)
    check("SKILL_DUP+CONVERTED is rank 1 (skills are covering artifacts)",
          picked.get("skill-conv.md", (0,))[0] == 1)
    check("SKILL_DUP rank-1 reason names successor-mode",
          "successor-mode" in picked.get("skill-conv.md", (0, ""))[1])
    check("SKILL_DUP without CONVERTED is not shortlisted",
          "skill-part.md" not in picked)
    check("SKILL_NEAR+SOME_DEAD is rank 3",
          picked.get("snear-some.md", (0,))[0] == 3)
    check("ordering is by rank", [rk for rk, _, _ in
          shortlist(rows, claimed=set())] == sorted(
          rk for rk, _, _ in shortlist(rows, claimed=set())))

    # fail-closed paths — inject the exact bad inputs
    for label, bad in (("empty TSV", ""),
                       ("header-only TSV", "\t".join(HEADER) + "\n"),
                       ("wrong header", "a\tb\tc\n1\t2\t3\n")):
        try:
            parse_tsv(bad)
            code = 0
        except SystemExit as e:
            code = e.code
        check(f"{label} exits 3, never a clean empty shortlist", code == 3)

    with tempfile.TemporaryDirectory() as tmp:
        st = Store(pathlib.Path(tmp))
        check("selftest store starts unclaimed (fixture sanity)",
              st.claimed_ids() == set())

    # BUG 1: --root must scope standing rulings to the corpus under test,
    # not always the live personal ledger. Test ruling_base_for() directly —
    # can't touch the real home ledger from a selftest.
    check("ruling_base_for(None) is the live ruling.BASE (default unchanged)",
          ruling_base_for(None) == ruling.BASE)
    with tempfile.TemporaryDirectory() as tmp:
        std_root = pathlib.Path(tmp) / "homehome" / ruling.CORPUS_REL
        std_root.mkdir(parents=True)
        check("standard-shaped --root resolves to its own base",
              ruling_base_for(str(std_root)) == (pathlib.Path(tmp) / "homehome").resolve())
        weird_root = pathlib.Path(tmp) / "just_a_dir"
        weird_root.mkdir()
        check("non-standard-shaped --root -> None (suppress nothing, BUG 1 RED)",
              ruling_base_for(str(weird_root)) is None)

    # end-to-end probe: a --root corpus with no ledger must not report any
    # candidate suppressed, regardless of what the live personal ledger holds.
    with tempfile.TemporaryDirectory() as tmp:
        corpus = pathlib.Path(tmp) / "home2" / ruling.CORPUS_REL
        corpus.mkdir(parents=True)
        tsv_path = pathlib.Path(tmp) / "in.tsv"
        tsv_path.write_text(tsv(("dead.md", "30", "0.5", "1", "PARTIAL", "-", "ALL_DEAD")))
        import subprocess
        proc = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()),
             "--tsv-file", str(tsv_path), "--root", str(corpus)],
            capture_output=True, text=True)
        check("--root end-to-end: exits 0", proc.returncode == 0)
        check("--root end-to-end: 0 suppressed by standing rulings",
              "0 suppressed by standing rulings" in proc.stdout)
        check("--root end-to-end: candidate still present",
              "dead.md" in proc.stdout)

    # ---- ledger-facing behaviour, end-to-end through a fixture home -------
    # A real home shape (<home>/homunculus/instincts/personal + .distill), a
    # real ledger on disk, the real CLI. The ruling ledger's per-record hash
    # dispatch and the DEFER read side are the two places a silent regression
    # would suppress or announce the wrong thing.
    import datetime
    import json
    import subprocess

    def fixture_home(tmp, lessons, records):
        home = pathlib.Path(tmp) / "home"
        corpus = home / ruling.CORPUS_REL
        corpus.mkdir(parents=True)
        for stem, text in lessons.items():
            (corpus / f"{stem}.md").write_text(text)
        led = ruling.rulings_path(home)
        led.parent.mkdir(parents=True, exist_ok=True)
        led.write_text("".join(json.dumps(r) + "\n" for r in records))
        return home, corpus

    def run_cli(home, tsv_text, tmp):
        p = pathlib.Path(tmp) / "in.tsv"
        p.write_text(tsv_text)
        return subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()),
             "--tsv-file", str(p), "--root", str(home / ruling.CORPUS_REL)],
            capture_output=True, text=True)

    def lesson_text(evid, checked="2026-08-01"):
        return ("---\nid: x\nevidence_count: %d\ncites: []\n"
                "updated: \"2026-08-01\"\nlast_checked: \"%s\"\n---\n"
                "# lesson\nbody\n" % (evid, checked))

    def drop_rec(stem, home, **extra):
        path = f"{ruling.CORPUS_REL}/{stem}.md"
        algo = extra.get("hash_algo", "v1")
        rec = {"id": stem, "verdict": "DROP", "rank": 2,
               "flags": {"gate1": "-", "gate2": "-"},
               "why": f"adjudicated: {stem}", "ruled": "2026-08-01",
               "subjects": [{"path": path,
                             "sha256": ruling.subject_hash(home / path, algo)}]}
        rec.update(extra)
        return rec

    # Mixed ledger: one v1 record (no hash_algo) and one v2, both live, both
    # after a bookkeeping-only edit. v1 must re-open, v2 must stay suppressed.
    with tempfile.TemporaryDirectory() as tmp:
        home, corpus = fixture_home(
            tmp, {"legacy-one": lesson_text(3), "modern-one": lesson_text(3)}, [])
        recs = [drop_rec("legacy-one", home),
                drop_rec("modern-one", home, hash_algo="v2")]
        ruling.rulings_path(home).write_text(
            "".join(json.dumps(r) + "\n" for r in recs))
        rows = tsv(("legacy-one.md", "30", "0.5", "3", "PARTIAL", "-", "ALL_DEAD"),
                   ("modern-one.md", "30", "0.5", "3", "PARTIAL", "-", "ALL_DEAD"))
        out = run_cli(home, rows, tmp).stdout
        check("mixed ledger: both suppressed while untouched",
              "2 suppressed by standing rulings" in out)
        for stem in ("legacy-one", "modern-one"):
            (corpus / f"{stem}.md").write_text(lesson_text(3, checked="2026-08-19"))
        out = run_cli(home, rows, tmp).stdout
        check("v1 record re-opens after a last_checked-only edit "
              "(old sensitivity preserved — no migration wave)",
              "rank 2  legacy-one.md" in out)
        check("v2 record survives a last_checked-only edit (RED)",
              "suppressed by ruling" in out and "modern-one" in out
              and "rank 2  modern-one.md" not in out)

    # Standing DEFERs: latest ruling per id only, and the accrual announcement.
    def defer_rec(stem, home, ruled, why, accrual=True, evid=5, days=30):
        path = f"{ruling.CORPUS_REL}/{stem}.md"
        rec = {"id": stem, "verdict": "DEFER", "rank": 1,
               "flags": {"gate1": "-", "gate2": "-"}, "why": why,
               "ruled": ruled, "hash_algo": "v2",
               "subjects": [{"path": path,
                             "sha256": ruling.subject_hash(home / path, "v2")}]}
        if accrual:
            rec["accrual"] = {"evidence_count": evid, "no_accrual_days": days}
        return rec

    old = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    recent = (datetime.date.today() - datetime.timedelta(days=5)).isoformat()
    with tempfile.TemporaryDirectory() as tmp:
        home, corpus = fixture_home(
            tmp, {"met-one": lesson_text(5), "young-one": lesson_text(5),
                  "moved-one": lesson_text(9), "legacy-defer": lesson_text(5)},
            [])
        ruling.rulings_path(home).write_text("".join(json.dumps(r) + "\n" for r in [
            defer_rec("met-one", home, "2026-07-01", "superseded first ruling"),
            defer_rec("met-one", home, old, "latest ruling on met-one"),
            defer_rec("young-one", home, recent, "ruled too recently"),
            defer_rec("moved-one", home, old, "lesson kept accruing", evid=5),
            defer_rec("legacy-defer", home, old, "predates the accrual field",
                      accrual=False),
        ]))
        out = run_cli(home, tsv(
            ("plain.md", "30", "0.5", "1", "CONVERTED", "-", "CITES_OK")), tmp).stdout
        check("Standing DEFERs prints one line per id, not one per record (RED)",
              out.count("met-one —") == 1)
        check("the LATEST ruling's why is the one shown (RED)",
              "latest ruling on met-one" in out
              and "superseded first ruling" not in out)
        met_line = [l for l in out.splitlines() if "DEFER condition MET" in l]
        check("condition MET announced for the due DEFER (RED)",
              len(met_line) == 1)
        check("the announcement names the exact review handoff (RED)",
              "DEFER condition MET — take to retirement review" in out)
        lines = out.splitlines()
        met_idx = [i for i, l in enumerate(lines) if "DEFER condition MET" in l]
        check("the MET announcement sits directly under its own DEFER line",
              len(met_idx) == 1 and "met-one" in lines[met_idx[0] - 1])
        check("a DEFER ruled 5 days ago is not announced",
              "young-one" in out and "young-one" not in "".join(met_line))
        check("a DEFER whose lesson kept accruing is not announced",
              "moved-one" in out and "moved-one" not in "".join(met_line))
        check("a legacy DEFER with no accrual block stays manual",
              "legacy-defer" in out and "legacy-defer" not in "".join(met_line))
        check("the tool announces only — it never says it retired anything",
              "retired" not in out.lower())

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Ruling ledger for `instinct-prune` Phase C — remembers human verdicts so
adjudicated candidates stop resurfacing on every shortlist run.

Design: docs/superpowers/plans/2026-08-01-ruling-ledger-design.md. VEX-shaped
dispositions (verdict + mandatory challengeable why) with SARIF-shaped
invalidation (full-content sha256 of every file the judgment compared — any
change re-opens the candidate).

One JSON object per line, append-only, in `<corpus>/.distill/rulings.jsonl`.
Single-writer by design: O_APPEND of one newline-terminated line is the whole
concurrency story — do not add locking, add a second writer and this file's
premise is wrong.

Verdicts:
  DROP   the flag is an artifact / coverage judged insufficient to retire.
  DEFER  genuine future candidate blocked on a named condition (why names it).
There is no ACCEPT: acceptance IS the retire.py transaction, and a retired
file leaves the corpus, so it cannot resurface.

A ruling suppresses a shortlist candidate iff ALL hold:
  1. same id;
  2. the candidate is not STRONGER than what was ruled on — by rank
     (candidate_rank >= ruling.rank; rank 1 is strongest) and by gate-1 flag
     (a SKILL_NEAR ruling never covers a later SKILL_DUP);
  3. every recorded subject still exists with an unchanged sha256.

Fail direction: a missing/unreadable/malformed ledger suppresses NOTHING — a
tool that cannot see its memory must not claim it remembers.

Never invoked by a gate or hook. A ruling is minted only inside Phase C, after
the human speaks — "prune never decides alone" applies to remembering too.

Usage:
  ruling.py <id> --verdict DROP|DEFER --rank N --why "…" \
            [--covering <bare rule/skill stem>] [--gate1 F] [--gate2 F]
  ruling.py --check <id>     # standing rulings for id + validity; exit 0 iff one is valid
  ruling.py --selftest
"""
import datetime
import hashlib
import json
import os
import pathlib
import sys

BASE = pathlib.Path.home() / ".claude"
CORPUS_REL = "homunculus/instincts/personal"
STRENGTH = {"RULE_DUP": 2, "SKILL_DUP": 2, "RULE_NEAR": 1, "SKILL_NEAR": 1}


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rulings_path(base):
    return base / CORPUS_REL / ".distill/rulings.jsonl"


def resolve_covering(base, stem):
    """Bare rule stem or skill dir name → path. Same vocabulary and exit-4
    guard as retire.py --successor: a path-form successor once committed a
    transaction whose links never resolved."""
    if "/" in stem or stem.endswith(".md"):
        print(f"ruling: --covering takes a bare stem, not a path: {stem!r}",
              file=sys.stderr)
        raise SystemExit(4)
    for cand in (base / "rules" / f"{stem}.md",
                 base / "skills" / stem / "SKILL.md"):
        if cand.is_file():
            return cand
    print(f"ruling: no rules/{stem}.md or skills/{stem}/SKILL.md under {base}",
          file=sys.stderr)
    raise SystemExit(4)


def load_rulings(base):
    """All rulings, or None when the ledger cannot be trusted (fail-open:
    the caller must then suppress nothing). A missing file is an empty
    ledger, not an untrusted one."""
    p = rulings_path(base)
    if not p.exists():
        return []
    out = []
    try:
        for ln in p.read_text().splitlines():
            if not ln.strip():
                continue
            r = json.loads(ln)
            if r["verdict"] not in ("DROP", "DEFER"):
                raise ValueError(f"unknown verdict {r['verdict']!r}")
            out.append(r)
    except (ValueError, KeyError, OSError) as e:
        print(f"ruling: ledger unreadable ({e}) — suppressing NOTHING; "
              f"repair {p}", file=sys.stderr)
        return None
    return out


def subjects_valid(ruling, base):
    for s in ruling["subjects"]:
        p = base / s["path"]
        if not p.is_file() or _sha(p) != s["sha256"]:
            return False
    return True


def suppresses(ruling, stem, rank, gate1, base):
    return (ruling["verdict"] == "DROP"
            and ruling["id"] == stem
            and rank >= ruling["rank"]
            and STRENGTH.get(gate1, 0)
                <= STRENGTH.get(ruling["flags"].get("gate1", "-"), 0)
            and subjects_valid(ruling, base))


def apply_rulings(picked, rulings, base):
    """Partition shortlist rows into (kept, suppressed_with_ruling).
    rulings=None (untrusted ledger) keeps everything."""
    if not rulings:
        return list(picked), []
    kept, suppressed = [], []
    for rank, fname, reason in picked:
        stem = fname.removesuffix(".md")
        row = next((r for r in rulings
                    if suppresses(r, stem, rank,
                                  _gate1_of(reason), base)), None)
        (suppressed if row else kept).append(
            ((rank, fname, reason), row) if row else (rank, fname, reason))
    return kept, suppressed


def _gate1_of(reason):
    """The gate-1 flag a shortlist reason line was built from. Reasons embed
    the flag verbatim (classify() f-strings); absence means gate 1 was '-'."""
    for flag in STRENGTH:
        if flag in reason:
            return flag
    return "-"


def write_ruling(base, id_, verdict, rank, why, covering, gate1, gate2):
    if verdict not in ("DROP", "DEFER"):
        print(f"ruling: verdict must be DROP or DEFER, got {verdict!r}",
              file=sys.stderr)
        raise SystemExit(2)
    if not why.strip() or why.strip().lower() == "artifact":
        print("ruling: --why is mandatory and must be challengeable — name "
              "the covering artifact or the false-positive shape",
              file=sys.stderr)
        raise SystemExit(2)
    inst = base / CORPUS_REL / f"{id_}.md"
    if not inst.is_file():
        print(f"ruling: no live instinct {inst}", file=sys.stderr)
        raise SystemExit(2)
    subjects = [{"path": str(inst.relative_to(base)), "sha256": _sha(inst)}]
    if covering:
        cov = resolve_covering(base, covering)
        subjects.append({"path": str(cov.relative_to(base)),
                         "sha256": _sha(cov)})
    rec = {"id": id_, "verdict": verdict, "rank": rank,
           "flags": {"gate1": gate1, "gate2": gate2}, "why": why.strip(),
           "ruled": datetime.date.today().isoformat(), "subjects": subjects}
    p = rulings_path(base)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (json.dumps(rec) + "\n").encode())
    finally:
        os.close(fd)
    return rec


def check(base, id_):
    rulings = load_rulings(base)
    if rulings is None:
        return 1
    mine = [r for r in rulings if r["id"] == id_]
    if not mine:
        print(f"no standing ruling for {id_}")
        return 1
    any_valid = False
    for r in mine:
        valid = subjects_valid(r, base)
        any_valid = any_valid or valid
        print(f"{r['ruled']} {r['verdict']} rank={r['rank']} "
              f"gate1={r['flags'].get('gate1', '-')} "
              f"[{'VALID' if valid else 'STALE — subjects changed, re-adjudicate'}]"
              f" — {r['why']}")
    return 0 if any_valid else 1


def main(argv):
    args = list(argv)
    if args[:1] == ["--check"]:
        return check(BASE, args[1])
    id_ = None
    opt = {"verdict": None, "rank": None, "why": "", "covering": None,
           "gate1": "-", "gate2": "-"}
    while args:
        a = args.pop(0)
        if a.startswith("--"):
            key = a[2:]
            if key not in opt:
                print(__doc__)
                return 2
            opt[key] = args.pop(0)
        elif id_ is None:
            id_ = a
        else:
            print(__doc__)
            return 2
    if not (id_ and opt["verdict"] and opt["rank"]):
        print(__doc__)
        return 2
    rec = write_ruling(BASE, id_, opt["verdict"], int(opt["rank"]),
                       opt["why"], opt["covering"], opt["gate1"], opt["gate2"])
    print(f"recorded: {rec['verdict']} {id_} ({len(rec['subjects'])} subjects "
          f"hashed) -> {rulings_path(BASE)}")
    return 0


# ---------------------------------------------------------------- selftest
def selftest():
    import tempfile
    ok = True

    def chk(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    with tempfile.TemporaryDirectory() as tmp:
        base = pathlib.Path(tmp)
        corpus = base / CORPUS_REL
        corpus.mkdir(parents=True)
        (base / "rules").mkdir()
        inst = corpus / "some-lesson.md"
        inst.write_text("# lesson\n")
        cov = base / "rules" / "cov.md"
        cov.write_text("# rule carrying it\n")

        # writer guards — inject the exact bad inputs
        for label, kw in (
                ("empty --why rejected", dict(why="  ")),
                ("--why 'artifact' alone rejected", dict(why="Artifact")),
                ("unknown verdict rejected", dict(verdict="MAYBE"))):
            try:
                d = dict(id_="some-lesson", verdict="DROP", rank=1,
                         why="covered by rules/cov.md in full", covering=None,
                         gate1="SKILL_NEAR", gate2="-")
                d.update(kw)
                write_ruling(base, **d)
                code = 0
            except SystemExit as e:
                code = e.code
            chk(label + " (exit 2)", code == 2)
        try:
            resolve_covering(base, "rules/cov.md")
            code = 0
        except SystemExit as e:
            code = e.code
        chk("path-form --covering refused (exit 4, retire.py vocabulary)",
            code == 4)

        write_ruling(base, "some-lesson", "DROP", 1,
                     "covered by rules/cov.md in full", "cov",
                     "SKILL_NEAR", "-")
        rulings = load_rulings(base)
        chk("ruling appended and parseable", rulings and len(rulings) == 1)
        chk("both subjects hashed",
            len(rulings[0]["subjects"]) == 2)

        picked = [(1, "some-lesson.md", "SKILL_NEAR+CONVERTED — skill carries it"),
                  (1, "other.md", "SKILL_DUP+CONVERTED — skill carries it")]
        kept, sup = apply_rulings(picked, rulings, base)
        chk("matching candidate suppressed", len(sup) == 1
            and sup[0][0][1] == "some-lesson.md")
        chk("unruled candidate kept", any(f == "other.md" for _, f, _ in kept))

        # probe 2 (strength): NEAR ruling must NOT cover a later DUP flag
        picked_dup = [(1, "some-lesson.md", "SKILL_DUP+CONVERTED — stronger")]
        kept, sup = apply_rulings(picked_dup, rulings, base)
        chk("SKILL_NEAR ruling does not suppress SKILL_DUP", not sup)
        # rank direction: a rank-2 ruling never covers a rank-1 candidate
        r2 = dict(rulings[0], rank=2, flags={"gate1": "-", "gate2": "ALL_DEAD"})
        chk("rank-2 ruling does not suppress rank-1 candidate",
            not suppresses(r2, "some-lesson", 1, "-", base))
        chk("rank-2 ruling does suppress rank-2 candidate",
            suppresses(r2, "some-lesson", 2, "-", base))
        chk("DEFER never suppresses (it is a promise, not a dismissal)",
            not suppresses(dict(rulings[0], verdict="DEFER"),
                           "some-lesson", 1, "SKILL_NEAR", base))

        # probe 1 (RED-first invalidation): touch the covering file → re-open
        cov.write_text("# rule carrying it\nedited\n")
        kept, sup = apply_rulings(picked, rulings, base)
        chk("edited covering subject re-opens the candidate (RED probe)",
            not sup and len(kept) == 2)
        cov.write_text("# rule carrying it\n")
        chk("restoring the byte-identical subject suppresses again",
            apply_rulings(picked, rulings, base)[1])
        inst.write_text("# lesson\nnew evidence\n")
        chk("instinct change (new evidence) re-opens the candidate",
            not apply_rulings(picked, rulings, base)[1])
        inst.write_text("# lesson\n")
        cov.unlink()
        chk("deleted covering subject re-opens the candidate",
            not apply_rulings(picked, rulings, base)[1])
        cov.write_text("# rule carrying it\n")

        # probe 3 (fail-open): truncated ledger suppresses NOTHING
        p = rulings_path(base)
        p.write_text(p.read_text()[:-20])
        chk("truncated ledger reads as untrusted (None)",
            load_rulings(base) is None)
        chk("untrusted ledger suppresses nothing",
            apply_rulings(picked, None, base) == (picked, []))
        p.unlink()
        chk("missing ledger is empty, not untrusted", load_rulings(base) == [])

        write_ruling(base, "some-lesson", "DEFER", 1,
                     "blocked on liveness — revisit when it stops accruing",
                     "cov", "SKILL_NEAR", "-")
        chk("--check exits 0 on a valid standing ruling",
            check(base, "some-lesson") == 0)
        chk("--check exits 1 for an unruled id", check(base, "nope") == 1)

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

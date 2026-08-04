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
            [--covering <bare rule/skill stem> | --artifact] \
            [--gate1 F] [--gate2 F]
  # rank-1 DROP requires exactly one of --covering / --artifact:
  #   --covering  a rule/skill carries this lesson (hashed as a 2nd subject)
  #   --artifact  the gate-1 flag is a false positive; nothing covers it
  #               (single-subject ruling; instinct-file change is the only
  #               invalidation trigger)
  ruling.py --check <id>     # standing rulings for id + validity; exit 0 iff one is valid
  ruling.py --selftest
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
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


def _validate_ruling_row(r):
    """Structural check beyond "is it JSON" — BUG 3: a shape-valid-but-
    field-missing/mistyped row (e.g. {"verdict":"DROP"}) previously survived
    load_rulings and crashed shortlist later with a raw KeyError, breaking
    the "malformed ledger -> suppress nothing, warn" contract. Raises
    ValueError; the caller's except turns that into the fail-open None."""
    if not isinstance(r, dict):
        raise ValueError(f"row is not an object: {r!r}")
    if not isinstance(r.get("id"), str):
        raise ValueError("missing/mistyped 'id' (want str)")
    if r.get("verdict") not in ("DROP", "DEFER"):
        raise ValueError(f"unknown verdict {r.get('verdict')!r}")
    if not isinstance(r.get("rank"), int) or isinstance(r.get("rank"), bool):
        raise ValueError("missing/mistyped 'rank' (want int)")
    if not isinstance(r.get("flags"), dict):
        raise ValueError("missing/mistyped 'flags' (want dict)")
    if not isinstance(r.get("why"), str):
        raise ValueError("missing/mistyped 'why' (want str)")
    if not isinstance(r.get("ruled"), str):
        raise ValueError("missing/mistyped 'ruled' (want str)")
    subjects = r.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        raise ValueError("missing/empty/mistyped 'subjects' (want non-empty list)")
    seen_paths = set()
    for s in subjects:
        if not isinstance(s, dict) or not isinstance(s.get("path"), str) \
                or not isinstance(s.get("sha256"), str):
            raise ValueError(f"malformed subject entry {s!r}")
        subject_path = s["path"]
        pure = pathlib.PurePosixPath(subject_path)
        if pure.is_absolute() or "\\" in subject_path or ".." in pure.parts:
            raise ValueError(f"unsafe subject path {subject_path!r}")
        if subject_path in seen_paths:
            raise ValueError(f"duplicate subject path {subject_path!r}")
        seen_paths.add(subject_path)
        if not re.fullmatch(r"[0-9a-f]{64}", s["sha256"]):
            raise ValueError(f"invalid subject sha256 for {subject_path!r}")
    instinct_subject = f"{CORPUS_REL}/{r['id']}.md"
    if instinct_subject not in seen_paths:
        raise ValueError(f"missing live-instinct subject {instinct_subject!r}")
    # 'justification' ("covered"/"artifact") is OPTIONAL — legacy records
    # predate it. Missing must NOT untrust the ledger (that would silently
    # disable every standing ruling); only a present-but-wrong-type value is
    # rejected.
    if "justification" in r and not isinstance(r["justification"], str):
        raise ValueError("mistyped 'justification' (want str)")
    if r.get("justification") == "artifact" and len(subjects) != 1:
        raise ValueError("artifact ruling must contain only the instinct subject")
    if r.get("justification") == "covered" and len(subjects) < 2:
        raise ValueError("covered ruling requires a covering subject")


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
            _validate_ruling_row(r)
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


def write_ruling(base, id_, verdict, rank, why, covering, gate1, gate2,
                 artifact=False):
    if verdict not in ("DROP", "DEFER"):
        print(f"ruling: verdict must be DROP or DEFER, got {verdict!r}",
              file=sys.stderr)
        raise SystemExit(2)
    if not why.strip() or why.strip().lower() == "artifact":
        print("ruling: --why is mandatory and must be challengeable — name "
              "the covering artifact or the false-positive shape",
              file=sys.stderr)
        raise SystemExit(2)
    # Rank-1 DROP has two distinct justifications (design doc ~L45): COVERED
    # ("rule/skill X carries this lesson" — X is hashed, deleting it re-opens
    # the candidate) or ARTIFACT ("the gate-1 flag is a false positive;
    # nothing covers this lesson" — no file to hash, so only the instinct
    # subject exists and the invalidation trigger is the instinct changing).
    # Exactly one must be named; the operator must not be forced to lie
    # (--covering an unrelated file) to write a legitimate ARTIFACT ruling.
    if verdict == "DROP" and rank == 1:
        if covering and artifact:
            print("ruling: rank-1 DROP takes --covering OR --artifact, "
                  "not both (contradictory: a ruling can't both name a "
                  "covering file and claim nothing covers it)",
                  file=sys.stderr)
            raise SystemExit(2)
        if not covering and not artifact:
            print("ruling: rank-1 DROP requires exactly one of "
                  "--covering <stem> (a rule/skill carries this lesson — "
                  "hashed, deletion re-opens the candidate) or --artifact "
                  "(the gate-1 flag is a false positive; nothing covers "
                  "this lesson) — design "
                  "docs/superpowers/plans/2026-08-01-ruling-ledger-design.md "
                  "~L45", file=sys.stderr)
            raise SystemExit(2)
    inst = base / CORPUS_REL / f"{id_}.md"
    if not inst.is_file():
        print(f"ruling: no live instinct {inst}", file=sys.stderr)
        raise SystemExit(2)
    subjects = [{"path": str(inst.relative_to(base)), "sha256": _sha(inst)}]
    justification = None
    if covering:
        cov = resolve_covering(base, covering)
        subjects.append({"path": str(cov.relative_to(base)),
                         "sha256": _sha(cov)})
        justification = "covered"
    elif artifact:
        justification = "artifact"
    rec = {"id": id_, "verdict": verdict, "rank": rank,
           "flags": {"gate1": gate1, "gate2": gate2}, "why": why.strip(),
           "ruled": datetime.date.today().isoformat(), "subjects": subjects}
    if justification:
        rec["justification"] = justification
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
    artifact = False
    while args:
        a = args.pop(0)
        if a == "--artifact":
            artifact = True
            continue
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
                       opt["why"], opt["covering"], opt["gate1"], opt["gate2"],
                       artifact=artifact)
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
                ("unknown verdict rejected", dict(verdict="MAYBE")),
                ("rank-1 DROP with neither --covering nor --artifact "
                 "rejected (BUG 2, RED)", dict(covering=None)),
                ("rank-1 DROP with BOTH --covering and --artifact rejected "
                 "(contradictory, RED)", dict(covering="cov", artifact=True))):
            try:
                d = dict(id_="some-lesson", verdict="DROP", rank=1,
                         why="covered by rules/cov.md in full", covering=None,
                         gate1="SKILL_NEAR", gate2="-", artifact=False)
                d.update(kw)
                write_ruling(base, **d)
                code = 0
            except SystemExit as e:
                code = e.code
            chk(label + " (exit 2)", code == 2)

        # RED, message content: "neither" case must name BOTH options, not
        # just tell the operator to use --covering.
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            try:
                write_ruling(base, "some-lesson", "DROP", 1,
                             "no artifact and no covering file exist", None,
                             "SKILL_NEAR", "-")
            except SystemExit:
                pass
        msg = buf.getvalue()
        chk("neither-flag error names --covering", "--covering" in msg)
        chk("neither-flag error names --artifact", "--artifact" in msg)
        try:
            resolve_covering(base, "rules/cov.md")
            code = 0
        except SystemExit as e:
            code = e.code
        chk("path-form --covering refused (exit 4, retire.py vocabulary)",
            code == 4)

        # BUG 2 positive cases: covering is mandatory only for rank-1 DROP.
        # Isolated in their own temp base so they don't pollute `base`'s
        # ledger ahead of the "ruling appended" count check below.
        with tempfile.TemporaryDirectory() as tmp2:
            base2 = pathlib.Path(tmp2)
            (base2 / CORPUS_REL).mkdir(parents=True)
            (base2 / CORPUS_REL / "some-lesson.md").write_text("# lesson\n")
            try:
                write_ruling(base2, "some-lesson", "DROP", 2,
                             "weaker flag, no covering artifact yet", None,
                             "-", "-")
                code = 0
            except SystemExit as e:
                code = e.code
            chk("rank-2 DROP without --covering still allowed", code == 0)
            try:
                write_ruling(base2, "some-lesson", "DEFER", 1,
                             "blocked on liveness, no covering artifact", None,
                             "-", "-")
                code = 0
            except SystemExit as e:
                code = e.code
            chk("DEFER rank-1 without --covering allowed (design mandates "
                "covering for rank-1 subjects; ambiguous on DROP vs DEFER, "
                "so only DROP is enforced here)", code == 0)

        write_ruling(base, "some-lesson", "DROP", 1,
                     "covered by rules/cov.md in full", "cov",
                     "SKILL_NEAR", "-")
        rulings = load_rulings(base)
        chk("ruling appended and parseable", rulings and len(rulings) == 1)
        chk("both subjects hashed",
            len(rulings[0]["subjects"]) == 2)
        chk("--covering ruling records justification 'covered'",
            rulings[0]["justification"] == "covered")

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

        # --artifact ruling: no covering file exists to hash — single
        # subject, justification "artifact", and the ONLY invalidation
        # trigger is the instinct itself changing (no 2nd subject to touch).
        with tempfile.TemporaryDirectory() as tmp3:
            base3 = pathlib.Path(tmp3)
            inst3 = base3 / CORPUS_REL
            inst3.mkdir(parents=True)
            lesson3 = inst3 / "false-positive-lesson.md"
            lesson3.write_text("# lesson\n")
            rec3 = write_ruling(base3, "false-positive-lesson", "DROP", 1,
                                "skills-wave generic-title probe — SKILL_DUP "
                                "is a title-word artifact, no skill carries "
                                "this lesson", None, "SKILL_DUP", "-",
                                artifact=True)
            chk("--artifact ruling has exactly 1 subject (no covering file)",
                len(rec3["subjects"]) == 1)
            chk("--artifact ruling records justification 'artifact'",
                rec3["justification"] == "artifact")
            rulings3 = load_rulings(base3)
            picked3 = [(1, "false-positive-lesson.md",
                       "SKILL_DUP+CONVERTED — skill carries it")]
            kept3, sup3 = apply_rulings(picked3, rulings3, base3)
            chk("--artifact ruling suppresses the matching candidate",
                len(sup3) == 1)
            lesson3.write_text("# lesson\nnew evidence\n")
            chk("editing the instinct re-opens an --artifact ruling "
                "(its only invalidation trigger)",
                not apply_rulings(picked3, rulings3, base3)[1])

        # legacy shape (BUG 3 fix must not regress this): a real record with
        # no 'justification' key — all 5 live rank-1 DROPs predate the field
        # — must load fine and keep suppressing, NOT untrust the ledger.
        with tempfile.TemporaryDirectory() as tmp4:
            base4 = pathlib.Path(tmp4)
            (base4 / CORPUS_REL).mkdir(parents=True)
            (base4 / CORPUS_REL / "legacy-lesson.md").write_text("# l\n")
            legacy_rec = dict(write_ruling(
                base4, "legacy-lesson", "DROP", 1,
                "legacy record predating justification", None,
                "SKILL_DUP", "-", artifact=True))
            del legacy_rec["justification"]
            p4 = rulings_path(base4)
            p4.write_text(json.dumps(legacy_rec) + "\n")
            rulings4 = load_rulings(base4)
            chk("legacy record with no 'justification' loads (ledger NOT None)",
                rulings4 is not None and len(rulings4) == 1)
            picked4 = [(1, "legacy-lesson.md",
                       "SKILL_DUP+CONVERTED — skill carries it")]
            chk("legacy record without 'justification' still suppresses",
                len(apply_rulings(picked4, rulings4, base4)[1]) == 1)

        # probe 3 (fail-open): truncated ledger suppresses NOTHING
        p = rulings_path(base)
        p.write_text(p.read_text()[:-20])
        chk("truncated ledger reads as untrusted (None)",
            load_rulings(base) is None)
        chk("untrusted ledger suppresses nothing",
            apply_rulings(picked, None, base) == (picked, []))
        p.unlink()
        chk("missing ledger is empty, not untrusted", load_rulings(base) == [])

        # BUG 3 (RED): structurally malformed-but-valid-JSON rows must fail
        # into None (warn, suppress nothing), never KeyError downstream.
        p.write_text('{"verdict":"DROP"}\n')
        chk("row missing id/rank/etc. -> None, no KeyError (BUG 3, RED)",
            load_rulings(base) is None)
        p.write_text(json.dumps({"id": "x", "verdict": "DROP", "rank": "1",
                                  "flags": {}, "why": "w", "ruled": "2026-01-01",
                                  "subjects": []}) + "\n")
        chk("row with rank as string -> None", load_rulings(base) is None)
        empty_subject_rec = {
            "id": "some-lesson", "verdict": "DROP", "rank": 1,
            "flags": {"gate1": "SKILL_NEAR", "gate2": "-"},
            "why": "malformed empty-subject fixture", "ruled": "2026-01-01",
            "subjects": [],
        }
        p.write_text(json.dumps(empty_subject_rec) + "\n")
        empty_loaded = load_rulings(base)
        chk("DROP with no subjects makes the ledger untrusted (RED)",
            empty_loaded is None)
        chk("empty-subject ruling suppresses nothing",
            apply_rulings(picked, empty_loaded, base) == (picked, []))
        p.unlink()

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

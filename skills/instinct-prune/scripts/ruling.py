#!/usr/bin/env python3
"""Ruling ledger for `instinct-prune` Phase C — remembers human verdicts so
adjudicated candidates stop resurfacing on every shortlist run.

Design: docs/superpowers/plans/2026-08-01-ruling-ledger-design.md. VEX-shaped
dispositions (verdict + mandatory challengeable why) with SARIF-shaped
invalidation (sha256 of every file the judgment compared — any change re-opens
the candidate).

Subject hash, per record (`hash_algo`, absent = v1):
  v1  full file content. Every standing ruling written before 2026-08-19.
  v2  content minus the frontmatter keys `cites`, `last_checked`, `updated` —
      the bookkeeping class that caused 3 of the 10 re-adjudications in the
      2026-08-05→08-19 window. Body edits of any kind still invalidate.
No migration wave: a v1 record keeps v1 sensitivity until it is naturally
re-ruled, at which point it comes back as v2.

One JSON object per line, append-only, in `<corpus>/.distill/rulings.jsonl`.
Single-writer by design: O_APPEND of one newline-terminated line is the whole
concurrency story — do not add locking, add a second writer and this file's
premise is wrong.

Verdicts:
  DROP   the flag is an artifact / coverage judged insufficient to retire.
  DEFER  genuine future candidate blocked on a named condition (why names it).
         A DEFER also records the lesson's `evidence_count` at ruling time plus
         `no_accrual_days` (default 30). When the count has not moved for that
         long, shortlist ANNOUNCES the condition as met — it never retires.
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
            [--gate1 F] [--gate2 F] [--no-accrual-days N]
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

# Subject-hash v2 (2026-08-19 tuning, T1): the keys whose edits are pure
# bookkeeping and must not re-open an otherwise untouched ruling. 3 of the 10
# re-adjudications in the 2026-08-05→08-19 window were exactly this class.
# `evidence_count` is deliberately NOT here — a changed count IS accrual, and
# the DEFER exit condition reads it.
BOOKKEEPING_KEYS = ("cites:", "last_checked:", "updated:")
HASH_ALGOS = ("v1", "v2")
DEFAULT_NO_ACCRUAL_DAYS = 30


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strip_bookkeeping(text):
    """Drop the bookkeeping keys from a leading `--- … ---` frontmatter block.

    Line-based and pinned, NOT parse-and-reserialize: round-tripping YAML has
    unstable key ordering and quoting, which would manufacture exactly the
    false invalidations this function exists to kill. Body bytes are never
    touched, and text with no leading frontmatter (rules/*.md, skills' SKILL.md)
    passes through unchanged — stripping is a no-op there, by design."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"),
               None)
    if end is None:
        return text  # unterminated frontmatter is not frontmatter
    out, dropping = [lines[0]], False
    for ln in lines[1:end]:
        if ln[:1] in (" ", "\t"):
            if not dropping:      # an indented continuation of a kept key
                out.append(ln)
            continue
        dropping = ln.startswith(BOOKKEEPING_KEYS)
        if not dropping:
            out.append(ln)
    return "\n".join(out + lines[end:])


def subject_hash(path, algo="v1"):
    """The one hash implementation. Both the writer (write_ruling) and the
    verifier (subjects_valid) go through here — two copies would drift, and
    drift here silently breaks suppression."""
    if algo != "v2":
        return _sha(path)
    raw = path.read_bytes()
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        return hashlib.sha256(raw).hexdigest()  # binary subject: full content
    return hashlib.sha256(strip_bookkeeping(text).encode()).hexdigest()


def read_evidence_count(path):
    """Current frontmatter `evidence_count`, or None when the file is missing,
    has no frontmatter, or the value will not parse. None means "stays manual"
    everywhere it is read — never a guess, never a crash."""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.fullmatch(r"evidence_count:\s*(\d+)\s*", ln)
        if m:
            return int(m.group(1))
    return None


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
    # 'hash_algo' and 'accrual' are both OPTIONAL — an absent 'hash_algo' means
    # v1 (full content), an absent 'accrual' means a legacy DEFER that stays
    # manual. Missing must not untrust the ledger; only present-but-wrong is
    # rejected. An UNKNOWN algo is rejected: we cannot verify a hash we cannot
    # compute, and guessing v1 would silently suppress on a false match.
    if "hash_algo" in r and r["hash_algo"] not in HASH_ALGOS:
        raise ValueError(f"unknown 'hash_algo' {r['hash_algo']!r} "
                         f"(want one of {HASH_ALGOS})")
    if "accrual" in r:
        accrual = r["accrual"]
        if not isinstance(accrual, dict):
            raise ValueError("mistyped 'accrual' (want object)")
        for key in ("evidence_count", "no_accrual_days"):
            val = accrual.get(key)
            if not isinstance(val, int) or isinstance(val, bool) or val < 0:
                raise ValueError(f"missing/mistyped accrual.{key} "
                                 f"(want non-negative int)")


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
    """Per-record hash dispatch: an absent `hash_algo` is a v1 record and keeps
    its original full-content sensitivity. There is no migration wave — a
    standing ruling comes back as v2 only when it is naturally re-ruled."""
    algo = ruling.get("hash_algo", "v1")
    for s in ruling["subjects"]:
        p = base / s["path"]
        if not p.is_file() or subject_hash(p, algo) != s["sha256"]:
            return False
    return True


def latest_defers(rulings):
    """One DEFER per id — the last one written. The ledger stays append-only;
    only the read side collapses (T3: 10 lines for 3 files, with stale
    `[subjects changed]` markers on rulings a later record already replaced)."""
    latest = {}
    for r in rulings or []:
        if r.get("verdict") == "DEFER":
            latest[r["id"]] = r
    return list(latest.values())


def defer_condition_met(ruling, base, today=None):
    """True iff this DEFER's inactivity condition has come due: the lesson's
    `evidence_count` is unchanged since the ruling AND `no_accrual_days` have
    passed. ANNOUNCE-ONLY — the caller prints, it never retires. "Prune never
    decides alone" applies here too.

    Every unknown answers False (stays manual): a legacy record with no
    accrual block, an unreadable current count, a missing file, a nonsense
    date. Never raises."""
    if ruling.get("verdict") != "DEFER":
        return False
    accrual = ruling.get("accrual")
    if not isinstance(accrual, dict):
        return False
    at_ruling = accrual.get("evidence_count")
    days = accrual.get("no_accrual_days", DEFAULT_NO_ACCRUAL_DAYS)
    if not isinstance(at_ruling, int) or not isinstance(days, int):
        return False
    current = read_evidence_count(base / CORPUS_REL / f"{ruling['id']}.md")
    if current is None or current != at_ruling:
        return False
    try:
        ruled = datetime.date.fromisoformat(ruling.get("ruled", ""))
    except (TypeError, ValueError):
        return False
    return ((today or datetime.date.today()) - ruled).days >= days


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
                 artifact=False, no_accrual_days=DEFAULT_NO_ACCRUAL_DAYS):
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
    # A DEFER's exit condition is inactivity, so the count it will be compared
    # against must be readable NOW. Writing a DEFER we can never evaluate would
    # produce a promise that silently stays manual forever — refuse instead.
    accrual = None
    if verdict == "DEFER":
        at_ruling = read_evidence_count(inst)
        if at_ruling is None:
            print(f"ruling: DEFER needs a readable `evidence_count` in the "
                  f"frontmatter of {inst} — the exit condition compares it "
                  f"against the count at ruling time; fix the file first",
                  file=sys.stderr)
            raise SystemExit(2)
        accrual = {"evidence_count": at_ruling,
                   "no_accrual_days": int(no_accrual_days)}
    subjects = [{"path": str(inst.relative_to(base)),
                 "sha256": subject_hash(inst, "v2")}]
    justification = None
    if covering:
        cov = resolve_covering(base, covering)
        subjects.append({"path": str(cov.relative_to(base)),
                         "sha256": subject_hash(cov, "v2")})
        justification = "covered"
    elif artifact:
        justification = "artifact"
    rec = {"id": id_, "verdict": verdict, "rank": rank,
           "flags": {"gate1": gate1, "gate2": gate2}, "why": why.strip(),
           "ruled": datetime.date.today().isoformat(), "subjects": subjects,
           "hash_algo": "v2"}
    if justification:
        rec["justification"] = justification
    if accrual:
        rec["accrual"] = accrual
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
           "gate1": "-", "gate2": "-",
           "no-accrual-days": str(DEFAULT_NO_ACCRUAL_DAYS)}
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
    try:
        days = int(opt["no-accrual-days"])
        if days < 0:
            raise ValueError
    except ValueError:
        print(f"ruling: --no-accrual-days must be a non-negative integer, got "
              f"{opt['no-accrual-days']!r}", file=sys.stderr)
        return 2
    rec = write_ruling(BASE, id_, opt["verdict"], int(opt["rank"]),
                       opt["why"], opt["covering"], opt["gate1"], opt["gate2"],
                       artifact=artifact, no_accrual_days=days)
    print(f"recorded: {rec['verdict']} {id_} ({len(rec['subjects'])} subjects "
          f"hashed, {rec['hash_algo']}) -> {rulings_path(BASE)}")
    if "accrual" in rec:
        print(f"  accrual: evidence_count={rec['accrual']['evidence_count']} "
              f"at ruling time; announce when unchanged for "
              f"{rec['accrual']['no_accrual_days']} days")
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
        # Frontmatter with a real evidence_count: a DEFER cannot be written
        # without one (its exit condition compares against it), and the
        # bookkeeping-strip probes below need keys to strip.
        LESSON = ("---\nid: some-lesson\nevidence_count: 4\n"
                  "cites: []\nupdated: \"2026-08-01\"\n"
                  "last_checked: \"2026-08-01\"\n---\n# lesson\n")
        inst = corpus / "some-lesson.md"
        inst.write_text(LESSON)
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
            (base2 / CORPUS_REL / "some-lesson.md").write_text(LESSON)
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
        inst.write_text(LESSON + "new evidence\n")
        chk("instinct change (new evidence) re-opens the candidate",
            not apply_rulings(picked, rulings, base)[1])
        inst.write_text(LESSON)
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

    # ---- T1: subject-hash v2 (bookkeeping-insensitive) --------------------
    # 3 of the 10 re-adjudications in the 2026-08-05→08-19 window were caused
    # by a cites/last_checked/updated-only edit invalidating an otherwise
    # untouched ruling. v2 hashes the file MINUS those frontmatter keys.
    def _lesson(evid=3, cites='cites: []', body="# lesson\nsome content\n",
                checked="2026-08-01", updated="2026-08-01"):
        return ("---\n"
                "id: v2-lesson\n"
                "trigger: \"a literal string | another one\"\n"
                f"evidence_count: {evid}\n"
                f"{cites}\n"
                f"updated: \"{updated}\"\n"
                f"last_checked: \"{checked}\"\n"
                "---\n" + body)

    with tempfile.TemporaryDirectory() as tmp5:
        base5 = pathlib.Path(tmp5)
        corpus5 = base5 / CORPUS_REL
        corpus5.mkdir(parents=True)
        (base5 / "rules").mkdir()
        lesson5 = corpus5 / "v2-lesson.md"
        lesson5.write_text(_lesson())
        rule5 = base5 / "rules" / "cov5.md"
        rule5.write_text("# a covering rule, no frontmatter\n")

        rec5 = write_ruling(base5, "v2-lesson", "DROP", 1,
                            "covered by rules/cov5.md", "cov5", "RULE_DUP", "-")
        chk("new ruling is stamped hash_algo v2 (RED)",
            rec5.get("hash_algo") == "v2")
        picked5 = [(1, "v2-lesson.md", "RULE_DUP+CONVERTED — rule carries it")]

        def suppressed5():
            return bool(apply_rulings(picked5, load_rulings(base5), base5)[1])

        chk("v2: byte-identical subject still suppresses", suppressed5())
        lesson5.write_text(_lesson(cites='cites:\n  - "rules/cov5.md"'))
        chk("v2 survives a cites-only edit (RED — the 3-of-10 case)",
            suppressed5())
        lesson5.write_text(_lesson(checked="2026-08-19"))
        chk("v2 survives a last_checked-only edit (RED)", suppressed5())
        lesson5.write_text(_lesson(updated="2026-08-19"))
        chk("v2 survives an updated-only edit (RED)", suppressed5())
        lesson5.write_text(_lesson(evid=4))
        chk("v2 re-opens on an evidence_count edit (accrual is NOT bookkeeping)",
            not suppressed5())
        lesson5.write_text(_lesson(body="# lesson\nrewritten content\n"))
        chk("v2 re-opens on a body edit", not suppressed5())
        lesson5.write_text(_lesson())
        chk("v2 suppresses again once the body is restored", suppressed5())
        rule5.write_text("# a covering rule, no frontmatter\nedited\n")
        chk("v2 re-opens when a frontmatter-less covering file changes "
            "(stripping is a no-op there)", not suppressed5())
        rule5.write_text("# a covering rule, no frontmatter\n")

        # v1 records (no hash_algo) keep their old full-content sensitivity —
        # no migration wave; standing rulings are unchanged until re-ruled.
        legacy5 = dict(rec5)
        legacy5.pop("hash_algo", None)
        legacy5["subjects"] = [dict(s, sha256=_sha(base5 / s["path"]))
                               for s in legacy5["subjects"]]
        rulings_path(base5).write_text(json.dumps(legacy5) + "\n")
        chk("v1 record (no hash_algo) suppresses a byte-identical file",
            suppressed5())
        lesson5.write_text(_lesson(checked="2026-08-19"))
        chk("v1 record still re-opens on a last_checked-only edit "
            "(no migration wave — old sensitivity preserved)",
            not suppressed5())
        lesson5.write_text(_lesson())

    # ---- T2: DEFER accrual bookkeeping ------------------------------------
    with tempfile.TemporaryDirectory() as tmp6:
        base6 = pathlib.Path(tmp6)
        corpus6 = base6 / CORPUS_REL
        corpus6.mkdir(parents=True)
        bare6 = corpus6 / "bare-lesson.md"
        bare6.write_text("# no frontmatter at all\n")
        try:
            write_ruling(base6, "bare-lesson", "DEFER", 1,
                         "blocked on a named condition", None, "-", "-")
            code = 0
        except SystemExit as e:
            code = e.code
        chk("DEFER without a readable evidence_count is refused (exit 2, RED)",
            code == 2)

        live6 = corpus6 / "v2-lesson.md"
        live6.write_text(_lesson(evid=7))
        rec6 = write_ruling(base6, "v2-lesson", "DEFER", 1,
                            "blocked on accrual — revisit when it stops "
                            "accruing evidence", None, "-", "-")
        chk("DEFER records the evidence_count at ruling time (RED)",
            rec6.get("accrual", {}).get("evidence_count") == 7)
        chk("DEFER records no_accrual_days, default 30 (RED)",
            rec6.get("accrual", {}).get("no_accrual_days") == 30)
        chk("a DROP carries no accrual block",
            "accrual" not in write_ruling(
                base6, "v2-lesson", "DROP", 2, "unrelated drop", None, "-", "-"))

        old = datetime.date.today() - datetime.timedelta(days=31)
        young = datetime.date.today() - datetime.timedelta(days=29)
        met = dict(rec6, ruled=old.isoformat())
        chk("condition MET: count unchanged and ≥ 30 days elapsed",
            defer_condition_met(met, base6))
        chk("not met: 29 days elapsed",
            not defer_condition_met(dict(rec6, ruled=young.isoformat()), base6))
        live6.write_text(_lesson(evid=8))
        chk("not met: the lesson accrued (count moved) — the clock resets",
            not defer_condition_met(met, base6))
        live6.write_text(_lesson(evid=7))
        legacy6 = dict(met)
        legacy6.pop("accrual", None)
        chk("legacy DEFER without accrual stays manual (announces nothing)",
            not defer_condition_met(legacy6, base6))
        live6.write_text("# frontmatter dropped by hand\n")
        chk("unreadable current evidence_count stays manual, never crashes",
            not defer_condition_met(met, base6))
        live6.unlink()
        chk("a missing lesson file stays manual, never crashes",
            not defer_condition_met(met, base6))
        chk("a DROP is never a DEFER condition",
            not defer_condition_met(dict(met, verdict="DROP"), base6))
        chk("a nonsense ruled date stays manual",
            not defer_condition_met(dict(met, ruled="not-a-date"), base6))

        # latest-per-id (T3): the ledger stays append-only; only the read side
        # collapses. Order is ledger order — the last record written wins.
        seq = [dict(rec6, ruled="2026-08-01", why="first"),
               dict(rec6, ruled="2026-08-02", why="second"),
               dict(rec6, id="other-lesson", ruled="2026-08-01", why="other")]
        latest = latest_defers(seq)
        chk("latest_defers keeps one record per id", len(latest) == 2)
        chk("latest_defers keeps the LAST record for a re-ruled id",
            next(r for r in latest if r["id"] == "v2-lesson")["why"] == "second")

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

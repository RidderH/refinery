#!/usr/bin/env python3
"""Drive the whole `instinct-distill` loop over `fixtures/demo/` — shortlist → index → gate.

The other selftests build their fixtures inline with `tempfile`, which proves each script
against text written three lines above the assertion. This one runs the pipeline over a
corpus that lives **on disk in the real file format**, so a change to the format contract
(`instinct-format/SKILL.md`) that no inline fixture happens to exercise still fails here.

Every fixture file exists to cover ONE branch; `fixtures/README.md` is the map. The gate
branches covered: `eligible`, first-observations-only (the merged-document case), untyped
`?`, same-date repeated failures, un-migrated legacy entries, and a lesson whose latest
entries are `successful_recall` (counter-evidence, never a promotion).

Two things here are not decoration:

- **The archive branch.** `record()` reads `<id>.evidence.md` EXCLUSIVELY when one exists.
  The eligible fixture's body carries one `repeated_failure` and its archive carries two —
  so an assertion of 2 fails the moment anything starts judging on the body excerpt.
- **The archive that parses ZERO.** `test-database-is-not-reset-between-suites` has an
  archive in the unsupported paragraph shape. The archive still wins, and the hint is
  `UNPARSED`. Falling back to the body there is a silent WRONG PASS, not a degradation:
  that body reads `eligible` on two old `repeated_failure` entries while the archive's
  newest entry is a `successful_recall`, the value that declines a cluster.
- **Cold start (#10).** The same pipeline against an EMPTY directory must yield zero
  clusters and zero proposals, not a crash and not a lowered threshold.

Usage:
    python3 demo_run.py [FIXTURE_ROOT]
    python3 demo_run.py --selftest
"""
import pathlib
import re
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import build_index as B  # noqa: E402
import shortlist as S  # noqa: E402

DEMO = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "demo"

# The intended cluster: same domain, overlapping trigger/action vocabulary, author-linked.
PAIR = ("build-cache-key-omits-the-config-hash",
        "config-change-does-not-invalidate-the-build-cache")
# Shares "cache", "config", "change", "stale" with the pair and is STILL excluded, because
# `domain` is a partition boundary and no threshold can admit an unlinked cross-domain file.
CROSS_DOMAIN = "schema-cache-serves-the-config-from-before-the-change"
# Body parses 2 repeated_failure on distinct dates — `eligible` on the body ALONE. Its
# archive is written in the unsupported paragraph shape, so it parses 0, and its real newest
# entry is a `successful_recall`: the value that DECLINES a cluster. This is the fixture that
# fails when `record()` falls back to the body on a zero-parsing archive.
ZERO_ARCHIVE = "test-database-is-not-reset-between-suites"
# The two fixtures that own an `<id>.evidence.md` sibling.
ARCHIVED = (PAIR[0], ZERO_ARCHIVE)

# Exact gate_hint prefixes, one per branch. Prefixes, not equality, for the two hints that
# quote counts — the numbers are asserted separately where they carry meaning.
EXPECTED = {
    # "over 2 dates", not 5: gate condition 1 counts the distinct dates of the
    # `repeated_failure` entries alone. The archive spans 5 dates across all outcomes, and
    # `distinct_dates` still reports 5 — the two numbers are different questions.
    PAIR[0]: "eligible: 2 repeated_failure over 2 dates",
    PAIR[1]: "ineligible: 0 repeated_failure, needs 2",
    "retry-loop-swallows-the-original-error":
        "ineligible: 0 prose + 0 legacy + 1 untyped",
    "backoff-timer-doubles-twice-per-attempt":
        "ineligible: repeated failures share one date",
    CROSS_DOMAIN: "ineligible: 1 repeated_failure, needs 2",
    "plan-checkboxes-outlive-the-plan":
        "ineligible: 0 prose + 2 legacy + 0 untyped",
    "read-the-plan-before-proposing-a-sweep":
        "ineligible: 0 repeated_failure, needs 2",
    ZERO_ARCHIVE: "UNPARSED: the sibling .evidence.md exists but parsed 0 entries",
}


def pipeline(root):
    """shortlist → index, exactly as SKILL.md steps 2 and 5 run it. Returns
    (clusters, {id: record}). An empty corpus returns ([], {}) — never an exception."""
    records, malformed = S.load(root)
    clusters, _ = S.build(records, top_k=12, min_score=0.0)
    index = {i: B.record(root, i) for i in sorted(records)}
    return clusters, index, malformed


def proposals(index):
    """Files the gate would let through. `gate_hint` is advisory and GATES.md owns the
    decision, but "starts with eligible" is the only mechanical read of it there is."""
    return [i for i, r in index.items()
            if not r.get("missing") and not r.get("malformed")
            and r["gate_hint"].startswith("eligible")]


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    root = pathlib.Path(args[0]) if args else DEMO
    clusters, index, malformed = pipeline(root)
    print(f"corpus={len(index)} files  clusters={len(clusters)}  "
          f"proposals={len(proposals(index))}")
    print()
    for c in clusters:
        nb = ", ".join(f"{n['id']}{'*' if n['linked'] else ''}:{n['score']}"
                       for n in c["neighbours"])
        print(f"  [{c['domain']:<10}] {c['seed']}\n{'':>15}-> {nb}")
    print()
    print(f"{'id':<54}{'src':<9}gate_hint")
    for i, r in index.items():
        print(f"  {i:<52}{r['evidence']['source']:<9}{r['gate_hint']}")
    if malformed:
        print(f"\nMALFORMED — the run above is INCOMPLETE ({len(malformed)}):",
              file=sys.stderr)
        for n in malformed:
            print(f"  {n}", file=sys.stderr)
        return 1
    return 0


# -------------------------------------------------------------------- selftest
def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    clusters, index, malformed = pipeline(DEMO)
    by_seed = {c["seed"]: c for c in clusters}

    check("demo corpus loads with no malformed files", malformed == [])
    check("demo corpus is 8 lesson files (archives are not candidates)", len(index) == 8)

    # ---- shortlist: the intended cluster, and only it
    seed, partner = PAIR
    check("intended pair seeds a cluster", seed in by_seed)
    nb = {n["id"] for n in by_seed.get(seed, {"neighbours": []})["neighbours"]}
    check("intended partner is the seed's only neighbour", nb == {partner})
    check("author link on the pair is flagged, and is a tiebreak not the driver",
          all(n["linked"] for n in by_seed[seed]["neighbours"]))
    check("cross-domain file is not a neighbour of EITHER pair member",
          all(CROSS_DOMAIN not in {n["id"] for n in by_seed[s]["neighbours"]}
              for s in PAIR if s in by_seed))
    # It shares vocabulary with the pair, so its absence is structural, not lexical —
    # the same shape as shortlist.py's own "threshold 0, top-k 99" assertion.
    floor, _ = S.build(S.load(DEMO)[0], top_k=99, min_score=0.0)
    check("cross-domain file stays excluded at threshold 0, top-k 99",
          CROSS_DOMAIN not in
          {n["id"] for n in {c["seed"]: c for c in floor}[seed]["neighbours"]})
    check("cross-domain file seeds no cluster of its own (partition of one)",
          CROSS_DOMAIN not in by_seed)

    # ---- gate: one branch per fixture, each with its OWN reason
    for ident, expected in EXPECTED.items():
        check(f"gate_hint[{ident}] -> {expected[:46]}",
              index[ident]["gate_hint"].startswith(expected))
    check("exactly one fixture is eligible", proposals(index) == [seed])
    check("the five non-eligible reasons are genuinely distinct",
          len({index[i]["gate_hint"] for i in
               (PAIR[1], "retry-loop-swallows-the-original-error",
                "backoff-timer-doubles-twice-per-attempt",
                "plan-checkboxes-outlive-the-plan", ZERO_ARCHIVE)}) == 5)

    # ---- an archive that parses ZERO must stop the run, never fall back to the body
    z = index[ZERO_ARCHIVE]
    check("zero-parsing archive is reported UNPARSED, naming the archive",
          z["gate_hint"].startswith("UNPARSED") and ".evidence.md" in z["gate_hint"])
    # The body ALONE reads eligible — 2 repeated_failure on distinct dates, and an
    # `evidence_count` that matches, so nothing else blocks it. If that verdict ever leaks
    # into the hint, the run promotes a lesson whose archive says the opposite.
    #
    # `"eligible: " not in hint` is NOT the assertion to write here: "ineligible: " contains
    # "eligible: " as a substring, so it passes on any ineligible verdict and would have
    # read green for the wrong reason under a body fallback that reported
    # "ineligible: parsed 2 of 4 declared entries".
    check("the body's `eligible` verdict never appears in the hint",
          not z["gate_hint"].startswith("eligible")
          and "eligible: 2 repeated_failure" not in z["gate_hint"])
    check("archive parsed 0 while the body parsed 2 — both reported, neither guessed",
          z["evidence"]["archive_entries"] == 0 and z["evidence"]["body_entries"] == 2)
    check("the archive wins even at zero entries", z["evidence"]["source"] == "archive")
    # The proof that a body fallback would be WRONG, not merely inconsistent: the archive's
    # newest entry is a successful_recall, which GATES.md declines outright.
    newest = next(l for l in (DEMO / f"{ZERO_ARCHIVE}.evidence.md").read_text().split("\n")
                  if re.match(r"\d{4}-\d{2}-\d{2}\s", l))
    check("the archive's newest entry is the successful_recall the body omits",
          "`successful_recall`" in newest)

    # ---- the archive branch: record() reads the sibling EXCLUSIVELY when it exists
    r = index[seed]
    check("eligible fixture is read from its archive, not its body",
          r["evidence"]["source"] == "archive")
    check("archive-only counts reach the gate (body shows 1 repeated_failure, archive 2)",
          r["evidence"]["repeated_failure"] == 2 and r["distinct_dates"] == 5)
    body_only = B.evidence_stats(
        (DEMO / f"{seed}.md").read_text().split("---\n", 2)[2])
    check("judging the body excerpt instead would have MISSED it",
          body_only["repeated_failure"] == 1)
    check("every fixture without an archive is read from its body",
          all(index[i]["evidence"]["source"] == "body"
              and index[i]["evidence"]["archive_entries"] is None
              for i in index if i not in ARCHIVED))

    # ---- the merged-document hole (GATES.md condition 2, arriving via first_observation)
    m = index[PAIR[1]]
    check("3 merged first observations over 3 dates manufacture no recurrence",
          m["evidence"]["first_observation"] == 3 and m["distinct_dates"] == 3
          and m["evidence"]["repeated_failure"] == 0)

    # ---- successful_recall is counter-evidence, never a promotion
    sr = index["read-the-plan-before-proposing-a-sweep"]
    check("successful_recall entries are typed and count toward nothing",
          sr["evidence"]["successful_recall"] == 2
          and sr["evidence"]["untyped"] == 0
          and not sr["gate_hint"].startswith("eligible"))

    # ---- untyped is unverified, never absent (#8)
    u = index["retry-loop-swallows-the-original-error"]
    check("untyped `?` is reported separately from zero",
          u["evidence"]["untyped"] == 1 and u["evidence"]["repeated_failure"] == 1)

    # ---- cold start (#10): an EMPTY corpus, not an empty fixture directory
    with tempfile.TemporaryDirectory() as tmp:
        empty = pathlib.Path(tmp)
        c0, i0, bad0 = pipeline(empty)
        check("cold start: zero clusters, zero records, no malformed",
              c0 == [] and i0 == {} and bad0 == [])
        check("cold start: zero proposals rather than a crash", proposals(i0) == [])
        rows, bad = B.frontier(empty)
        check("cold start: the typing frontier is empty, not undefined",
              rows == [] and bad == [])

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Frontmatter + evidence index for a shortlisted set. Plan §1.

Reads `shortlist.py --json` (or explicit ids) and emits one compact record per file, so the
model judges a shortlist rather than scanning 357 one-liners (decision #6).

The index carries the **eligibility signals**, not just metadata: per-file counts of typed
evidence by outcome. Without them the model re-derives the recurrence gate by eye, which is
the guessing `reference/GATES.md` exists to prevent. `untyped` is reported separately from
zero — an untyped entry is *unverified*, never *absent* (#8).

Skim, not read: this is a router. A file that reaches the gate is read in FULL before any
`action:` or outcome is authored (`instinct-format/SKILL.md:31-33`).

Usage:
    python3 shortlist.py --json | python3 build_index.py
    python3 build_index.py --ids a b c [ROOT]
    python3 build_index.py --selftest
"""
import json
import pathlib
import re
import sys

FORMAT_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "instinct-format" / "scripts"
sys.path.insert(0, str(FORMAT_SCRIPTS))
from instinct_record import InstinctFormatError, parse_file, parse_text  # noqa: E402

DEFAULT_ROOT = pathlib.Path.home() / ".claude/homunculus/instincts/personal"

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
EVSEC = re.compile(r"^##\s+[A-Za-z ]*Evidence\b.*?$", re.M | re.I)
ANYH2 = re.compile(r"^##\s", re.M)
# - **DATE** `outcome` ...   (`?` = migrated but not yet classified)
ENTRY = re.compile(r"^[-*]\s+\*\*(\d{4}-\d{2}-\d{2})\*\*\s+`([a-z_?]+)`")
# spec-format entry that predates the typed schema
LEGACY = re.compile(r"^[-*]\s+\*\*(\d{4}-\d{2}-\d{2})\*\*(?!\s+`)")
# pre-schema PROSE entry: a column-0 bullet with no bold date at all
PROSE = re.compile(r"^[-*]\s+\S")
OUTCOMES = ("repeated_failure", "successful_recall", "confirmation", "correction",
            # A lesson's FIRST observation — the event that created it. Counts toward
            # nothing in the recurrence gate, but it is a TYPED value, so a
            # single-observation file is honestly ineligible instead of permanently
            # "unverified". Without it, 182 evidence_count:1 files and the 280
            # source-tagged entries across 51 merged topic documents could never be
            # typed at all: on a merged doc every entry is the first observation of a
            # different source lesson. (User ruling 2026-07-29, pilot §1A step 4.)
            "first_observation")


def field(fm, name):
    """Compatibility adapter backed by the canonical frontmatter parser."""
    try:
        value = parse_text(f"---\n{fm}\n---\n").values.get(name)
    except InstinctFormatError:
        return None
    return None if value is None else str(value)


def evidence_stats(body, whole=False):
    """Counts by outcome across EVERY evidence section — `## Evidence` and
    `## Additional evidence` both. Matching only the canonical heading reported zero
    entries for the highest-degree node in the corpus.

    THREE entry shapes, not two. Counting only the bold-date forms made
    `verify-before-building` (6 entries, all prose bullets) report every counter as 0 —
    identical to a file with no evidence at all. Zero-because-unparsed must never look
    like zero-because-absent, so legacy PROSE bullets are counted in their own bucket.
    """
    st = {o: 0 for o in OUTCOMES}
    # `dates` is every entry's date (informational). `rf_dates` is the dates of the
    # REPEATED_FAILURE entries only — recurrence-gate condition 1 is about those and
    # nothing else. Judging it on `dates` let a file with two same-day recurrences pass
    # as long as some OTHER entry supplied a second date, which `first_observation`
    # makes routine: a merged document is full of non-counting dates.
    st.update({"untyped": 0, "legacy": 0, "prose": 0, "unknown": 0,
               "dates": [], "rf_dates": []})
    # An `<id>.evidence.md` archive has NO `## Evidence` heading — it is
    # `# Evidence archive — <id>`, a preamble, then bullets. Section-scanning it finds
    # nothing and reports every counter as 0, which the declared-vs-counted self-check
    # correctly flagged on 74 files. For an archive the whole document is the section.
    sections = [body] if whole else [
        body[m.end():(ANYH2.search(body, m.end()).start()
                      if ANYH2.search(body, m.end()) else len(body))]
        for m in EVSEC.finditer(body)
    ]
    for sec in sections:
        for line in sec.split("\n"):
            e = ENTRY.match(line)
            if e:
                st["dates"].append(e.group(1))
                # Validate against OUTCOMES, never against `st`. `in st` silently
                # dropped a token that was neither `?` nor a known outcome — so a
                # mistyped `succesful_recall` (the value that DECLINES a cluster)
                # incremented nothing, appeared in no counter, and let the file report
                # `eligible`. It also ACCEPTED bookkeeping keys: `dates` hit
                # `list += 1` and crashed the run, `prose`/`legacy`/`untyped` inflated
                # the wrong bucket and gave a right verdict an invented reason.
                if e.group(2) == "?":
                    st["untyped"] += 1
                elif e.group(2) in OUTCOMES:
                    st[e.group(2)] += 1
                    if e.group(2) == "repeated_failure":
                        st["rf_dates"].append(e.group(1))
                else:
                    st["unknown"] += 1
                continue
            lg = LEGACY.match(line)
            if lg:
                st["legacy"] += 1
                st["dates"].append(lg.group(1))
                continue
            # column-0 bullet in an evidence section that is neither typed nor bold-dated:
            # a pre-schema prose entry. Indented bullets are continuation prose, not entries.
            if PROSE.match(line):
                st["prose"] += 1
    return st


def record(root, ident):
    p = pathlib.Path(root) / f"{ident}.md"
    if not p.exists():
        return {"id": ident, "missing": True}
    try:
        parsed = parse_file(p)
    except (OSError, InstinctFormatError):
        return {"id": ident, "malformed": True}
    if parsed.kind == "legacy":
        return {"id": ident, "malformed": True}
    values, body = parsed.values, parsed.body
    raw_ec = values.get("evidence_count")
    ec = raw_ec if isinstance(raw_ec, int) and raw_ec >= 0 else 0

    def text_value(name):
        value = values.get(name)
        return None if value is None else str(value)
    # The body carries only "latest 2" by format contract; the sibling archive carries the
    # COMPLETE history (`instinct-format/SKILL.md:184-187`, "The body is an excerpt; the
    # archive is the record"). The recurrence gate needs >=2
    # repeated_failure over distinct dates, so judging on the body excerpt would
    # systematically under-count and silently fail eligible files.
    archive = p.with_name(p.stem + ".evidence.md")
    st = evidence_stats(body)
    st["source"] = "body"
    st["archive_entries"] = None          # None = no archive exists
    if archive.exists():
        # The archive is the record; the body is an excerpt holding "latest 2"
        # (`instinct-format/SKILL.md:184-187`). So the archive ALWAYS wins when it
        # exists — including when it parses zero.
        #
        # Falling back to the body in that case was tried and reverted: an archive in
        # the unsupported paragraph shape parses 0, and the body excerpt could then
        # report `eligible` on two old `repeated_failure`s while the archive's newest
        # entries were `successful_recall` — the value that DECLINES the cluster. That
        # trades a loud failure for a silent wrong pass, which is the exact
        # zero-because-unparsed / zero-because-absent confusion this package exists to
        # prevent. `gate_hint` reports the empty archive by name instead.
        body_total = _total(st)
        st = evidence_stats(archive.read_text(), whole=True)
        st["source"] = "archive"
        st["archive_entries"] = _total(st)
        st["body_entries"] = body_total
    distinct = sorted(set(st.pop("dates")))
    # Condition 1 is scoped to the repeated_failure entries — see evidence_stats.
    rf_distinct = len(set(st.pop("rf_dates")))
    return {
        "id": ident,
        "domain": text_value("domain"),
        "confidence": text_value("confidence"),
        "evidence_count": ec,
        "schema_version": text_value("schema_version"),
        "created": text_value("created"),
        "updated": text_value("updated"),
        "promoted_to": text_value("promoted_to"),
        "trigger": text_value("trigger"),
        "action": text_value("action"),
        "evidence": st,
        "distinct_dates": len(distinct),
        # Advisory only — GATES.md owns the decision. Reported so the model can see
        # WHY something is ineligible instead of inferring it.
        "gate_hint": gate_hint(st, rf_distinct, ec),
    }


def _total(st):
    """Every entry the parser recognised, of any kind."""
    return (sum(st[k] for k in OUTCOMES) + st["untyped"] + st["legacy"]
            + st["prose"] + st["unknown"])


def gate_hint(st, distinct, declared=None):
    counted = _total(st)
    # An archive exists and yielded nothing. Never silently judge the body instead: the
    # body is an excerpt by contract, so its verdict can contradict the record it
    # summarises. Name the archive and stop.
    if st.get("archive_entries") == 0:
        return (f"UNPARSED: the sibling .evidence.md exists but parsed 0 entries "
                f"(body has {st.get('body_entries', 0)}) — the archive is the record, "
                f"so fix the archive or the parser. Not judged from the body")
    # Self-check FIRST. A file whose frontmatter claims entries we cannot parse is an
    # unreadable file, not an empty one — and reporting it as "0 repeated_failure" is the
    # right verdict for the wrong reason, which is how a parser bug hides as a gate result.
    if declared is not None and counted == 0 and declared > 0:
        return (f"UNPARSED: frontmatter declares {declared} entries, parsed 0 "
                f"— fix the parser, do not read this as ineligible")
    if declared is not None and counted < declared:
        return (f"ineligible: parsed {counted} of {declared} declared entries "
                f"— {declared - counted} unreadable, so eligibility is unknown, not false")
    # An out-of-vocabulary outcome is a typo or schema drift, never a pass — it blocks
    # exactly as `untyped` does. Reported in the SAME message rather than an earlier
    # return, so one typo cannot hide five untyped entries: the hint's job is to state
    # the whole reason, and a file usually has more than one kind of outstanding work.
    if st["prose"] or st["legacy"] or st["untyped"] or st["unknown"]:
        extra = f" + {st['unknown']} unknown" if st["unknown"] else ""
        return (f"ineligible: {st['prose']} prose + {st['legacy']} legacy + "
                f"{st['untyped']} untyped{extra} — unverified, not absent (#8)")
    if st["repeated_failure"] < 2:
        return f"ineligible: {st['repeated_failure']} repeated_failure, needs 2"
    if distinct < 2:
        return "ineligible: repeated failures share one date — one observation, written twice"
    return f"eligible: {st['repeated_failure']} repeated_failure over {distinct} dates"


def frontier(root):
    """Which files must be typed before the gate can judge them — as a per-domain queue.

    Regenerate this; never trust a pasted list. The first frontier (34 files) was an ad-hoc
    query built on the link-graph premise that §1D falsified, and it went stale silently.

    Under partition-then-judge the ordering signal is a file's best WITHIN-PARTITION score,
    not link degree — and the queue is per-domain, because the judge works one partition at
    a time and only that partition needs typing first.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    import shortlist as S

    recs, malformed = S.load(root)
    clusters, _ = S.build(recs, top_k=12, min_score=0.0)
    best = {c["seed"]: c["top_score"] for c in clusters}

    rows = []
    for ident in recs:
        r = record(root, ident)
        if r.get("missing") or r.get("malformed"):
            continue
        ev = r["evidence"]
        # Entries the frontmatter declares that the parser could not see. This is the ONLY
        # counter derived from the declared number rather than from parsed content, and it
        # exists because the other two filters both fail closed in the same direction:
        #
        #   - the `evidence_count < 2` shortcut below trusts a DECLARED count, and
        #   - `needs` is summed from PARSED counters, which are all zero by definition
        #     for a file nothing could be parsed from.
        #
        # So the files whose real count is precisely what nobody knows were the ones the
        # work queue never showed — blocked and invisible at once. Declared-but-unparsed is
        # outstanding work, not absence. NOT the same bug as the `unknown` omission below:
        # that was a term missing from a fold over a widened state space; this is a filter
        # trusting a number that is untrustworthy for exactly these files.
        unreadable = max(0, r["evidence_count"] - _total(ev))
        if unreadable == 0 and r["evidence_count"] < 2:
            continue                      # cannot satisfy the recurrence gate at all
        # `unknown` belongs in this sum for the same reason `untyped` does: it blocks the
        # gate, so it is outstanding work. Omitting it made a file with a mistyped outcome
        # permanently ineligible AND invisible to the queue whose whole job is to clear
        # that state.
        needs = (ev["legacy"] + ev["untyped"] + ev["prose"] + ev["unknown"]
                 + unreadable) > 0
        if not needs:
            continue
        rows.append({"id": ident, "domain": r["domain"] or "(none)",
                     "evidence_count": r["evidence_count"],
                     "legacy": ev["legacy"], "untyped": ev["untyped"], "prose": ev["prose"],
                     "unknown": ev["unknown"], "unreadable": unreadable,
                     "score": best.get(ident, 0.0)})
    rows.sort(key=lambda d: (d["domain"], -d["score"], d["id"]))
    return rows, malformed


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if "--frontier" in args:
        rest = [a for a in args if a != "--frontier"]
        root = pathlib.Path(rest[0]) if rest else DEFAULT_ROOT
        rows, malformed = frontier(root)
        import collections
        by = collections.Counter(r["domain"] for r in rows)
        entries = collections.Counter()
        for r in rows:
            entries[r["domain"]] += (r["legacy"] + r["untyped"] + r["prose"]
                                     + r["unknown"] + r["unreadable"])
        # Reported in its own column, never folded into `entries`. An unreadable entry is
        # work of a different KIND — it needs the parser or the file fixed before anyone
        # can type it — and a queue that renders it as ordinary typing work sends someone
        # to classify an entry they cannot see.
        unreadable = collections.Counter()
        for r in rows:
            unreadable[r["domain"]] += r["unreadable"]
        print(f"TYPING FRONTIER — {len(rows)} files, "
              f"{sum(entries.values())} entries, across {len(by)} partitions "
              f"({sum(unreadable.values())} of those entries are UNREADABLE)")
        print(f"{'domain':<14}{'files':>6}{'entries':>9}{'unreadable':>12}"
              f"   judge this partition first ->")
        for dom, n in sorted(by.items(), key=lambda kv: -kv[1]):
            head = ", ".join(r["id"] for r in rows if r["domain"] == dom)[:62]
            print(f"{dom:<14}{n:>6}{entries[dom]:>9}{unreadable[dom]:>12}   {head}…")
        if malformed:
            print(f"\nMALFORMED (excluded — frontier INCOMPLETE): {malformed}", file=sys.stderr)
            return 1
        return 0
    if "--ids" in args:
        i = args.index("--ids")
        rest = args[i + 1:]
        ids, root = [], DEFAULT_ROOT
        for a in rest:
            if pathlib.Path(a).is_dir():
                root = pathlib.Path(a)
            else:
                ids.append(a)
    else:
        data = json.load(sys.stdin)
        root = DEFAULT_ROOT
        ids = []
        for c in data.get("clusters", []):
            for x in [c["seed"]] + [n["id"] for n in c["neighbours"]]:
                if x not in ids:
                    ids.append(x)
    records = [record(root, i) for i in ids]
    print(json.dumps({"root": str(root), "records": records}, indent=2))
    # Same contract as shortlist.py and §1A step 6: a file that could not be read is
    # NAMED and changes the exit code. Recording `malformed: true` inside a 3,000-line
    # JSON blob and returning 0 is a silent skip wearing a field name.
    broken = [r["id"] for r in records if r.get("malformed") or r.get("missing")]
    if broken:
        print(f"\nUNREADABLE — index is INCOMPLETE ({len(broken)}):", file=sys.stderr)
        for b in broken:
            print(f"  {b}", file=sys.stderr)
        return 1
    return 0


# -------------------------------------------------------------------- selftest
def selftest():
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "eligible.md").write_text(
            "---\nschema_version: 2\nid: eligible\ndomain: harness\nevidence_count: 3\n---\n\n"
            "## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-03-04** `repeated_failure` — two\n\n"
            "## Additional evidence\n"
            "- **2026-05-06** `confirmation` — three\n")
        (root / "sameday.md").write_text(
            "---\nschema_version: 2\nid: sameday\nevidence_count: 2\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-01-02** `repeated_failure` — same day\n")
        (root / "untyped.md").write_text(
            "---\nschema_version: 2\nid: untyped\nevidence_count: 2\n---\n\n## Evidence\n"
            "- **2026-01-02** `?` — not classified\n"
            "- **2026-03-04** `repeated_failure` — one\n")
        (root / "legacy.md").write_text(
            "---\nid: legacy\nevidence_count: 2\n---\n\n## Evidence\n"
            "- **2026-01-02** — never migrated\n")

        r = record(root, "eligible")
        check("counts entries across BOTH evidence sections",
              r["evidence"]["repeated_failure"] == 2 and r["evidence"]["confirmation"] == 1)
        check("eligible cluster reported eligible", r["gate_hint"].startswith("eligible"))
        check("distinct dates counted", r["distinct_dates"] == 3)

        r2 = record(root, "sameday")
        check("two repeated_failures on ONE date are ineligible",
              r2["gate_hint"].startswith("ineligible") and "one observation" in r2["gate_hint"])

        r3 = record(root, "untyped")
        check("untyped entry blocks eligibility", r3["gate_hint"].startswith("ineligible"))
        check("untyped reported separately from zero", r3["evidence"]["untyped"] == 1)

        r4 = record(root, "legacy")
        check("legacy un-migrated entry counted as legacy, not typed",
              r4["evidence"]["legacy"] == 1 and r4["evidence"]["repeated_failure"] == 0)
        check("legacy file ineligible", r4["gate_hint"].startswith("ineligible"))

        # Recurrence-gate condition 1 is about the dates of the REPEATED_FAILURE entries,
        # not about how many dates the file mentions. Two same-day recurrences plus any
        # other entries on other dates must NOT qualify: that is one observation written
        # twice, exactly what `sameday.md` above covers — but reachable only when the
        # extra dates come from entries that do not count. `first_observation` made this
        # common: a merged doc supplies plenty of non-counting dates.
        (root / "spreaddates.md").write_text(
            "---\nschema_version: 2\nid: spreaddates\nevidence_count: 4\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-01-02** `repeated_failure` — SAME DAY as the one above\n"
            "- **2026-03-04** `first_observation` — different date, does not count\n"
            "- **2026-05-06** `confirmation` — different date, does not count\n")
        rs = record(root, "spreaddates")
        check("distinct dates counted over repeated_failure ONLY, not all entries",
              rs["gate_hint"] == "ineligible: repeated failures share one date "
                                 "— one observation, written twice")

        # And the converse: two repeated_failures on genuinely distinct dates still
        # qualify when other entries share those dates.
        (root / "spreadok.md").write_text(
            "---\nschema_version: 2\nid: spreadok\nevidence_count: 3\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-03-04** `repeated_failure` — genuinely later\n"
            "- **2026-05-06** `first_observation` — does not count\n")
        check("two repeated_failures on distinct dates still qualify",
              record(root, "spreadok")["gate_hint"] ==
              "eligible: 2 repeated_failure over 2 dates")

        # A lesson observed exactly once. It must be TYPED (not `untyped`, which means
        # "we could not tell") and still INELIGIBLE (one observation is not a recurrence).
        # Before `first_observation` existed there was no way to say both at once.
        (root / "firstobs.md").write_text(
            "---\nschema_version: 2\nid: firstobs\nevidence_count: 1\n---\n\n## Evidence\n"
            "- **2026-01-02** `first_observation` — the event that created the lesson\n")
        r5 = record(root, "firstobs")
        check("first_observation is a recognised typed outcome",
              r5["evidence"]["first_observation"] == 1)
        check("first_observation is NOT counted as untyped",
              r5["evidence"]["untyped"] == 0 and r5["evidence"]["legacy"] == 0
              and r5["evidence"]["prose"] == 0)
        check("first_observation does NOT satisfy the recurrence gate",
              r5["gate_hint"] == "ineligible: 0 repeated_failure, needs 2")
        check("first_observation does NOT trip the declared-vs-counted self-check",
              "UNPARSED" not in r5["gate_hint"])

        # A merged topic doc: 3 sources, each contributing its own first observation.
        # Must NOT read as a recurrence — that is the manufacturing failure gate
        # condition 2 guards against, arriving by a different route.
        (root / "merged.md").write_text(
            "---\nschema_version: 2\nid: merged\nevidence_count: 3\n---\n\n## Evidence\n"
            "- **2026-01-02** `first_observation` — from source-a\n"
            "- **2026-03-04** `first_observation` — from source-b\n"
            "- **2026-05-06** `first_observation` — from source-c\n")
        r6 = record(root, "merged")
        check("3 merged first observations over 3 dates are still ineligible",
              r6["distinct_dates"] == 3 and r6["gate_hint"].startswith("ineligible"))

        # An outcome token that is not in OUTCOMES used to be dropped silently: the
        # membership test was `in st`, so a typo incremented nothing and appeared in no
        # counter. A mistyped `successful_recall` — the value GATES.md says DECLINES a
        # cluster — became invisible and the file reported `eligible`.
        (root / "typo.md").write_text(
            "---\nschema_version: 2\nid: typo\nevidence_count: 3\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-03-04** `repeated_failure` — two\n"
            "- **2026-05-06** `succesful_recall` — TYPO: counter-evidence, one 's'\n")
        rt = record(root, "typo")
        check("unrecognised outcome is counted, not dropped",
              rt["evidence"]["unknown"] == 1)
        check("unrecognised outcome BLOCKS the gate, naming itself",
              rt["gate_hint"].startswith("ineligible") and "1 unknown" in rt["gate_hint"])

        # `in st` also ACCEPTED bookkeeping keys. `dates` was a list -> TypeError, which
        # killed --ids and --frontier mid-run; `prose`/`legacy`/`untyped` inflated the
        # wrong counter and produced a right verdict with an invented reason.
        (root / "collide.md").write_text(
            "---\nschema_version: 2\nid: collide\nevidence_count: 1\n---\n\n## Evidence\n"
            "- **2026-01-02** `dates` — collides with a bookkeeping key\n")
        rc = record(root, "collide")
        check("bookkeeping-key collision does not crash", rc.get("id") == "collide")
        check("bookkeeping-key collision counted as unknown",
              rc["evidence"]["unknown"] == 1 and rc["evidence"]["prose"] == 0)

        (root / "collide2.md").write_text(
            "---\nschema_version: 2\nid: collide2\nevidence_count: 2\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-03-04** `prose` — collides with the prose counter\n")
        check("a typed entry is never reported as an un-migrated prose bullet",
              record(root, "collide2")["evidence"]["prose"] == 0)

        # A stub archive must not shadow a body that carries the real evidence, and must
        # not blame the parser for it.
        (root / "stub.md").write_text(
            "---\nschema_version: 2\nid: stub\nevidence_count: 2\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — one\n"
            "- **2026-03-04** `repeated_failure` — two\n")
        (root / "stub.evidence.md").write_text("# Evidence archive — stub\n\nTBD.\n")
        rst = record(root, "stub")
        # Falling back to the body here was tried and reverted — see record(). The body
        # is an excerpt, so its verdict can contradict the record it summarises; an
        # archive that parsed nothing must fail LOUD, never resolve to `eligible`.
        check("an archive parsing zero entries is reported, not silently replaced",
              rst["gate_hint"].startswith("UNPARSED")
              and ".evidence.md" in rst["gate_hint"])
        # NOT `"eligible: " not in hint` — "ineligible: " CONTAINS "eligible: ", so that
        # form passes on every ineligible verdict and would read green for the wrong
        # reason under a body fallback reporting "ineligible: parsed 2 of 4".
        check("a zero-parsing archive never yields an eligible verdict",
              not rst["gate_hint"].startswith("eligible"))
        check("the body's entry count is surfaced for the fix",
              rst["evidence"]["body_entries"] == 2)

        # The dangerous shape, from review: an archive in the unsupported PARAGRAPH form
        # parses 0, while the body excerpt holds two old repeated_failures. Judging the
        # body would report `eligible` even though the archive's newest entries are
        # `successful_recall` — the value that DECLINES a cluster.
        (root / "para.md").write_text(
            "---\nschema_version: 2\nid: para\n---\n\n## Evidence\n"
            "- **2026-01-02** `repeated_failure` — old excerpt\n"
            "- **2026-03-04** `repeated_failure` — old excerpt\n")
        (root / "para.evidence.md").write_text(
            "# Evidence archive — para\n\n"
            "2026-07-01 `successful_recall` consulted and it worked — declines\n")
        check("a stale body excerpt cannot outvote an unparseable archive",
              record(root, "para")["gate_hint"].startswith("UNPARSED"))

        # ------------------------------------------------------------ frontier() itself
        # `frontier()` had NO selftest coverage at all while being edited — neither the
        # `unreadable` term nor the older `unknown` one had a fixture. Both exist because
        # the queue's other two filters fail closed in the SAME direction, so a file whose
        # count nobody can verify was blocked and invisible at once.
        (root / "unreadable.md").write_text(
            "---\nschema_version: 2\nid: unreadable\ndomain: harness\n"
            "evidence_count: 4\n---\n\n## Evidence\n\n"
            "A bare paragraph the parser cannot see, so this file parses ZERO.\n")
        (root / "singleton.md").write_text(
            "---\nschema_version: 2\nid: singleton\ndomain: harness\n"
            "evidence_count: 1\n---\n\n## Evidence\n"
            "- **2026-01-02** `first_observation` — the only one, fully readable\n")
        # The two fixtures that ISOLATE the `evidence_count < 2` shortcut. Without them a
        # neuter of that line stays green for an unrelated reason: `unreadable.md`
        # declares 4 so the `< 2` test never fires on it, and `singleton.md` is also
        # filtered by `needs == 0`, so removing the shortcut changes nothing either. Each
        # of these can only be decided by the shortcut itself.
        (root / "lonework.md").write_text(          # 1 declared, work to do, but < 2
            "---\nschema_version: 2\nid: lonework\ndomain: harness\n"
            "evidence_count: 1\n---\n\n## Evidence\n"
            "- **2026-01-02** `?` — untyped, so `needs` is TRUE for this file\n")
        (root / "lonehidden.md").write_text(        # 1 declared, and it is unreadable
            "---\nschema_version: 2\nid: lonehidden\ndomain: harness\n"
            "evidence_count: 1\n---\n\n## Evidence\n\n"
            "A bare paragraph, so this file parses ZERO of its 1 declared entry.\n")
        rows, _ = frontier(root)
        by_id = {r["id"]: r for r in rows}

        check("FRONTIER: a file declaring N with 0 parsed appears, with unreadable == N",
              "unreadable" in by_id and by_id["unreadable"]["unreadable"] == 4)
        check("FRONTIER: its unreadable count is reported apart from typing work",
              by_id["unreadable"]["legacy"] == 0 and by_id["unreadable"]["untyped"] == 0
              and by_id["unreadable"]["prose"] == 0)
        # The `evidence_count < 2` shortcut trusts a DECLARED number. It may only skip a
        # file whose entries are all readable — otherwise it skips exactly the files whose
        # real count is unknown.
        check("FRONTIER: unreadable == 0 and evidence_count < 2 still skips",
              "singleton" not in by_id and "lonework" not in by_id)
        check("FRONTIER: a declared-but-unparsed file is NOT skipped by that shortcut",
              "unreadable" in by_id and "lonehidden" in by_id
              and by_id["lonehidden"]["unreadable"] == 1)
        # `needs` is summed from PARSED counters, every one of which is 0 for a file
        # nothing could be parsed from. Without `unreadable` in the sum this row vanishes.
        check("FRONTIER: the `needs` sum includes unreadable, so the row survives it",
              by_id["unreadable"]["evidence_count"] == 4)
        check("FRONTIER: `unknown` is in the queue too — a mistyped outcome is work",
              "typo" in by_id and by_id["typo"]["unknown"] == 1)
        check("FRONTIER: a fully typed, gate-ready file stays OUT of the queue",
              "eligible" not in by_id and "spreadok" not in by_id)

        check("missing file reported, not crashed", record(root, "nope")["missing"])
        (root / "bad.md").write_text("no frontmatter\n")
        check("malformed frontmatter reported, not crashed", record(root, "bad")["malformed"])

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

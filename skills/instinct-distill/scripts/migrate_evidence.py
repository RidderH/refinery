#!/usr/bin/env python3
"""Migrate instinct evidence entries to the typed schema (schema_version: 2).

MECHANICAL HALF ONLY. This script never decides an `outcome` — it normalises the two
evidence formats onto one shape, recovers `observed_at` where it is literally present,
and marks every entry `?` (untyped). Classification is a model/human judgment made
afterwards, one file at a time, reading the file in full. See ../reference/GATES.md.

Entry shape after migration:

    - **2026-07-19** `repeated_failure` (Acme_App) — prose
    - **2026-07-19** `?` — prose                      <- untyped, ineligible for the gate

Why inline and not frontmatter: the dates already live in the body. Copying them into
frontmatter would create a second source of truth that rots, which is the failure
`instinct-format` exists to prevent.

Usage:
    python3 migrate_evidence.py <file.md> [<file.md> ...]   # migrate named files
    python3 migrate_evidence.py --report                    # corpus-wide, writes nothing
    python3 migrate_evidence.py --report <file.md> ...      # per-file dry run, writes nothing
    python3 migrate_evidence.py --selftest                  # prove it goes red

Four entry shapes are handled: typed, spec-format, legacy prose bullet, and a bare
column-0 PARAGRAPH. A paragraph's date comes from the paragraph itself, or — only when it
has none — from the single ISO date of its enclosing `## ` heading, which is reported
separately as `heading-dated` because that date was not previously stated in the file.
A paragraph with neither is left untouched, never dated by guess.

Idempotent: re-running on a migrated file is a byte-for-byte no-op.
"""
import contextlib
import datetime
import io
import pathlib
import re
import sys
import tempfile

FORMAT_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "instinct-format" / "scripts"
sys.path.insert(0, str(FORMAT_SCRIPTS))
from instinct_record import InstinctFormatError, parse_text  # noqa: E402

DEFAULT_ROOT = pathlib.Path.home() / ".claude/homunculus/instincts/personal"
UNTYPED = "`?`"
OUTCOMES = ("repeated_failure", "successful_recall", "confirmation", "correction",
            "first_observation")   # see build_index.py / GATES.md — user ruling 2026-07-29

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# Any heading naming Evidence, not just the canonical one. `retry-backoff-must-be-jittered`
# — the highest-degree node in the whole link graph — keeps its dated entries under
# `## Additional evidence`, so an exact-match on `## Evidence` normalises zero of them
# while reporting success.
EVSEC = re.compile(r"^##\s+[A-Za-z ]*Evidence\b.*?$", re.M | re.I)
ANYH2 = re.compile(r"^##\s", re.M)
# already migrated: - **DATE** `outcome-or-?` ...
DONE = re.compile(r"^[-*]\s+\*\*(\d{4}-\d{2}-\d{2})\*\*\s+`([a-z_?]+)`")
# spec format, not yet typed: - **DATE** (Proj) — prose   /  - **DATE** — prose
SPEC = re.compile(r"^([-*]\s+)\*\*(\d{4}-\d{2}-\d{2})\*\*(\s*)")
# An ENTRY is a column-0 bullet. An indented bullet is continuation prose belonging to
# the entry above it — counting those as undated entries overstates the manual work and
# blocks schema_version on files that are actually fine.
BULLET = re.compile(r"^[-*]\s+\S")
# The 4th entry shape: a bare column-0 PARAGRAPH inside an evidence section. Fences are
# tracked so a dated line inside a ``` block is never mistaken for one.
FENCE = re.compile(r"^\s*(?:```|~~~)")
H2 = re.compile(r"^##\s+(.*)$")
# A paragraph entry never opens with one of these. `*`/`-` are bullets (handled above) or
# a bold/italic lead like `**Pattern:**`, which is structural prose inside an entry's
# section, not an entry of its own.
NOTPARA = ("-", "*", "#", ">", "`")
# A `---` thematic break. `verify-docs-before-assuming-api-shape.evidence.md` delimits its
# entries with these and says so in its own preamble; blank lines inside one of its blocks
# are paragraph breaks WITHIN an entry, not entry boundaries.
HR = re.compile(r"^-{3,}\s*$")
# How far into a paragraph a date may sit and still count as "this paragraph LEADS with a
# date". `Acme_App session 2026-05-28: …` puts it at index 17 (0-based); a date buried in the
# middle of a sentence is a reference to a date, not the entry's own timestamp.
DATE_LEAD = 60
ISO = re.compile(r"(\d{4}-\d{2}-\d{2})")
MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
LOOSE = re.compile(rf"\b({MONTHS})[a-z]*\s+(\d{{1,2}}),?\s+(\d{{4}})\b")


class Malformed(Exception):
    """Frontmatter could not be parsed. Never swallowed — see step 6 of §1A."""


def valid_date(s):
    try:
        datetime.date.fromisoformat(s)
        return True
    except ValueError:
        return False


def find_date(text):
    """Return an ISO date literally present in the bullet, or None. Never invents one."""
    for m in ISO.finditer(text):
        if valid_date(m.group(1)):
            return m.group(1)
    m = LOOSE.search(text)
    if m:
        mon = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split().index(m.group(1)) + 1
        cand = f"{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}"
        if valid_date(cand):
            return cand
    return None


def heading_date(heading):
    """The one ISO date an enclosing heading names, or None. Never a guess.

    None when the heading names NO date and — deliberately — when it names MORE THAN ONE.
    Rule (2) below promotes an implicit date into an explicit one that a human then
    reviews in a diff; an ambiguous container has no single date to promote, so it is
    left alone rather than resolved by picking the first match.
    """
    if not heading:
        return None
    found = {m.group(1) for m in ISO.finditer(heading) if valid_date(m.group(1))}
    return found.pop() if len(found) == 1 else None


def evidence_spans(body):
    """All (start, end, heading) evidence-section bodies, in order. Empty list if none.

    A file may carry more than one — `## Evidence` plus `## Additional evidence`. The
    heading text rides along because it is the only place a paragraph entry's date can
    come from when the paragraph itself carries none (rule 2 in `migrate_section`).
    """
    spans = []
    for m in EVSEC.finditer(body):
        nxt = ANYH2.search(body, m.end())
        spans.append((m.end(), nxt.start() if nxt else len(body), m.group(0)))
    return spans


def block_end(lines, i):
    """End index of the column-0 block starting at `lines[i]`.

    A block runs to the next blank line, fence, heading, thematic break or column-0
    bullet. Indented lines belong to it — they are continuation prose.
    """
    j = i + 1
    while j < len(lines):
        nxt = lines[j]
        if (not nxt.strip() or FENCE.match(nxt) or nxt.startswith("#")
                or HR.match(nxt) or BULLET.match(nxt)):
            break
        j += 1
    return j


def blocks_of(lines):
    """Split lines into (kind, start, end) blocks. Kinds: blank, fence, bullet, other."""
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if not line.strip():
            out.append(("blank", i, i + 1))
            i += 1
        elif FENCE.match(line):
            j = i + 1
            while j < n and not FENCE.match(lines[j]):
                j += 1
            out.append(("fence", i, min(j + 1, n)))
            i = min(j + 1, n)
        elif line[:1] in " \t":
            # An indented line with no block above it (a section can open mid-list).
            out.append(("other", i, block_end(lines, i)))
            i = out[-1][2]
        elif BULLET.match(line) or DONE.match(line) or SPEC.match(line):
            out.append(("bullet", i, block_end(lines, i)))
            i = out[-1][2]
        else:
            out.append(("other", i, block_end(lines, i)))
            i = out[-1][2]
    return out


def bullet_date(lines):
    """(kind, date) for a column-0 bullet block. kind is 'typed', 'spec', 'loose' or None.

    None means the bullet carries no date of its own — which makes it CONTINUATION prose
    of the entry it sits under, not an entry. `stacked-pr-…evidence.md`'s recovery steps
    (`- git rebase origin/main …`) are four such bullets under one dated heading; counting
    them as entries turned one observation into five.
    """
    head = lines[0]
    if DONE.match(head):
        return "typed", DONE.match(head).group(1)
    m = SPEC.match(head)
    if m:
        return ("spec", m.group(2)) if valid_date(m.group(2)) else (None, None)
    d = find_date(head)
    return ("loose", d) if d else (None, None)


def joined(lines):
    return " ".join(l.strip() for l in lines if l.strip())


def indent(lines, st=None, allow_bullets=False):
    """Indent continuation PROSE by two spaces. Never a column-0 bullet.

    `build_index.PROSE` matches column 0 ONLY, so indenting a line is not cosmetic — it
    DELETES that line as a parsed entry. For a paragraph that costs nothing (a column-0
    paragraph was never an entry to the index). For a bullet it is destruction: measured
    at 72 files and 146 entries when this function indented everything, and it is exactly
    the fold that `SKILL.md § What counts as one evidence entry` forbids a human from
    doing by hand. An undated bullet stays at column 0, where the index still counts it as
    unclassified `prose` — visible as outstanding work rather than silently gone.

    `allow_bullets` is the ONE unambiguous case: a list the entry's own first paragraph
    introduces with a trailing colon ("Ran three ctx7 lookups in parallel:"). There the
    bullets are the sentence's object, not separate incidents. Every other undated bullet
    stays at column 0. `st` is threaded through so a demotion can never happen uncounted.
    """
    out, demoted = [], 0
    for l in lines:
        if not l.strip():
            out.append(l)
        elif BULLET.match(l):
            if allow_bullets:
                out.append("  " + l)
                demoted += 1
            else:
                out.append(l)              # a column-0 entry that must survive
        else:
            out.append("  " + l)
    if st is not None:
        st["demoted"] += demoted
    return out


def opens_entry(text):
    """Does this paragraph open a new entry — i.e. does it lead with its own date?

    Only consulted when NO coarser delimiter governs the segment. A dated heading or a
    `---` rule already declares where an entry begins; blank lines do not. A paragraph
    that does not lead with a date is continuation of the entry above it.
    """
    d = find_date(text[:DATE_LEAD])
    return d is not None


def segments_of(lines, heading):
    """Split a document (or one evidence section) into segments by its GOVERNING structure.

    Detected in order, coarsest first — never segment on blank lines when a coarser
    delimiter is present:

      1. dated `## ` headings   (`green-guards-…`, `stacked-pr-…`) — one entry per heading
      2. `---` thematic breaks  (`verify-docs-before-assuming-api-shape`) — one entry per block
      3. neither                — a paragraph is an entry only if it LEADS with a date

    Text before the first delimiter is the document PREAMBLE and is never an entry.
    """
    heads = [i for i, l in enumerate(lines) if l.startswith("## ")]
    if heads:
        segs = [{"lo": 0, "hi": heads[0], "heading": heading, "governed": False,
                 "preamble": True}]
        for k, h in enumerate(heads):
            hi = heads[k + 1] if k + 1 < len(heads) else len(lines)
            segs.append({"lo": h + 1, "hi": hi, "heading": lines[h],
                         "governed": heading_date(lines[h]) is not None,
                         "preamble": False, "head_line": h})
        return segs
    rules = [i for i, l in enumerate(lines) if HR.match(l)]
    if rules:
        segs = [{"lo": 0, "hi": rules[0], "heading": heading, "governed": False,
                 "preamble": True}]
        for k, r in enumerate(rules):
            hi = rules[k + 1] if k + 1 < len(rules) else len(lines)
            segs.append({"lo": r + 1, "hi": hi, "heading": heading, "governed": True,
                         "preamble": False, "rule_line": r})
        return segs
    return [{"lo": 0, "hi": len(lines), "heading": heading,
             "governed": heading_date(heading) is not None, "preamble": False}]


def emit_body(body, st, hdate, out):
    """Emit one accumulated body entry: first block as the bullet, the rest indented."""
    if not body:
        return
    text = joined(body[0])
    rest = [l for blk in body[1:] for l in blk]
    tail = []
    while rest and not rest[-1].strip():
        tail.insert(0, rest.pop())      # trailing blanks stay unindented
    d, via_heading = find_date(text), False
    if d is None and hdate is not None:
        d, via_heading = hdate, True
    if d is None:
        st["undatable"] += 1
        for blk in body:
            out.extend(blk)
        return
    out.append(f"- **{d}** {UNTYPED} — {text}")
    # A colon ending the entry's first paragraph is the only evidence in the file that the
    # bullets below it are that sentence's list rather than undated incidents of their own.
    out.extend(indent(rest, st, allow_bullets=text.rstrip().endswith(":")))
    out.extend(tail)
    st["normalised"] += 1
    if via_heading:
        st["heading_dated"] += 1


def migrate_section(sec, st, heading=None):
    """Rewrite one evidence section (or a whole archive), accumulating stats into `st`.

    The unit of evidence is NOT the paragraph. It is whatever the document's own
    structure says it is — the body under one dated `## ` heading, one `---`-delimited
    block, one dated column-0 bullet, or (only when none of those govern) one paragraph
    that leads with its own date. Segmenting on blank lines instead turned a four-
    paragraph entry into four entries and a `Merged from:` provenance line into a fifth.

    A date is resolved as: (1) inside the entry text; (2) failing that ONLY, the single
    ISO date of the enclosing `## ` heading, counted separately as `heading_dated`;
    (3) failing both, the text is left COMPLETELY untouched and counted `undatable`.
    """
    lines = sec.split("\n")
    out = []
    for seg in segments_of(lines, heading):
        if "head_line" in seg:
            out.append(lines[seg["head_line"]])
        if "rule_line" in seg:
            out.append(lines[seg["rule_line"]])
        seg_lines = lines[seg["lo"]:seg["hi"]]
        if seg["preamble"]:
            # A preamble is not evidence. `green-guards-…` opens with a `Scope note:` that
            # names a date; dating it produced an entry describing a FILE SPLIT.
            out.extend(seg_lines)
            st["preamble_skipped"] += 1 if joined(seg_lines) else 0
            continue
        hdate = heading_date(seg["heading"])
        blks = blocks_of(seg_lines)
        # TIER 3 — dated column-0 bullets govern. When a segment already lists its
        # evidence as dated bullets, a stray column-0 paragraph is a note ABOUT the list
        # (an age note flagging that an earlier entry has not been re-verified against
        # current behaviour), not another observation. Dating it added a sixth entry to a
        # five-entry archive.
        bullets_govern = not seg["governed"] and any(
            k == "bullet" and bullet_date(seg_lines[lo:hi])[0] for k, lo, hi in blks)
        body, started, done_body, after_entry = [], False, False, False
        for kind, lo, hi in blks:
            blk = seg_lines[lo:hi]
            if kind == "blank":
                if body:
                    body.append(blk)        # a blank INSIDE an entry, kept as a block
                else:
                    out.append(blk[0])
                continue
            if kind == "bullet":
                bk, bd = bullet_date(blk)
                if bk is not None:
                    emit_body(body, st, hdate, out)
                    body, done_body, after_entry = [], True, True
                    if bk == "typed":
                        st["typed"] += 1
                        out.extend(blk)
                    elif bk == "spec":
                        out.append(SPEC.sub(rf"\g<1>**\g<2>** {UNTYPED}\g<3>",
                                            blk[0], count=1))
                        out.extend(blk[1:])
                        st["normalised"] += 1
                    else:
                        out.append(f"- **{bd}** {UNTYPED} — "
                                   f"{re.sub(r'^[-*][ \t]+', '', blk[0])}")
                        out.extend(blk[1:])
                        st["normalised"] += 1
                    continue
                # undated bullet: continuation prose of the entry it sits under
                if body or (seg["governed"] and not done_body):
                    body.append(blk)
                elif after_entry:
                    # Trails an entry. AMBIGUOUS: it may be that entry's continuation, or
                    # a separate incident nobody dated. Left at column 0 — the index keeps
                    # counting it as unclassified `prose`, so it stays visible as work.
                    # Indenting it here is what destroyed 146 entries across 72 files.
                    st["undatable"] += 1
                    out.extend(blk)
                else:
                    # Nothing above it to continue: it is a bullet entry we could not
                    # date. Left untouched and counted — never silently dropped.
                    st["undatable"] += 1
                    out.extend(blk)
                continue
            if kind == "fence" or (kind == "other" and blk[0][:1] in NOTPARA):
                # Bold leads, blockquotes, fenced code: never an entry on their OWN
                # evidence, but they are part of an entry whose boundary something else
                # already declared. A `---`-delimited archive's third block can open
                # with a bold lead naming a sub-pattern and a parenthetical date —
                # skipping it because of the leading `**` dropped a whole entry the
                # delimiter had already marked out.
                if body or (seg["governed"] and not done_body):
                    body.append(blk)
                else:
                    out.extend(blk)
                continue
            # a column-0 paragraph
            if bullets_govern:
                st["undatable"] += 1        # a note beside a bulleted list, not an entry
                out.extend(blk)
            elif body and (seg["governed"] or not opens_entry(joined(blk))):
                body.append(blk)            # continuation of the entry above
            elif done_body and seg["governed"]:
                out.extend(blk)             # governed segment already produced its entry
            elif seg["governed"] or opens_entry(joined(blk)) or started:
                emit_body(body, st, hdate, out)
                body, started = [blk], True
            else:
                # A column-0 paragraph inside an evidence section that leads with no date
                # and has no entry above it to continue. Undecidable, so: untouched, and
                # counted, so the lint gate can see it. (A document PREAMBLE is different
                # — it is decidably not evidence, and was skipped above.)
                st["undatable"] += 1
                out.extend(blk)
        emit_body(body, st, hdate, out)
    return "\n".join(out)


def migrate_text(text, name="<mem>"):
    """Return (new_text, stats). Pure — no I/O."""
    st = {"typed": 0, "normalised": 0, "undatable": 0, "heading_dated": 0,
          "preamble_skipped": 0, "demoted": 0, "no_section": False}

    # An `<id>.evidence.md` archive carries NO frontmatter and NO `## Evidence` heading —
    # it is `# Evidence archive — <id>`, a preamble, then the bullets. For it the whole
    # document is the section, exactly as `build_index.evidence_stats(..., whole=True)`
    # already treats it.
    #
    # This exemption is load-bearing, not a convenience. `build_index.record()` reads the
    # archive EXCLUSIVELY whenever one exists (the body carries only "latest 2" by format
    # contract), so while this script rejected archives the gate was judging 384 entries
    # across 64 frontier files that could never be typed — typing their bodies changed
    # nothing the gate read, and no amount of work could make them eligible.
    #
    # Keyed to the FILENAME, never to "frontmatter happens to be absent": a lesson file
    # missing its frontmatter is still a hard error. Consistent with build_index, a
    # column-0 bullet in an archive preamble is treated as an entry.
    if name.endswith(".evidence.md"):
        return migrate_section(text, st), st

    try:
        parsed = parse_text(text)
    except InstinctFormatError as exc:
        raise Malformed(f"{name}: {exc}")
    if parsed.kind == "legacy":
        raise Malformed(f"{name}: no parseable YAML frontmatter")

    # Byte offsets still come from this narrow boundary regex because migration must
    # preserve the original frontmatter bytes. Its meaning has already been validated
    # by the canonical parser above; this is slicing, not a second interpretation.
    fm = FM.match(text)
    if not fm:
        raise Malformed(f"{name}: no parseable YAML frontmatter")
    if re.search(r"^\s+\S", fm.group(1), re.M) and not re.search(
        r"^[A-Za-z_][\w]*:", fm.group(1), re.M
    ):
        raise Malformed(f"{name}: frontmatter has no top-level keys")

    body = text[fm.end():]
    spans = evidence_spans(body)
    if not spans:
        st["no_section"] = True
        return text, st

    # rebuild back-to-front so earlier offsets stay valid
    newbody = body
    for start, end, head in reversed(spans):
        newbody = (newbody[:start] + migrate_section(newbody[start:end], st, head)
                   + newbody[end:])
    newfm = fm.group(1)
    if not re.search(r"^schema_version:", newfm, re.M):
        # only claim v2 once every bullet in the section is date-bearing
        if st["undatable"] == 0:
            newfm = "schema_version: 2\n" + newfm
    return f"---\n{newfm}\n---\n{newbody}", st


def _parsed(text, name):
    """What `build_index` counts in this text. Imported lazily — `run()` is the only
    caller, and the codemod must stay importable without pulling the index in."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import build_index as B
    if name.endswith(".evidence.md"):
        return B._total(B.evidence_stats(text, whole=True))
    m = FM.match(text)
    return B._total(B.evidence_stats(text[m.end():])) if m else 0


def run(paths, write=True, per_file=False):
    tot = {"typed": 0, "normalised": 0, "undatable": 0, "heading_dated": 0,
           "demoted": 0, "files": 0, "no_section": 0}
    errs, refused = [], []
    for p in paths:
        try:
            old = p.read_text()
            new, st = migrate_text(old, p.name)
        except Malformed as e:
            errs.append(str(e))
            continue
        tot["files"] += 1
        for k in ("typed", "normalised", "undatable", "heading_dated", "demoted"):
            tot[k] += st[k]
        tot["no_section"] += 1 if st["no_section"] else 0
        if per_file:
            # `dated-in-place` vs `from-heading` is the review split: rule (2) writes a
            # date the file never stated, so those entries are the ones a human must
            # check. Every other rewrite reuses a date the entry already carried.
            print(f"{p.name}: rewrites={st['normalised']} "
                  f"(dated-in-place={st['normalised'] - st['heading_dated']}, "
                  f"from-heading={st['heading_dated']})  undatable={st['undatable']}"
                  f"  demoted={st['demoted']}  already-typed={st['typed']}"
                  f"{'  [no Evidence section]' if st['no_section'] else ''}"
                  f"{'' if new != old else '  [no change]'}")
        # REFUSE TO WRITE rather than fold. Indenting a column-0 bullet deletes it as a
        # parsed entry, and `SKILL.md § What counts as one evidence entry` forbids
        # resolving a shortfall by folding entries to fit a number. A codemod must not do
        # silently what a human is told never to do — so a migration that would leave the
        # file with FEWER READABLE ENTRIES THAN IT HAS TODAY stops and names the file.
        #
        # LOSS is the ONLY refusal condition, and deliberately so. A second branch once
        # refused when `after < declared` too, which sounds stricter and was pure false
        # positive: measured on the live corpus it fired on 7 files, EVERY ONE of them
        # `before == after` — nothing destroyed, merely frontmatter that was already stale
        # before the codemod ran. It blocked a strictly-improving migration on files the
        # codemod cannot help, left them in the unparseable legacy shape, and made a
        # corpus-wide run unable to ever exit 0 — so the documented ship sequence
        # (migrate → fix counts → make it blocking) had no reachable first step. Five of
        # the seven were files this very script had already migrated: it refused to
        # reproduce the state it produced. A stale declared count is real, but it is the
        # lint's `STALE COUNT` to report and a human's to fix, not the codemod's to block.
        #
        # Do NOT re-add it as `after < min(before, dec)` either: that reads as equivalent
        # but TOLERATES destroying entries as long as the file still meets its declared
        # number — and a file may legitimately hold more entries than it declares, so that
        # is the forbidden fold wearing a stricter-looking predicate. Any loss is refused.
        # (Both `before >= dec > after` and `after < min(before, dec)` also imply
        # `after < before`, so as an `elif` the branch is simply unreachable — a fixture
        # written for it would pass because the branch is dead, not because it works.)
        if new != old:
            before, after = _parsed(old, p.name), _parsed(new, p.name)
            if after < before:
                refused.append(
                    f"{p.name}: LOSS: would drop readable entries {before} -> {after} "
                    f"({st['demoted']} bullet(s) demoted to continuation)")
                continue
        if write and new != old:
            p.write_text(new)
    print(f"files={tot['files']}  already-typed={tot['typed']}  normalised={tot['normalised']}"
          f"  (of those, heading-dated={tot['heading_dated']} — REVIEW THESE)"
          f"  undatable={tot['undatable']}  demoted={tot['demoted']}"
          f"  no-Evidence-section={tot['no_section']}")
    if refused:
        # Loud, and it changes the exit code: a refusal that only prints is a file the
        # next run will silently mangle.
        print(f"\nREFUSED — migrating these would DESTROY readable evidence "
              f"({len(refused)}):", file=sys.stderr)
        for r in refused:
            print(f"  {r}", file=sys.stderr)
    if errs:
        # Hard error, never a silent skip: a skipped file is indistinguishable from
        # one that legitimately failed the gate.
        print("\nMALFORMED — not migrated:", file=sys.stderr)
        for e in errs:
            print(f"  {e}", file=sys.stderr)
        return 1
    return 1 if refused else 0


def selftest():
    """Prove each branch goes red on its own bad input, then green on good."""
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    good = ("---\nid: x\nevidence_count: 2\n---\n\n## Evidence (n=2, latest 2)\n"
            "- **2026-07-19** — did a thing\n- Session 2026-07-27 (Acme_App): did another\n")
    new, st = migrate_text(good)
    check("spec + legacy bullets both normalised", st["normalised"] == 2)
    check("untyped marker present twice", new.count(UNTYPED) == 2)
    check("schema_version: 2 stamped", "schema_version: 2" in new)
    check("legacy date recovered inline", "**2026-07-27**" in new)
    check("outcome never invented", not any(o in new for o in OUTCOMES))

    again, st2 = migrate_text(new)
    check("IDEMPOTENT — second pass byte-identical", again == new)
    check("second pass sees them as typed", st2["typed"] == 2 and st2["normalised"] == 0)

    bad_date = "---\nid: x\n---\n\n## Evidence\n- **2026-13-45** — impossible date\n"
    n3, st3 = migrate_text(bad_date)
    check("malformed date NOT accepted", st3["undatable"] == 1 and st3["normalised"] == 0)
    check("malformed date blocks schema_version", "schema_version" not in n3)

    nodate = "---\nid: x\n---\n\n## Evidence\n- no date anywhere in this bullet\n"
    n4, st4 = migrate_text(nodate)
    check("undated bullet left untouched", "- no date anywhere" in n4 and st4["undatable"] == 1)

    nosec = "---\nid: x\n---\n\n# Title\n\nBody with no evidence heading.\n"
    n5, st5 = migrate_text(nosec)
    check("missing ## Evidence -> flagged, file untouched", st5["no_section"] and n5 == nosec)

    try:
        migrate_text("no frontmatter at all\n", "probe.md")
        check("malformed frontmatter raises", False)
    except Malformed as e:
        check("malformed frontmatter raises, naming the file", "probe.md" in str(e))

    # Regression: `retry-backoff-must-be-jittered` (highest link degree in the corpus) keeps its
    # dated entries under `## Additional evidence`. Matching only `## Evidence` normalised
    # zero of them while reporting success.
    multi = ("---\nid: x\n---\n\n## Evidence\n- Session 2026-01-02: first\n\n"
             "## Heuristic\nnot evidence, must not be touched\n\n"
             "## Additional evidence\n- Session 2026-03-04: second\n")
    n7, st7 = migrate_text(multi)
    check("second evidence section also migrated", st7["normalised"] == 2)
    check("intervening non-evidence section untouched",
          "## Heuristic\nnot evidence, must not be touched" in n7)

    # Regression: indented bullets are continuation prose, not undated entries.
    nested = ("---\nid: x\n---\n\n## Evidence\n- Acme_App, 2026-05-30: the entry\n"
              "  - First check: a sub-point with no date\n"
              "  - Second sub-point, also undated\n")
    n8, st8 = migrate_text(nested)
    check("continuation bullets not counted as undated entries", st8["undatable"] == 0)
    check("continuation bullets passed through verbatim",
          "  - First check: a sub-point with no date" in n8)
    check("nested file reaches schema_version 2", "schema_version: 2" in n8)

    # ------------------------------------------------------- the 4th shape: PARAGRAPHS
    # A bare column-0 paragraph is an entry. Before this, `build_index` counted it as zero
    # and this script left it alone, so a file could declare N entries, hold N, and report
    # none of them — zero-because-unparsed wearing the face of zero-because-absent.
    para = ("---\nid: x\nevidence_count: 1\n---\n\n## Evidence\n\n"
            "Acme_App session 2026-05-28: the plan used the wrong pseudo-class and the\n"
            "review panel caught it.\n")
    p1, sp1 = migrate_text(para)
    check("PARA: dated paragraph becomes one entry", sp1["normalised"] == 1)
    check("PARA: multi-line paragraph joins into ONE line, not one bullet per line",
          p1.count(UNTYPED) == 1
          and "— Acme_App session 2026-05-28: the plan used the wrong pseudo-class "
              "and the review panel caught it." in p1)
    check("PARA: date came from the paragraph, not the heading",
          sp1["heading_dated"] == 0 and "**2026-05-28**" in p1)
    check("PARA: reaches schema_version 2", "schema_version: 2" in p1)
    p1b, sp1b = migrate_text(p1)
    check("PARA: idempotent — second pass byte-identical", p1b == p1)
    check("PARA: second pass sees it as typed",
          sp1b["typed"] == 1 and sp1b["normalised"] == 0)

    # Rule (2): the date lives in the heading only. Counted separately — this is the one
    # branch that writes a date the file never stated, so it must be reviewable by name.
    hpara = ("---\nid: x\n---\n\n## Additional evidence — same-component variant "
             "(added 2026-05-27)\n\nThe trap also fires within a single component when a\n"
             "header total is rendered alongside a drilldown.\n")
    p2, sp2 = migrate_text(hpara)
    check("PARA rule 2: heading date used when the paragraph has none",
          "- **2026-05-27** " + UNTYPED in p2)
    check("PARA rule 2: counted in its own reviewable bucket",
          sp2["heading_dated"] == 1 and sp2["normalised"] == 1)
    # No "the section heading survives" assertion here on purpose: a section span starts
    # AFTER its heading, so migrate_section never sees it and no neuter of this file can
    # make such a check go red. It would be green for a reason that has nothing to do with
    # the code under test. The provable form is the archive case below, where a `## `
    # heading really is inside the scanned text.

    # Priority: an in-paragraph date WINS over the heading's. Reversing these two would
    # silently re-date entries that already said when they happened.
    both = ("---\nid: x\n---\n\n## Evidence (added 2026-01-01)\n\n"
            "Reference: Finance Tab 2026-05-27 — the real date of this observation.\n")
    p3, sp3 = migrate_text(both)
    check("PARA: in-paragraph date beats the heading date",
          "**2026-05-27**" in p3 and "**2026-01-01**" not in p3)
    check("PARA: an in-paragraph date is NOT counted as heading-dated",
          sp3["heading_dated"] == 0)

    # Ambiguity guard: two dates in the heading is not a date. Picking the first would be
    # a guess, and rule (2) exists precisely to avoid guessing.
    amb = ("---\nid: x\n---\n\n## Evidence — 2026-01-01 through 2026-02-02\n\n"
           "A paragraph with no date of its own.\n")
    p4, sp4 = migrate_text(amb)
    check("PARA: ambiguous heading (2 dates) does NOT date the paragraph",
          sp4["undatable"] == 1 and sp4["normalised"] == 0)
    check("PARA: ambiguous case leaves the paragraph verbatim",
          "A paragraph with no date of its own." in p4 and UNTYPED not in p4)
    check("PARA: ambiguous case blocks schema_version", "schema_version" not in p4)

    # Rule (3): no date anywhere. Never invented.
    und = ("---\nid: x\n---\n\n## Evidence\n\nA paragraph with no date at all.\n")
    p5, sp5 = migrate_text(und)
    check("PARA: undatable paragraph left COMPLETELY untouched", p5 == und)
    check("PARA: undatable paragraph counted", sp5["undatable"] == 1)

    # Non-entry prose must not be swallowed. A fenced block can hold a dated line; a
    # `**Bold:**` lead is structure inside an entry's section, not an entry.
    skip = ("---\nid: x\n---\n\n## Evidence\n\n```\n2026-01-01 not an entry, it is code\n```\n\n"
            "> 2026-02-02 a blockquote is not an entry\n\n"
            "**Pattern:** 2026-03-03 a bold lead is not an entry\n")
    p6, sp6 = migrate_text(skip)
    check("PARA: fenced code / blockquote / bold-lead are not entries",
          sp6["normalised"] == 0 and sp6["undatable"] == 0)
    # This shape is not hypothetical: skipping a `**Bold lead.**` block ONE LINE at a time
    # left its wrapped remainder at column 0, so the tail of a sentence opened a new
    # paragraph and became an entry — a fabricated observation, dated from the heading.
    wrapped = ("---\nid: x\n---\n\n## Evidence — 2026-07-29\n\n"
               "**Distinction from the entry above.** That one is a stale cache key the\n"
               "sweep never touched — a config problem. This one is unreachable by\n"
               "construction.\n")
    p6b, sp6b = migrate_text(wrapped)
    check("PARA: a wrapped block becomes exactly ONE entry, never one per line",
          sp6b["normalised"] == 1 and p6b.count("- **2026-07-29**") == 1)
    check("PARA: the whole block's text survives in that one entry",
          "**Distinction from the entry above.** That one is a stale cache key the "
          "sweep never touched — a config problem. This one is unreachable by "
          "construction." in p6b)

    check("PARA: fenced code preserved byte-for-byte (body unchanged; only the "
          "frontmatter gains schema_version)",
          p6.split("---\n", 2)[2] == skip.split("---\n", 2)[2])

    # Paragraphs in an `<id>.evidence.md` archive — the shape `demo/` uses to prove the
    # UNPARSED branch, and the reason 30 real corpus entries are invisible.
    apara = ("# Evidence archive — x\n\nA preamble with no date, which must survive.\n\n"
             "2026-07-21 `successful_recall` — the newest entry, in paragraph shape.\n\n"
             "2026-06-03 `confirmation` — a second one.\n")
    p7, sp7 = migrate_text(apara, "x.evidence.md")
    check("PARA ARCHIVE: both paragraph entries normalised", sp7["normalised"] == 2)
    check("PARA ARCHIVE: undated preamble left alone",
          "A preamble with no date, which must survive." in p7 and sp7["undatable"] == 1)
    check("PARA ARCHIVE: h1 title untouched", p7.startswith("# Evidence archive — x\n"))
    p7b, _ = migrate_text(p7, "x.evidence.md")
    check("PARA ARCHIVE: idempotent", p7b == p7)

    # Rule (2) inside an archive. Only here does a `## ` heading appear INSIDE the text
    # being scanned — a lesson file's section span stops at the next `## ` — so this is
    # the one case that exercises the running-heading branch at all.
    ahead = ("# Evidence archive — x\n\n## Round 2 — 2026-06-05\n\n"
             "A paragraph carrying no date of its own.\n\n"
             "## Round 3 — 2026-01-01 and 2026-02-02\n\n"
             "Another undated paragraph, under an ambiguous heading.\n")
    p8, sp8 = migrate_text(ahead, "x.evidence.md")
    check("PARA ARCHIVE: a `## ` heading inside the archive supplies rule 2",
          "- **2026-06-05** " + UNTYPED in p8 and sp8["heading_dated"] == 1)
    check("PARA ARCHIVE: the running heading is per-section, and an ambiguous one "
          "still declines", sp8["undatable"] == 1 and "2026-01-01**" not in p8)
    check("PARA ARCHIVE: heading lines themselves untouched",
          "## Round 2 — 2026-06-05" in p8 and "## Round 3 — 2026-01-01 and 2026-02-02" in p8)

    # ------------------------------------------------- RECONCILIATION: never fabricate
    # The assertion the shape-by-shape checks above structurally cannot make. A codemod
    # that emits MORE entries than the file declares has invented evidence, and every
    # individual shape assertion can pass while that happens — they each look at one
    # block, and fabrication is a property of the WHOLE file.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import build_index as _B

    def parsed_entries(text, name="x.md"):
        if name.endswith(".evidence.md"):
            return _B._total(_B.evidence_stats(text, whole=True))
        return _B._total(_B.evidence_stats(text[FM.match(text).end():]))

    multi = ("---\nid: x\nevidence_count: 2\n---\n\n"
             "## Evidence — 2026-06-01 (Acme_App PR #113)\n\n"
             "The main-moved-again branch, confirmed. User reported a GitHub conflict.\n\n"
             "Diagnosed with git first; listed the files main touched that overlapped.\n\n"
             "**Resolution.** Resolved the one conflict, re-verified, told user to push.\n\n"
             "## Additional evidence — 2026-07-02 (second session)\n\n"
             "A second observation, also spanning\ntwo source lines.\n\n"
             "With a second paragraph that belongs to it.\n")
    m1, sm1 = migrate_text(multi)
    def declared_in(text):
        """The file's own declared count — read from it, never a literal in the test."""
        return int(_B.field(FM.match(text).group(1), "evidence_count"))

    check("RECONCILE: a 2-entry file yields exactly 2 entries, not 5",
          parsed_entries(m1) == 2)
    check("RECONCILE: parsed_after never exceeds the declared evidence_count",
          parsed_entries(m1) <= declared_in(m1))
    check("RECONCILE: a multi-paragraph entry is ONE entry", sm1["normalised"] == 2)
    check("RECONCILE: no continuation text is dropped",
          "Diagnosed with git first" in m1 and "**Resolution.**" in m1
          and "With a second paragraph that belongs to it." in m1)
    check("RECONCILE: continuation is indented, so build_index reads it as prose",
          "\n  Diagnosed with git first" in m1)
    m1b, _ = migrate_text(m1)
    check("RECONCILE: idempotent", m1b == m1)

    # Synthetic regression fixture, modelled on the general shape a merged evidence
    # archive can take: a preamble with a Scope note that NAMES a date, two dated `##`
    # headings each holding several paragraphs (one led by `**Bold.**`, one ending in a
    # `Merged from:` provenance line and a summary paragraph), then already-dated
    # column-0 bullets. A live archive of this shape can declare N and hold N while
    # segmenting on blank lines alone would nearly double the count.
    gg = ("# Evidence archive — g\n\n"
          "Full history. The lesson lives in `g.md`; this file exists so the pattern\n"
          "stays visible.\n\n"
          "Scope note: on 2026-07-28 this file was split. Three entries about a stale\n"
          "manifest moved to another archive.\n\n"
          "## 2026-07-29 — two guards where one subsumes the other\n\n"
          "Acme_App, task T-12: add two validation guards to computeRetryDelay.\n\n"
          "The two are not independent; every input that trips the specific guard also\n"
          "trips the general one.\n\n"
          "Caught before writing either guard, by asking what fixture makes only the new\n"
          "guard fire.\n\n"
          "**Distinction from the entry below.** That one is a live guard whose branch\n"
          "the fixture never visited.\n\n"
          "## 2026-07-29 — a neuter that stayed green because the fixture never reached\n\n"
          "Acme_App, executing the validation-hardening plan. Neutered the emitter and\n"
          "the test stayed green.\n\n"
          "It was not dead. A probe showed the buffer never empties in that window.\n\n"
          "Merged from: session observation, `/instinct-analyze` 2026-07-29.\n\n"
          "Read together, the six below form a ladder of how little a green suite can\n"
          "mean.\n\n"
          "- **2026-07-10** *(from duplicate-validation-layers)* — two independent\n"
          "  defences meant neutering one alone never went red.\n"
          "- **2026-06-05** *(from test-encodes-the-bug-as-expected)* — the test encoded\n"
          "  the silent-drop bug as expected behaviour.\n")
    g1, sg = migrate_text(gg, "g.evidence.md")
    check("GREEN-GUARDS SHAPE: exactly 4 entries — 2 headed + 2 bullets, not 9",
          parsed_entries(g1, "g.evidence.md") == 4)
    check("GREEN-GUARDS SHAPE: the Scope-note preamble is NOT dated into an entry",
          "**2026-07-28**" not in g1 and sg["preamble_skipped"] == 1)
    check("GREEN-GUARDS SHAPE: a `Merged from:` provenance line is not its own entry",
          g1.count("- **") == 4)
    check("GREEN-GUARDS SHAPE: both headed entries dated from their heading",
          sg["heading_dated"] == 2)
    check("GREEN-GUARDS SHAPE: the already-dated bullets stay their own entries",
          "- **2026-07-10** " + UNTYPED in g1 and "- **2026-06-05** " + UNTYPED in g1)
    check("GREEN-GUARDS SHAPE: every paragraph's text survives",
          "Read together, the six below form a ladder" in g1
          and "Merged from: session observation" in g1
          and "**Distinction from the entry below.**" in g1)
    g2, _ = migrate_text(g1, "g.evidence.md")
    check("GREEN-GUARDS SHAPE: idempotent", g2 == g1)

    # `---`-delimited archive (`verify-docs-before-assuming-api-shape.evidence.md`): one
    # entry per block however many paragraphs and sub-bullets it holds, preamble excluded.
    hr = ("# Evidence archive — h\n\nFull history. Entries are `---`-delimited.\n\n"
          "---\n\nAcme_App session 2026-05-28: ran three ctx7 lookups in parallel:\n"
          "- `/tailwindlabs/tailwindcss.com` on `@theme inline` — confirmed.\n"
          "- `/resend/react-email` on email images — verbatim quote.\n\n"
          "Plus three web searches that refined two recommendations.\n\n"
          "---\n\n"
          "**Stale-cache claims are a sub-pattern of this** (2026-04-27): wrote a\n"
          "confident time-stamped claim with no source.\n")
    h1, sh = migrate_text(hr, "h.evidence.md")
    check("HR SHAPE: one entry per `---` block, not per paragraph",
          parsed_entries(h1, "h.evidence.md") == 2)
    check("HR SHAPE: a block opening with `**bold**` is still an entry",
          "- **2026-04-27** " + UNTYPED in h1)
    check("HR SHAPE: undated sub-bullets are continuation, not entries",
          "  - `/resend/react-email`" in h1)
    check("HR SHAPE: preamble excluded", sh["preamble_skipped"] == 1)

    # Bullets govern: a dated bullet list makes a stray paragraph a NOTE about the list,
    # not another observation. A file whose evidence is a short dated bullet list can
    # carry an "Age note:" paragraph that names a date and, unguarded, becomes a sixth
    # entry in a five-entry archive.
    note = ("# Evidence archive — n\n\n"
            "- **2026-07-19** `?` — the first entry.\n"
            "- **2026-02-17** `?` — the second entry.\n\n"
            "Age note: the 2026-02-17 entry has not been re-verified against the current\n"
            "harness.\n")
    n1, sn = migrate_text(note, "n.evidence.md")
    check("BULLETS GOVERN: a dated note beside a bulleted list is not an entry",
          parsed_entries(n1, "n.evidence.md") == 2 and sn["normalised"] == 0)
    check("BULLETS GOVERN: the note is left untouched and counted",
          "Age note: the 2026-02-17 entry" in n1 and sn["undatable"] == 1)

    # ------------------------------------------------- DEMOTION: never destroy an entry
    # Indenting a column-0 bullet does not tidy it, it DELETES it — `build_index.PROSE`
    # matches column 0 only. Measured at 72 files / 146 entries before this was caught,
    # and none of it showed in any stat. Modelled on a file whose evidence section is
    # exactly this shape: a dated bullet followed by an undated one, which went 2
    # readable entries -> 1.
    demo = ("---\nid: x\nevidence_count: 3\n---\n\n## Evidence\n"
            "- **2026-05-04** `?` — the dated entry.\n"
            "- A second observation nobody dated.\n")
    d1, sd = migrate_text(demo)
    check("DEMOTION: an undated bullet after an entry stays at column 0",
          "\n- A second observation nobody dated." in d1)
    check("DEMOTION: so it is still readable to build_index",
          parsed_entries(d1) == 2 and sd["demoted"] == 0)
    check("DEMOTION: and it is counted as outstanding work, not silently kept",
          sd["undatable"] == 1)

    # The one case where indenting IS right: a list the entry's own sentence introduces
    # with a colon. Counted in `demoted` so it can never happen unrecorded.
    colon = ("---\nid: x\nevidence_count: 1\n---\n\n## Evidence — 2026-05-28\n\n"
             "Ran three ctx7 lookups in parallel:\n"
             "- `/tailwindlabs/tailwindcss.com` — confirmed.\n"
             "- `/resend/react-email` — verbatim quote.\n")
    c3, sc3 = migrate_text(colon)
    check("DEMOTION: a colon-introduced list IS indented, and counted",
          "  - `/resend/react-email` — verbatim quote." in c3 and sc3["demoted"] == 2)

    # The guard, and the two fixtures split along the axis that actually exists: REFUSE
    # on loss, and MUST-NOT-REFUSE on a merely-stale declared count. They were once one
    # fixture that tripped both of two branches at once, asserting only "unwritten" and
    # `rc == 1` — so neutering EITHER branch left the suite green, because the other fired
    # and produced a different message nothing was checking. The guard against destroying
    # 146 entries could have broken and shipped green. Hence: one condition per fixture,
    # and the assertion binds to the MESSAGE, never to `rc` alone.
    #
    # `evidence_count: 1` here equals the post-migration count on purpose — the declared
    # value cannot influence this outcome, so only the loss condition can explain a refusal.
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "shrink.md"
        f.write_text("---\nid: shrink\nevidence_count: 1\n---\n\n"
                     "## Evidence — 2026-05-28 (one incident)\n\n"
                     "A paragraph that will become the entry:\n"
                     "- an undated sub-bullet the colon rule will indent\n"
                     "- and a second one\n")
        src = f.read_text()
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = run([f], write=True)
        check("DEMOTION: a shrinking migration is REFUSED, not written",
              f.read_text() == src)
        check("DEMOTION: and the refusal changes the exit code", rc == 1)
        check("DEMOTION: the refusal names LOSS and the counts, so the branch is identifiable",
              "LOSS:" in err.getvalue() and "2 -> 1" in err.getvalue())

    # The converse, and the regression test for a false positive that cost real work: a
    # file whose declared count is simply STALE — nothing is destroyed (before == after),
    # the frontmatter was already ahead of the parser before the codemod ran — MUST be
    # migrated, not refused. A branch refusing `after < declared` fired on 7 live files,
    # every one of them `before == after`, blocking strictly-improving migrations and
    # making a corpus run unable to exit 0. Reinstating it reddens THIS assertion alone.
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "stale.md"
        f.write_text("---\nid: stale\nevidence_count: 9\n---\n\n"
                     "## Evidence\n"
                     "- Session 2026-05-28: an undated prose bullet, migrated in place.\n")
        with contextlib.redirect_stderr(io.StringIO()), \
                contextlib.redirect_stdout(io.StringIO()):
            rc = run([f], write=True)
        out = f.read_text()
        check("STALE DECLARED: a merely-stale count is NOT a refusal — the file is written",
              "- **2026-05-28** `?`" in out)
        check("STALE DECLARED: and it does not poison the exit code", rc == 0)

    # The ONLY shape in which `opens_entry` decides anything: an archive with no heading,
    # no `---` and no dated bullets, where entries ARE paragraphs (the shape
    # `fixtures/demo/test-database-…evidence.md` uses). A paragraph that leads with a date
    # opens an entry; one that does not is continuation of the entry above.
    #
    # Added after a neuter of `opens_entry` stayed GREEN. It was not a dead check: every
    # other fixture is governed by a heading, a rule or a bullet list, so none of them
    # reached this branch. A neuter aimed at a branch the fixtures never enter is a wrong
    # probe, not a dead assertion.
    cont = ("# Evidence archive — c\n\nFull history, newest first.\n\n"
            "2026-07-21 `successful_recall` — the newest entry. Read before writing a\n"
            "new integration suite.\n\n"
            "The reset was put in setup from the start and the second run passed.\n\n"
            "2026-06-03 `confirmation` — a second suite failed on exactly this pattern.\n")
    c1, sc = migrate_text(cont, "c.evidence.md")
    check("CONTINUATION: an undated paragraph continues the dated one above it",
          parsed_entries(c1, "c.evidence.md") == 2 and sc["normalised"] == 2)
    check("CONTINUATION: it is indented, not left as a second column-0 paragraph",
          "\n  The reset was put in setup from the start" in c1)
    check("CONTINUATION: only the leading prose is undatable, not the continuation",
          sc["undatable"] == 1)
    c2, _ = migrate_text(c1, "c.evidence.md")
    check("CONTINUATION: idempotent", c2 == c1)

    typed = ("---\nschema_version: 2\nid: x\n---\n\n## Evidence\n"
             "- **2026-07-19** `repeated_failure` (Acme_App) — real classification\n")
    n6, st6 = migrate_text(typed)
    check("classified entry preserved verbatim", n6 == typed and st6["typed"] == 1)

    # Regression: an `<id>.evidence.md` archive has NO frontmatter and NO `## Evidence`
    # heading — it is `# Evidence archive — <id>`, a preamble, then bullets. It was
    # rejected as Malformed, while `build_index.record()` reads the archive EXCLUSIVELY
    # whenever one exists. So the gate judged 384 entries across 64 frontier files that
    # this script refused to type: typing their bodies changed nothing the gate saw, and
    # they could never become eligible however much work was done.
    archive = ("# Evidence archive — x\n\nFull history, newest first. The lesson lives in\n"
               "`x.md`; this file exists so the pattern stays visible.\n\n"
               "- **2026-05-07** *(from old-source)* — Acme_App. Did a thing.\n\n"
               "- Session 2026-04-01 (Acme_App): did another.\n")
    n9, st9 = migrate_text(archive, "x.evidence.md")
    check("ARCHIVE: no frontmatter is not Malformed", st9["normalised"] == 2)
    check("ARCHIVE: untyped marker present twice", n9.count(UNTYPED) == 2)
    check("ARCHIVE: legacy date recovered inline", "**2026-04-01**" in n9)
    check("ARCHIVE: preamble prose untouched", "Full history, newest first." in n9)
    check("ARCHIVE: no schema_version stamped (it has no frontmatter)",
          "schema_version" not in n9)
    check("ARCHIVE: outcome never invented", not any(o in n9 for o in OUTCOMES))
    a2, st10 = migrate_text(n9, "x.evidence.md")
    check("ARCHIVE: idempotent", a2 == n9 and st10["typed"] == 2 and st10["normalised"] == 0)

    # A plain .md with no frontmatter must STILL raise — the archive exemption is keyed
    # to the filename, not to "frontmatter happens to be absent".
    try:
        migrate_text("no frontmatter at all\n", "ordinary.md")
        check("non-archive still raises on missing frontmatter", False)
    except Malformed:
        check("non-archive still raises on missing frontmatter", True)

    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "probe.md"
        f.write_text(good)
        run([f], write=True)
        first = f.read_text()
        run([f], write=True)
        check("on-disk round trip idempotent", f.read_text() == first)

        # NEGATIVE, against real files rather than text written to satisfy the assertion:
        # a fully conformant fixture must come out BYTE-IDENTICAL. Every inline case above
        # asserts what the codemod changes; this one asserts what it must not.
        demo = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "demo"
        conformant = ["backoff-timer-doubles-twice-per-attempt.md",
                      "retry-loop-swallows-the-original-error.md",
                      "schema-cache-serves-the-config-from-before-the-change.md"]
        for fn in conformant:
            src = (demo / fn).read_text()
            copy = pathlib.Path(tmp) / fn
            copy.write_text(src)
            run([copy], write=True)
            check(f"NEGATIVE: conformant fixture untouched — {fn}",
                  copy.read_text() == src)

        a = pathlib.Path(tmp) / "probe.evidence.md"
        a.write_text(archive)
        rc = run([a], write=True)
        check("ARCHIVE: run() exit 0, not MALFORMED", rc == 0)
        afirst = a.read_text()
        run([a], write=True)
        check("ARCHIVE: on-disk round trip idempotent", a.read_text() == afirst)

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if "--report" in args:
        # Explicit paths make `--report` a per-file dry run over exactly those files —
        # archives included, which the corpus-wide default deliberately still excludes.
        named = [pathlib.Path(a) for a in args if not a.startswith("--")]
        files = named or sorted(p for p in DEFAULT_ROOT.glob("*.md")
                                if not p.name.endswith(".evidence.md"))
        return run(files, write=False, per_file=bool(named) or "--per-file" in args)
    if not args:
        print(__doc__)
        return 2
    return run([pathlib.Path(a) for a in args], write=True)


if __name__ == "__main__":
    sys.exit(main())

# fixtures

A demo corpus for `instinct-distill`, exercised end-to-end by
[`../scripts/demo_run.py`](../scripts/demo_run.py):

```bash
python3 scripts/demo_run.py --selftest   # must print SELFTEST: PASS
python3 scripts/demo_run.py              # human-readable walkthrough
```

**Invented content only.** Every lesson, date and error string here is fabricated, and the
subjects are neutral placeholders ("the web app", "service-a"). Nothing in this directory
refers to a real project. The live corpus lives in `~/.claude/homunculus/instincts/personal/`
and is never touched by these scripts.

**Why a corpus on disk rather than another inline `tempfile` fixture.** The other five
selftests build their input three lines above the assertion, so they prove a script against
text written to satisfy it. These files are in the real
[`instinct-format`](../../instinct-format/SKILL.md) shape — full frontmatter, `## Evidence`
sections, one sibling archive — so a change to the format contract fails here even when no
inline fixture happens to cover it.

## `demo/` — one file per branch

Eight lesson files across five `domain` partitions, plus two evidence archives.

| File | domain | Covers |
|---|---|---|
| `build-cache-key-omits-the-config-hash.md` | harness | **The only `eligible` file.** 2 `repeated_failure` on distinct dates. Also the **archive branch**: it has an `<id>.evidence.md` sibling, which `record()` reads *exclusively* — its body excerpt shows only 1 `repeated_failure`, so anything judging on the body misses it. |
| `build-cache-key-omits-the-config-hash.evidence.md` | — | The full history: all five outcome values except `successful_recall`, newest first, no `## Evidence` heading (archives are scanned whole). |
| `config-change-does-not-invalidate-the-build-cache.md` | harness | **The merged-document case.** Three `first_observation` entries on three distinct dates, each tagged `*(from <source>)*`. Typed, and counts toward nothing — this is the hole in gate condition 2 that `first_observation` closes. Also the **cluster partner**: same domain, overlapping trigger/action vocabulary, author-linked to the file above. |
| `retry-loop-swallows-the-original-error.md` | debugging | **Untyped `?`.** One entry is unclassified, so eligibility is *unverified*, not false (#8) — even though the other entry is a real `repeated_failure`. |
| `backoff-timer-doubles-twice-per-attempt.md` | debugging | **Same-date recurrence.** Two `repeated_failure` entries sharing one `observed_at` — one observation written twice, so gate condition 1 rejects it. |
| `schema-cache-serves-the-config-from-before-the-change.md` | supabase | **The cross-domain control.** Deliberately shares "cache", "config", "change" and "stale" with the harness pair, and is still excluded at `min_score=0.0, top_k=99`, because `domain` is a partition boundary and the author never linked it. Its exclusion is structural, not lexical. |
| `plan-checkboxes-outlive-the-plan.md` | workflow | **Legacy entries.** Spec-format `- **DATE** —` bullets that predate the typed schema: counted in their own bucket, never as typed, never as zero. |
| `test-database-is-not-reset-between-suites.md` + `.evidence.md` | testing | **The archive that parses ZERO — the silent-wrong-pass guard.** Its body holds 2 `repeated_failure` on distinct dates and an `evidence_count` that matches, so the body *alone* reads a clean `eligible`. Its archive is written in the unsupported paragraph shape (no leading `-`), so the parser yields 0 — and the archive's real newest entry is a `successful_recall`, which DECLINES a cluster. The archive wins anyway and the hint is `UNPARSED`. Revert that and the file reports `eligible: 2 repeated_failure over 2 dates`: a promotion the record contradicts. |
| `read-the-plan-before-proposing-a-sweep.md` | workflow | **`successful_recall` as counter-evidence.** Its two latest entries are the lesson working as intended; a cluster in that state is declined, not promoted. Also exercises `## Additional evidence` as a second evidence heading. |

## Branches covered by no fixture file

- **Cold start (#10)** — asserted against an empty `tempfile` directory inside
  `demo_run.py`, deliberately *not* an empty fixture directory: an empty dir does not
  survive git, and a stray file in it would silently become a candidate.
- **`malformed` / `missing` / `UNPARSED`** — already covered by `build_index.py --selftest`
  and `shortlist.py --selftest`. `demo_run.py` asserts the demo corpus reports *no*
  malformed files, so an accidental format break here fails loudly.

## Changing a fixture

Every file's counts are asserted by id in `demo_run.py`'s `EXPECTED` table. Editing an
evidence entry changes a gate branch — update the table in the same commit, and re-run the
neuter probe: **a green selftest proves nothing until you have watched the specific
assertion go red.**

Two values are load-bearing in ways that are easy to "tidy" into uselessness:

- `test-database-is-not-reset-between-suites.md`'s **`evidence_count: 2`**. Raise it to
  match the archive's four paragraphs and the body-only verdict becomes
  `ineligible: parsed 2 of 4 declared entries` — a loud failure. The fixture exists to
  demonstrate a *silent* wrong pass, so the count must let the body read `eligible`.
- The **paragraph shape** of that same archive. Add a leading `-` to any entry and it parses,
  the `UNPARSED` branch stops firing, and four assertions go quietly green forever.

To probe this file: make `record()` fall back to the body when the archive parses 0, and
watch it report `eligible: 2 repeated_failure over 2 dates`.

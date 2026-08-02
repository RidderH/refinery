# Converting an instinct to the lesson-first format

Read [`SKILL.md`](SKILL.md) first — it holds the format itself. This file holds what
converting adds: the rules, and the criterion that says when the conversion is done.

## Completion criterion

**Before writing, enumerate every claim, caveat, cross-reference, checklist row and evidence
entry in the original. After writing, each item on that list resolves to a location — a line
in the lesson file, or a line in the archive.** The enumeration is the deliverable; the
rewrite is what makes it true.

An adversarial verifier runs this same enumeration afterwards. Anything the enumeration misses
is what it finds, so running it first is the cheaper order.

**This governs authoring or refreshing a single `action:` line too** — even on a file you change
in no other way. An `action:` is a whole-file compression, so it needs the whole-file
enumeration; writing one from an excerpt reliably captures the original claim and misses every
correction stapled below it. On 2026-07-28, 280 lines written from `head -20`..`head -28` spot-
checked at 7 wrong in 13, one of them factually inverted by text 92 lines below the excerpt.

## Rules

1. **Every claim in the output traces to a claim in the original.** Where the original states
   a general behaviour once and a specific observation once, the output carries both, each at
   its original strength. A single observation stays a single observation.
2. **Every evidence entry survives** — verbatim in `<id>.evidence.md` for files with 3+
   entries, with the latest two also summarised in the lesson body.
3. **Checklist rows are lesson content.** A table or list of `situation → the specific check
   to run` is the most actionable thing an instinct carries. Keep every row, in the body,
   with its own trigger phrase intact. Rows collapse into a general step only when the general
   step names the same check.
4. **Confirm coverage by grep before recording a removal.** When the archive carries something
   the lesson drops, grep the archive for it and let the result stand as the record.
5. **Replace an inline copy of a volatile fact with a pointer to its authoritative source** —
   an allowlist enumeration, a line number, a pinned version. The copy is what goes stale; the
   pointer is what stays true. Stable mechanism claims stay inline.
6. **Carry `[[links]]` and cross-references through** — they encode corpus structure.
7. **Carry `id`, `created`, `confidence` and `evidence_count` through unchanged.** Set
   `updated` and `last_checked` to today; add `version_pin: null` unless the original names a
   version. Drop any `status:` line the original carries — the field was removed from the
   format on 2026-07-29. If the original has `promoted_to:`, carry it and repo-qualify it.
8. **Derive a `## Symptom` from the trigger and evidence** where the original has none. A file
   with a symptom is findable; that is the whole point of the format.
9. **Declare `cites:`** — you just read the body in full, which is the only way to know which
   paths are load-bearing. List the project-qualified paths whose disappearance should make
   someone re-read the lesson; write `cites: []` when the lesson is path-independent. `[]` is
   the common answer and is a statement, not a skipped step. Contract and rules:
   [SKILL.md](SKILL.md). Until a file declares the field, prune's gate 2 guesses from prose and
   mis-flags example paths and external-repo paths — declaring is how a file leaves that
   false-positive population permanently.

## Size — measured on the body

**The body shrinks: the prose between the title and `## Evidence`.** Frontmatter and the
mandated `## Symptom` heading are structure, and structure is not bloat to compress away.

Count the body, not the file. The format's frontmatter is 14 lines where the old one was 8,
and `## Symptom` is net-new, so **any conforming file has a floor around 30 lines**. An earlier
version of this rule demanded the whole file be strictly shorter, which is unsatisfiable for a
short input — a 19-line original cannot produce a conforming 18-line file, and one batch failed
5 of 5 against a rule that could not be met.

Keeping the frontmatter is deliberate, not a concession. Metadata-enriched retrieval measures
**82.5% precision against 73.3%** for content-only, so those fields are what make the corpus
findable — the opposite of overhead.

Shrink the body by removing restatement. Where every remaining line is an instruction, the file
is already minimal — leave it and record that in your notes.

## Before converting: check the floor

**An original under ~35 lines is a MERGE candidate, not a conversion candidate.** Converting it
alone can only pad it toward the floor. The right operation is consolidation with topically
similar instincts — see [`MERGING.md`](MERGING.md).

162 of 509 files in this corpus sit below the floor. Converting each in place would inflate a
third of the corpus to satisfy a format rather than make anything easier to find.

Route first, then convert: file under ~35 lines → `MERGING.md`; file over it → the rules below.

## What the first two batches got wrong

Ten conversions, three rejected. Each failure maps to a rule above:

| Failure | Rule |
|---|---|
| Five checklist rows dropped as "duplicated evidence detail" — no evidence entry mentioned them | 3, 4 |
| Two files passed fidelity while growing | Size |
| "the auto-rebase fixes branch content, **never** the PR's base pointer" — one observation universalised, while the original's contrary general claim was deleted | 1 |
| Mechanism caveats lost: `ghost-resurrected`, `supabase migration list` drift | 5 (stable claims stay inline) |

---
name: instinct-format
description: Define and validate the instinct file contract when creating, converting, reviewing, or supplying the contract to another skill.
license: MIT
---

# Instinct file format

The contract for every file in `~/.claude/homunculus/instincts/personal/`. `/instinct-analyze`
and the conversion agents both read this, so the format stays identical across them.

Resolve `SKILL_DIR` to the absolute directory containing this `SKILL.md` before running a
bundled command. Do not assume the current working directory or a particular agent's install
root.

Working on an existing file? Route by size first:

- **Under ~35 lines** → [`MERGING.md`](MERGING.md). Below this format's floor, a file is a
  consolidation candidate; converting it alone only pads it toward structure.
- **Over ~35 lines** → [`CONVERTING.md`](CONVERTING.md), which holds the conversion rules and
  the completion criterion.

## Why this shape

The corpus is **pull-only**: nothing is injected at session start. A file earns its place by
being found by **symptom** search and cheap to read once found. Three cost tiers follow, and
the format serves them:

| Tier | How | Cost | Answers |
|---|---|---|---|
| Find | `grep -l "<literal error text>"` | ~9 tok | does anything exist? |
| Skim | `head -14 <file>` | ~140 tok | what is the lesson? |
| Read | full file | ~700 tok | why, mechanism, caveats |
| Audit | `<id>.evidence.md` | on demand | how was this confirmed? |

The Skim tier is why `action:` sits in frontmatter: the **lesson** must arrive without opening
the body. Skim is a **reader's** tier — never a writer's. Summarizing a file you have not read
in full is how a wrong `action:` gets minted.

## Frontmatter

```yaml
---
schema_version: 2                   # typed-evidence schema; lint_evidence.py treats files without it as unmigrated
id: kebab-case-name                 # equals the filename without .md
trigger: "literal strings a future searcher would type |
          error text, exit codes, command names, API names, file names |
          pipe-separated, and NEVER wrapped mid-phrase — see below"
action: "one line, imperative — the lesson itself, readable without the body"
                                    # authoring/refreshing it = read the file to the END
confidence: 0.3                     # 0.3 new · +0.1 per confirmation · cap 0.9
evidence_count: 1
domain: harness                     # controlled vocabulary, below
source: "session-observation"
created: "YYYY-MM-DD"
updated: "YYYY-MM-DD"
last_checked: "YYYY-MM-DD"          # docs-verification cache
version_pin: null                   # "next@16.0.3" for library claims; recheck when it moves
promoted_to: null                   # "<repo>/CLAUDE.md" when a copy lives there — see below
cites: []                           # paths this lesson's LIVENESS depends on — see below
---
```

**There is no `status:` field. Liveness is the filesystem.** A lesson is live if it sits in
`instincts/personal/`, and retired if it sits in `instincts/Archive/<YYYY-MM-DD>/` — where the
sibling `MANIFEST.md` records which topic document absorbed it and what it contributed. That
mechanism carried 280 retirements without a status field, so a field restating the directory a
file is already in adds nothing to grep and one more thing to keep true.

A `status: active | promoted | superseded` field was specced here until 2026-07-29 and removed:
nothing ever read it, `superseded` was never used once across 355 files, and `active` only ever
said "this file is where it is". `~/.claude/docs/adr/0001-instinct-liveness-is-filesystem.md`
has the full reasoning. Files still carrying a `status:` line are harmless — drop the line when
you next touch one, and do not sweep for them.

**`action` — authoring or refreshing it for a file you did not just write is a full-read
operation.** Read to the END, then write the line. These files grow by stapling corrections and
new failure modes to the *bottom*, so any excerpt boundary — `head -14`, `head -28` — sits
arbitrarily with respect to where a lesson's qualifications live. One long evidence entry is
enough. See [[corrections-are-appended-not-integrated]]. Authoring `action:` alone, on an
otherwise-unchanged file, is still governed by [`CONVERTING.md`](CONVERTING.md)'s completion
criterion.

**`trigger` — wrap only at a `|`, never mid-phrase.**

Retrieval is `grep`, and `grep` matches within one line. A trigger phrase broken across two
lines by YAML wrapping cannot be found by anyone typing that phrase — the file looks findable
and isn't. Break the line at a pipe boundary so every phrase stays whole:

```yaml
# BROKEN — "touch-based framer drag" spans the wrap and is unreachable
trigger: "framer-motion drag | you need to test touch-based
          framer drag | Input.dispatchTouchEvent"

# CORRECT — every phrase lives on one line
trigger: "framer-motion drag |
          you need to test touch-based framer drag |
          Input.dispatchTouchEvent"
```

Found 2026-07-28 during the first merge batch: three topic documents passed a short-string
reach check and still failed on the full original phrases, purely from wrapping. Verify with
the *complete* phrase from each original trigger, not a fragment of it — a fragment check
passes over exactly this defect.

**`promoted_to` — the address of a copy, not a state.**

Set it when this lesson's guidance was written into a project's always-loaded doc, so two
copies now exist. The instinct stays live; the field records where its twin is, because the
target never links back. Without it, editing the instinct silently leaves the copy stale and
nothing reveals that a copy was ever made.

**Qualify it with the repo.** `promoted_to: "CLAUDE.md"` is not an address — there are seven-plus
`CLAUDE.md` files on a typical machine, and resolving which one costs the same grep the field
exists to save. Write `promoted_to: "my-app/CLAUDE.md"`.

**`cites` — the paths whose disappearance should make someone re-read this lesson.**

Nothing else. Not every path the body mentions — only the ones where "this file is gone" means
"re-examine this lesson". Design:
`~/.claude/docs/superpowers/plans/2026-08-01-cites-frontmatter-design.md`.

The field exists because prune's gate 2 otherwise *infers* citations with a regex over the whole
body, and a regex cannot read intent. Two failure shapes are unfixable by pattern: an example
path (`a path like src/foo.ts`) and a path quoted from a repo that is not on this machine. Both
look like citations, resolve nowhere, and resurface as ALL_DEAD on every shortlist run. Declaring
removes them by construction.

- **Project-qualified, like `promoted_to`.** First segment is the on-disk project dir name, or
  `.claude`; the rest is repo-relative. A bare `src/lib/foo.ts` is not an address — unqualified
  paths are what produced 56 bogus ALL_DEAD flags on gate 2's first run.
- **`cites: []` is a statement, not an omission** — "this lesson is deliberately
  path-independent" (shell behaviour, API semantics, workflow lessons). Gate 2 reports
  `CITES_NONE`, which never shortlists by a staleness rank.
- **Field absent = unmigrated**, and gate 2 falls back to the regex. Presence IS the migration
  marker; do NOT bump `schema_version` for it (that number belongs to the typed-evidence schema,
  which migrates on its own schedule).
- **Never declare a path that cannot resolve on this machine.** External-repo and absent-project
  paths stay in prose — once `cites:` exists the body is not read at all, so quoting them is
  free. A lesson *about* an absent project takes `cites: []` and names the project in prose.
- **Example paths stay in prose.** That is the entire point.
- **Cap ~8 entries.** More means the lesson cites a subsystem, not files — cite the directory's
  most load-bearing file, or split the lesson.

Authoring `cites:` is a **full-read operation**, same as `action:` and for the same reason: you
cannot know which paths are load-bearing from an excerpt. Population is lazy — written on new
instincts at creation, and on existing files when one is next opened for conversion or merge.
**No bulk sweep.**

**Frontmatter speaks for the whole document.** On a topic document that absorbed several
sources, a frontmatter `promoted_to` claims *the merged lesson* has a copy. When only one
absorbed source was promoted, that belongs in the body as a `**Promotion note:**` naming the
source id and the target — leave the frontmatter field `null`. Otherwise a reader who greps
frontmatter concludes the whole lesson is duplicated when most of it is not.

**`domain` — use exactly one:**
`harness` · `workflow` · `testing` · `git` · `supabase` · `data` · `nextjs` · `frontend` ·
`infra` · `debugging` · `security` · `code-style`

Map anything else onto its closest match. This vocabulary is closed; the old corpus grew 77
ad-hoc values (`supabase/rls`, `shell, zsh, harness`) until the field meant nothing.

The two least obvious boundaries:

- **`infra`** — anything outside the repo that has to be configured for the code to run:
  Coolify, Vercel, Docker, CI images, Sentry, hosts, npm/lockfile mechanics.
- **`frontend`** — client-side lessons that are not Next.js-specific: React Flow, Tailwind,
  react-email, Leaflet, design tokens, i18n. Next-specific behaviour is `nextjs`.

Both were added 2026-07-28 during the corpus-wide remap. Without them the deploy and
component-library lessons had no home but `workflow`, which would have absorbed a third of the
corpus and stopped discriminating anything.

## Body

```markdown
# Title — the lesson as a sentence, not a topic

## Symptom
Literally what you see: error text verbatim, exit code, the observed wrong behaviour.
This is what makes the file findable — copy the real string.

## What's actually happening
The mechanism, 2-5 lines. The part that pushes against the default assumption. A file
whose mechanism restates its own error message has nothing to teach.

## Do this
Numbered, imperative, adaptable — the action and the reason, leaving room to adapt.
Cross-link as [[instinct-id]].

## Evidence (n=<total>, latest 2)
- **YYYY-MM-DD** — one or two sentences: what happened, what it proved.
- **YYYY-MM-DD** — same.

→ full history: `<id>.evidence.md`
```

## Length

Target **~50 lines**. Past ~80 the file is holding two lessons or an uncompacted evidence
pile — split it. Anthropic's published ceiling for an agent `MEMORY.md` is 200 lines / 25KB,
and the documented guidance for skills is structural ("overview and navigation", detail loaded
on demand) rather than numeric.

Compression comes from removing **restatement**. Where a file is all instruction and no
restatement, it is already minimal — leave it long and say so.

## Writing a tool that reads this corpus

Three facts about the real files, each of which produced a silent miscount in one session
(2026-07-29) while building `instinct-distill`. All three fail the same way: a count of **zero**
that means "I could not see it", indistinguishable from "there is none".

1. **Evidence lives under more than one heading.** Match `^##\s+[A-Za-z ]*Evidence\b`, never the
   literal `## Evidence`. `verify-before-building` — the highest-degree node in the whole link
   graph — keeps its dated entries under `## Additional evidence`. Matching exactly returned
   **zero entries for it while reporting success**, and later miscounted two files as having no
   evidence section at all.
2. **There are three entry shapes, not one.** Typed `- **DATE** \`outcome\` —`, spec-format
   `- **DATE** —`, and pre-schema prose (`- Session 2026-07-27 (Beta_Site): …`). Measured on the
   live corpus: 77 spec-format, **274 prose**, 6 with no evidence section. A tool that counts only
   bold dates sees a quarter of the corpus. **Indented bullets are continuation prose, not
   entries** — counting those overstated outstanding work by ~62 bullets.
3. **The body is an excerpt; the archive is the record.** The body holds "latest 2" by contract
   (below). Any tool judging on *history* — recurrence, dates spanned, outcome mix — must read
   `<id>.evidence.md` when it exists, or it systematically under-counts every file that has one.
   The archive has **no `## Evidence` heading**: it is `# Evidence archive — <id>`, a preamble,
   then bullets. Scan the whole document.

**Cross-check `evidence_count` against what you parsed, and fail loudly when they disagree.**
`declares 6, parsed 0` is a parser bug; reporting it as "0 entries" hands the caller a verdict that
is correct for entirely the wrong reason. This check found bugs 1 and 3 above within a minute of
being added. See [[a-signal-that-cannot-discriminate-answers-nothing]].

## Evidence archive

Files with **3+ evidence entries** get a sibling `<id>.evidence.md`: the complete history,
newest first, entries copied verbatim.

The archive earns its keep. The most valuable discovery in this corpus came from noticing the
same failure recurring across three separate entries — a root cause no single entry revealed.
Compacting that history into the lesson file would have destroyed the signal.

```markdown
# Evidence archive — <id>

Full history. The lesson lives in `<id>.md`; this file exists so the pattern
*across* entries stays visible.
```

## Definition of done

- `id` matches the safe filename stem; required fields and the closed domain vocabulary validate.
- `trigger` keeps every literal retrieval phrase on one physical line, and `action` reflects a
  full read rather than a skim.
- The body contains the Symptom, mechanism, action, and evidence required by this contract.
- `evidence_count` reconciles with visible incidents and the sibling archive is authoritative
  when present.
- The canonical `scripts/instinct_record.py` parser returns no diagnostics for a completed file.

Validate completion with:

```bash
python3 "$SKILL_DIR/scripts/instinct_record.py" --check <id>.md
```

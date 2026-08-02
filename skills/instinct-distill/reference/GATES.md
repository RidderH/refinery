# Gates

What a cluster must prove before `instinct-distill` will promote it. Decision #8: the
recurrence gate requires **typed** evidence. Untyped is *unverified*, therefore ineligible —
never assumed qualifying.

## Why typing is required at all

Rev 1 assumed "an evidence date later than `created`" proved the lesson existed and recurred.
Two facts kill that:

- `MERGING.md:53` — on a merged file `created` is backdated to the **oldest** source, so a
  later date proves nothing about when this lesson existed.
- Evidence entries record **successes as well as failures**. A 2026-07-29 entry reading *"this
  file was grepped before forming an opinion (the instinct worked)"* is the opposite of a
  recurrence, and a date comparison cannot tell it from *"third recurrence of the same framing
  failure"* — both are just "later than `created`".

So the outcome must be stated, not inferred.

## The `outcome` taxonomy

Exactly one per entry. The decision rule is **what the entry proves about the lesson**, not
what the session was doing.

| Value | Decision rule |
|---|---|
| `repeated_failure` | The lesson already existed and was **not** applied — re-derived, forgotten, or contradicted in practice. **Only this counts toward the recurrence gate.** |
| `successful_recall` | The lesson was **consulted** and changed what happened. Proof the instinct works; proof it is *not* recurring. |
| `confirmation` | The lesson's claim was independently re-observed without being consulted. Raises confidence, not recurrence. |
| `correction` | The entry **changes the lesson** — a caveat, a wrong mechanism, a superseded fix. |
| `first_observation` | The event that **created** the lesson. It did not exist before this entry, so nothing could have recalled, confirmed or repeated it. Counts toward nothing. |

### Why `first_observation` exists (user ruling 2026-07-29)

The other four values all presuppose the lesson **already existed**. A lesson's founding
observation satisfies none of them, and the pilot found it is not an edge case: 8 of the first
10 entries typed were founding observations.

Leaving them `?` was the alternative, and it is wrong for a specific reason. `?` means *"we could
not tell"* — a parser or judgment failure, fixable by looking harder. A first observation is
fully understood: it happened once, on a known date, and that is all it will ever prove. Those
are different facts and the gate must not conflate them, because #8 turns untyped into
**permanently** ineligible: 182 files declare `evidence_count: 1`, and a single observation
cannot recur by definition.

It also closes a hole in gate condition 2. **On a merged topic document every entry is the first
observation of a *different* source lesson** — 51 archives carry **288** source-tagged entries
(`grep -ho '\*(from [^)]*)\*' *.evidence.md | wc -l`; a stricter `[a-z0-9-]` pattern reports 280
and misses 8 tags that carry an inline annotation). Typed
as `repeated_failure`, a five-source merge would manufacture five recurrences on five distinct
dates and sail through a gate built to prevent exactly that. Condition 2 only catches entries all
drawn from *one* source file, so it would not have fired.

**A merged document inherits its sources' evidence, never their recurrence.** If the merged
lesson genuinely recurs, that recurrence is observed *after* the merge and typed then.

### `confirmation` vs `successful_recall` — the boundary that will blur

Both look like "it worked". The discriminator is **whether the file was read**.

- Read it, then acted differently → `successful_recall`
- Never opened it, and the world behaved as it says → `confirmation`

If an entry does not say which, it is not typeable. Mark it `` `?` `` and leave it ineligible;
do not guess. A guessed `successful_recall` silently suppresses a real recurrence.

**But `` `?` `` is for "I cannot tell what this is", not "I cannot choose between two values that
both count for nothing".** `confirmation` and `successful_recall` are gate-identical — neither
counts toward recurrence — so an entry ambiguous *only* between those two is not a gate question,
and marking it `` `?` `` asserts something stronger than the evidence supports: `` `?` `` makes a
file **permanently ineligible**, which is a claim about the lesson, not about your certainty.
Pick the better-supported of the two and say why in the entry. Reserve `` `?` `` for ambiguity
that spans a counting and a non-counting value.

### Real examples — all from this corpus

`repeated_failure`
> **2026-07-19** (`doc-blocker-hook-is-write-tool-scoped`) — *"a re-derivation of this
> instinct's own headline … I described the block to the user as a 'hole' in the guard … then
> reasoned my way, over two turns, back to exactly what this file already stated at
> confidence 0.9."*

> **2026-07-27** (same file) — *"third recurrence of the same framing failure, and the first
> with a diagnosed root cause."*

`successful_recall`
> **2026-07-29** (same file) — *"This file was grepped **before** forming an opinion (the
> instinct worked)."*

> **2026-07-29**, later (same file) — *"Followed this file's step 1 — read the live `ALLOWED`
> list in `doc-blocker.js` first — and wrote to the allowlisted directory on the next attempt;
> no re-derivation, no heredoc."*

`confirmation`
> **2026-07-19** (same file) — *"passive confirmation across a full session — no probe needed.
> … All three allowlist claims behaved exactly as documented."*

> **2026-07-19** (`authoring-and-testing-claude-code-hooks`) — *"third confirmation that the
> `stop_hook_active` guard prevents the runaway."*

`first_observation`
> **2026-04-02** (`module-resolution-belongs-to-whatever-loads-the-file`, `created: 2026-04-02`)
> — *"Beta_Site Phase 0b: three successive attempts to fix Leaflet SSR … Only the combination of
> the last two worked."* The entry's own date **is** the file's `created` date; there was no
> lesson to recall, confirm or repeat.

> **2026-05-07** (`nextjs-module-evaluation-traps`, tagged *(from next-config-ts-loaded-as-cjs)*)
> — the founding observation of a source instinct that was later merged in. The merged document
> is older than the entry; the *lesson the entry created* was not.

**The `created`-date test is a hint, not the rule.** It works for an unmerged file, and fails on
a merged one, where `created` is backdated to the oldest source (`MERGING.md:53`). On a merged
document the question is *"did the lesson this entry founded exist before it?"* — and a
`*(from <source>)*` tag is the corpus telling you the answer is no.

`correction`
> **2026-07-06** (`doc-blocker-hook-is-write-tool-scoped`) — *"Corrected the project memory
> `hooks-blokkeren-planbestanden`, which had wrongly implied `PLAN*.md` was allowlisted (it is
> not)."*

> **2026-07-29** (`green-guards-prove-nothing-until-you-make-them-red`) — the neutered emitter
> stayed green, which the lesson's own rule read as "assertion is dead"; a reachability probe
> showed the branch was never entered. *"The probe was wrong, not the test."*

## The recurrence gate

A cluster qualifies only if, **after** typing:

1. **≥2 entries typed `repeated_failure`**, on **distinct `observed_at` dates**. Two entries
   from one session are one observation *for gate purposes* — the dates are de-duplicated
   (`rf_distinct = len(set(rf_dates))`), so same-day entries cannot stack toward this
   condition however many are recorded.
   **This does not license lowering `evidence_count` to match.** That field is an INVENTORY of
   distinct incidents observed, not a count of gate-qualifying dates, and the two legitimately
   differ — see `SKILL.md § What counts as one evidence entry`. Independence is enforced here,
   by the date set, and nowhere else; the inventory does not need to enforce it a second time.
2. Those entries are **not all** inside a single source file's pre-merge history — otherwise a
   merged topic document inherits its sources' recurrences and every merge manufactures a
   qualifying cluster.
3. Every entry counted carries a date. An untyped `` `?` `` entry never counts, in either
   direction — it is not evidence of absence.
4. `first_observation` entries are **typed and do not count**. A file consisting only of them is
   *ineligible*, not *unverified* — the difference between "this has been looked at and has not
   recurred" and "we do not know what this is".
5. An outcome token outside this taxonomy blocks the gate exactly as `` `?` `` does. A typo is
   not a value: `succesful_recall` must never be silently ignored, because the value it was
   trying to be is the one that *declines* a cluster.

### Scope: this gate is per-CLUSTER; `build_index.py` scores per-FILE

`gate_hint` judges **one file at a time** and is explicitly advisory. The conditions above are
about a **cluster**, and two of them cannot be evaluated from a single file:

- Condition 1 may be satisfied *across* a cluster — two files each contributing one
  `repeated_failure` on different dates — which no per-file hint can see.
- Condition 2 (not all recurrences drawn from one source's pre-merge history) needs the
  `*(from <source>)*` tags across the cluster's archives. **No code implements it today.**

So a run must not treat `gate_hint.startswith("eligible")` as the whole gate. It is a per-file
filter; the cluster-level judgment is the model's, reading the files in full, against this
document. Where the two disagree, this document wins.

`successful_recall` is **counter-evidence**: a lesson being recalled successfully is working as
intended and needs no promotion. A cluster whose most recent typed entry is `successful_recall`
is **declined**, with that reason recorded in the ledger.

## Cold start (#10)

When nothing qualifies, emit the readiness readout — corpus size, how many files are typed, how
many entries are `repeated_failure`, and what the frontier looks like. Never lower the
thresholds for a small corpus: a stranger's first distillation being a bad always-on rule
poisons every later session.

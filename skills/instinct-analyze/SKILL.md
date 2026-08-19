---
name: instinct-analyze
description: Review the current conversation for durable lessons, verify each candidate, and present ranked recommendations before writing anything.
license: MIT
disable-model-invocation: true
---

Analyze the current conversation for instinct-worthy lessons, verify each candidate before recommending it, and present a ranked recommendation list. Write nothing until the user picks.

**There is no cap on how many instincts you may recommend — which means the gates below carry the entire load.** A candidate reaches SAVE only by passing every gate with cited evidence. "It seems useful" is not a pass. The corpus is already large and retrieval is a keyword grep, so every low-value instinct added makes every future retrieval worse. Dropping a mediocre candidate costs nothing; saving one costs every future session.

**Model and timing (advice to the human; the skill runs on whatever the session runs).**
Capture is pure judgment writing into a permanent corpus — run it on the highest-judgment
model available, never an economy tier (as of 2026-08: Fable 5). One exception: Phase 1
harvests from the *transcript in context*, so on a very long session anything already
summarized away is invisible to it — there, a large-context model (Opus 5 1M) that still
holds the raw turns beats a sharper model reading a summary. Best of both: invoke
`/instinct-analyze` before a long session compacts, while the evidence is still verbatim.

---

## Phase 1 — Harvest

Scan the current conversation for four signal types. Do not filter yet; collect everything.

1. **User corrections** — "no", "instead", "don't", "why did you...", or any redirect of your approach
2. **Surprising bugs** — root cause was non-obvious: silent failure, type lying about runtime, missing wiring, wrong layer
3. **Technical discoveries** — gotchas, workarounds, undocumented behavior found by doing
4. **Approach changes** — first attempt failed, a different strategy worked
5. **Harness and config lessons** — how Claude Code itself behaved: a hook that blocked or fired unexpectedly, a permission or classifier denial and what got the work through, a tool that failed in a non-obvious way, a technique for probing or verifying config, a difference between skills / commands / workflows / agents that changed what you built

Signal 5 is not a lesser category. Instincts live in `~/.claude/homunculus/` — **global, every project**. A harness lesson pays off in every repo, while a repo-specific one pays off in exactly one. Do not skip Phase 1 on a session that "was only config work"; config sessions are where signal 5 comes from.

For each, write one line: what happened, and what the lesson would be.

**Anti-signals — do not harvest these:**
- Something you did correctly (an instinct is an intervention, not a trophy)
- A restatement of an error message that already says what to do
- Project facts already in `CLAUDE.md`, a `.claude/rules/*.md`, or a skill
- "Be more careful" / "verify before asserting" in any phrasing — these already exist as always-on rules

## Phase 2 — Triage (no tools, cheap)

Kill candidates here before spending verification tokens. Each survivor must pass all three:

| Gate | Test | Fails when |
|---|---|---|
| **A. Counterfactual** | Would having had this instinct *changed what you did* this session? Name the specific action it would have prevented or redirected. | You can only say "it would have been good to know" |
| **B. Recurrence** | Will the trigger fire again? Name the recurring context — a stack, a repo, a workflow step. | The condition was specific to this one task |
| **C. Non-derivable** | Could you get this in 30 seconds at the moment of need — from the error message, the types, the code, or (for harness lessons) the hook output and config file in front of you? | The failure is self-explaining once it happens |

Apply gate C fairly to harness lessons: a hook's rejection message usually says *what* it blocked, not *why*, *which allowlist governs it*, or *what gets the work through*. "The error told me something" is not the same as "the error told me the lesson".

### Gate D — existing-coverage sweep

Run this **only on candidates that already passed A, B and C** — it is the one place Phase 2 touches tools, and gating it behind the free checks keeps triage cheap.

One batched grep over everything that could already own the lesson, not the corpus alone:

```bash
grep -rl -i -E "<distinctive phrase>|<error text>|<api name>" \
  ~/.claude/homunculus/instincts/personal/ \
  ~/.claude/rules/ ~/.claude/skills/ ~/.claude/agents/
```

> **2026-07-30 — removed a dead search path from this gate.** This grep also listed
> `~/.claude/homunculus/inherited/`, which **has never existed**. The sole corpus is
> `instincts/personal/`. A duplicate check is exactly the gate that must not under-search, and an
> empty glob does not error — it matches nothing and greps exit 0. Not theoretical: a run on
> 2026-07-30 spawned two refuters that both reported sweeping `personal/ + inherited/` and both
> returned the `inherited/` leg **clean**. One withdrew the claim when challenged.
> A dead path in a search is indistinguishable from a search that found nothing.
> The path is gone, not stubbed — do not reinstate it. If an `inherited/` concept is ever
> wanted, create the directory AND say what belongs in it in the same change.
> (2026-07-31, Phase 3: `commands/instinct-status.md` and the empty untracked
> `homunculus/instincts/inherited/` directory it referenced were both removed — the same
> decision, applied.)

Measured on a full run: this surfaced the exact killing file for **6 of 9 survivors in under a second**, where the Phase 3 refuters cost ~126k tokens to reach the same conclusions. Five of those six hits were *outside* the corpus — a rules file, an agent definition, and a skill this same session had edited. Grepping only `homunculus/` would have caught one.

Then judge, per hit:

- **Verbatim or near-verbatim coverage** → drop here, cite `file:line` in "Considered and dropped", spawn no verifier.
- **Partial overlap** → carry to Phase 3, and hand the agent the path. A refuter that starts from the existing text argues about whether coverage is *sufficient*, instead of spending its budget rediscovering that coverage exists.
- **No hit** → carry to Phase 3 unseeded.

**Open every file that matched.** A match count is a reason to read, never a substitute for reading — grep hits on a shared keyword, not a shared meaning, and dropping a genuinely new candidate because a word collided is the one way this gate makes things worse.

Watch for the commonest hit of all: **a skill or rule edited earlier in this same session.** A lesson banked at the right altitude an hour ago is not a second lesson now.

State the gate result explicitly per candidate. A candidate failing any gate is dropped here and appears in the final output only under "Considered and dropped".

## Phase 3 — Verify (parallel sub-agents, one per survivor)

**Classify first, then route.** Candidates need different evidence, and running every check on every candidate wastes tokens on irrelevant lookups. Tag each survivor:

| Class | Claim is about | Checks that apply |
|---|---|---|
| `LIBRARY` | a library, framework, SDK, API, CLI behavior | (a) (b) (c) (d) |
| `REPO` | this codebase — a schema, a file, a callsite, an RLS policy | (a) (b) (d) — skip (c) |
| `WORKFLOW` | the user's preferences, git habits, process | (a) (d) — skip (b) unless a config file can confirm it |
| `HARNESS` | Claude Code itself — hooks, permissions, classifier denials, settings.json, skills vs commands vs workflows, tool quirks | (a) (b) (d) — see below |

The class is a **primary routing label, not an exclusive type**. Apply checks based on every
claim the candidate makes. In particular, any claim about a library, framework, SDK, API, or
CLI requires check (c), even when its primary class is `HARNESS`, `REPO`, or `WORKFLOW`. For
example, a Codex hook observation is primarily `HARNESS`, but a claim about what the Codex CLI
supports still requires current Codex documentation.

`HARNESS` claims are **verifiable, and must be verified** — never wave them through as "just how the tool behaves". They are testable more directly than most code claims:
- Extract the hook's command out of `settings.json` and pipe a synthetic payload into it; assert the exit code and output for both a should-block and a should-pass input
- Read the matcher and the allowlist regex directly rather than inferring policy from one rejection message
- `ls`/`grep` the file that proves the mechanism (which hooks are registered, whether a script is referenced at all)

Record the probe command in `reality_citation`. A `HARNESS` candidate with no probe is unverified, same as any other.

**Verifiers are refuters, not supporters.** Spawn one **read-only** sub-agent per survivor (an `Explore`-type agent, or state explicitly that it must not use Write/Edit — nothing is written before the user picks). Brief each one to *kill* its candidate, not to confirm it:

> Try to refute this candidate. Find the existing instinct that already covers it, the code that contradicts it, or the doc that shows it is standard documented behavior. **Default to DROP when uncertain.** A candidate survives only if you tried to kill it and failed.

This framing is deliberate: an agent asked to "verify" a lesson from its own session will find support for it. With no cap on saves, refutation is the only thing keeping the corpus from growing every session.

**Bound the verification.** A verifier stops as soon as every applicable check has a definite
outcome; it does not keep searching for stronger support. One duplicate sweep, one focused
reality probe, and the required documentation lookup are normally enough. If a tool call
stalls, the same lookup fails repeatedly, or a required fact cannot be established, return
`UNVERIFIED` with the evidence gathered so far. Do not wait indefinitely or broaden into an
open-ended research task.

Give each agent the candidate's claim, its class, the relevant file paths, and the checks below. Require the verdict back as structured output against this shape:

```
{ id, class, verdict: SAVE|DROP|BUMP|MERGE|ELSEWHERE|UNVERIFIED,
  dup: NEW|EXACT <id>|PARTIAL <id>,
  confidence_action: BUMP|UNCHANGED,
  reality_citation: "<file:line or exact command + relevant output>" | null,
  docs_citation: "<ctx7 ref + version>" | null,
  home: INSTINCT|"<target path>",
  kill_attempt: "<what you tried in order to refute it>" }
```

`verdict` says what to do with the lesson; `dup` says how it relates to the existing corpus;
`confidence_action` independently records whether this session is a fresh confirmation of an
existing lesson. They may combine: a candidate can `MERGE` new trigger coverage into an
existing file **and** return `confidence_action: BUMP`. `SAVE` starts a new instinct at 0.3;
`BUMP` adds 0.1 to an existing one; `UNCHANGED` leaves an existing instinct's confidence and
evidence count alone. A `SAVE` still creates its initial evidence entry with `evidence_count: 1`.

Only these combinations are valid:

| `verdict` | Allowed `dup` | Allowed `confidence_action` | Meaning |
|---|---|---|---|
| `SAVE` | `NEW` | `UNCHANGED` | Create a new instinct at confidence 0.3. |
| `DROP` | any | `UNCHANGED` | Recommend no write. |
| `BUMP` | `EXACT <id>` | `BUMP` | Add independent confirming evidence to the exact existing lesson. |
| `MERGE` | `PARTIAL <id>` | `UNCHANGED` or `BUMP` | Extend partial coverage; bump only when the session independently confirms the lesson. |
| `ELSEWHERE` | any | `UNCHANGED` | Route the lesson outside the instinct corpus. |
| `UNVERIFIED` | any | `UNCHANGED` | Do not recommend a write until reality is established. |

Before Phase 4, validate the tuple against this table. Ask the verifier once to correct an
invalid combination; if it still does not return a valid tuple, treat it as `UNVERIFIED`.

**A candidate with `reality_citation: null` may not return `SAVE`.** Return `UNVERIFIED` and say what stopped the probe; the orchestrator runs it before ranking. An agent that could not confirm a claim will often report that honestly in prose and still mark it `SAVE` — measured once: a verifier wrote "could not reproduce directly — this is a real gap, not a pass" and returned `SAVE` anyway, on a claim the orchestrator's own probe then refuted twice. Honesty in the narrative is not a control; the verdict field is.

Also treat a blocked tool call as a fact to check, not a cause to report. The same verifier attributed its failure to "the Bash permission system denied the command as a CONFIG WRITE VIA SHELL" — that hook emits `additionalContext` only and cannot deny anything. Read the hook before naming it.

**(a) Duplicate check** — gate D already swept for obvious coverage and may have handed you a path to start from; go deeper than a keyword match. Grep `~/.claude/homunculus/instincts/personal/` — the sole corpus — for the candidate's key terms, and for the domain, including synonyms gate D would not have guessed. (This line named a second path, `inherited/`, that has never existed; see the note under Phase 2's grep.) Return one of:
- `NEW` — no existing instinct covers this
- `EXACT <id>` — an existing instinct already covers the same lesson
- `PARTIAL <id>` — an existing instinct covers only part of the lesson

**(b) Reality check** — is the claim actually true *on this branch, right now*? Open the file,
run the grep, count the callsites. Return an exact `file:line` citation or the complete command
and relevant output that prove it. Commands must be copy-paste reproducible: no `...`, omitted
arguments, placeholders, or prose standing in for the probe. Include the runtime or tool version
when behavior may vary by version. A claim that cannot be cited this way is not verified — say
so rather than inventing support, and return `UNVERIFIED` rather than `SAVE`.

A candidate harvested from the session narrative carries a *causal* claim ("X failed because Y") that was usually inferred from one error message and never tested. Reproduce it in a scratch repo before believing it. Measured once: "a staged file blocks `git stash push -- <paths>`" was inferred from a single `not uptodate` error, and two clean-room probes contradicted it — the mechanism was never established, and the instinct would have been confidently wrong.

**(c) Externality check** — if the claim is about a library, framework, SDK, API, or CLI, verify against docs with `npx ctx7@latest library <name> "<question>"` then `npx ctx7@latest docs <id> "<question>"`. Never answer from training memory. The outcomes are not equivalent:
- Docs **contradict** the claim → `DROP`. The session lesson was wrong; report what the docs actually say.
- Docs **confirm** it and the behavior is counterintuitive → `SAVE`, with the doc citation and the version it was verified against.
- Docs **state it plainly** and it is discoverable at the moment of need → `DROP`. "Read the docs" is not an instinct.
- Behavior is **version-specific** → `SAVE`, pin the version in frontmatter and note what would invalidate it.
- Not a library claim → `N/A`, skip to (d).

**(d) Home check** — is an instinct the right artifact at all? An instinct fires from a keyword-grep hook at session start. Alternatives, and when they win:
- **CLAUDE.md / path-scoped rule** — always-relevant project invariant, must be in context every time, not only when keywords match
- **An existing skill's Gotchas section** — the strongest default when a skill already covers this territory. A gotcha listed inside the skill that owns the task is read exactly when it is relevant; a standalone instinct has to win a keyword-match lottery to be seen at all. Prefer this whenever a matching skill exists.
- **An existing skill** — the lesson belongs in a procedure someone already follows
- **A hook or settings change** — the behavior should be enforced mechanically, not remembered

**Fixing the config does not consume the lesson.** If this session changed a hook, an allowlist, or a setting, ask separately: *did I also learn something durable about how the harness behaves?* The fix stops that one blockage; the instinct is what stops the next session losing an hour to the same class of surprise. Both can be true, and routing a lesson to "I already fixed it" is the most common way a `HARNESS` candidate gets wrongly dropped.
- **An instinct** — situational, fires on a recognizable trigger, useless the other 95% of the time

Return `INSTINCT` or the better home with a specific target path.

## Phase 4 — Recommend

Present survivors **ranked by expected value** (how often the trigger fires × how much pain it prevents), highest first. One block each:

```
N. [id] — VERDICT: SAVE | BUMP <id> to 0.X | MERGE into <id> [and BUMP to 0.X] | ELSEWHERE → <path>
   trigger: "when <condition, containing the literal terms that will appear in future work>"
   action:  <what to do>
   In plain terms: <one sentence, no jargon — what this means and why it matters>
   Evidence:  reality <file:line or exact command + relevant output> | docs <ctx7 citation + version, or N/A>
   Gates:     counterfactual <the action it would have changed> · recurrence <the context> · non-derivable <why>
```

Then a short **Considered and dropped** list — candidate, and the gate it failed, in a few words. This is not padding: it shows the gates ran, and stops the same weak candidate being re-proposed next session.

Finally, state your own recommendation on which to actually save. The user should not have to decide unguided.

**Write nothing to disk until the user picks.**

## Writing bar

Retrieval is a manual `grep -l -i` across the corpus, run by the model once the topic is known and searched by *symptom* — the literal error text, exit code, or phrase just seen. Nothing ranks or pre-selects for you (`~/.claude/hooks/surface-instincts.sh` names no files). Design for that:

- The `trigger:` line **must contain the literal strings that will appear in future work** — error text, filenames, API names, table names. An abstractly-phrased trigger is unretrievable and therefore dead on arrival.
- Length is not quality. A 250-line instinct outranks nothing; it just matches more keywords by accident and crowds out precise ones. Keep it as short as the lesson allows. Start at a few lines and one gotcha; let it grow only when a later session hits a new edge case.
- One instinct = one lesson. If you are writing "also, related to this..." — that is a second instinct, or it is padding.
- Do not state the obvious. Claude can already read the codebase and the error message. An instinct earns its place by pushing against the default behavior — if it describes what would have happened anyway, it is noise.

## Instinct file format

**The format lives in the `instinct-format` skill — read it before writing.** It is the single
source of truth for frontmatter fields, the closed `domain` vocabulary, the body structure, and
the evidence-archive rule. Do not write an instinct from memory of this skill; the spec has
changed under it before.

- [`~/.claude/skills/instinct-format/SKILL.md`](../instinct-format/SKILL.md) — the file contract
- `../instinct-format/MERGING.md` — when a candidate should join an existing file instead of becoming a new one
- `../instinct-format/CONVERTING.md` — rules and completion criterion for rewriting an existing file

Two points that bear directly on what this skill produces:

- **A candidate under ~35 lines is a MERGE candidate, not a new file.** A `SAVE` verdict on a
  short lesson usually means "add it to the topic document that already owns this territory".
  Check before creating a new id.
- **`trigger:` must never wrap mid-phrase.** Retrieval is line-based grep, so a phrase broken
  across two YAML lines is unreachable. Break only at a `|`.

New instincts start at confidence 0.3. A `BUMP` adds 0.1 (cap 0.9), increments `evidence_count`,
and appends the new evidence — it does not rewrite the existing action unless this session proved
that action wrong.

### Trigger-collision check — warn-only, run it before you write

Immediately before writing a new file, grep the candidate's **own `trigger:` strings** — the
literal phrases you just composed — against the corpus. This is a narrower question than
**Gate D**, which swept *lesson content* at triage: here you ask whether this trigger line will
pull a file that already exists.

```bash
grep -rn -i -F -e "<candidate trigger phrase>" -e "<second phrase>" \
  ~/.claude/homunculus/instincts/personal/
```

One phrase per `-e`, fixed-string (`-F`) — a future session greps these strings exactly as
written, so search them the same way.

- **No hit** — mint the new file.
- **Hit** — name the colliding file to the user with the matching line, and offer the
  evidence-append instead: a `BUMP` or `MERGE` onto that file rather than a new id. Two files
  answering one grep is the retrieval failure this check exists to catch; the future session
  reads whichever surfaces first and never learns the other exists.

**This check never refuses a capture.** It reports and recommends; the user decides, as
everywhere else in this skill. If they choose the new file after seeing the collision, write it.

Write instinct files to `~/.claude/homunculus/instincts/personal/`.

## Definition of done

- Every harvested candidate has an explicit gate outcome and every survivor has a refutation attempt.
- Every SAVE/BUMP/MERGE recommendation cites current reality; library claims also cite current docs.
- The recommendation states the target artifact and literal retrieval trigger.
- Dropped candidates are named with their failing gate.
- Nothing is written until the user selects a recommendation; after selection, the result passes
  the `instinct-format` contract and records the new evidence.
- The capture is **committed** to the local `~/.claude` repo — both the corpus
  (`homunculus/instincts/personal/`) and its bookkeeping
  (`homunculus/instincts/personal/.distill/`), staged by explicit path. An uncommitted capture is
  not done: it survives only in the working tree, where the next session's sweep counts it as
  saved while a stray checkout drops it. Local repo only — this repo has no remote, and
  `git push` has nothing to push to.

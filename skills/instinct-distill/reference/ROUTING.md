# Routing

Where a qualifying cluster goes. Read this **after** the cluster has passed
[`GATES.md`](GATES.md) — routing decides the shape of a promotion, never whether it is earned.

Decision #1: this ladder must cover **commands and agents**, not only skills. `/evolve`
proposed "skill, command, or agent" and gave no rule for choosing between them, which is why
its deletion (Phase 0 item 0.5) was blocked on this file existing.

---

## The ladder

Cheapest first. **Stop at the first rung that fits** — a lesson routed one rung too high costs
context on every session forever, and nothing ever demotes it.

| # | Surface | Costs | Choose when |
|---|---|---|---|
| 0 | **Fold** into a live skill/rule | nothing new | An existing artifact already covers the area |
| 1 | **New skill** `skills/<name>/SKILL.md` | lazy load | The lesson is knowledge needed when a describable trigger appears |
| 2 | **Skill + rule stub** `rules/<name>.md` | **every session, forever** | …and the trigger fires *before* anyone would load a skill (§ below) |
| 3 | **Command** `commands/<name>.md` | lazy, user-invoked | The lesson is a procedure a human runs at a moment they choose |
| 4 | **Agent** `agents/<name>.md` | lazy, own context | The lesson is a *role* that needs its own context window and tool restriction |
| 5 | **Hook proposal** (staged, never registered) | none until installed | The lesson is a deterministic check on a tool call, enforceable without model attention |
| 6 | **Project `CLAUDE.md`** | that repo only | The lesson is true of one repository, not of the practice |
| 7 | **Decline** | nothing | It qualified on recurrence but no surface fits |

Rung 7 is a real outcome, recorded in the ledger with its reason (#13). A cluster that recurs
but cannot be expressed as a trigger, a procedure, a role, or a check is a *reporting* result,
not a failure of the run.

**Write the artifact to its live path, not to a staging directory.** A skill is live only at
`~/.claude/skills/<name>/SKILL.md`; `~/.claude/homunculus/evolved/` is **not** on the
skill-discovery path, so a draft written there never activates and nothing reports that it
didn't. `/evolve` carried this warning because it had already cost three drafts left inert for
months, and the directory still holds an un-activated `agents/plan-reviewer.md` today. Use
`evolved/` only for staging you intend to promote by hand, and never as the end of a run.

---

## Rung 0 — fold is the default, and the gate is a named bug

Non-proliferation. Before proposing any *new* artifact, answer:

> **Name a concrete failure the new artifact prevents that folding into `<existing>` does not.**

Cannot name one → **fold**. This is the same devil's-advocate gate `rules/agents.md` applies to
refactor candidates, and it exists because "it deserves its own file" is always available and
never falsifiable.

A fold still ships: the lesson's text lands in the target's own voice, the sources are archived,
and the MANIFEST records the lineage. Rung 0 is not "do nothing".

---

## Rung 1 vs rung 2 — the only expensive decision on this ladder

Decision #5: **skill-only by default.** A rule is added only when the promotion names *why
auto-invocation is too late or unreliable*. "It's important" is not that reason — every lesson
in the corpus is important to someone.

A rule is warranted when the moment of relevance is **the moment of framing**: by the time a
model would recognise it should load a skill, it has already formed the wrong opinion, or a
conflicting instruction is already in its context and a lazily-loaded file cannot rebut text
that arrived first.

The two clearest existing pairs earned it for *specific*, non-generalising reasons:

- `rules/search-tools.md` duplicates its skill because a plugin injects a conflicting always-on
  directive at SessionStart (`skills/search-and-research-routing/SKILL.md:37`). A skill loaded
  later cannot argue with a directive already in context.
- `rules/workflow.md`'s scope-discipline block duplicates
  `skills/plan-workflow-conventions/SKILL.md:22` because it must fire on **user pushback** —
  the one moment nobody thinks to load a skill.

Neither generalises to "every promotion gets a stub". The always-loaded budget has no headroom
and no eviction mechanism; a stub added today is paid for by every session for the life of the
config.

**Write the justification into the rule's own text**, not just the ledger. A stub whose reason
lives only in a transaction record is indistinguishable, six months later, from one added by
habit — and the reason is the only thing that would ever license removing it.

---

## Rung 3 — command vs skill

The discriminator is **who decides it is time**.

- Model decides, from a trigger it can recognise → **skill**
- Human decides, at a moment of their choosing → **command**

Capture (`/instinct-analyze`), status readouts, cleanup sweeps and anything destructive are
human-timed: there is no trigger text that should cause a model to start archiving files on its
own initiative.

**In this package, prefer a skill carrying `disable-model-invocation: true`** (#12). It is a
command in behaviour — only a human can start it — with a skill's directory layout, so it can
own `scripts/`, `reference/` and `fixtures/` beside it. A bare `commands/*.md` is the right
shape only for a procedure with no supporting files.

---

## Rung 4 — agent

An agent is not a place to put knowledge. Route here only when the lesson is about **how a
delegated worker should be constituted**: its tool set, its independence from the main context,
or the fact that the work must not see the main thread's conclusions.

Tells that a cluster is really an agent:

- The lesson is "get an independent opinion on X" — independence *is* the mechanism, and it
  cannot be had inside the context that formed the opinion.
- The work is a fan-out over many files where the value is the conclusion, not the reading.
- The correct tool set is *narrower* than the parent's, and that narrowing is the safety
  property (read-only review, no-write audit).

Two frontmatter facts, both of which have silently mis-shaped agents in this repo:

- `tools:` is a **comma-separated string** (`tools: Read, Grep, Glob`), never a YAML array. An
  array is silently ignored and the agent receives **all** tools — the safety property you
  routed here for, quietly absent (`rules/agents.md`).
- Hooks belong in agent frontmatter, not `settings.json`, for agent-scoped behaviour.

---

## Rung 5 — hooks are propose-only, permanently

Decision #9. A distillation may **write a hook file and the registration snippet**; it may
never edit `settings.json`.

A hook entry is arbitrary code executed on every matching tool call, in the session that
installed it. Auto-registration would let a distillation break the very run that produced it,
and this repo's own adapter records the running session as a serial resource for exactly that
reason. The proposal is staged, the human installs it, and the uninstall instructions ship with
it.

Route here only when the check is **deterministic on the tool call's own inputs**. A hook that
needs to know intent is a skill wearing a hook's clothes — `command-content-hook-matches-text-not-intent`
is that lesson, already in the corpus.

---

## Rung 6 — project `CLAUDE.md`

A lesson about one repository's schema, deploy target, or local convention is not a lesson about
the practice. It goes to that repo and stays there.

Test: rewrite the lesson with every project-specific noun removed. If nothing load-bearing
survives, it is rung 6.

---

## Citation and lineage (#4)

Every promotion, at every rung, records:

1. **Target path + section** — the live covering artifact, not a directory. `skills/foo/SKILL.md`
   is not a citation; `skills/foo/SKILL.md § Rung 1 vs rung 2` is.
2. **Source ids** — every instinct consumed, so coverage is checkable in both directions.
3. **MANIFEST lineage** in the archive dir, with restore commands **and every `[[link]]` the
   retirement leaves dangling**. A link into a retired id resolves to nothing the moment the file
   moves, and the MANIFEST is the only place that records what it used to point at.

**Verify before archiving.** `grep` a distinctive phrase from the lesson in the target and
confirm it is actually there. "The artifact covers it" is the single easiest claim to assert
without checking, and the source is gone by the time anyone notices it did not.

**Keep any instinct an always-on rule cites as its evidence source.** The rule is an index; the
instinct holds the evidence. Archiving it strands the rule's justification — `grep` `rules/*.md`
for the id before touching it.

**Archive, never delete**, into `homunculus/instincts/Archive/<YYYY-MM-DD>/`. Note the trap: the
doc-blocker hook is `Write`-scoped and its allowlist covers
`homunculus/instincts/personal/` — *not* the archive dir. Create the MANIFEST via Bash, or the
write is refused (`hooks/doc-blocker.js`; read the live `ALLOWED` list rather than trusting this
sentence).

**Never bulk-compact the survivors.** Instincts are grep-surfaced and cost nothing until
retrieved, so the lever is archiving overlap — not shortening bodies that no session loads.

---

## What this file replaces

`commands/evolve.md` clustered on "3+ per domain" and offered "skill, command, or agent" with no
discriminator, no fold-first default, no always-loaded budget, and no gate. Its five surviving
rulings — fold-before-new, `homunculus/evolved/` is not on the discovery path, keep-if-cited,
archive-don't-delete, never-bulk-compact — are carried above. It is deleted once this file
ships (Phase 0 item 0.5).

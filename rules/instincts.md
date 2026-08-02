# Instinct System

You have learned instincts from past sessions stored in `~/.claude/homunculus/instincts/personal/`. These encode hard-won lessons — patterns that previously caused bugs, wasted time, or surprised the user.

## When to consult instincts

**At session start:** `surface-instincts.sh` runs and tells you the corpus exists — a lesson count and search guidance, nothing more. It names no files and pre-selects nothing: it fires before you have spoken, so its only input is the directory, and ranking on that was removed 2026-07-27 rather than tuned (`hooks/surface-instincts.sh:3-27` explains why, and forbids re-adding a ranked list). Selection is yours, at the moment relevance is knowable.

**Before forming an opinion about a guard, hook, or permission denial** — not after. A hook blocked a Write, a classifier denied an edit, a tool behaved unexpectedly: grep the corpus *first*. This is the most-repeated failure in the whole system — `doc-blocker-hook-is-write-tool-scoped` is the corpus's most-evidenced lesson, and several of its entries are it being re-derived from scratch by someone who had already been told. The instinct only pays off at the moment of framing; consulted afterwards it just confirms a conclusion already announced.

**Before debugging:** Before proposing fixes for production issues, check instincts for the technology involved (e.g., `grep -li "cookie\|traefik\|coolify" ~/.claude/homunculus/instincts/personal/*.md`).

**Before speculative fixes:** The `verify-before-building` instinct (confidence 0.9) says: write a 5-minute probe test BEFORE implementing a full solution. Don't push speculative fixes to production — diagnose first.

## How to consult

```bash
# Find relevant instincts by keyword
grep -li "keyword" ~/.claude/homunculus/instincts/personal/*.md

# Read the most relevant ones
cat ~/.claude/homunculus/instincts/personal/<instinct-name>.md
```

Don't read all instincts — the session-start hook prints the live count, and it is in the hundreds; just read the ones matching the current task's keywords.

## Adding to the corpus

Capture is **manual**: run `/instinct-analyze`. There is no automatic capture — the Stop-hook that used to propose instincts inline was removed 2026-07-27 because it bypassed every verification gate, and the `continuous-learning-v2` skill (whose `observe.sh` was never registered) was fully uninstalled 2026-08-02. Nothing writes to the corpus unless you ask it to.

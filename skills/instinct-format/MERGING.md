# Merging instincts into a topic document

Read [`SKILL.md`](SKILL.md) first — the merged file follows the same format. This file holds
when to merge and how.

## Why merge exists

A corpus of 509 small files is a pile of **isolated fragments**. The maintenance cycle for
agent memory is to consolidate: select non-overlapping groups of small, topically similar
documents and rewrite each group into one normalized **topic document** that serves as a
semantic unit.

Merging is the operation that actually reduces the corpus, and unlike deletion it costs no
lesson. Three files saying "a sub-agent's report is not evidence" retrieve worse than one file
saying it well: a symptom search hits three partial answers and the reader picks one.

## When a file merges instead of converting

- **Under ~35 lines.** Below the format's floor, solo conversion pads it toward structure
  without making it easier to find.
- **Shares a symptom with a sibling.** Two files a searcher would want to read together are
  one file.
- **Cross-links a sibling** with `[[id]]` and adds little the sibling lacks.

A file over the floor that stands alone converts normally. Size alone does not force a merge —
a short, sharp, unique lesson is fine as it is; it merges only when a genuine sibling exists.

## Finding a cluster

Work from symptoms, since that is how the corpus is read:

1. Take the candidate's most literal trigger string; `grep -l` it across the corpus.
2. Follow its `[[links]]` — an author already asserting a relationship is the strongest signal.
3. Check shared `domain` plus overlapping trigger vocabulary.

**Groups must be non-overlapping.** A file belongs to exactly one merge. Where a file could
join two clusters, either the clusters are one cluster or the file is genuinely distinct —
decide before writing, never merge the same file twice.

Three to five files is a workable group. Past that the topic document stops being one lesson.

## The merged file

`id` names the **topic**, not the loudest member: `subagent-reports-are-not-evidence`, not
`subagent-forgets-to-commit`.

`trigger` carries **the literal strings from every merged file.** This is the load-bearing
step. A searcher who would have found any one of the originals must find the merged file — if
`idle_notification` retrieved a file before the merge, it retrieves the topic document after,
or the merge has destroyed reach.

`confidence` takes the highest of the group. `evidence_count` is the sum of *actual entries*,
which the old field frequently misstates — count them. `created` is the earliest.

The body carries every distinct lesson. Where members disagree, the disagreement is content:
say which holds when, rather than silently picking one.

## Evidence

One archive, `<topic-id>.evidence.md`, holding every entry from every member verbatim, newest
first, each tagged with the id it came from so provenance survives:

```markdown
- **2026-07-19** *(from subagent-forgets-to-commit)* — …
```

## Scripts

Two helpers live in [`scripts/`](scripts/):

- `subfloor.py` — lists every sub-floor file grouped by domain. This is the merge worklist;
  run it at the start of a batch, since the previous batch changed it.
- `dangling_links.py` — finds `[[links]]` that no longer resolve. **Run it as part of
  retiring, not after several batches.** Merging removes ids and surviving files link to
  them, so each batch silently breaks inbound links. Repoint every link whose target was
  retired at the topic document that absorbed it; links that never resolved (skill names,
  project-memory names, literal `[[ … ]]` syntax) are pre-existing and should be left alone.

## Retiring the originals

Originals move to `~/.claude/homunculus/instincts/Archive/<YYYY-MM-DD>/` with a `MANIFEST.md`
naming the topic document that absorbed each — the convention prior prunes already use. They
are not deleted; a merge that loses a lesson is recoverable only if the source still exists.

## Completion criterion

**Every claim, caveat, cross-reference, checklist row and evidence entry from every member
resolves to a location in the topic document or its archive — and every literal trigger string
from every member appears in the merged `trigger`.**

Verify the second half by running it: for each original, take the **complete phrase** from its
old trigger — not a short fragment of it — and `grep -l` it. Each must return the topic
document.

The full phrase matters because the common failure is a trigger wrapped mid-phrase across two
YAML lines, which `grep` can never match. A fragment short enough to fit on one line passes
while the phrase a searcher would actually type fails. See the `trigger` section of
[`SKILL.md`](SKILL.md).

**Exclude the archives with `--exclude`, never with a bare `grep -v evidence`:**

```bash
grep -l "<string>" --exclude='*.evidence.md' *.md
```

A substring filter on the word `evidence` also removes any topic document whose *id* contains
it — `subagent-reports-are-not-evidence.md` is exactly that file, and it is this document's own
worked example. When that happened, all six checks reported failure on a merge that was
correct, and the obvious repair — adding trigger strings that were already present — would have
corrupted a good file while turning the checks green. `--exclude` keeps the archives out of the
result set instead of filtering them out afterwards, which removes the class of error rather
than one instance of it.

A verification that can exclude its own subject can report a false pass just as easily as a
false failure. Prefer the form that cannot.

## Worked example

Three files, one lesson — *a sub-agent's own report is not evidence, check git*:

| file | lines |
|---|---|
| `subagent-forgets-to-commit` | 19 |
| `subagent-idle-is-not-report-delivered` | 77 |
| `agent-notification-can-lag-check-git-as-source-of-truth` | 55 |

→ `subagent-reports-are-not-evidence.md` + one archive. The trigger carries
`idle_notification`, `idleReason available`, "completed", and the commit-verification strings,
so all three old retrieval paths still land.

---
name: instinct-prune
description: Retire stale instincts from the corpus — bucket, shortlist, human-verify, then transactional archive. Run by a human on demand — never model-invoked. The hygiene half of the pair whose other half is instinct-distill.
disable-model-invocation: true
---

# instinct-prune

Distill **merges by content** (these five files are one lesson). Prune **retires by
staleness** (this file no longer describes reality, or is already carried elsewhere).
`instinct-format` owns the file contract; the shared ledger (`instinct-distill/scripts/ledger.py`)
owns the transaction protocol.

**Prune never decides alone.** Every gate emits a *candidate flag*; a human or a reviewing
agent rules on each candidate. This is not ceremony — gate 2's first run scored 56 files
ALL_DEAD, every one of them wrong (correct lessons about other checkouts).

**A human starts this run.** Nothing here is auto-invoked: Phase D archives files, rewrites
citations and advances the shared watermark.

The authoritative spec is `docs/superpowers/plans/2026-07-30-instinct-prune-spec-draft.md`
(§6b settled in full). This file is the run procedure, not the reasoning.

---

## The run

```
A bucket (read-only) → B shortlist (read-only) → C verify (human, per candidate) → D retire (transactional)
```

### Phase A — bucket

```bash
bash ~/.claude/skills/instinct-prune/scripts/instinct-buckets.sh          # summary
bash ~/.claude/skills/instinct-prune/scripts/instinct-buckets.sh --tsv    # machine-readable, input to Phase B
```

Read-only; writes nothing, moves nothing. Three gates per file: 0 FORMAT (converted to
lesson-first spec?), 1 PROMOTED (carried by an always-on rule? — single-file co-occurrence),
2 REPO-TRUE (do its cited paths still exist?). **Fails closed**: exit 3 (tmpdir) / exit 4
(row-count mismatch) are failed runs — never read an empty report as a clean corpus.

The script moved from `hooks/` into `scripts/` 2026-08-01 (Phase 4 scrub, first slice);
the dead `build_token_index` stub was deleted in the same move.

### Phase B — shortlist

```bash
bash ~/.claude/skills/instinct-prune/scripts/instinct-buckets.sh --tsv | python3 ~/.claude/skills/instinct-prune/scripts/shortlist.py
```

Ranked worklist, stated reason per row; touches no file. Ranking: **1** RULE_DUP+CONVERTED,
**2** ALL_DEAD, **3** RULE_NEAR+SOME_DEAD. Load-bearing exclusions (spec §3): claimed files
are invisible; UNVERIFIABLE/UNCITED/CITES_NONE never shortlist by a staleness rank (the first
two are location artifacts, not staleness evidence; `CITES_NONE` is a file that DECLARED
`cites: []` — path-independent by authorial statement) — but those gate-2 exclusions do NOT apply to rank 1,
which is a content judgment. Fail-closed: empty/malformed TSV is exit 3, never an empty
shortlist.

**Rank-1 retirements are additionally blocked on ruling R3 (evidence lineage)**: a RULE_DUP
file is a candidate only when the rule carries the lesson AND the instinct holds no evidence
the rule would lose. Phase C owns that comparison; the shortlist row carries
`evidence_count` so the reviewer sees what is at stake.

### Phase C — verify each candidate (the human loop)

One candidate at a time. **Retrieval is verified before retirement, never assumed.**

Per candidate:

1. Extract every literal trigger string from the file.
2. Grep each one against the corpus *minus* this file.
3. If the covering artifact does not come back for a given string, **the merge is
   incomplete** — fix the artifact, or drop the candidate. Do not retire on the strength of
   the gate flag.
4. For `RULE_DUP`, confirm the rule text actually states the lesson. Four co-occurring title
   words is a duplicate *signal*; reading the rule is the verification.
   **Coverage alone does not retire a live lesson.** If the instinct is still ACCRUING
   (recent evidence, recent corrections), the corpus is where new evidence lands — the
   covering rule/skill has no accretion mechanism. Keep it: set `promoted_to:` to the
   covering artifact (the format's drift tracker) and record a DEFER "blocked on liveness —
   retire when it stops accruing". Ruled this way three times 2026-08-01/02 (green-guards,
   probe-reality, config-self-modification); KCS "reuse is review" is the outside precedent.
5. Only here, if freshness matters, does the gate-3-shaped docs check run — against a set
   already narrowed to a handful.
6. **Record the verdict** when the candidate stays live (DROP = flag is an artifact /
   coverage insufficient; DEFER = blocked on a named condition):

   ```bash
   python3 ~/.claude/skills/instinct-prune/scripts/ruling.py <id> --verdict DROP \
     --rank <n> --gate1 <flag> --why "<challengeable ruling>" [--covering <bare stem>]
   ```

   Shortlist subtracts it on future runs — **visibly**, one line per suppression — until
   any hashed subject changes (new evidence on the instinct, an edit to the covering
   rule/skill), which re-opens it for fresh review. An accepted retirement needs no
   ruling: retire.py removes the file from the corpus. Design:
   `docs/superpowers/plans/2026-08-01-ruling-ledger-design.md`; ledger:
   `personal/.distill/rulings.jsonl`. Human-initiated only, like everything here.

#### Phase C for a no-successor (`ALL_DEAD`) candidate

The retrieval check above cannot apply: there is no covering artifact that must "come back".
In its place, three checks:

1. **Re-run gate 2 fresh** on the file at verification time — the shortlist flag may be
   stale. `UNVERIFIABLE` is not dead: a path that will not resolve on *this* machine keeps
   the candidate **out** (§3's exclusion applies here too).
2. **A human rules the lesson describes a dead reality** — not a live lesson whose cited
   paths merely moved. Gate flags are candidate flags; this ruling is the verification.
3. **Enumerate the candidate's inbound links** (`grep -l '\[\[<id>\]\]' personal/*.md`) and
   record them in the MANIFEST. They are deliberately left in place; the recorded list is
   the hand-fix worklist.

**MANIFEST shape** — the ordinary format (spec §5) plus one extra section, which IS the
intent-marker:

```markdown
## → (no successor)

| retired file | lines | why retired |
|---|---|---|
| `usd-sdf-api-traps.md` | 78 | ALL_DEAD — every cited path gone; ruled stale 2026-… |

Inbound links deliberately left (fix by hand): `citer.md` → `[[usd-sdf-api-traps]]`
```

`dangling_links.py` parses that exact heading and reports such ids in a fourth bucket —
*"declared no-successor retirements — deliberate, fix by hand"* — separate from *"links to
RETIRED ids — repoint these"*. The marker is written at `MANIFEST_UPDATED`, so a crash
before that step leaves no marker and the links correctly read as debt: **the marker's
absence is the crash signal.** An `ALL_DEAD` retirement deliberately does not touch its
citers; restore is the same `mv` commands, and the citing links then resolve again on their
own.

### Phase D — retire

```bash
python3 ~/.claude/skills/instinct-prune/scripts/retire.py <id> --successor <rule> --why "<ruling>"      # dry-run
python3 ~/.claude/skills/instinct-prune/scripts/retire.py <id> --no-successor --why "<ruling>"          # dry-run, ALL_DEAD
# read the dry-run plan, then re-run with --apply
python3 ~/.claude/skills/instinct-prune/scripts/retire.py --resume <cluster_id> --apply     # after a crash
python3 ~/.claude/skills/instinct-prune/scripts/retire.py --rollback <cluster_id> --apply   # undo non-terminal
```

`--successor` takes the **bare rule stem or skill dir name** (`agents`, not
`rules/agents.md`) — the vocabulary `dangling_links.py` classifies as SUCCESSOR; retire.py
refuses anything else (exit 4).

**Dry-run first, always.** ARCHIVE-FIRST sequence on `PruneTransaction` (spec §5 — the
ordering is decided-by-dependency; do not re-argue it):

```
CLAIMED → ARCHIVED → CITATIONS_REPOINTED → MANIFEST_UPDATED → COMMITTING → COMMITTED
```

Intent is recorded before each effect, every effect is idempotent. A crash inside the
`COMMITTING` bracket is rolled **forward** by `recover()` + a no-argument commit; rollback
from `COMMITTING` refuses. Sources move to `homunculus/instincts/Archive/<YYYY-MM-DD>/`;
there is no delete path (spec §0.2).

Gate after each applied retirement:

```bash
python3 ~/.claude/skills/instinct-format/scripts/dangling_links.py
```

A `--no-successor` retiree must land in the **INTENT bucket** (declared no-successor), not
RETIRED — RETIRED means the marker never got written.

---

**Gates are CANDIDATE FLAGS, not verdicts.** Prune never decides alone.

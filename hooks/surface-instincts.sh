#!/bin/bash
# SessionStart: point at the instinct corpus. Do NOT pre-select from it.
#
# History (2026-07-27): this hook used to inject the top N instinct filenames,
# ranked by keyword hit-count for the current project directory. That was
# removed, not tuned, because the ranking could not work in principle:
#
#   - It fires BEFORE the user speaks, so its only input is the directory.
#     A directory prior is weak; the real signal arrives one message later.
#   - It counted keyword hits per file, so it rewarded LENGTH, not quality.
#     Measured: surfaced files averaged 98-100 lines against an 82-line corpus
#     mean, and sat at confidence 0.4-0.7 while every 0.9-confidence instinct
#     scored 0-3 matches and could never place.
#   - A wrong guess is not neutral. Six Supabase/RLS filenames opened a session
#     that touched no database, priming the wrong frame.
#   - It demonstrably failed at its own job: it fired, and a 0.9-confidence
#     instinct with 10 evidence entries about the exact guard being tripped was
#     still re-derived from scratch for the third time.
#
# What survives is the only thing a pre-topic hook can honestly contribute:
# telling the model the corpus exists, and when to search it. Selection is
# deferred to the moment relevance is knowable — a grep once the topic is known.
# A filename-only symptom search costs ~9 tokens; a full read ~2,200. Cheap
# lookup first, expensive read only if the lookup earns it.
#
# Do not "improve" this by re-adding a ranked file list. That is the thing that
# was removed, and the reasons above are not fixed by better keywords.

INSTINCT_DIR="$HOME/.claude/homunculus/instincts/personal"

[ -d "$INSTINCT_DIR" ] || exit 0

# Count lessons only. Each instinct may carry a sibling <id>.evidence.md archive,
# which is not a lesson — counting those overstated the corpus by 65 (419 vs 354).
COUNT=$(ls "$INSTINCT_DIR"/*.md 2>/dev/null | grep -vc '\.evidence\.md$')
[ "$COUNT" -gt 0 ] 2>/dev/null || exit 0

cat <<EOF
Instincts: $COUNT past-session lessons in $INSTINCT_DIR

Search them BEFORE forming an opinion about a guard, a hook, a permission
denial, or a root cause — not after. Searching once a theory is stated only
confirms it.

Search by SYMPTOM, not by topic: the literal error text, exit code, or phrase
you just saw. \`grep -l "BLOCKED: Unnecessary documentation" *.md\` returns one
file; \`grep -l hook *.md\` returns 63 and buries it.

Capture is manual: /instinct-analyze. Nothing writes here on its own.
EOF

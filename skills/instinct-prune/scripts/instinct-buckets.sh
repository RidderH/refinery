#!/bin/bash
# /instinct-prune PHASE A — bucket the corpus with the three FREE gates.
#
# Read-only. Writes nothing, moves nothing. Prints counts + a TSV report so the
# gates can be calibrated before a single file is touched.
#
# Gate 3 (docs/ctx7) is deliberately absent: it is the expensive one, it is
# event-driven off version_pin, and it only ever runs on gate 0-2 survivors.
#
# Usage:  bash ~/.claude/skills/instinct-prune/scripts/instinct-buckets.sh          # summary
#         bash ~/.claude/skills/instinct-prune/scripts/instinct-buckets.sh --tsv    # per-file rows
set -uo pipefail

DIR="$HOME/.claude/homunculus/instincts/personal"
RULES="$HOME/.claude/rules"
SKILLS="$HOME/.claude/skills"
TSV=${1:-}

cd "$DIR" || exit 1

# ---------------------------------------------------------------- gate 0
# FINDABLE: can a symptom search single this file out?
#
# v1 inspected only the `trigger:` line and asked whether it "looked literal".
# That was measuring the wrong text — grep searches the FILENAME and the whole
# BODY. It flagged amend-after-push-use-force-with-lease as VAGUE when
# `grep -l force-with-lease` returns it first, the term being in its own name.
#
# v2 asks the question that actually matters: does this file contain at least
# one token DISTINCTIVE enough to retrieve it specifically? A term appearing in
# 200 files ("supabase", "test") cannot single anything out; a term appearing in
# <=3 ("force-with-lease", "dispatchEvent") can. Filename tokens count, since
# they are part of the searchable text.
# v2 asked whether the file contains a token rare enough to single it out. That
# returned FINDABLE for 510 of 510: rare tokens are the long tail of ordinary
# prose, so the test is trivially satisfied. v1 (does the trigger LOOK literal)
# was over-strict on text grep never reads; v2 is vacuous. Both failed because
# findability is not decidable without knowing the term a future searcher will
# actually type.
#
# So gate 0 no longer tries. Findability is fixed by CONSTRUCTION during
# conversion — the new format requires literal symptom strings in `trigger:` —
# rather than measured beforehand. What is measurable, and what this now
# reports, is conversion progress: does the file meet the new spec?
#
# A converted file has an `action:` line in frontmatter (the skim tier) and a
# `## Symptom` heading. Both are mechanical, unambiguous checks.
findable() {
  local has_action has_symptom
  grep -q '^action:' "$1" && has_action=1 || has_action=0
  grep -q '^## Symptom' "$1" && has_symptom=1 || has_symptom=0
  if [ "$has_action" -eq 1 ] && [ "$has_symptom" -eq 1 ]; then echo "CONVERTED"
  elif [ "$has_action" -eq 1 ] || [ "$has_symptom" -eq 1 ]; then echo "PARTIAL"
  else echo "OLD_FORMAT"; fi
}

# ---------------------------------------------------------------- gate 1
# PROMOTED: is this lesson already carried by an always-on rule or a skill?
# Uses the instinct's TITLE words as the probe — a title is the most compressed
# statement of the lesson we have. 4+ distinctive words co-occurring in a rule
# is a strong duplicate signal; it is a candidate flag, not a verdict.
promoted() {
  local title words hits
  title=$(grep -m1 '^# ' "$1" | sed 's/^# //')
  [ -z "$title" ] && { echo "-"; return; }
  words=$(echo "$title" | tr 'A-Z' 'a-z' | tr -cs 'a-z' '\n' \
          | grep -vE '^(the|a|an|is|are|not|and|or|of|to|in|on|for|when|it|its|by|be|with|at|as|from|that|this|do|does|but|if)$' \
          | awk 'length > 4' | sort -u | head -6)
  [ -z "$words" ] && { echo "-"; return; }
  # CO-OCCURRENCE in a single covering file (spec §6b-8, settled 2026-07-31).
  # The previous code greped each word independently across ALL rule files, so a
  # title scattered over four different rules scored as RULE_DUP — 14 of its 19
  # flags had no single rule containing every probe word. "Carried by a rule"
  # means ONE file carries the lesson; score each file separately, take the best.
  #
  # 2026-08-01: skills/*/SKILL.md joined the covering corpus — the first real
  # successor-mode retirement candidate was carried by a SKILL and gate 1 never
  # flagged it (it only read rules/). One `grep -li` per probe word across both
  # corpora (6 greps/lesson, not 6 x N files); uniq -c per file = words carried.
  # On a tie, a rule wins over a skill: rules are always-on, skills trigger-load.
  local total; total=$(echo "$words" | wc -l | tr -d ' ')
  [ "$total" -lt 4 ] && { echo "-"; return; }
  local hits top bestline bestfile
  # The instinct system's own skills (instinct-*) are excluded from the
  # covering corpus: they are built FROM these lessons and quote the corpus's
  # philosophy verbatim, so they match their own reflection — 4 of the first
  # wave's 10 SKILL_DUP flags were this shape (e.g. dead-code-signals matching
  # instinct-prune's "candidate flags, not verdicts"). Circular coverage is
  # not coverage.
  hits=$(while read -r w; do
           [ -z "$w" ] && continue
           grep -li -- "$w" "$RULES"/*.md "$SKILLS"/*/SKILL.md 2>/dev/null
         done <<< "$words" | grep -v '/skills/instinct-' | sort | uniq -c | sort -rn)
  [ -z "$hits" ] && { echo "-"; return; }
  top=$(echo "$hits" | head -1 | awk '{print $1}')
  bestline=$(echo "$hits" | awk -v c="$top" '$1 == c && $2 ~ /\/rules\//{print; exit}')
  [ -z "$bestline" ] && bestline=$(echo "$hits" | head -1)
  bestfile=$(echo "$bestline" | awk '{print $2}')
  local kind=RULE
  case "$bestfile" in "$SKILLS"/*) kind=SKILL;; esac
  if [ "$top" -ge "$total" ]; then echo "${kind}_DUP"
  elif [ "$top" -ge $((total - 1)) ]; then echo "${kind}_NEAR"
  else echo "-"; fi
}

# ---------------------------------------------------------------- gate 2
# REPO-TRUE: does the file cite a path that no longer exists? Only files that
# make a citation can fail this; a file citing nothing is UNCITED, which is a
# quality signal (unverifiable) rather than a staleness one.
#
# TWO PATHS, chosen by the file itself (design:
# docs/superpowers/plans/2026-08-01-cites-frontmatter-design.md):
#   * `cites:` present  -> DECLARED. The author named the paths this lesson's
#     liveness depends on; the body is not read at all. `cites: []` is a
#     statement ("deliberately path-independent") and reports CITES_NONE.
#   * `cites:` absent   -> INFERRED. Today's regex over the whole body, all
#     current labels, unchanged. Presence of the field IS the migration marker;
#     no schema_version bump (that number belongs to the evidence schema, which
#     migrates on a different schedule).
# Inference has two failure shapes a regex can never fix, because it cannot read
# intent: example paths (`a path like src/foo.ts`) and paths quoted from repos
# that are not on this machine. Both resurface on every shortlist run. Declaring
# removes them by construction rather than by pattern.
# Roots to resolve a cited path against. The corpus spans every project the user
# has worked in, so checking only one repo scores every other project's citation
# as stale — that bug produced 56 bogus ALL_DEAD on the first run (a source path
# belonging to one project does not resolve under another project's root).
#
# Discovered, not hardcoded: one project lives at its own top-level directory,
# everything else under a shared parent directory of checkouts. A hardcoded name
# list silently rots as projects are added or
# renamed — the first version of this claimed three projects were missing when
# they were simply one directory away.
# Machine-specific values come from a gitignored config (template:
# local-projects.conf.example, same dir) so no client/project name ships in
# this script. Defaults below let the script run configless: discovery still
# works; only other-machine ABSENT protection and extra roots need the file.
LP_CONF="${LOCAL_PROJECTS_CONF:-$HOME/.claude/homunculus/local-projects.conf}"
PROJECT_PARENTS="$HOME/Code_projects"
EXTRA_ROOTS=""
ABSENT_NAMES=""
PATH_PREFIXES=""
if [ -f "$LP_CONF" ]; then
  # Refuse a world-writable config: it is sourced, i.e. executed.
  case "$(stat -f '%Lp' "$LP_CONF" 2>/dev/null || stat -c '%a' "$LP_CONF" 2>/dev/null)" in
    *[2367]) echo "FATAL: $LP_CONF is world-writable — refusing to source it" >&2; exit 3;;
  esac
  # shellcheck disable=SC1090
  . "$LP_CONF"
fi
ROOTS="$EXTRA_ROOTS $HOME/.claude $HOME"
# Gate-2 cited-path alternation: generic first segments, plus per-machine
# PATH_PREFIXES. `skills` must stay ahead of `scripts`-bearing tails (see the
# prefix-truncation note at the grep site).
PREFIX_ALT='src|supabase|docs|\.claude|scripts|skills'
for pfx in $PATH_PREFIXES; do PREFIX_ALT="$PREFIX_ALT|$pfx"; done
# Two levels deep: sub-projects live inside projects (several checkouts sit one
# directory below a single parent project). A depth-1 scan reports
# them missing and their instincts become falsely UNVERIFIABLE. Depth-2 entries
# are only added when they look like a real checkout (.git / package.json), so
# asset and report folders do not inflate the root list.
for parent in $PROJECT_PARENTS; do
  [ -d "$parent" ] || continue
  ROOTS="$ROOTS $parent"
  for proj in "$parent"/*/; do
    [ -d "$proj" ] || continue
    ROOTS="$ROOTS ${proj%/}"
    for sub in "$proj"*/; do
      [ -d "$sub" ] || continue
      if [ -e "${sub}.git" ] || [ -e "${sub}package.json" ]; then
        ROOTS="$ROOTS ${sub%/}"
      fi
    done
  done
done

# Declared `cites:` entries are PROJECT-QUALIFIED (first segment is the on-disk
# project dir name, or `.claude`), like `promoted_to`. A discovered root is
# often the project dir itself, so `<root>/<entry>` would double the project
# name; resolving against each root's PARENT too makes the qualified form land.
# No new resolver — the same discovered ROOTS, one extra base each.
ROOT_PARENTS=""
for r in $ROOTS; do
  rp=$(dirname "$r")
  case " $ROOTS $ROOT_PARENTS " in *" $rp "*) ;; *) ROOT_PARENTS="$ROOT_PARENTS $rp";; esac
done

# A project the corpus talks about but that is NOT on this machine cannot be
# judged here: an unresolvable path is absence of evidence, not evidence of
# staleness. Those files report UNVERIFIABLE, never ALL_DEAD, so prune can never
# delete a correct lesson about a repo that lives on another disk.
#
# Derived by testing each candidate name against the discovered roots above,
# rather than assuming. Names come from ABSENT_NAMES in the config — add one
# only as the corpus starts citing it. Configless default is empty: gate 2
# then cannot distinguish "on another disk" from "gone", so other-machine
# lessons flag ALL_DEAD — gates are candidate flags, not verdicts, but
# multi-project users should set ABSENT_NAMES to configure the noise away.
ABSENT_RE=""
for proj_name in $ABSENT_NAMES; do
  # Substring match, not equality: the on-disk directory is often a longer form
  # of the name the corpus uses (corpus says "legacy-checkout", disk has
  # "legacy-checkout-verification"). Exact matching declared it absent.
  found=0
  lc_name=$(echo "$proj_name" | tr 'A-Z' 'a-z')
  for r in $ROOTS; do
    case "$(basename "$r" | tr 'A-Z' 'a-z')" in *"$lc_name"*) found=1; break;; esac
  done
  [ "$found" -eq 0 ] && ABSENT_RE="${ABSENT_RE:+$ABSENT_RE|}$proj_name"
done

# Frontmatter only: the leading `---` block. A body line starting with `cites:`
# must not be mistaken for the field, and prose is explicitly out of scope for
# the declared branch.
frontmatter() { awk 'NR==1 && $0=="---"{fm=1;next} fm && $0=="---"{exit} fm' "$1"; }

has_cites() { frontmatter "$1" | grep -q '^cites:'; }

# Prints one declared entry per line; prints nothing for `cites: []`. Block
# sequence form only (`cites:` then `  - "path"`), matching the design's
# examples — an inline non-empty list is not the contract.
cites_decl() {
  frontmatter "$1" | awk '
    /^cites:[[:space:]]*\[[[:space:]]*\][[:space:]]*$/ { exit }
    /^cites:/ { inc=1; next }
    inc && /^[[:space:]]+-[[:space:]]/ {
      line = $0
      sub(/^[[:space:]]+-[[:space:]]+/, "", line)
      sub(/[[:space:]]+$/, "", line)
      gsub(/^"|"$/, "", line)
      gsub(/\047/, "", line)
      print line
      next
    }
    inc && /^[^[:space:]]/ { inc = 0 }
  '
}

repo_true() {
  if has_cites "$1"; then repo_true_declared "$1"; else repo_true_inferred "$1"; fi
}

# DECLARED branch. A dead entry here is a strong signal, not an artifact: a human
# named it load-bearing and it is gone. The ~8 cap matches the inferred branch's
# `head -8` — more than that means the lesson cites a subsystem, not files.
repo_true_declared() {
  local paths dead=0 total=0 p r found
  paths=$(cites_decl "$1" | head -8)
  [ -z "$paths" ] && { echo "CITES_NONE"; return; }
  while read -r p; do
    [ -z "$p" ] && continue
    total=$((total+1))
    found=0
    for r in $ROOTS $ROOT_PARENTS; do
      if [ -e "$r/$p" ]; then found=1; break; fi
    done
    [ -e "$p" ] && found=1
    if [ "$found" -eq 0 ]; then dead=$((dead+1)); fi
  done <<< "$paths"
  if [ "$dead" -eq 0 ]; then echo "CITES_OK"; return; fi
  # Rule 4 (never declare a path that cannot resolve here) should make this
  # unreachable, but the guard stays: fail toward not-deleting.
  if [ -n "$ABSENT_RE" ] && grep -qiE "$ABSENT_RE" "$1"; then echo "UNVERIFIABLE"; return; fi
  if [ "$dead" -eq "$total" ]; then echo "ALL_DEAD"; else echo "SOME_DEAD"; fi
}

repo_true_inferred() {
  local paths dead=0 total=0 p r found
  # Extension class allows 2-16 chars incl digits/hyphens: the old {2,4} cap
  # truncated archive-suffix citations (docs/archive/CLAUDE.md.autoforge ->
  # …CLAUDE.md.auto), manufacturing a false ALL_DEAD on a path that exists.
  # `skills` must precede `scripts`-bearing tails in the alternation: without it,
  # skills/instinct-distill/scripts/recall_test.py was captured from `scripts/`
  # onward — a live path truncated into a false ALL_DEAD (prefix-side twin of
  # the {2,4} extension truncation fixed earlier on this same line).
  # First-segment alternation: generic prefixes plus per-machine PATH_PREFIXES
  # from the config (a project subdir name instincts cite paths under).
  paths=$(grep -oE "(${PREFIX_ALT})/[A-Za-z0-9_./-]+\.[a-z0-9-]{2,16}" "$1" 2>/dev/null | sort -u | head -8)
  [ -z "$paths" ] && { echo "UNCITED"; return; }
  while read -r p; do
    [ -z "$p" ] && continue
    total=$((total+1))
    found=0
    for r in $ROOTS; do
      if [ -e "$r/$p" ]; then found=1; break; fi
    done
    [ -e "$p" ] && found=1
    if [ "$found" -eq 0 ]; then dead=$((dead+1)); fi
  done <<< "$paths"
  # Resolution wins over the name heuristic: a file may name an absent project
  # yet cite paths that resolve elsewhere (projects get renamed — legacy-checkout
  # became Client_Portal). Only fall back to UNVERIFIABLE when paths actually
  # failed to resolve AND the file names a project that is not on this machine.
  if [ "$dead" -eq 0 ]; then echo "CITES_OK"; return; fi
  if [ -n "$ABSENT_RE" ] && grep -qiE "$ABSENT_RE" "$1"; then echo "UNVERIFIABLE"; return; fi
  if [ "$dead" -eq "$total" ]; then echo "ALL_DEAD"; else echo "SOME_DEAD"; fi
}

# ---------------------------------------------------------------- selftest
# Gate 2 only. Every case is a real file on disk scored by the real repo_true,
# against the real discovered ROOTS — a mocked resolver would prove nothing
# about the thing that actually mis-scores paths.
if [ "$TSV" = "--selftest" ]; then
  ST=$(mktemp -d "${TMPDIR:-/tmp}/instinct-buckets-st.XXXXXX") || { echo "FATAL: mktemp -d failed" >&2; exit 3; }
  trap 'rm -rf "$ST"' EXIT
  # Two path families, on purpose. LIVE/DEAD start with `.claude`, so the
  # INFERRED regex sees them too. LIVE_Q/DEAD_Q start with a segment outside
  # PREFIX_ALT, so only the declared branch can see them.
  #
  # That distinction is load-bearing, and it was found by probe: with declared
  # entries drawn from the `.claude` family, neutering the whole declared branch
  # left cites-ok/cites-dead/cites-mixed GREEN — the inferred regex was
  # re-deriving the same labels from the frontmatter text, so those checks
  # asserted nothing about the code they were written for. Keep the declared
  # fixtures on the _Q family or the neuter probe stops discriminating.
  LIVE=".claude/skills/instinct-prune/scripts/instinct-buckets.sh"
  DEAD=".claude/skills/instinct-prune/scripts/no-such-file-ever.sh"
  # LIVE_Q must resolve on ANY install, not just the author's. It named a
  # specific instinct file until the clean-room test caught it: 3 of these 8
  # checks failed on a virgin config, because that lesson only exists in one
  # person's corpus. The corpus DIRECTORY is the safe choice — this script
  # cd's into it at startup, so it is guaranteed to exist whenever the selftest
  # can run at all.
  LIVE_Q="homunculus/instincts/personal"
  DEAD_Q="homunculus/instincts/personal/no-such-instinct-ever.md"
  ST_FAIL=0
  fixture() { printf '%s\n' "$2" > "$ST/$1.md"; }
  expect() {
    local got; got=$(repo_true "$ST/$1.md")
    if [ "$got" = "$2" ]; then echo "  PASS  $1 -> $2"
    else echo "  FAIL  $1 -> got $got, want $2"; ST_FAIL=$((ST_FAIL+1)); fi
  }

  # The inferred branch must be untouched by this change.
  fixture uncited "---
id: x
---
No paths here at all."
  expect uncited UNCITED

  fixture inferred-dead "---
id: x
---
See $DEAD for the trap."
  expect inferred-dead ALL_DEAD

  # Declared branch.
  fixture cites-empty "---
id: x
cites: []
---
Path-independent lesson."
  expect cites-empty CITES_NONE

  fixture cites-ok "---
id: x
cites:
  - \"$LIVE_Q\"
---
body"
  expect cites-ok CITES_OK

  # RED-first: a declared entry pointing nowhere MUST flag, or the declared
  # branch is decorative. This is the whole reason ALL_DEAD is strong here.
  fixture cites-dead "---
id: x
cites:
  - \"$DEAD_Q\"
---
body"
  expect cites-dead ALL_DEAD

  fixture cites-mixed "---
id: x
cites:
  - \"$LIVE_Q\"
  - \"$DEAD_Q\"
---
body"
  expect cites-mixed SOME_DEAD

  # The payoff: once declared, prose is invisible to gate 2. Same dead path as
  # inferred-dead above, which scored ALL_DEAD — here it must not be read.
  fixture cites-prose-invisible "---
id: x
cites:
  - \"$LIVE_Q\"
---
The extractor captured $DEAD out of a live path."
  expect cites-prose-invisible CITES_OK

  # `cites:` in the BODY is prose, not the field.
  fixture cites-body-only "---
id: x
---
cites: []
See $DEAD here."
  expect cites-body-only ALL_DEAD

  if [ "$ST_FAIL" -eq 0 ]; then echo "selftest: PASS (8 checks)"; exit 0
  else echo "selftest: FAIL ($ST_FAIL of 8)"; exit 1; fi
fi

# ---------------------------------------------------------------- report
[ "$TSV" = "--tsv" ] && printf "file\tlines\tconf\tevid\tgate0\tgate1\tgate2\n"

# macOS ships bash 3.2 — no associative arrays. Tally via a temp TSV instead.
#
# Explicit template, NOT bare `mktemp` or `mktemp -t`: on macOS both ignore
# $TMPDIR and use the Darwin per-user dir (_CS_DARWIN_USER_TEMP_DIR), which is
# unwritable under a sandbox. Bare mktemp then failed, TALLY became the empty
# string, every append went to `""`, and the script REPORTED AN EMPTY CORPUS
# WITH EXIT 0 — a clean-looking run that never happened.
mktmp() { mktemp "${TMPDIR:-/tmp}/instinct-buckets.XXXXXX"; }
TALLY=$(mktmp) || { echo "FATAL: mktemp failed in ${TMPDIR:-/tmp} — set TMPDIR to a writable dir" >&2; exit 3; }
trap 'rm -f "$TALLY"' EXIT

# Without nullglob an unmatched `*.md` survives as the literal string, so an
# empty or wrong corpus dir tallies one phantom row and exits 0 instead of
# tripping the TOTAL-eq-0 guard below. Proved by pointing DIR at an empty dir.
shopt -s nullglob

TOTAL=0; LONG=0
for f in *.md; do
  case "$f" in *.evidence.md) continue;; esac
  TOTAL=$((TOTAL+1))
  g0=$(findable "$f"); g1=$(promoted "$f"); g2=$(repo_true "$f")
  lines=$(wc -l < "$f" | tr -d ' ')
  conf=$(sed -n 's/^confidence: *//p' "$f" | head -1)
  evid=$(sed -n 's/^evidence_count: *//p' "$f" | head -1)
  if [ "${lines:-0}" -gt 30 ] 2>/dev/null; then LONG=$((LONG+1)); fi
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n" "${f%.md}" "$lines" "$conf" "$evid" "$g0" "$g1" "$g2" >> "$TALLY"
done

# Fail closed. A bucketing run that produced no rows, or fewer rows than files
# it walked, is a FAILED run — not a clean corpus. Reporting it as a result is
# how an empty answer gets acted on.
ROWS=$(wc -l < "$TALLY" | tr -d ' ')
if [ "$TOTAL" -eq 0 ]; then
  echo "FATAL: walked 0 files in $DIR — wrong directory, or the corpus is gone" >&2; exit 4
fi
if [ "${ROWS:-0}" -ne "$TOTAL" ]; then
  echo "FATAL: tallied $ROWS rows for $TOTAL files — the report is incomplete, refusing to print it" >&2; exit 4
fi

if [ "$TSV" = "--tsv" ]; then cat "$TALLY"; exit 0; fi

col() { cut -f"$1" "$TALLY" | sort | uniq -c | sort -rn | sed 's/^/  /'; }

echo "corpus: $TOTAL files"
echo
echo "FORMAT — conversion progress to the lesson-first spec"; col 5
echo
echo "GATE 1 — promoted (already covered by an always-on rule?)"; col 6
echo
echo "GATE 2 — repo-true (do its cited paths still exist?)"; col 7
echo
echo "FORMAT — over the 30-line cap: $LONG of $TOTAL"
echo
echo "Gates are CANDIDATE FLAGS, not verdicts. Calibrate against --tsv before moving anything."

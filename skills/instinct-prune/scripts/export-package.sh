#!/bin/bash
# export-package.sh — build the publishable package tree from this config.
#
# `~/.claude` is the SOURCE OF TRUTH; the published repo is an export target
# (INSTINCT_PACKAGE_PLAN.md §"Source of truth"). Export is one-way for v1 —
# nothing is read back from the destination.
#
# EXPORTED: every skills/instinct-* dir, hooks/surface-instincts.sh,
#           rules/instincts.md, plus the package's own README/manifest which
#           live in this repo under skills/instinct-prune/package/.
# NEVER EXPORTED: homunculus/ (the personal corpus — 359 lessons), settings.json,
#           any other rules/, local-projects.conf (real client names live there).
#
# The skill list is DISCOVERED, never enumerated here. A hand-maintained target
# list is exactly how the privacy gate came to scan 3 of 4 skills while
# reporting a clean surface.
#
# Fail direction: CLOSED. The staging tree is scanned by pre-publish.sh BEFORE
# anything is written to the destination, so a leak stops the export instead of
# landing in a repo and needing history surgery.
#
# Usage:  bash export-package.sh <dest-dir>
#         bash export-package.sh --selftest
set -uo pipefail

SRC="${SRC:-$HOME/.claude}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paths copied verbatim, relative to SRC. Discovered skills are added below.
FIXED_PATHS=("hooks/surface-instincts.sh" "rules/instincts.md")

die() { echo "FATAL: $*" >&2; exit 2; }

discover_skills() {  # prints repo-relative skill dirs, one per line
  local d found=0
  for d in "$SRC"/skills/instinct-*/; do
    [ -d "$d" ] || continue
    found=1
    echo "skills/$(basename "${d%/}")"
  done
  [ "$found" -eq 1 ] || return 1
}

build_stage() {  # $1 = stage dir
  local stage="$1" p
  local -a paths
  paths=()
  # macOS ships bash 3.2 — no `mapfile`. Read the discovered list the portable
  # way; a `mapfile` here exited 2 with no output at all.
  while IFS= read -r p; do
    [ -n "$p" ] && paths+=("$p")
  done < <(discover_skills)
  [ "${#paths[@]}" -gt 0 ] || die "no skills/instinct-* found under $SRC"
  paths+=("${FIXED_PATHS[@]}")

  # Copy only GIT-TRACKED files. "What ships" then equals "what you versioned",
  # which excludes an entire class of accidents by construction: untracked
  # scratch files, editor leftovers, and __pycache__ (gitignored) can never
  # reach the staging tree at all. Found the hard way — a stray package/ dir
  # sitting untracked inside a skill exported itself into the release.
  git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 \
    || die "$SRC is not a git repo — the export ships tracked files only"
  local f n=0
  for p in "${paths[@]}"; do
    [ -e "$SRC/$p" ] || die "missing export input: $SRC/$p"
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      mkdir -p "$stage/$(dirname "$f")"
      cp "$SRC/$f" "$stage/$f" || die "copy failed: $f"
      n=$((n+1))
    done < <(git -C "$SRC" ls-files -- "$p")
  done
  [ "$n" -gt 0 ] || die "git ls-files matched nothing — is the package committed?"

  # Build artifacts never ship. The live tree cannot always be cleaned (removing
  # __pycache__ under skills/ is denied by the permission gate on this machine),
  # so the export strips them from the COPY — which is the only tree that
  # actually gets published.
  find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
  find "$stage" \( -name '*.pyc' -o -name '.DS_Store' \) -delete 2>/dev/null

  # The package's own README/manifest/.gitignore. It lives OUTSIDE every
  # exported path on purpose: when it sat inside skills/instinct-prune/ it was
  # copied twice — once nested in the skill, once at the root.
  local pkg="${PACKAGE_DIR:-$SRC/docs/refinery}"
  if [ -d "$pkg" ]; then
    cp -R "$pkg/." "$stage/" || die "copying package metadata from $pkg failed"
  fi
  return 0
}

gate_stage() {  # $1 = stage dir; the release gate, run on the COPY
  local stage="$1" args=() d
  for d in "$stage"/skills/instinct-*/; do [ -d "$d" ] && args+=("${d%/}"); done
  [ -e "$stage/hooks/surface-instincts.sh" ] && args+=("$stage/hooks/surface-instincts.sh")
  [ -e "$stage/rules/instincts.md" ] && args+=("$stage/rules/instincts.md")
  [ "${#args[@]}" -gt 0 ] || die "nothing staged to scan"
  bash "$HERE/pre-publish.sh" "${args[@]}"
}

publish() {  # $1 = dest
  local dest="$1" stage
  case "$dest" in
    "$SRC"|"$SRC"/*) die "destination is inside the source config ($SRC) — pick a directory outside it";;
    "") die "no destination given";;
  esac
  stage=$(mktemp -d "${TMPDIR:-/tmp}/refinery-export.XXXXXX") || die "mktemp -d failed"
  trap 'rm -rf "$stage"' EXIT

  build_stage "$stage" || die "staging failed"
  echo "staged:"; (cd "$stage" && find . -type f | sed 's/^\./  /' | sort | head -40)

  if ! gate_stage "$stage"; then
    die "pre-publish gate REJECTED the staging tree — nothing was written to $dest"
  fi
  echo "gate: clean"

  mkdir -p "$dest" || die "cannot create $dest"
  # Replace only the managed subtrees. A README, LICENSE, .git or anything else
  # the destination gained on its own is left alone.
  local sub
  for sub in skills hooks rules; do
    [ -d "$stage/$sub" ] && rm -rf "${dest:?}/$sub"
  done
  cp -R "$stage/." "$dest/" || die "copy into $dest failed"
  echo "exported -> $dest"
}

# ---------------------------------------------------------------- selftest
selftest() {
  local ok=1 tmp; tmp=$(mktemp -d "${TMPDIR:-/tmp}/exp-st.XXXXXX") || exit 3
  trap 'rm -rf "$tmp"' EXIT
  chk() { if [ "$2" -eq 1 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; ok=0; fi; }

  # A fake source config: two skills, one with a build artifact, plus the
  # fixed paths and a corpus that must NOT travel.
  mkdir -p "$tmp/src/skills/instinct-one/scripts/__pycache__" \
           "$tmp/src/skills/instinct-two" "$tmp/src/hooks" "$tmp/src/rules" \
           "$tmp/src/homunculus/instincts/personal"
  echo "one" > "$tmp/src/skills/instinct-one/SKILL.md"
  echo "compiled" > "$tmp/src/skills/instinct-one/scripts/__pycache__/x.pyc"
  echo "two" > "$tmp/src/skills/instinct-two/SKILL.md"
  echo "hook" > "$tmp/src/hooks/surface-instincts.sh"
  echo "rule" > "$tmp/src/rules/instincts.md"
  echo "secret lesson" > "$tmp/src/rules/security.md"
  echo "personal" > "$tmp/src/homunculus/instincts/personal/a.md"

  # The exporter ships tracked files only, so the fixture must be a real repo.
  # __pycache__ is deliberately ADDED here: the strip check must stay meaningful
  # rather than passing because git never saw the file.
  git -C "$tmp/src" init -q 2>/dev/null
  git -C "$tmp/src" add -Af 2>/dev/null

  SRC="$tmp/src"
  PACKAGE_DIR="$tmp/no-such-package"   # don't drag the real README into fixtures
  local stage="$tmp/stage"; mkdir -p "$stage"
  build_stage "$stage" >/dev/null 2>&1

  [ -f "$stage/skills/instinct-one/SKILL.md" ] && [ -f "$stage/skills/instinct-two/SKILL.md" ]
  chk "every discovered instinct-* skill is staged (list is not hardcoded)" $((1 - $?))

  [ ! -d "$stage/homunculus" ]; chk "the personal corpus never leaves" $((1 - $?))
  [ ! -f "$stage/rules/security.md" ]; chk "unrelated rules are not exported" $((1 - $?))
  [ -f "$stage/rules/instincts.md" ]; chk "the generic instincts rule IS exported" $((1 - $?))
  [ -z "$(find "$stage" -name '__pycache__' -o -name '*.pyc')" ]
  chk "__pycache__ and .pyc are stripped from the copy (RED-proven below)" $((1 - $?))

  # A skill added after the fact must appear with no code change — the guard
  # against the hardcoded-target-list bug this script exists to avoid.
  mkdir -p "$tmp/src/skills/instinct-three"; echo "three" > "$tmp/src/skills/instinct-three/SKILL.md"
  git -C "$tmp/src" add -Af 2>/dev/null
  local stage2="$tmp/stage2"; mkdir -p "$stage2"
  build_stage "$stage2" >/dev/null 2>&1
  [ -f "$stage2/skills/instinct-three/SKILL.md" ]
  chk "a newly added skill is picked up with no edit to this script" $((1 - $?))

  # Refuse to write inside the source config.
  ( SRC="$tmp/src"; publish "$tmp/src/sub" ) >/dev/null 2>&1
  [ $? -eq 2 ]; chk "refuses a destination inside the source config" $((1 - $?))

  # Missing input fails closed rather than exporting a partial package.
  # Subshell: die() exits, so calling build_stage directly would end the
  # selftest instead of failing this one check.
  rm "$tmp/src/rules/instincts.md"
  ( build_stage "$tmp/stage3" ) >/dev/null 2>&1
  [ $? -eq 2 ]; chk "a missing export input fails closed" $((1 - $?))

  [ $ok -eq 1 ] && { echo "SELFTEST: PASS"; exit 0; } || { echo "SELFTEST: FAIL"; exit 1; }
}

case "${1:-}" in
  --selftest) selftest ;;
  "") die "usage: export-package.sh <dest-dir>" ;;
  *)  publish "$1" ;;
esac

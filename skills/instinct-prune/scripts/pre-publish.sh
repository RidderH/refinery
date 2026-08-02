#!/bin/bash
# pre-publish.sh — privacy gate for publishing the instinct package.
#
# Two layers (each catches what the other cannot):
#   1. Name denylist — private client/project names and personal identifiers.
#      Standard secret scanners match credential SHAPES and will never flag a
#      proper noun, so this stays a grep. The list is READ FROM the gitignored
#      local-projects.conf (DENY_NAMES) — hardcoding it here would ship the
#      very names the gate exists to block.
#   2. gitleaks — credential shapes (keys, tokens), offline mode.
#
# Fail direction: CLOSED. No config, empty DENY_NAMES, or missing gitleaks is
# a failed gate (exit 2), not a pass — a gate that cannot see its rules must
# not wave the release through. SKIP_GITLEAKS=1 downgrades only layer 2, for
# machines where installing it is genuinely impossible; say so in the release
# notes if used.
#
# Usage:
#   bash pre-publish.sh [dir ...]   # default: the publishable package surface
#   bash pre-publish.sh --selftest  # RED-proves both layers on a temp tree
set -uo pipefail

CONF="${LOCAL_PROJECTS_CONF:-$HOME/.claude/homunculus/local-projects.conf}"
# Every skill in the published package, plus the hook. `instinct-analyze` was
# MISSING here until 2026-08-02 while the package shipped four skills — the
# gate reported a clean surface it had never read. A scanner that enumerates
# its own targets by hand drifts the moment the package grows; the guard below
# refuses to run when this list and the on-disk `instinct-*` skills disagree.
DEFAULT_SURFACE=("$HOME/.claude/skills/instinct-analyze"
                 "$HOME/.claude/skills/instinct-distill"
                 "$HOME/.claude/skills/instinct-format"
                 "$HOME/.claude/skills/instinct-prune"
                 "$HOME/.claude/hooks/surface-instincts.sh")

# Fail closed on an under-enumerated surface: if a new skills/instinct-* dir
# exists that DEFAULT_SURFACE does not name, the default scan is incomplete and
# must not report "clean". Only applies to the no-argument invocation.
SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
check_surface_complete() {
  local d missing=""
  for d in "$SKILLS_DIR"/instinct-*/; do
    [ -d "$d" ] || continue
    case " ${DEFAULT_SURFACE[*]} " in
      *" ${d%/} "*) ;;
      *) missing="$missing ${d%/}";;
    esac
  done
  [ -z "$missing" ] && return 0
  echo "FAIL: DEFAULT_SURFACE does not cover:$missing — add them, or the gate scans less than it ships" >&2
  return 2
}

load_deny() {
  [ -f "$CONF" ] || { echo "FAIL: no config at $CONF — the denylist lives there; copy local-projects.conf.example" >&2; return 2; }
  if [ "$(uname)" = "Darwin" ]; then perms=$(stat -f '%Lp' "$CONF"); else perms=$(stat -c '%a' "$CONF"); fi
  case "$perms" in *[2367]) echo "FAIL: $CONF is world-writable ($perms) — refusing to source it" >&2; return 2;; esac
  DENY_NAMES=""
  # shellcheck disable=SC1090
  . "$CONF"
  [ -n "${DENY_NAMES// /}" ] || { echo "FAIL: DENY_NAMES empty in $CONF — an empty denylist passes everything; that is not a scan" >&2; return 2; }
  return 0
}

scan() {  # $@ = dirs/files to scan; returns 0 clean, 1 hits, 2 cannot-run
  load_deny || return 2
  local rc=0 name hits
  for name in $DENY_NAMES; do
    # -w: whole words only — a deny name like "ring" must not match inside
    # "boring" (this gate's own first baseline run tripped on exactly such a
    # substring). Hyphenated names still match.
    hits=$(grep -rnw --binary-files=without-match -F -- "$name" "$@" 2>/dev/null)
    if [ -n "$hits" ]; then
      echo "LEAK [$name]:"; echo "$hits" | head -20; rc=1
    fi
  done
  # __pycache__ ships in tarballs even when gitignored — refuse if present.
  local pyc; pyc=$(find "$@" -name '__pycache__' -type d 2>/dev/null | head -3)
  [ -n "$pyc" ] && { echo "LEAK [__pycache__ dirs present — compiled copies of scrubbed strings]:"; echo "$pyc"; rc=1; }
  if [ "${SKIP_GITLEAKS:-0}" != "1" ]; then
    if ! command -v gitleaks >/dev/null 2>&1; then
      echo "FAIL: gitleaks not installed (layer 2). Install it, or SKIP_GITLEAKS=1 with a note in the release notes." >&2
      return 2
    fi
    local d
    for d in "$@"; do
      gitleaks dir "$d" --no-banner --exit-code 9 >/dev/null 2>&1
      case $? in
        0) : ;;
        9) echo "LEAK [gitleaks] in $d — rerun: gitleaks dir $d --no-banner -v"; rc=1 ;;
        *) echo "FAIL: gitleaks errored on $d — rerun: gitleaks dir $d --no-banner -v" >&2; return 2 ;;
      esac
    done
  fi
  return $rc
}

# ---------------------------------------------------------------- selftest
selftest() {
  local ok=1 tmp; tmp=$(mktemp -d "${TMPDIR:-/tmp}/prepub.XXXXXX") || exit 3
  trap 'rm -rf "$tmp"' EXIT
  chk() { if [ "$2" -eq 1 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; ok=0; fi; }

  mkdir -p "$tmp/tree"
  echo "clean content" > "$tmp/tree/a.md"
  printf 'DENY_NAMES="Secret_Client"\n' > "$tmp/conf"; chmod 600 "$tmp/conf"

  # Layer 1 RED: planted name must trip
  echo "mentions Secret_Client here" > "$tmp/tree/b.md"
  CONF="$tmp/conf"; SKIP_GITLEAKS=1
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "planted denylist name trips the gate (RED)" $((1 - $?))
  rm "$tmp/tree/b.md"
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk "clean tree passes after removal (GREEN)" $((1 - $?))

  # Substring must NOT trip: deny "ring" vs body "boring" (regression: the
  # first live baseline flagged a short deny name inside an ordinary word)
  printf 'DENY_NAMES="ring"\n' > "$tmp/conf3"; chmod 600 "$tmp/conf3"
  echo "nothing boring here" > "$tmp/tree/c.md"
  CONF="$tmp/conf3" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk "substring of a word does not trip (-w)" $((1 - $?))
  echo "the ring flow" >> "$tmp/tree/c.md"
  CONF="$tmp/conf3" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "same name as whole word still trips" $((1 - $?))
  rm "$tmp/tree/c.md"

  # __pycache__ RED
  mkdir -p "$tmp/tree/__pycache__"
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "__pycache__ dir trips the gate (RED)" $((1 - $?))
  rmdir "$tmp/tree/__pycache__"

  # Fail-closed probes: each bad state must be exit 2, never a pass
  CONF="$tmp/nope" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "missing config fails closed" $((1 - $?))
  printf 'DENY_NAMES=""\n' > "$tmp/conf2"; chmod 600 "$tmp/conf2"
  CONF="$tmp/conf2" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "empty DENY_NAMES fails closed" $((1 - $?))
  chmod 666 "$tmp/conf"
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "world-writable config refused" $((1 - $?))
  chmod 600 "$tmp/conf"

  # Surface-completeness guard. The bug it exists for: the package shipped four
  # instinct-* skills while DEFAULT_SURFACE named three, so the gate passed on a
  # surface it had never read.
  mkdir -p "$tmp/skills/instinct-alpha" "$tmp/skills/instinct-beta"
  DEFAULT_SURFACE=("$tmp/skills/instinct-alpha" "$tmp/skills/instinct-beta")
  SKILLS_DIR="$tmp/skills" check_surface_complete >/dev/null 2>&1
  [ $? -eq 0 ]; chk "fully-enumerated surface passes the completeness guard" $((1 - $?))
  mkdir -p "$tmp/skills/instinct-gamma"
  SKILLS_DIR="$tmp/skills" check_surface_complete >/dev/null 2>&1
  [ $? -eq 2 ]; chk "an unlisted instinct-* skill fails the gate closed (RED)" $((1 - $?))

  SKIP_GITLEAKS=0
  if ! command -v gitleaks >/dev/null 2>&1; then
    scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "missing gitleaks fails closed (layer 2)" $((1 - $?))
  fi

  [ $ok -eq 1 ] && { echo "SELFTEST: PASS"; exit 0; } || { echo "SELFTEST: FAIL"; exit 1; }
}

case "${1:-}" in
  --selftest) selftest ;;
  "") check_surface_complete || exit 2; scan "${DEFAULT_SURFACE[@]}"; exit $? ;;
  *)  scan "$@"; exit $? ;;
esac

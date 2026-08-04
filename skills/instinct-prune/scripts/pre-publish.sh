#!/bin/bash
# pre-publish.sh — privacy gate for publishing the instinct package.
#
# Three layers (each catches what the others cannot):
#   1. Name denylist — private client/project names and personal identifiers,
#      checked against both file CONTENTS and file PATHS (a name can leak via
#      its filename with clean contents). Standard secret scanners match
#      credential SHAPES and will never flag a proper noun, so this stays a
#      grep. The list is READ FROM the gitignored local-projects.conf
#      (DENY_NAMES) — hardcoding it here would ship the very names the gate
#      exists to block.
#   2. gitleaks — credential shapes (keys, tokens), offline mode.
#   3. Identifier shapes — email addresses and absolute /Users//home paths, in
#      contents and filenames. Not credential shapes (gitleaks has no reason to
#      know these), not proper nouns (DENY_NAMES can't enumerate them) — a
#      third grep with its own patterns. ALLOW_IDENTIFIERS (space-separated
#      fixed strings — a known-fine mailbox or domain) suppresses known-fine
#      hits; unset means nothing is allowlisted. No literal email/path
#      examples in this file itself: this script ships as part of the
#      surface it scans, and layer 3 has no way to tell its own doc text
#      from a real leak.
#
# .git/ is EXCLUDED from every layer of the tree scan below. This gate judges
# shipped CONTENT (the working tree); a repo's HISTORY (reflogs, remote URLs,
# commit authorship in .git/config and .git/logs) is a different surface with
# its own dedicated scan in export-package.sh (gitleaks in git-history mode +
# a denylist sweep over the commit patches). Scoping the tree scan to the
# tree is not under-enumeration — conflating the two just means every clone's
# local-only reflog (which is never pushed as a file) fails this gate forever.
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
  # Fail closed on a broken conf: `. "$CONF"` on a syntax error prints to
  # stderr but the sourcing shell continues with whatever assigned before the
  # error — DENY_NAMES could already hold a partial/valid-looking list and the
  # gate would approve publication having read less than it thinks it did.
  # bash -n first so a parse error never even reaches the running gate state.
  bash -n "$CONF" 2>/dev/null || { echo "FAIL: $CONF has a syntax error — refusing to source it" >&2; return 2; }
  DENY_NAMES=""
  ALLOW_IDENTIFIERS=""
  # shellcheck disable=SC1090
  . "$CONF" || { echo "FAIL: $CONF failed to source — refusing" >&2; return 2; }
  [ -n "${DENY_NAMES// /}" ] || { echo "FAIL: DENY_NAMES empty in $CONF — an empty denylist passes everything; that is not a scan" >&2; return 2; }
  return 0
}

# $1 = literal string; suppressed only if it appears verbatim in a
# space-separated ALLOW_IDENTIFIERS entry (fixed strings, not patterns).
_id_allowed() {
  local a
  for a in $ALLOW_IDENTIFIERS; do
    case "$1" in *"$a"*) return 0 ;; esac
  done
  return 1
}

# Filename layers must judge the path relative to the scanned ROOT, not the
# absolute path on disk — every absolute path under this operator's tree
# starts with their own home directory, which is not part of what a package
# export ships. $1 = "word" (grep -Fw) or "ere" (grep -E); $2 = pattern;
# remaining args = roots. Word mode emits one absolute path per match. ERE
# mode emits `path:occurrence` so allowlisting is per identifier rather than
# per pathname.
# `-name .git -prune -o ... -print`: .git/ is history, not shipped content —
# see the file-header note.
_scan_relpaths() {
  local mode="$1" pat="$2" root p rel; shift 2
  for root in "$@"; do
    while IFS= read -r p; do
      if [ -f "$root" ] || [ -L "$root" ]; then
        rel=$(basename "$root")
      else
        rel="${p#"$root"}"
      fi
      if [ "$mode" = word ]; then
        printf '%s' "$rel" | grep -Fwq -- "$pat" && echo "$p"
      else
        while IFS= read -r match; do
          [ -n "$match" ] && printf '%s:%s\n' "$p" "$match"
        done < <(printf '%s' "$rel" | grep -Eo -- "$pat")
      fi
    done < <(find "$root" -name .git -prune -o -print 2>/dev/null)
  done
}

scan() {  # $@ = dirs/files to scan; returns 0 clean, 1 hits, 2 cannot-run
  load_deny || return 2
  local rc=0 name hits namehits
  for name in $DENY_NAMES; do
    # -w: whole words only — a deny name like "ring" must not match inside
    # "boring" (this gate's own first baseline run tripped on exactly such a
    # substring). Hyphenated names still match.
    hits=$(grep -rnw --binary-files=without-match --exclude-dir=.git -F -- "$name" "$@" 2>/dev/null)
    if [ -n "$hits" ]; then
      echo "LEAK [$name]:"; echo "$hits" | head -20; rc=1
    fi
    # Filenames leak a name even with clean contents (e.g. a file literally
    # NAMED after the client). Same -w word-boundary semantics as content.
    namehits=$(_scan_relpaths word "$name" "$@")
    if [ -n "$namehits" ]; then
      echo "LEAK [$name in filename]:"; echo "$namehits" | head -20; rc=1
    fi
  done
  # Layer 3: identifier shapes — emails and absolute home paths. Tilde form
  # (~/.claude/...) is fine and NOT flagged; only /Users/<name>/ and
  # /home/<name>/ are personal-machine paths.
  local label re idhits
  for label in email abs-home-path; do
    case "$label" in
      email) re='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' ;;
      abs-home-path) re='/(Users|home)/[A-Za-z0-9._-]+/' ;;
    esac
    # -o (one match per output line, "file:line:match") so a line carrying
    # BOTH an allowlisted and a non-allowlisted identifier is judged per
    # occurrence, not suppressed wholesale because ONE of its matches is
    # allowlisted — checking the full line against ALLOW_IDENTIFIERS would
    # let a leaking identifier ride along next to a fine one.
    idhits=$(grep -rnoE --binary-files=without-match --exclude-dir=.git -- "$re" "$@" 2>/dev/null)
    if [ -n "$idhits" ]; then
      local filtered occ match found=0
      filtered=""
      while IFS= read -r occ; do
        [ -z "$occ" ] && continue
        match="${occ##*:}"
        _id_allowed "$match" && continue
        found=1
        filtered="$filtered$occ
"
      done <<<"$idhits"
      [ "$found" -eq 1 ] && { echo "LEAK [identifier: $label]:"; printf '%s' "$filtered" | head -20; rc=1; }
    fi
    idhits=$(_scan_relpaths ere "$re" "$@")
    if [ -n "$idhits" ]; then
      local nfiltered nline nfound=0
      nfiltered=""
      local nmatch
      while IFS= read -r nline; do
        [ -z "$nline" ] && continue
        nmatch="${nline##*:}"
        _id_allowed "$nmatch" && continue
        nfound=1
        nfiltered="$nfiltered$nline
"
      done <<<"$idhits"
      [ "$nfound" -eq 1 ] && { echo "LEAK [identifier: $label in filename]:"; printf '%s' "$nfiltered" | head -20; rc=1; }
    fi
  done
  # __pycache__ ships in tarballs even when gitignored — refuse if present.
  # (.git-pruned: a __pycache__ can't exist inside .git, but stay consistent.)
  local pyc; pyc=$(find "$@" -name .git -prune -o -name '__pycache__' -type d -print 2>/dev/null | head -3)
  [ -n "$pyc" ] && { echo "LEAK [__pycache__ dirs present — compiled copies of scrubbed strings]:"; echo "$pyc"; rc=1; }
  if [ "${SKIP_GITLEAKS:-0}" != "1" ]; then
    # gitleaks in `dir` mode does not raw-scan .git/ contents — verified
    # empirically: a repo with .git/config + reflog containing an email and
    # a remote URL scanned as ~12 bytes (the tracked file diff only) and
    # reported no leaks. No exclusion needed here; history is its own gate
    # in export-package.sh.
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

  # Filename layer: deny name in the PATH itself, clean contents.
  CONF="$tmp/conf"
  echo "clean content" > "$tmp/tree/Secret_Client-notes.md"
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "planted denylist name in filename trips the gate (RED)" $((1 - $?))
  rm "$tmp/tree/Secret_Client-notes.md"
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk "clean tree passes after filename removal (GREEN)" $((1 - $?))

  # Identifier-shapes fixtures are built at runtime, never written literally
  # in this file: pre-publish.sh is itself part of DEFAULT_SURFACE, so a
  # literal email/path in its source would trip layer 3 on every future run.
  at="@"; slash="/"
  planted_email="alex${at}example.com"
  allow_email="noreply${at}anthropic.com"
  planted_path="${slash}Users${slash}alex${slash}ClientX"

  # Identifier-shapes layer: email in content.
  echo "Contact $planted_email for details" > "$tmp/tree/d.md"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "planted email trips the gate (RED)" $((1 - $?))
  rm "$tmp/tree/d.md"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk "tree clean of emails passes (GREEN)" $((1 - $?))

  # Identifier-shapes layer: absolute home path in content. Tilde form must
  # NOT trip (docs legitimately use ~/.claude/...).
  echo "See $planted_path for the source tree" > "$tmp/tree/e.md"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "planted /Users/<name>/ path trips the gate (RED)" $((1 - $?))
  rm "$tmp/tree/e.md"
  echo "See ~/.claude/skills for the source" > "$tmp/tree/e.md"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk "tilde-form home path does not trip" $((1 - $?))
  rm "$tmp/tree/e.md"

  # ALLOW_IDENTIFIERS suppresses only the listed identifier, not others.
  printf 'DENY_NAMES="Secret_Client"\nALLOW_IDENTIFIERS="%s"\n' "$allow_email" > "$tmp/conf4"; chmod 600 "$tmp/conf4"
  echo "$allow_email is fine, $planted_email is not" > "$tmp/tree/f.md"
  CONF="$tmp/conf4" scan "$tmp/tree" >/dev/null 2>&1; rc=$?
  echo "$allow_email is fine" > "$tmp/tree/f.md"
  CONF="$tmp/conf4" scan "$tmp/tree" >/dev/null 2>&1; rc2=$?
  rm "$tmp/tree/f.md"
  [ "$rc" -eq 1 ] && [ "$rc2" -eq 0 ]; chk "ALLOW_IDENTIFIERS suppresses only the allowlisted identifier" $((1 - $?))

  # Filename occurrences need the same per-match filtering as contents. If
  # the whole path is passed to _id_allowed, one allowed address hides every
  # second address carried by that filename.
  mixed_name="${allow_email}_${planted_email}.md"
  echo "clean content" > "$tmp/tree/$mixed_name"
  CONF="$tmp/conf4" scan "$tmp/tree" >/dev/null 2>&1; rc=$?
  rm "$tmp/tree/$mixed_name"
  [ "$rc" -eq 1 ]; chk "allowlisted filename identifier cannot hide a second leak (RED)" $((1 - $?))

  # When the root itself is a file, `${p#$root}` is empty. The basename still
  # belongs to the publication surface and must be scanned.
  echo "clean content" > "$tmp/tree/Secret_Client-root.md"
  CONF="$tmp/conf" scan "$tmp/tree/Secret_Client-root.md" >/dev/null 2>&1; rc=$?
  rm "$tmp/tree/Secret_Client-root.md"
  [ "$rc" -eq 1 ]; chk "directly scanned file keeps its basename in the filename gate (RED)" $((1 - $?))

  # .git/ exclusion boundary — the tree scan judges shipped content; history
  # (reflogs, .git/config, remote URLs) is a separate, dedicated scan and
  # must not fail this gate forever on every local clone.
  mkdir -p "$tmp/tree/.git/logs"
  echo "mentions Secret_Client and $planted_email" > "$tmp/tree/.git/config"
  echo "mentions Secret_Client and $planted_email" > "$tmp/tree/.git/logs/HEAD"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk ".git/ contents are excluded from the tree scan" $((1 - $?))

  # Same deny name + email OUTSIDE .git, same tree — must still trip. Proves
  # the exclusion is scoped to .git, not a layer-wide no-op.
  echo "mentions Secret_Client and $planted_email" > "$tmp/tree/tracked.md"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "same content outside .git still trips (exclusion is scoped)" $((1 - $?))
  rm "$tmp/tree/tracked.md"

  # .gitignore (a FILE) and .github (a DIR) must NOT be swallowed by a sloppy
  # `.git*` prefix match — only the exact `.git` directory name is excluded.
  echo "mentions Secret_Client" > "$tmp/tree/.gitignore"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk ".gitignore file is still scanned (not a .git* prefix match)" $((1 - $?))
  rm "$tmp/tree/.gitignore"
  mkdir -p "$tmp/tree/.github"
  echo "mentions Secret_Client" > "$tmp/tree/.github/workflow.md"
  CONF="$tmp/conf" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk ".github/ dir is still scanned (not a .git* prefix match)" $((1 - $?))
  rm -rf "$tmp/tree/.github" "$tmp/tree/.git"

  # Fail-closed probes: each bad state must be exit 2, never a pass
  CONF="$tmp/nope" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "missing config fails closed" $((1 - $?))
  printf 'DENY_NAMES=""\n' > "$tmp/conf2"; chmod 600 "$tmp/conf2"
  CONF="$tmp/conf2" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "empty DENY_NAMES fails closed" $((1 - $?))
  chmod 666 "$tmp/conf"
  scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "world-writable config refused" $((1 - $?))
  chmod 600 "$tmp/conf"

  # Syntax-error conf must fail closed, not run with whatever parsed before
  # the error.
  printf 'DENY_NAMES="x"\nif [\n' > "$tmp/confbad"; chmod 600 "$tmp/confbad"
  CONF="$tmp/confbad" scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "syntax-error conf fails closed" $((1 - $?))

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

  CONF="$tmp/conf"
  SKIP_GITLEAKS=0
  if ! command -v gitleaks >/dev/null 2>&1; then
    scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 2 ]; chk "missing gitleaks fails closed (layer 2)" $((1 - $?))
  else
    # gitleaks RED: a neutered/stubbed layer 2 would still pass a `true`
    # invocation — plant a shape gitleaks' own generic-api-key rule catches
    # and prove exit 1, so the layer is proven live, not just present.
    printf 'token = "ghp_%s"\n' "1234567890abcdefghijklmnopqrst12345" > "$tmp/tree/leaky.env"
    scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 1 ]; chk "planted credential trips gitleaks layer (RED)" $((1 - $?))
    rm "$tmp/tree/leaky.env"
    scan "$tmp/tree" >/dev/null 2>&1; [ $? -eq 0 ]; chk "clean tree passes gitleaks layer (GREEN)" $((1 - $?))
  fi

  [ $ok -eq 1 ] && { echo "SELFTEST: PASS"; exit 0; } || { echo "SELFTEST: FAIL"; exit 1; }
}

case "${1:-}" in
  --selftest) selftest ;;
  "") check_surface_complete || exit 2; scan "${DEFAULT_SURFACE[@]}"; exit $? ;;
  *)  scan "$@"; exit $? ;;
esac

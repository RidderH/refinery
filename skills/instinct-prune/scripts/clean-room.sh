#!/bin/bash
# Clean-room install test — Phase 5 acceptance gate.
#
# Installs the EXPORTED package into a throwaway HOME with no prior Claude Code
# config, exercises it, then uninstalls per the README. Every command is the
# README's, verbatim where possible: the point is to test the documented
# install, not a convenient one.
set -uo pipefail

PKG="${1:?usage: clean-room.sh <exported-package-dir>}"
# Captured BEFORE HOME is redirected below. Used to assert the package never
# leaks the packager's own home into a stranger's session. Derived, never
# written down: the first version hardcoded the author's path here and the
# publish gate refused to ship the file, correctly — a privacy test must not
# itself be the leak.
REAL_HOME="$HOME"
ROOM="${ROOM:-$(mktemp -d "${TMPDIR:-/tmp}/cleanroom.XXXXXX")}"
FAIL=0
chk() { if [ "$2" -eq 1 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; FAIL=$((FAIL+1)); fi; }
note() { echo "  ....  $1"; }

echo "clean room: $ROOM"
echo "package:    $PKG"
echo

MANIFEST_TOOL="$PKG/skills/instinct-prune/scripts/package_manifest.py"
PACKAGE_MANIFEST="$PKG/package-manifest.json"

# A HOME with absolutely nothing in it.
export HOME="$ROOM/home"
mkdir -p "$HOME"
[ ! -e "$HOME/.claude" ]; chk "start state: no ~/.claude exists at all" $((1 - $?))

# ---------------------------------------------------------------- install
echo; echo "INSTALL (README steps, verbatim)"
# Mirrors README.md's manifest-driven install. The package root differs only
# because this test receives a temporary export path instead of ~/refinery.
python3 "$MANIFEST_TOOL" install "$PACKAGE_MANIFEST" "$PKG" "$HOME/.claude"
mkdir -p "$HOME/.claude/homunculus/instincts/personal"

n=$(find "$HOME/.claude/skills" -maxdepth 1 -name 'instinct-*' -type d | wc -l | tr -d ' ')
[ "$n" -eq 4 ]; chk "all 4 skills installed (got $n)" $((1 - $?))
for s in analyze distill format prune; do
  [ -f "$HOME/.claude/skills/instinct-$s/SKILL.md" ]
  chk "instinct-$s/SKILL.md present" $((1 - $?))
done
[ -f "$HOME/.claude/hooks/surface-instincts.sh" ]; chk "hook installed" $((1 - $?))
[ -f "$HOME/.claude/rules/instincts.md" ]; chk "rule installed" $((1 - $?))
[ -f "$PKG/LICENSE" ]; chk "root MIT license shipped with the package" $((1 - $?))
[ ! -e "$HOME/.claude/LICENSE" ]; chk "package metadata license is not copied into agent config" $((1 - $?))

# The package must not have dragged the author's world along.
[ -z "$(find "$HOME/.claude" -name 'local-projects.conf' 2>/dev/null)" ]
chk "no machine-specific config was installed" $((1 - $?))
[ -z "$(find "$HOME/.claude" -name '__pycache__' -type d 2>/dev/null)" ]
chk "no build artifacts were installed" $((1 - $?))
[ ! -e "$HOME/.claude/settings.json" ]
chk "install did NOT write settings.json (registration stays manual)" $((1 - $?))

# -------------------------------------------------------------- agent profiles
echo; echo "AGENT-SPECIFIC SKILL ROOT PROFILES"
ROOT_TOOL="$PKG/skills/instinct-distill/scripts/skill_roots.py"
CODEX_PROFILE_HOME="$ROOM/codex-profile-home"
mkdir -p "$CODEX_PROFILE_HOME"
env HOME="$CODEX_PROFILE_HOME" XDG_CONFIG_HOME="$CODEX_PROFILE_HOME/.config" \
  python3 "$ROOT_TOOL" setup codex "$PKG" >/dev/null
[ -L "$CODEX_PROFILE_HOME/.codex/skills/instinct-distill" ]
chk "Codex-only profile links Refinery under ~/.codex/skills" $((1 - $?))
[ -f "$CODEX_PROFILE_HOME/.config/refinery/skill-roots.json" ]
chk "Codex-only profile persists its canonical root" $((1 - $?))
[ ! -e "$CODEX_PROFILE_HOME/.claude" ]
chk "Codex-only profile creates no Claude configuration" $((1 - $?))

SHARED_PROFILE_HOME="$ROOM/shared-profile-home"
mkdir -p "$SHARED_PROFILE_HOME"
env HOME="$SHARED_PROFILE_HOME" XDG_CONFIG_HOME="$SHARED_PROFILE_HOME/.config" \
  python3 "$ROOT_TOOL" setup shared "$PKG" >/dev/null
[ -L "$SHARED_PROFILE_HOME/.agents/skills/instinct-distill" ]
chk "shared profile installs the canonical Agent Skills link" $((1 - $?))
[ -L "$SHARED_PROFILE_HOME/.claude/skills/instinct-distill" ]
chk "shared profile adds the Claude discovery adapter" $((1 - $?))

# ---------------------------------------------------------------- hook
echo; echo "SESSION-START HOOK"
hook_out=$(bash "$HOME/.claude/hooks/surface-instincts.sh" 2>&1); hook_rc=$?
[ "$hook_rc" -eq 0 ]; chk "hook exits 0 on an empty corpus (rc=$hook_rc)" $((1 - $?))
# SILENCE is the documented behaviour here (surface-instincts.sh:36 exits early
# on COUNT=0). A brand-new user with no lessons must not be handed a wall of
# text about searching a corpus that is empty. The first draft of this test
# asserted the opposite and was wrong about the hook, not the other way round.
[ -z "$hook_out" ]; chk "hook stays SILENT on an empty corpus (no first-run noise)" $((1 - $?))

# ---------------------------------------------------------------- tooling
echo; echo "TOOLING SELFTESTS UNDER THE FRESH CONFIG"
while IFS= read -r s; do
  out=$(python3 "$s" --selftest 2>&1); rc=$?
  case "$out" in *PASS*) matched=1;; *) matched=0;; esac
  ok=0; [ "$rc" -eq 0 ] && [ "$matched" -eq 1 ] && ok=1
  chk "$(basename "$s") --selftest (rc=$rc, $(echo "$out" | tail -1))" "$ok"
done < <(find "$HOME/.claude/skills" -name '*.py' -exec grep -l -- '--selftest' {} + | sort)

bs="$HOME/.claude/skills/instinct-prune/scripts/instinct-buckets.sh"
if [ -f "$bs" ]; then
  out=$(bash "$bs" --selftest 2>&1); rc=$?
  case "$out" in *PASS*) matched=1;; *) matched=0;; esac
  ok=0; [ "$rc" -eq 0 ] && [ "$matched" -eq 1 ] && ok=1
  chk "instinct-buckets.sh --selftest (rc=$rc, $(echo "$out" | tail -1))" "$ok"
fi

# ---------------------------------------------------------------- empty corpus
echo; echo "BEHAVIOUR ON AN EMPTY CORPUS (must fail closed, not report clean)"
# Existence first, and the EXACT exit code (4 = walked 0 files). A generic
# "nonzero" would also pass when the script is simply missing from the package
# (bash exits 127) — the very fail-open this section exists to catch.
[ -f "$bs" ]; chk "buckets script is present in the install" $((1 - $?))
out=$(bash "$bs" 2>&1); rc=$?
[ "$rc" -eq 4 ]; chk "empty corpus is a FAILED run with the fail-closed exit (rc=$rc, want 4)" $((1 - $?))
case "$out" in *"walked 0 files"*) ok=1;; *) ok=0;; esac
chk "and it says why" "$ok"

# ---------------------------------------------------------------- real use
echo; echo "FIRST REAL LESSON"
cat > "$HOME/.claude/homunculus/instincts/personal/a-first-lesson.md" <<'LESSON'
---
id: a-first-lesson
trigger: "some literal symptom string"
action: "Do the thing."
confidence: 0.3
evidence_count: 1
domain: harness
source: "session-observation"
created: "2026-08-02"
updated: "2026-08-02"
last_checked: "2026-08-02"
version_pin: null
cites: []
---

# A first lesson

## Symptom
Something observable happened.

## Do this
The thing.
LESSON
out=$(bash "$bs" 2>&1); rc=$?
[ "$rc" -eq 0 ]; chk "buckets runs clean with one lesson (rc=$rc)" $((1 - $?))
case "$out" in *"corpus: 1 files"*) ok=1;; *) ok=0;; esac
chk "and counts it" "$ok"
case "$out" in *CITES_NONE*) ok=1;; *) ok=0;; esac
chk "the new cites: field works on a virgin install" "$ok"

# Now that a lesson exists, the hook must actually speak — and must describe
# THIS install, not the author's machine.
hook_out=$(bash "$HOME/.claude/hooks/surface-instincts.sh" 2>&1)
[ -n "$hook_out" ]; chk "hook speaks once the corpus has a lesson" $((1 - $?))
case "$hook_out" in *"1 past-session lessons"*) ok=1;; *) ok=0;; esac
chk "hook reports the right count" "$ok"
case "$hook_out" in *"$HOME/.claude/homunculus"*) ok=1;; *) ok=0;; esac
chk "hook points at THIS install's corpus" "$ok"
case "$hook_out" in *"$REAL_HOME"*) ok=0;; *) ok=1;; esac
chk "hook does not leak the packager's home directory" "$ok"

sl="$HOME/.claude/skills/instinct-prune/scripts/shortlist.py"
out=$(bash "$bs" --tsv 2>/dev/null | python3 "$sl" 2>&1); rc=$?
[ "$rc" -eq 0 ]; chk "shortlist runs end-to-end on a 1-file corpus (rc=$rc)" $((1 - $?))
note "shortlist said: $(echo "$out" | head -1 | cut -c1-90)"

# ---------------------------------------------------------------- README CONFIGURATION SNIPPETS
echo; echo "README CONFIGURATION SNIPPETS"
python3 - "$PKG/README.md" <<'PY'
import json, re, sys
md = open(sys.argv[1]).read()
blocks = re.findall(r"```json\n(.*?)```", md, re.S)
if not blocks:
    print("  FAIL  README has no json block"); sys.exit(1)
claude_ok = codex_ok = False
for b in blocks:
    try:
        obj = json.loads(b)
    except Exception as e:
        print(f"  FAIL  README json does not parse: {e}"); sys.exit(1)
    h = obj.get("hooks")
    if isinstance(h, list):
        if h and h[0].get("type") == "command" and "surface-instincts.sh" in h[0].get("command", ""):
            claude_ok = True
    elif isinstance(h, dict):
        starts = h.get("SessionStart")
        if (isinstance(starts, list) and starts and isinstance(starts[0].get("hooks"), list)
                and any("surface-instincts.sh" in item.get("command", "")
                        for item in starts[0]["hooks"] if isinstance(item, dict))):
            codex_ok = True
if not claude_ok:
    print("  FAIL  README has no valid Claude Code session-start hook snippet"); sys.exit(1)
if not codex_ok:
    print("  FAIL  README has no valid Codex SessionStart hook snippet"); sys.exit(1)
print("  PASS  README Claude Code and Codex hook snippets parse and target the hook")
PY
[ $? -eq 0 ] || FAIL=$((FAIL+1))

# ---------------------------------------------------------------- update
echo; echo "UPDATE (README steps, verbatim — remove-then-reinstall)"
# Plant a file that a hypothetical older package version shipped but the
# current export does not. The documented update (rm -rf then reinstall)
# must remove it; a bare re-run of the install cp lines would not, since
# cp -R never deletes files absent from the source.
obsolete="$HOME/.claude/skills/instinct-prune/obsolete-from-old-version.sh"
echo '#!/bin/bash' > "$obsolete"
[ -f "$obsolete" ]; chk "obsolete file planted before update" $((1 - $?))

python3 "$MANIFEST_TOOL" update "$PACKAGE_MANIFEST" "$PKG" "$HOME/.claude"

[ ! -f "$obsolete" ]; chk "update removes files deleted upstream (obsolete file gone)" $((1 - $?))
[ -f "$HOME/.claude/homunculus/instincts/personal/a-first-lesson.md" ]
chk "update leaves the corpus untouched (first lesson survives)" $((1 - $?))

# ---------------------------------------------------------------- uninstall
echo; echo "UNINSTALL (README steps, verbatim)"
python3 "$MANIFEST_TOOL" uninstall "$PACKAGE_MANIFEST" "$PKG" "$HOME/.claude"

[ -z "$(find "$HOME/.claude/skills" -maxdepth 1 -name 'instinct-*' 2>/dev/null)" ]
chk "all skills removed" $((1 - $?))
[ ! -f "$HOME/.claude/hooks/surface-instincts.sh" ]; chk "hook removed" $((1 - $?))
[ ! -f "$HOME/.claude/rules/instincts.md" ]; chk "rule removed" $((1 - $?))
[ -f "$HOME/.claude/homunculus/instincts/personal/a-first-lesson.md" ]
chk "the user's corpus SURVIVES uninstall (README promises this)" $((1 - $?))

echo
if [ "$FAIL" -eq 0 ]; then echo "CLEAN ROOM: PASS"; else echo "CLEAN ROOM: FAIL ($FAIL)"; fi
echo "room kept at: $ROOM"
exit $((FAIL > 0))

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

# A HOME with absolutely nothing in it.
export HOME="$ROOM/home"
mkdir -p "$HOME"
[ ! -e "$HOME/.claude" ]; chk "start state: no ~/.claude exists at all" $((1 - $?))

# ---------------------------------------------------------------- install
echo; echo "INSTALL (README steps, verbatim)"
mkdir -p "$HOME/.claude/skills" "$HOME/.claude/hooks" "$HOME/.claude/rules"
cp -R "$PKG"/skills/instinct-* "$HOME/.claude/skills/" 2>/dev/null
cp "$PKG/hooks/surface-instincts.sh" "$HOME/.claude/hooks/" 2>/dev/null
cp "$PKG/rules/instincts.md" "$HOME/.claude/rules/" 2>/dev/null
mkdir -p "$HOME/.claude/homunculus/instincts/personal"

n=$(find "$HOME/.claude/skills" -maxdepth 1 -name 'instinct-*' -type d | wc -l | tr -d ' ')
[ "$n" -eq 4 ]; chk "all 4 skills installed (got $n)" $((1 - $?))
for s in analyze distill format prune; do
  [ -f "$HOME/.claude/skills/instinct-$s/SKILL.md" ]
  chk "instinct-$s/SKILL.md present" $((1 - $?))
done
[ -f "$HOME/.claude/hooks/surface-instincts.sh" ]; chk "hook installed" $((1 - $?))
[ -f "$HOME/.claude/rules/instincts.md" ]; chk "rule installed" $((1 - $?))

# The package must not have dragged the author's world along.
[ -z "$(find "$HOME/.claude" -name 'local-projects.conf' 2>/dev/null)" ]
chk "no machine-specific config was installed" $((1 - $?))
[ -z "$(find "$HOME/.claude" -name '__pycache__' -type d 2>/dev/null)" ]
chk "no build artifacts were installed" $((1 - $?))
[ ! -e "$HOME/.claude/settings.json" ]
chk "install did NOT write settings.json (registration stays manual)" $((1 - $?))

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
  out=$(python3 "$s" --selftest 2>&1 | tail -1)
  case "$out" in *PASS*) ok=1;; *) ok=0;; esac
  chk "$(basename "$s") --selftest ($out)" "$ok"
done < <(find "$HOME/.claude/skills" -name '*.py' -exec grep -l -- '--selftest' {} + | sort)

bs="$HOME/.claude/skills/instinct-prune/scripts/instinct-buckets.sh"
if [ -f "$bs" ]; then
  out=$(bash "$bs" --selftest 2>&1 | tail -1)
  case "$out" in *PASS*) ok=1;; *) ok=0;; esac
  chk "instinct-buckets.sh --selftest ($out)" "$ok"
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

# ---------------------------------------------------------------- README JSON
echo; echo "README SETTINGS SNIPPET"
python3 - "$PKG/README.md" <<'PY'
import json, re, sys
md = open(sys.argv[1]).read()
blocks = re.findall(r"```json\n(.*?)```", md, re.S)
if not blocks:
    print("  FAIL  README has no json block"); sys.exit(1)
for b in blocks:
    try:
        obj = json.loads(b)
    except Exception as e:
        print(f"  FAIL  README json does not parse: {e}"); sys.exit(1)
    h = obj.get("hooks")
    if not isinstance(h, list) or not h or h[0].get("type") != "command":
        print("  FAIL  README hook snippet is not a command-hook array"); sys.exit(1)
    if "surface-instincts.sh" not in h[0].get("command", ""):
        print("  FAIL  README hook snippet does not invoke the hook"); sys.exit(1)
print("  PASS  README settings snippet parses and has the right shape")
PY
[ $? -eq 0 ] || FAIL=$((FAIL+1))

# ---------------------------------------------------------------- uninstall
echo; echo "UNINSTALL (README steps, verbatim)"
rm -rf "$HOME"/.claude/skills/instinct-*
rm -f "$HOME/.claude/hooks/surface-instincts.sh" "$HOME/.claude/rules/instincts.md"

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

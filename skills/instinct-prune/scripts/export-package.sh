#!/bin/bash
# export-package.sh — build the publishable package tree from this config.
#
# `~/.claude` is the SOURCE OF TRUTH; the published repo is an export target
# (INSTINCT_PACKAGE_PLAN.md §"Source of truth"). Export is one-way for v1 —
# nothing is read back from the destination.
#
# EXPORTED: every skills/instinct-* dir, hooks/surface-instincts.sh,
#           rules/instincts.md, plus the package's own README/manifest which
#           live in this repo under docs/refinery/ (outside every exported
#           path — nested inside a skill they shipped twice).
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
REQUIRED_PACKAGE_FILES=(".claude-plugin/plugin.json" ".gitignore"
                        "CONTENT-SAFETY.md" "PORTABILITY.md" "README.md"
                        "package-manifest.json" "skills.sh.json")

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

tracked_symlinks() {  # $1 = repo, rest = paths; prints tracked symlink paths
  local repo="$1"; shift
  git -C "$repo" ls-files -s -- "$@" | awk -F'\t' '{split($1,a," "); if (a[1]=="120000") print $2}'
}

tracked_mode() {  # $1 = repo $2 = single path; prints git mode (e.g. 100644)
  git -C "$1" ls-files -s -- "$2" | awk -F'\t' '{split($1,a," "); print a[1]}'
}

# Read committed bytes for $2 (repo-relative path) from $1 (repo) into $3
# (destination file), preserving the tracked executable bit. `git show` writes
# BYTES ONLY and drops the mode `cp` used to carry along, so the exec bit is
# restored from `ls-files -s` explicitly.
export_committed_file() {
  local repo="$1" f="$2" dst="$3"
  mkdir -p "$(dirname "$dst")"
  git -C "$repo" show "HEAD:$f" > "$dst" || die "reading committed bytes failed: $f"
  [ "$(tracked_mode "$repo" "$f")" = "100755" ] && chmod +x "$dst"
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

  # "Tracked pathname" is not the same guarantee as "tracked bytes". Resolve
  # the full flat file list FIRST so the symlink/dirty guards below run before
  # a single byte is written to the stage.
  local -a files
  files=()
  local f
  for p in "${paths[@]}"; do
    [ -e "$SRC/$p" ] || die "missing export input: $SRC/$p"
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      files+=("$f")
    done < <(git -C "$SRC" ls-files -- "$p")
  done
  [ "${#files[@]}" -gt 0 ] || die "git ls-files matched nothing — is the package committed?"

  # Refuse tracked SYMLINKS. `cp`/`git show` both dereference a symlink at
  # export time — a tracked symlink pointing into the private corpus would
  # export the private file's CONTENT under an innocent tracked name, invisible
  # to a "tracked paths only" review. Git marks a symlink blob with mode 120000
  # in `ls-files -s`; check every path in the export surface for it.
  local symlinks; symlinks=$(tracked_symlinks "$SRC" "${files[@]}")
  [ -z "$symlinks" ] || die "tracked symlink(s) would dereference on export:
$symlinks"

  # Refuse a DIRTY export surface. "Git-tracked only" restricts PATHNAMES, not
  # bytes — `cp` used to read the WORKING TREE, so an uncommitted edit to a
  # tracked file shipped bytes nobody reviewed or committed. Requiring a clean
  # tree, then reading from HEAD (below) rather than the working tree, makes
  # "committed bytes only" a structural property instead of a coincidence of
  # this check.
  local dirty; dirty=$(git -C "$SRC" status --porcelain -- "${files[@]}")
  [ -z "$dirty" ] || die "export surface is dirty — commit these first:
$dirty"

  local n=0
  for f in "${files[@]}"; do
    export_committed_file "$SRC" "$f" "$stage/$f"
    n=$((n+1))
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
  #
  # This used to be a wholesale `cp -R "$pkg/." "$stage/"` — it bypassed the
  # git-tracked allowlist entirely: untracked/staged-but-uncommitted files
  # shipped, and a nested .git dir inside $pkg would have been copied whole.
  # docs/refinery lives inside the SAME $SRC repo, so it gets the identical
  # tracked/symlink/dirty gate as everything else, not a shortcut.
  local pkg="${PACKAGE_DIR:-$SRC/docs/refinery}"
  [ -d "$pkg" ] \
    || die "package metadata directory missing: $pkg — refusing an incomplete release"
  if [ -d "$pkg" ]; then
    local pkg_phys src_phys pkg_rel
    pkg_phys=$(cd "$pkg" && pwd -P) || die "cannot resolve $pkg"
    src_phys=$(cd "$SRC" && pwd -P) || die "cannot resolve $SRC"
    case "$pkg_phys" in
      "$src_phys")   pkg_rel="." ;;
      "$src_phys"/*) pkg_rel="${pkg_phys#"$src_phys"/}" ;;
      *) die "PACKAGE_DIR ($pkg_phys) is outside the source repo ($src_phys) — wholesale-copying an ungated tree is exactly the bug this gate exists to prevent";;
    esac

    local required required_repo_path
    for required in "${REQUIRED_PACKAGE_FILES[@]}"; do
      if [ "$pkg_rel" = "." ]; then
        required_repo_path="$required"
      else
        required_repo_path="$pkg_rel/$required"
      fi
      git -C "$SRC" cat-file -e "HEAD:$required_repo_path" 2>/dev/null \
        || die "required package metadata is not committed at HEAD: $required_repo_path"
    done

    local -a pkg_files
    pkg_files=()
    local pf
    while IFS= read -r pf; do
      [ -n "$pf" ] && pkg_files+=("$pf")
    done < <(git -C "$SRC" ls-files -- "$pkg_rel")
    [ "${#pkg_files[@]}" -gt 0 ] || die "PACKAGE_DIR ($pkg) is not git-tracked in $SRC — nothing to export from it safely"

    local pkg_symlinks; pkg_symlinks=$(tracked_symlinks "$SRC" "${pkg_files[@]}")
    [ -z "$pkg_symlinks" ] || die "tracked symlink(s) in package metadata would dereference on export:
$pkg_symlinks"

    local pkg_dirty; pkg_dirty=$(git -C "$SRC" status --porcelain -- "${pkg_files[@]}")
    [ -z "$pkg_dirty" ] || die "package metadata export surface is dirty — commit these first:
$pkg_dirty"

    local dest_rel
    for pf in "${pkg_files[@]}"; do
      if [ "$pkg_rel" = "." ]; then dest_rel="$pf"; else dest_rel="${pf#"$pkg_rel"/}"; fi
      export_committed_file "$SRC" "$pf" "$stage/$dest_rel"
    done
  fi

  # A .git directory reaching the stage would ship the whole history (or the
  # SOURCE repo's history) into the release. Nothing above should be able to
  # produce one anymore, but assert it structurally rather than trusting that.
  local gitdirs; gitdirs=$(find "$stage" -name '.git' 2>/dev/null)
  [ -z "$gitdirs" ] || die "a .git directory reached the stage — refusing to publish it:
$gitdirs"
  return 0
}

gate_stage() {  # $1 = stage dir; the release gate, run on the COPY
  local stage="$1"
  # Sanity first: an empty stage passing a scan proves nothing.
  [ -n "$(find "$stage/skills" -maxdepth 1 -name 'instinct-*' -type d 2>/dev/null)" ] \
    || die "nothing staged to scan"
  verify_stage_manifest "$stage" \
    || die "package manifest does not describe the staged release"
  # Scan the WHOLE stage, never a hand-built subset. The first version
  # enumerated skills/hooks/rules and missed the package metadata copied to the
  # stage ROOT — README and plugin.json would have shipped unscanned while the
  # header claimed everything was gated. Same bug shape as pre-publish's own
  # DEFAULT_SURFACE drift: a list under-enumerates; a directory cannot.
  bash "$HERE/pre-publish.sh" "$stage"
}

reconcile_previous_metadata() {
  local dest="$1" incoming="$2" previous="$1/package-manifest.json"
  [ -f "$previous" ] || return 0
  python3 - "$previous" "$incoming" "$dest" <<'PY'
import json, pathlib, shutil, sys
previous, incoming, destination = map(pathlib.Path, sys.argv[1:])
try:
    old = json.loads(previous.read_text())
    new = json.loads(incoming.read_text())
except Exception as exc:
    raise SystemExit(f"cannot reconcile prior package manifest: {exc}")

def package_paths(data):
    return {item["destination"] for item in data.get("managed", [])
            if isinstance(item, dict) and item.get("ownership") == "package"}

stale = package_paths(old) - package_paths(new)
root = destination.resolve()
for relative in sorted(stale):
    pure = pathlib.PurePosixPath(relative)
    if (pure.is_absolute() or ".." in pure.parts or not pure.parts
            or ".git" in pure.parts):
        raise SystemExit(f"unsafe prior package-owned destination: {relative!r}")
    target = root / pure
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise SystemExit(f"prior package-owned destination traverses symlink: {current}")
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
PY
}

publish() {  # $1 = dest
  local dest="$1"
  case "$dest" in
    "$SRC"|"$SRC"/*) die "destination is inside the source config ($SRC) — pick a directory outside it";;
    "") die "no destination given";;
  esac
  # STAGE is the global set below, not a local — the EXIT trap fires after
  # this function's scope is gone, and a trap referencing a `local` here
  # printed "stage: unbound variable" on every successful run while still
  # leaking the staging dir (BUG D). set -u makes that failure loud but the
  # leak happens either way.
  STAGE=$(mktemp -d "${TMPDIR:-/tmp}/refinery-export.XXXXXX") || die "mktemp -d failed"
  trap '[ -n "${STAGE:-}" ] && rm -rf "$STAGE"' EXIT

  build_stage "$STAGE" || die "staging failed"
  echo "staged:"; (cd "$STAGE" && find . -type f | sed 's/^\./  /' | sort | head -40)

  if ! gate_stage "$STAGE"; then
    die "pre-publish gate REJECTED the staging tree — nothing was written to $dest"
  fi
  echo "gate: clean"

  mkdir -p "$dest" || die "cannot create $dest"
  # AUTHORITATIVE inside-source guard, physical paths. The case-match at the
  # top compares raw strings and is bypassed by a relative dest or a symlink
  # resolving into $SRC — and what it protects is an rm -rf that would delete
  # the LIVE skills/hooks/rules out of the config. Resolve both sides with
  # pwd -P (bash 3.2 has no realpath guarantee) right before the destructive
  # step. The early check stays: it fails fast before staging work.
  local dest_phys src_phys
  dest_phys=$(cd "$dest" 2>/dev/null && pwd -P) || die "cannot resolve $dest"
  src_phys=$(cd "$SRC" && pwd -P) || die "cannot resolve $SRC"
  case "$dest_phys" in
    "$src_phys"|"$src_phys"/*) die "destination resolves inside the source config ($dest_phys) — refusing to rm -rf there";;
  esac
  # Replace only the managed subtrees. A README, LICENSE, .git or anything else
  # the destination gained on its own is left alone.
  local sub
  reconcile_previous_metadata "$dest_phys" "$STAGE/package-manifest.json" \
    || die "could not reconcile metadata owned by the prior package manifest"
  for sub in skills hooks rules; do
    [ -d "$STAGE/$sub" ] && rm -rf "${dest_phys:?}/$sub"
  done
  cp -R "$STAGE/." "$dest_phys/" || die "copy into $dest_phys failed"

  # The stage-only gate above certifies the INCOMING bytes; it says nothing
  # about the destination the operator is about to push. "A README, LICENSE,
  # .git … left alone" above is deliberate for legitimate content, but it also
  # means a previously-committed-then-deleted private file stays recoverable
  # via `git show HEAD^:...` in that same history, invisible to a scan of the
  # working tree. Re-gate the MERGED destination tree, then — if it has
  # history at all — the destination's HISTORY too. A clean stage does not
  # certify the repo; only this does.
  if ! bash "$HERE/pre-publish.sh" "$dest_phys"; then
    die "destination gate REJECTED the merged tree at $dest_phys — fix before pushing (the copy already landed locally; nothing was pushed)"
  fi

  # Repository membership is a Git property, not a `.git is a directory`
  # property. Linked worktrees have a .git FILE, and a nested package
  # directory has no marker of its own even though the enclosing repository's
  # history is exactly what will carry the exported files.
  local history_root=""
  history_root=$(git -C "$dest_phys" rev-parse --show-toplevel 2>/dev/null)
  local history_probe_rc=$?
  if [ "$history_probe_rc" -ne 0 ]; then
    # A marker in an ancestor means Git SHOULD have resolved a worktree. Treat
    # failure there as a broken repository, not as a plain clean directory.
    local probe="$dest_phys" marker_found=0
    while [ "$probe" != "/" ]; do
      [ -e "$probe/.git" ] && { marker_found=1; break; }
      probe=$(dirname "$probe")
    done
    [ "$marker_found" -eq 0 ] \
      || die "destination appears repository-backed at $probe but Git could not resolve its worktree root"
    history_root=""
  fi

  if [ -n "$history_root" ]; then
    if [ "${SKIP_GITLEAKS:-0}" != "1" ]; then
      command -v gitleaks >/dev/null 2>&1 \
        || die "gitleaks not installed — cannot certify destination HISTORY at $history_root (SKIP_GITLEAKS=1 to downgrade, with a release-notes note)"
      gitleaks git "$history_root" --no-banner --exit-code 9 >/dev/null 2>&1
      case $? in
        0) : ;;
        9) die "gitleaks found a credential-shaped secret in DESTINATION HISTORY at $history_root — a clean stage does not certify the repo; history surgery is needed before pushing" ;;
        *) die "gitleaks errored scanning destination history at $history_root" ;;
      esac
    fi

    # Same denylist pre-publish.sh reads, applied to history instead of the
    # working tree — pre-publish.sh is being edited in parallel and is used
    # as a black box (scan(), not sourced), so this reloads the conf itself
    # rather than reaching into pre-publish's internals.
    local hist_conf="${LOCAL_PROJECTS_CONF:-$HOME/.claude/homunculus/local-projects.conf}"
    [ -f "$hist_conf" ] || die "no config at $hist_conf — cannot certify destination history against the denylist"
    local hist_perms
    if [ "$(uname)" = "Darwin" ]; then hist_perms=$(stat -f '%Lp' "$hist_conf"); else hist_perms=$(stat -c '%a' "$hist_conf"); fi
    case "$hist_perms" in *[2367]) die "$hist_conf is world-writable ($hist_perms) — refusing to source it for the history scan";; esac
    local DENY_NAMES="" ALLOW_IDENTIFIERS=""
    local ALLOW_PUBLIC_NAMES="${ALLOW_PUBLIC_NAMES:-}"
    # shellcheck disable=SC1090
    . "$hist_conf"
    [ -n "${DENY_NAMES// /}" ] || die "DENY_NAMES empty in $hist_conf — an empty denylist certifies nothing"

    # `git log -p` alone (no --format) prints the MEDIUM commit header —
    # "Author: Name <email>" — ahead of every diff. For a repo published as
    # github.com/<owner>/<repo>, the owner's own GitHub identity sits in that
    # header on every single commit; a denylist name matching the operator's
    # own identity then trips on 100% of commits regardless of file content.
    # That identity is public-by-construction repo plumbing, not the thing
    # this sweep protects. `--format=%B` replaces the header with just the
    # commit MESSAGE body — `-p` still appends the diff regardless of
    # --format — so this scans exactly "content that got pushed": the patch
    # and the message, never author/committer/date/hash.
    local hist_name hist_hits
    for hist_name in $DENY_NAMES; do
      local hist_public=0 public_name
      for public_name in $ALLOW_PUBLIC_NAMES; do
        [ "$hist_name" = "$public_name" ] && { hist_public=1; break; }
      done
      [ "$hist_public" -eq 1 ] && continue
      hist_hits=$(git -C "$history_root" log --all -p --format=%B 2>/dev/null | grep -Fw -- "$hist_name")
      [ -z "$hist_hits" ] || die "DESTINATION HISTORY at $history_root contains denylisted name [$hist_name] — a clean stage does not certify the repo; history surgery (filter-repo/rebase) is needed before pushing"
    done

    # Scan the same identifier classes as the tree gate across commit messages,
    # paths, and deleted diff content. Gitleaks does not treat ordinary email or
    # absolute home paths as secrets, but both are privacy-bearing history.
    local hist_ident hist_allowed
    while IFS= read -r hist_ident; do
      [ -n "$hist_ident" ] || continue
      hist_allowed=0
      local allowed
      for allowed in $ALLOW_IDENTIFIERS; do
        [ "$hist_ident" = "$allowed" ] && { hist_allowed=1; break; }
      done
      [ "$hist_allowed" -eq 1 ] \
        || die "DESTINATION HISTORY at $history_root contains private identifier [$hist_ident] — history surgery is needed before pushing"
    done < <(git -C "$history_root" log --all -p --format=%B 2>/dev/null \
      | grep -Eo '([A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|/(Users|home)/[A-Za-z0-9._-]+(/[A-Za-z0-9._/-]+)?)' \
      | sort -u)
  fi

  if [ -n "$history_root" ]; then
    echo "exported -> $dest_phys (tree + history gate: clean; repository root: $history_root)"
  else
    echo "exported -> $dest_phys (tree gate: clean; no Git history present)"
  fi
}

verify_stage_manifest() {
  local stage="$1"
  python3 - "$stage/package-manifest.json" "$stage" <<'PY'
import fnmatch, glob, json, pathlib, sys
manifest_path, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
try:
    data = json.loads(manifest_path.read_text())
except Exception as exc:
    raise SystemExit(f"invalid package manifest: {exc}")
if set(data) != {"schema_version", "managed"} or data["schema_version"] != 1:
    raise SystemExit("unsupported package manifest schema")
entries = []
for item in data["managed"]:
    required = {"source", "destination", "type", "mode", "ownership", "replace", "install"}
    if not isinstance(item, dict) or set(item) != required:
        raise SystemExit("package manifest entry does not match schema")
    source = item["source"]
    if pathlib.PurePosixPath(source).is_absolute() or ".." in pathlib.PurePosixPath(source).parts:
        raise SystemExit(f"unsafe package manifest source: {source}")
    matches = glob.glob(str(root / source))
    if not matches:
        raise SystemExit(f"package manifest source absent from stage: {source}")
    entries.append(item)
installer = root / "skills/instinct-prune/scripts/package_manifest.py"
if not installer.is_file():
    raise SystemExit(f"documented manifest installer is missing: {installer.relative_to(root)}")
linker = root / "skills/instinct-prune/scripts/link_agent_skills.py"
if not linker.is_file():
    raise SystemExit(f"documented agent skill linker is missing: {linker.relative_to(root)}")

def covered(relative, item):
    source = item["source"]
    if item["type"] != "directory_glob":
        return relative == source
    source_path = pathlib.PurePosixPath(source)
    rel_path = pathlib.PurePosixPath(relative)
    if len(rel_path.parts) <= len(source_path.parts):
        return False
    candidate = "/".join(rel_path.parts[:len(source_path.parts)])
    return fnmatch.fnmatchcase(candidate, source)

for path in sorted(p for p in root.rglob("*") if p.is_file()):
    relative = path.relative_to(root).as_posix()
    if not any(covered(relative, item) for item in entries):
        raise SystemExit(f"staged file is undeclared by package manifest: {relative}")
readme = (root / "README.md").read_text()
lines = readme.splitlines()
for action in ("install", "update", "uninstall"):
    if not any("package_manifest.py" in line and action in line for line in lines):
        # The documented command wraps after the script name, so also accept
        # an action on the immediately following line.
        if not any("package_manifest.py" in lines[i] and action in lines[i + 1]
                   for i in range(len(lines) - 1)):
            raise SystemExit(f"README does not document manifest action: {action}")
for action in ("install", "uninstall"):
    if not any("link_agent_skills.py" in lines[i] and action in lines[i + 1]
               for i in range(len(lines) - 1)):
        raise SystemExit(f"README does not document agent linker action: {action}")
PY
}

# ---------------------------------------------------------------- selftest
selftest() {
  local ok=1 tmp; tmp=$(mktemp -d "${TMPDIR:-/tmp}/exp-st.XXXXXX") || exit 3
  trap 'rm -rf "$tmp"' EXIT
  chk() { if [ "$2" -eq 1 ]; then echo "  PASS  $1"; else echo "  FAIL  $1"; ok=0; fi; }
  seed_metadata() {
    local repo="$1" pkg="$1/docs/refinery"
    mkdir -p "$pkg/.claude-plugin" "$pkg/evals" \
      "$repo/skills/instinct-prune/scripts"
    echo 'print("fixture installer")' > "$repo/skills/instinct-prune/scripts/package_manifest.py"
    echo 'print("fixture linker")' > "$repo/skills/instinct-prune/scripts/link_agent_skills.py"
    echo '{"name":"fixture"}' > "$pkg/.claude-plugin/plugin.json"
    echo "fixture ignores" > "$pkg/.gitignore"
    echo "fixture content safety" > "$pkg/CONTENT-SAFETY.md"
    echo "fixture portability" > "$pkg/PORTABILITY.md"
    echo '{"$schema":"https://skills.sh/schemas/skills.sh.schema.json"}' > "$pkg/skills.sh.json"
    echo '{"fixture":true}' > "$pkg/evals/privacy-gate-benchmark.json"
    printf '%s\n' \
      'link_agent_skills.py' \
      '  install fixture' \
      'link_agent_skills.py' \
      '  uninstall fixture' \
      'package_manifest.py install' \
      'package_manifest.py update' \
      'package_manifest.py uninstall' > "$pkg/README.md"
    cat > "$pkg/package-manifest.json" <<'JSON'
{"schema_version":1,"managed":[
 {"source":"skills/instinct-*","destination":"skills","type":"directory_glob","mode":"preserve","ownership":"managed","replace":"replace_matches","install":true},
 {"source":"hooks/surface-instincts.sh","destination":"hooks/surface-instincts.sh","type":"file","mode":"executable","ownership":"managed","replace":"replace","install":true},
 {"source":"rules/instincts.md","destination":"rules/instincts.md","type":"file","mode":"preserve","ownership":"managed","replace":"replace","install":true},
 {"source":".claude-plugin/plugin.json","destination":".claude-plugin/plugin.json","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":".gitignore","destination":".gitignore","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":"CONTENT-SAFETY.md","destination":"CONTENT-SAFETY.md","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":"PORTABILITY.md","destination":"PORTABILITY.md","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":"skills.sh.json","destination":"skills.sh.json","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":"README.md","destination":"README.md","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":"package-manifest.json","destination":"package-manifest.json","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false},
 {"source":"evals/privacy-gate-benchmark.json","destination":"evals/privacy-gate-benchmark.json","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false}
]}
JSON
  }

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
  seed_metadata "$tmp/src"

  # The exporter ships tracked files only, so the fixture must be a real repo.
  # __pycache__ is deliberately ADDED here: the strip check must stay meaningful
  # rather than passing because git never saw the file.
  git -C "$tmp/src" init -q 2>/dev/null
  git -C "$tmp/src" add -Af 2>/dev/null
  # A commit, not just a staged index: export reads bytes from HEAD now (BUG
  # A), so a fixture that only stages without committing has no HEAD to read.
  git -C "$tmp/src" -c user.email=t@t -c user.name=t commit -q -m x 2>/dev/null

  SRC="$tmp/src"
  PACKAGE_DIR="$tmp/src/docs/refinery"
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
  git -C "$tmp/src" -c user.email=t@t -c user.name=t commit -q -m "add instinct-three" 2>/dev/null
  local stage2="$tmp/stage2"; mkdir -p "$stage2"
  build_stage "$stage2" >/dev/null 2>&1
  [ -f "$stage2/skills/instinct-three/SKILL.md" ]
  chk "a newly added skill is picked up with no edit to this script" $((1 - $?))

  # Refuse to write inside the source config.
  ( SRC="$tmp/src"; publish "$tmp/src/sub" ) >/dev/null 2>&1
  [ $? -eq 2 ]; chk "refuses a destination inside the source config" $((1 - $?))

  # Symlink bypass: a dest that only RESOLVES inside the source must be refused
  # too. The early check is a raw string compare and cannot see it, so this
  # exercises the physical (pwd -P) guard that sits in front of the rm -rf.
  # The gate must PASS for control to reach that guard, so hand pre-publish a
  # satisfiable config with a name that matches nothing.
  printf 'DENY_NAMES="Zz_Never_Present"\n' > "$tmp/ppconf"; chmod 600 "$tmp/ppconf"
  ln -s "$tmp/src" "$tmp/link"
  ( SRC="$tmp/src"; LOCAL_PROJECTS_CONF="$tmp/ppconf" SKIP_GITLEAKS=1 publish "$tmp/link/sub" ) >/dev/null 2>&1
  [ $? -eq 2 ]; chk "refuses a dest that RESOLVES inside the source (symlink)" $((1 - $?))
  [ -f "$tmp/src/skills/instinct-one/SKILL.md" ]
  chk "and the live source tree survived the attempt" $((1 - $?))

  # Missing input fails closed rather than exporting a partial package.
  # Subshell: die() exits, so calling build_stage directly would end the
  # selftest instead of failing this one check.
  rm "$tmp/src/rules/instincts.md"
  ( build_stage "$tmp/stage3" ) >/dev/null 2>&1
  [ $? -eq 2 ]; chk "a missing export input fails closed" $((1 - $?))

  # ==========================================================================
  # BUG A: tracked symlink and dirty-tracked-file guards. Fresh fixtures below
  # (rather than reusing $tmp/src, which the previous checks left dirty) so
  # each guard is exercised in isolation.
  # ==========================================================================

  # ---- tracked symlink: refused BEFORE any byte is copied (RED) -----------
  local symfix="$tmp/symfix"
  mkdir -p "$symfix/skills/instinct-one" "$symfix/hooks" "$symfix/rules" "$symfix/private"
  echo "one" > "$symfix/skills/instinct-one/SKILL.md"
  echo "hook" > "$symfix/hooks/surface-instincts.sh"
  echo "rule" > "$symfix/rules/instincts.md"
  echo "PRIVATE_MARKER_9f2" > "$symfix/private/secret.md"
  seed_metadata "$symfix"
  git -C "$symfix" init -q 2>/dev/null
  ln -s "$symfix/private/secret.md" "$symfix/skills/instinct-one/innocent.md"
  git -C "$symfix" add -Af 2>/dev/null
  git -C "$symfix" -c user.email=t@t -c user.name=t commit -q -m x 2>/dev/null

  local symout symrc
  symout=$(SRC="$symfix" PACKAGE_DIR="$symfix/docs/refinery" build_stage "$tmp/stage-sym" 2>&1); symrc=$?
  [ "$symrc" -eq 2 ] && echo "$symout" | grep -q "tracked symlink"
  chk "tracked symlink refused before export, correct message (RED)" $((1 - $?))
  { [ ! -e "$tmp/stage-sym/skills/instinct-one/innocent.md" ] \
      && ! grep -rq "PRIVATE_MARKER_9f2" "$tmp/stage-sym" 2>/dev/null; }
  chk "private bytes never reached the stage via the symlink" $((1 - $?))

  # GREEN: same fixture, symlink removed & committed, now passes.
  git -C "$symfix" rm -q skills/instinct-one/innocent.md 2>/dev/null
  git -C "$symfix" -c user.email=t@t -c user.name=t commit -q -m "remove symlink" 2>/dev/null
  local sgout sgrc
  sgout=$(SRC="$symfix" PACKAGE_DIR="$symfix/docs/refinery" build_stage "$tmp/stage-sym-clean" 2>&1); sgrc=$?
  [ "$sgrc" -eq 0 ] && [ -f "$tmp/stage-sym-clean/skills/instinct-one/SKILL.md" ]
  chk "same fixture, symlink removed & committed, passes (GREEN)" $((1 - $?))

  # ---- dirty tracked file: refused, working-tree bytes never ship (RED) ---
  local dirtyfix="$tmp/dirtyfix"
  mkdir -p "$dirtyfix/skills/instinct-one" "$dirtyfix/hooks" "$dirtyfix/rules"
  echo "one" > "$dirtyfix/skills/instinct-one/SKILL.md"
  echo "hook" > "$dirtyfix/hooks/surface-instincts.sh"
  echo "rule" > "$dirtyfix/rules/instincts.md"
  seed_metadata "$dirtyfix"
  git -C "$dirtyfix" init -q 2>/dev/null
  git -C "$dirtyfix" add -Af 2>/dev/null
  git -C "$dirtyfix" -c user.email=t@t -c user.name=t commit -q -m x 2>/dev/null
  echo "uncommitted edit" >> "$dirtyfix/skills/instinct-one/SKILL.md"

  local dirtyout dirtyrc
  dirtyout=$(SRC="$dirtyfix" PACKAGE_DIR="$dirtyfix/docs/refinery" build_stage "$tmp/stage-dirty" 2>&1); dirtyrc=$?
  [ "$dirtyrc" -eq 2 ] && echo "$dirtyout" | grep -qi "dirty"
  chk "dirty tracked file refused — export reads committed bytes only (RED)" $((1 - $?))

  # GREEN: revert the uncommitted edit, same fixture now passes.
  git -C "$dirtyfix" checkout -q -- skills/instinct-one/SKILL.md 2>/dev/null
  local dcout dcrc
  dcout=$(SRC="$dirtyfix" PACKAGE_DIR="$dirtyfix/docs/refinery" build_stage "$tmp/stage-dirty-clean" 2>&1); dcrc=$?
  [ "$dcrc" -eq 0 ] && [ -f "$tmp/stage-dirty-clean/skills/instinct-one/SKILL.md" ]
  chk "same fixture, clean tree, passes (GREEN)" $((1 - $?))

  # ==========================================================================
  # BUG B: package-metadata directory gets the SAME tracked/symlink/dirty gate
  # as every other export path, not a wholesale `cp -R`.
  # ==========================================================================
  local pkgfix="$tmp/pkgfix"
  mkdir -p "$pkgfix/skills/instinct-one" "$pkgfix/hooks" "$pkgfix/rules" "$pkgfix/docs/refinery"
  echo "one" > "$pkgfix/skills/instinct-one/SKILL.md"
  echo "hook" > "$pkgfix/hooks/surface-instincts.sh"
  echo "rule" > "$pkgfix/rules/instincts.md"
  seed_metadata "$pkgfix"
  git -C "$pkgfix" init -q 2>/dev/null
  git -C "$pkgfix" add -Af 2>/dev/null
  git -C "$pkgfix" -c user.email=t@t -c user.name=t commit -q -m x 2>/dev/null
  # Untracked scratch file and a nested .git dir, added AFTER the commit —
  # neither is ever staged by `git add`.
  echo "UNTRACKED_LEAK_MARKER" > "$pkgfix/docs/refinery/scratch.md"
  mkdir -p "$pkgfix/docs/refinery/.git/objects"
  echo "fakegitdata" > "$pkgfix/docs/refinery/.git/objects/blah"

  local pkout pkrc
  # PACKAGE_DIR is a plain global from the block above (still "no-such-package"
  # there) — override it explicitly rather than relying on it being unset, so
  # this exercises the real default-path behavior instead of silently no-op'ing.
  pkout=$(SRC="$pkgfix" PACKAGE_DIR="$pkgfix/docs/refinery" build_stage "$tmp/stage-pkg" 2>&1); pkrc=$?
  [ "$pkrc" -eq 0 ]
  chk "package-metadata fixture with untracked extras still builds" $((1 - $?))
  [ ! -e "$tmp/stage-pkg/scratch.md" ]
  chk "untracked file under PACKAGE_DIR does not reach the stage" $((1 - $?))
  [ -z "$(find "$tmp/stage-pkg" -name '.git' 2>/dev/null)" ]
  chk "nested .git under PACKAGE_DIR does not reach the stage" $((1 - $?))
  [ -f "$tmp/stage-pkg/README.md" ]
  chk "tracked package metadata still lands at stage root" $((1 - $?))

  # The manifest is the export allowlist, not merely install metadata.
  echo "generic undeclared note" > "$pkgfix/docs/refinery/future-private.md"
  git -C "$pkgfix" add -Af docs/refinery/future-private.md 2>/dev/null
  git -C "$pkgfix" -c user.email=t@t -c user.name=t commit -q -m "undeclared metadata fixture" 2>/dev/null
  mkdir -p "$tmp/stage-pkg-undeclared"
  SRC="$pkgfix" PACKAGE_DIR="$pkgfix/docs/refinery" \
    build_stage "$tmp/stage-pkg-undeclared" >/dev/null 2>&1
  verify_stage_manifest "$tmp/stage-pkg-undeclared" >/dev/null 2>&1
  chk "tracked metadata absent from the manifest is rejected (RED)" $?
  git -C "$pkgfix" rm -q docs/refinery/future-private.md 2>/dev/null
  git -C "$pkgfix" -c user.email=t@t -c user.name=t commit -q -m "remove fixture" 2>/dev/null

  # PACKAGE_DIR pointing OUTSIDE the source repo (but existing) must fail
  # closed — wholesale-copying an ungated tree is exactly BUG B.
  local outsidepkg="$tmp/outside-pkg"; mkdir -p "$outsidepkg"; echo "x" > "$outsidepkg/README.md"
  local opout oprc
  opout=$(SRC="$pkgfix" PACKAGE_DIR="$outsidepkg" build_stage "$tmp/stage-pkg-outside" 2>&1); oprc=$?
  [ "$oprc" -eq 2 ] && echo "$opout" | grep -q "outside the source repo"
  chk "PACKAGE_DIR outside the source repo (existing dir) fails closed" $((1 - $?))

  # Package metadata is part of the release contract, not an optional extra.
  # A missing directory must fail instead of producing a successful package
  # with no README/plugin manifest/safety contract.
  local nopeout noperc
  nopeout=$(SRC="$pkgfix" PACKAGE_DIR="$tmp/does-not-exist" build_stage "$tmp/stage-pkg-noop" 2>&1); noperc=$?
  [ "$noperc" -eq 2 ] && echo "$nopeout" | grep -q "package metadata"
  chk "nonexistent PACKAGE_DIR fails closed (RED)" $((1 - $?))

  # ==========================================================================
  # BUG C: destination HISTORY is re-scanned, not just the fresh stage.
  # ==========================================================================
  local histsrc="$tmp/histsrc"
  mkdir -p "$histsrc/skills/instinct-one" "$histsrc/hooks" "$histsrc/rules"
  echo "one" > "$histsrc/skills/instinct-one/SKILL.md"
  echo "hook" > "$histsrc/hooks/surface-instincts.sh"
  echo "rule" > "$histsrc/rules/instincts.md"
  seed_metadata "$histsrc"
  git -C "$histsrc" init -q 2>/dev/null
  git -C "$histsrc" add -Af 2>/dev/null
  git -C "$histsrc" -c user.email=t@t -c user.name=t commit -q -m x 2>/dev/null

  # A destination repo where the deny-name file was committed, then DELETED —
  # HEAD (and thus a stage-only or working-tree-only scan) is clean; only
  # history still has it.
  local histdest="$tmp/histdest"; mkdir -p "$histdest"
  git -C "$histdest" init -q 2>/dev/null
  echo "Client_Zed_9f2 was here" > "$histdest/leftover.md"
  git -C "$histdest" add -Af 2>/dev/null
  git -C "$histdest" -c user.email=t@t -c user.name=t commit -q -m "add secret" 2>/dev/null
  git -C "$histdest" rm -q leftover.md 2>/dev/null
  git -C "$histdest" -c user.email=t@t -c user.name=t commit -q -m "remove secret" 2>/dev/null

  printf 'DENY_NAMES="Client_Zed_9f2"\n' > "$tmp/histconf"; chmod 600 "$tmp/histconf"

  local hpout hprc
  hpout=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
          LOCAL_PROJECTS_CONF="$tmp/histconf" SKIP_GITLEAKS=1 \
          publish "$histdest" 2>&1); hprc=$?
  [ "$hprc" -eq 2 ] && echo "$hpout" | grep -q "DESTINATION HISTORY"
  chk "deleted-but-committed deny-name in dest history is refused (RED, SKIP_GITLEAKS=1)" $((1 - $?))
  git -C "$histdest" log --all --oneline 2>/dev/null | grep -q "add secret"
  chk "and the offending commit is still sitting there — this is a history problem, not fixed by the tool" $((1 - $?))

  # Ordinary identifiers are privacy-bearing even when they are not secrets
  # and do not contain a configured client name.
  local piidest="$tmp/piidest"; mkdir -p "$piidest"
  git -C "$piidest" init -q 2>/dev/null
  printf 'contact %s@%s under /%s/%s/ClientX/private.md\n' \
    'alex' 'example.com' 'Users' 'alex' > "$piidest/old-pii.md"
  git -C "$piidest" add -Af 2>/dev/null
  git -C "$piidest" -c user.email=t@t -c user.name=t commit -q -m "add pii fixture" 2>/dev/null
  git -C "$piidest" rm -q old-pii.md 2>/dev/null
  git -C "$piidest" -c user.email=t@t -c user.name=t commit -q -m "remove pii fixture" 2>/dev/null
  local piiout piirc
  piiout=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
           LOCAL_PROJECTS_CONF="$tmp/histconf" SKIP_GITLEAKS=1 \
           publish "$piidest" 2>&1); piirc=$?
  [ "$piirc" -eq 2 ] && echo "$piiout" | grep -q "private identifier"
  chk "deleted email/home path in destination history is refused (RED)" $((1 - $?))

  # The same history boundary must hold for Git's other two destination
  # shapes. A linked worktree has a .git FILE, and a nested destination has no
  # .git entry at all; both still belong to an enclosing repository whose
  # history is part of what may be published.
  local workrepo="$tmp/workrepo" workdest="$tmp/workdest"
  mkdir -p "$workrepo"
  git -C "$workrepo" init -q 2>/dev/null
  echo "Client_Zed_9f2 was here" > "$workrepo/leftover.md"
  git -C "$workrepo" add -Af 2>/dev/null
  git -C "$workrepo" -c user.email=t@t -c user.name=t commit -q -m "add secret" 2>/dev/null
  git -C "$workrepo" rm -q leftover.md 2>/dev/null
  git -C "$workrepo" -c user.email=t@t -c user.name=t commit -q -m "remove secret" 2>/dev/null
  git -C "$workrepo" worktree add -q -b export-fixture "$workdest" 2>/dev/null

  local wout wrc
  wout=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
         LOCAL_PROJECTS_CONF="$tmp/histconf" SKIP_GITLEAKS=1 \
         publish "$workdest" 2>&1); wrc=$?
  [ "$wrc" -eq 2 ] && echo "$wout" | grep -q "DESTINATION HISTORY"
  chk "linked-worktree destination history is scanned (RED)" $((1 - $?))

  local nestedrepo="$tmp/nestedrepo" nesteddest="$tmp/nestedrepo/package"
  mkdir -p "$nestedrepo"
  git -C "$nestedrepo" init -q 2>/dev/null
  echo "Client_Zed_9f2 was here" > "$nestedrepo/leftover.md"
  git -C "$nestedrepo" add -Af 2>/dev/null
  git -C "$nestedrepo" -c user.email=t@t -c user.name=t commit -q -m "add secret" 2>/dev/null
  git -C "$nestedrepo" rm -q leftover.md 2>/dev/null
  git -C "$nestedrepo" -c user.email=t@t -c user.name=t commit -q -m "remove secret" 2>/dev/null
  mkdir -p "$nesteddest"

  local nout nrc
  nout=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
         LOCAL_PROJECTS_CONF="$tmp/histconf" SKIP_GITLEAKS=1 \
         publish "$nesteddest" 2>&1); nrc=$?
  [ "$nrc" -eq 2 ] && echo "$nout" | grep -q "DESTINATION HISTORY"
  chk "nested repository destination scans the enclosing history (RED)" $((1 - $?))

  # ---- history false positive: the repo owner's own commit AUTHOR identity
  # must NOT trip the gate. A repo published at github.com/<owner>/<repo> has
  # <owner> in "Author: <name> <email>" on every commit via plain `git log -p`
  # — that's public-by-construction plumbing, not content the denylist
  # protects. Real bug found running this against the actual published repo.
  local histdest2="$tmp/histdest2"; mkdir -p "$histdest2"
  git -C "$histdest2" init -q 2>/dev/null
  echo "clean content, no deny word here" > "$histdest2/x.md"
  git -C "$histdest2" add -Af 2>/dev/null
  git -C "$histdest2" -c user.name="AuthorDenyName_7q" -c user.email="t@t" commit -q -m "ordinary commit message" 2>/dev/null

  printf 'DENY_NAMES="AuthorDenyName_7q"\n' > "$tmp/histconf2"; chmod 600 "$tmp/histconf2"

  local hp2out hp2rc
  hp2out=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
           LOCAL_PROJECTS_CONF="$tmp/histconf2" SKIP_GITLEAKS=1 \
           publish "$histdest2" 2>&1); hp2rc=$?
  [ "$hp2rc" -eq 0 ]
  chk "author identity matching a deny name does NOT trip the history gate (false-positive fix)" $((1 - $?))

  # ---- history: a deny name in a commit MESSAGE (not the diff, not the
  # author) must STILL trip. Decision: a commit message is pushed content —
  # same category as a diff — so `--format=%B` keeps message bodies in scope
  # even though it drops the author/committer/date/hash header.
  local histdest3="$tmp/histdest3"; mkdir -p "$histdest3"
  git -C "$histdest3" init -q 2>/dev/null
  echo "clean content, no deny word here" > "$histdest3/x.md"
  git -C "$histdest3" add -Af 2>/dev/null
  git -C "$histdest3" -c user.name=t -c user.email=t@t commit -q -m "mentions MsgOnlyDeny_3z in the message body only" 2>/dev/null

  printf 'DENY_NAMES="MsgOnlyDeny_3z"\n' > "$tmp/histconf3"; chmod 600 "$tmp/histconf3"

  local hp3out hp3rc
  hp3out=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
           LOCAL_PROJECTS_CONF="$tmp/histconf3" SKIP_GITLEAKS=1 \
           publish "$histdest3" 2>&1); hp3rc=$?
  [ "$hp3rc" -eq 2 ] && echo "$hp3out" | grep -q "DESTINATION HISTORY"
  chk "deny name in a commit MESSAGE (not diff, not author) still trips the gate" $((1 - $?))

  # An explicitly public package identifier may intentionally occur in
  # history even when a broad local denylist includes it. The exception is
  # exact; pre-publish.sh separately proves another deny token still trips.
  local histdest4="$tmp/histdest4"; mkdir -p "$histdest4"
  git -C "$histdest4" init -q 2>/dev/null
  printf '%s\n' '@dataclasses.dataclass' 'class PublicRecord: pass' > "$histdest4/x.py"
  git -C "$histdest4" add -Af 2>/dev/null
  git -C "$histdest4" -c user.name=t -c user.email=t@t commit -q -m "PublicOwner_4k release" 2>/dev/null
  printf 'DENY_NAMES="PublicOwner_4k StillPrivate_8m"\n' > "$tmp/histconf4"; chmod 600 "$tmp/histconf4"

  local hp4out hp4rc
  hp4out=$(SRC="$histsrc" PACKAGE_DIR="$histsrc/docs/refinery" \
           LOCAL_PROJECTS_CONF="$tmp/histconf4" ALLOW_PUBLIC_NAMES="PublicOwner_4k" \
           SKIP_GITLEAKS=1 publish "$histdest4" 2>&1); hp4rc=$?
  [ "$hp4rc" -eq 0 ]
  chk "explicit public-name exception covers destination history" $((1 - $?))

  # ==========================================================================
  # BUG D: successful export leaves no unbound-variable trap output and no
  # leaked staging directory.
  # ==========================================================================
  local tmpdir_use="${TMPDIR:-/tmp}"
  local leak_before leak_after
  leak_before=$(find "$tmpdir_use" -maxdepth 1 -name 'refinery-export.*' 2>/dev/null | wc -l | tr -d ' ')

  printf 'DENY_NAMES="Zz_Never_Present_2"\n' > "$tmp/genconf"; chmod 600 "$tmp/genconf"
  local cleandest="$tmp/cleandest"
  mkdir -p "$cleandest"
  echo "obsolete package-owned metadata" > "$cleandest/OBSOLETE-PACKAGE-METADATA.md"
  cat > "$cleandest/package-manifest.json" <<'OLD_MANIFEST'
{"schema_version":1,"managed":[
 {"source":"OBSOLETE-PACKAGE-METADATA.md","destination":"OBSOLETE-PACKAGE-METADATA.md","type":"metadata","mode":"preserve","ownership":"package","replace":"replace","install":false}
]}
OLD_MANIFEST
  local cpout cprc
  cpout=$(SRC="$dirtyfix" PACKAGE_DIR="$dirtyfix/docs/refinery" \
          LOCAL_PROJECTS_CONF="$tmp/genconf" SKIP_GITLEAKS=1 \
          publish "$cleandest" 2>&1); cprc=$?
  [ "$cprc" -eq 0 ]
  chk "clean fixture publishes end-to-end successfully" $((1 - $?))
  echo "$cpout" | grep -qi "unbound variable"
  chk "no unbound-variable trap output on success (BUG D)" $?
  leak_after=$(find "$tmpdir_use" -maxdepth 1 -name 'refinery-export.*' 2>/dev/null | wc -l | tr -d ' ')
  [ "$leak_before" -eq "$leak_after" ]
  chk "staging dir does not leak after successful publish (BUG D)" $((1 - $?))
  [ -f "$cleandest/skills/instinct-one/SKILL.md" ]
  chk "destination actually received the exported files" $((1 - $?))
  [ ! -e "$cleandest/OBSOLETE-PACKAGE-METADATA.md" ]
  chk "metadata removed from the new manifest is reconciled from the prior receipt" $((1 - $?))

  [ $ok -eq 1 ] && { echo "SELFTEST: PASS"; exit 0; } || { echo "SELFTEST: FAIL"; exit 1; }
}

case "${1:-}" in
  --selftest) selftest ;;
  "") die "usage: export-package.sh <dest-dir>" ;;
  *)  publish "$1" ;;
esac

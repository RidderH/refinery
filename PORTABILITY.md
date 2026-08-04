# Portability — platform assumptions and defined behavior

What the scripts assume, and what happens when an assumption does not hold. "Fails
closed" below means a loud nonzero exit with a reason — never a clean-looking run that
silently did nothing.

## Shell: bash 3.2 is the floor

macOS ships bash 3.2 and every script is written to it: no `mapfile`, no associative
arrays, no `${var,,}`. (Both absences have bitten during development; the workarounds are
in the scripts with comments.) Newer bash works; `sh`/`zsh`/`dash` are not supported —
invoke as `bash script.sh`.

## Corpus root: a portable hook override, with a compatibility default

`surface-instincts.sh` accepts a corpus directory as its first argument or through
`REFINERY_INSTINCT_DIR`. This lets an agent-specific lifecycle adapter use any suitable
location. Without either, it uses the established default
`$HOME/.claude/homunculus/instincts/personal`.

The other v1 tooling still uses that established default, so do not set a different hook
directory unless you have also arranged for the skills to use the same corpus. Defined behavior
when the default path is absent:

- `surface-instincts.sh` — exits 0 silently (no corpus is a valid state, not an error).
- `instinct-buckets.sh` — exit 1 on a missing dir, exit 4 on an empty one ("walked 0
  files"). An empty corpus is a failed run, never a clean report.
- `pre-publish.sh` — exit 2 without its config file; an unreadable gate must not pass.

## Tools

| Tool | Needed by | If missing |
|---|---|---|
| `bash` 3.2+ | everything | nothing runs |
| `python3` | prune's shortlist/retire/ruling, all of distill and format's scripts | command-not-found; buckets and the hook still work (pure bash) |
| `git` | `export-package.sh` only | dies: "not a git repo — the export ships tracked files only" |
| `gitleaks` | `pre-publish.sh` layer 2 | exit 2, fail-closed; `SKIP_GITLEAKS=1` downgrades deliberately and should be noted in release notes |
| `grep`, `awk`, `sed`, `find` | throughout | developed against macOS/BSD + GNU userlands; uses a few widely-supported extensions (`grep --binary-files`, `find -maxdepth`, `find -delete`) that a strictly minimal POSIX userland lacks — that userland is not a target |

Python: requires ≥ 3.9 (`str.removesuffix`, used in `shortlist.py` and `ruling.py`);
tested on 3.14.

## Symlinks: supported for skills, not agent integration files

The four canonical skill directories may be symlinked into `~/.agents/skills` or a
project's `.agents/skills`. Agent discovery resolves each named skill directly, and bundled
commands resolve from the directory containing `SKILL.md`; they do not depend on a recursive
scan finding the symlink.

Hooks and instruction files still install by copy through `package_manifest.py`. Some `grep -r`
implementations do not descend into symlinked directories, and treating arbitrary hook/rule
destinations as links would weaken the package installer's ownership boundary. The dedicated
`link_agent_skills.py` therefore manages only `skills/instinct-*`, refuses conflicts before
creating any link, and removes only links that point to the selected checkout.

## Whitespace in paths

Corpus filenames cannot contain spaces — the format contract requires kebab-case ids
equal to the filename. A `$HOME` (or project parent) containing spaces is **not
supported**: root lists inside `instinct-buckets.sh` and the surface check in
`pre-publish.sh` word-split on whitespace. Defined behavior is misparsed root lists, so:
don't. This is a known, documented limitation rather than a bug queue item — fixing it
means rewriting every root loop for a case no development machine has.

## mktemp and sandboxed TMPDIR

Scripts create temp files as `mktemp "${TMPDIR:-/tmp}/name.XXXXXX"` — never bare
`mktemp`/`mktemp -t`, which on macOS ignore `$TMPDIR` and can point at a dir that is
unwritable under a sandbox. The observed failure mode of the bare form was a clean-looking
run that reported an empty corpus with exit 0; the explicit-template form fails closed
instead. Same discipline applies to any new script.

## Windows

Not supported natively. WSL (or any environment providing bash + python3 + POSIX tools)
is the supported route. No script has been tested there — treat WSL as "expected to work,
unverified".

## Locale and filenames

Corpus ids are ASCII kebab-case by contract, which sidesteps encoding issues in the
tooling. Instinct *bodies* may contain any UTF-8; the scripts only grep and hash them.

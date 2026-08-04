# Portability — platform assumptions and defined behavior

What the scripts assume, and what happens when an assumption does not hold. "Fails
closed" below means a loud nonzero exit with a reason — never a clean-looking run that
silently did nothing.

## Shell: bash 3.2 is the floor

macOS ships bash 3.2 and every script is written to it: no `mapfile`, no associative
arrays, no `${var,,}`. (Both absences have bitten during development; the workarounds are
in the scripts with comments.) Newer bash works; `sh`/`zsh`/`dash` are not supported —
invoke as `bash script.sh`.

## Config root: `$HOME/.claude`, hardcoded

The corpus lives at `$HOME/.claude/homunculus/instincts/personal` and the scripts say so
literally. Nonstandard config locations (`CLAUDE_CONFIG_DIR`, XDG paths) are **not
supported**. Defined behavior when the path is absent:

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

## Symlinks: install by copy

Some `grep -r` implementations do not descend into symlinked directories, so a skill
installed as a symlink can be invisible to recursive scans (this exact failure is in the
corpus that built this package). The README's install is `cp -R` for that reason. Symlink
installs are unsupported.

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

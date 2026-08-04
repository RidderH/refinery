# refinery

Turn what you learn in a session into something the next session already knows.

An Agent Skills suite with optional Claude Code integration: four skills capture lessons
from real sessions, keep them findable, retire the ones that stop being true, and promote
the ones that keep recurring into skills, rules, or hook proposals.

The loop is **learn → distill → improve**, and every step is human-started.

## What's in it

| Component | What it does |
|---|---|
| `instinct-analyze` | Capture: read a session, propose lessons, gate them before anything is written |
| `instinct-format` | The file contract every lesson obeys — frontmatter, triggers, evidence |
| `instinct-prune` | Retire: bucket the corpus, shortlist candidates, human-verify, archive transactionally |
| `instinct-distill` | Promote recurring lessons into skills, rules, commands, agents, or hook proposals |
| `hooks/surface-instincts.sh` | Optional Claude session-start notice that the corpus exists |
| `rules/instincts.md` | Optional Claude rule that tells Claude when to consult the corpus |

## Honest expectations

**This ships the machinery, not the lessons.** The corpus is yours and starts empty.

**Distillation needs months of recurrence data.** The whole premise is that a lesson earns
promotion by recurring across many sessions — a pattern seen once is a coincidence. Running
`instinct-distill` on a two-week-old corpus will mostly tell you it has nothing to promote,
and that is the correct answer. Gates are deliberately not lowered for small corpora: a
stranger's first distillation becoming a bad always-on rule would poison every later session.

**Consequential workflows are human-started.** Claude's `disable-model-invocation` metadata
and OpenAI's `allow_implicit_invocation: false` policy both protect analyze, prune, and
distill. They can archive files or propose durable guidance, so a human starts every run.
There is no background capture; the Stop-hook that once proposed lessons inline was removed
because it bypassed every verification gate.

## Install for Codex and compatible agents

Clone the repository once, then link the canonical skill directories into the shared
user-level Agent Skills directory:

```bash
git clone <this-repo> ~/refinery
python3 ~/refinery/skills/instinct-prune/scripts/link_agent_skills.py \
  install ~/refinery
mkdir -p ~/.claude/homunculus/instincts/personal
```

The default destination is `~/.agents/skills`. Pass another directory as the final argument
for an agent or project that uses a different discovery root:

```bash
python3 ~/refinery/skills/instinct-prune/scripts/link_agent_skills.py \
  install ~/refinery /path/to/project/.agents/skills
```

The linker performs a complete conflict check before creating anything. It never replaces a
real directory or a symlink owned by another checkout. Because the links point at the clone,
`git pull` updates the installed skills without a reinstall.

### Optional skills.sh install

skills.sh is a distribution channel, not a runtime requirement. Once this repository is
public, users can discover and install its skills from GitHub with:

```bash
npx skills add RidderH/refinery
```

Local development does not need skills.sh or a public repository; the symlink install above
works directly from any checkout.

## Install the Claude Code integration

Claude's hook and always-on rule are host-specific and deliberately use the existing copied
package installer. Run this in addition to the shared skill links when you want the complete
Claude integration:

```bash
git clone <this-repo> ~/refinery
python3 ~/refinery/skills/instinct-prune/scripts/package_manifest.py \
  install ~/refinery/package-manifest.json ~/refinery ~/.claude
mkdir -p ~/.claude/homunculus/instincts/personal
```

Then register the session-start hook by adding this to the `hooks.SessionStart` array in
`~/.claude/settings.json` — **the package never edits your settings for you**:

```json
{
  "hooks": [
    {
      "type": "command",
      "command": "bash ~/.claude/hooks/surface-instincts.sh",
      "statusMessage": "Surfacing instincts"
    }
  ]
}
```

### Update the Claude integration

The versioned package manifest owns exactly the four skills, hook, and rule. Update
reconciles those managed paths, removing files deleted upstream while leaving the corpus
and all unrelated configuration untouched:

```bash
python3 ~/refinery/skills/instinct-prune/scripts/package_manifest.py \
  update ~/refinery/package-manifest.json ~/refinery ~/.claude
```

Your corpus lives in `~/.claude/homunculus/` and is never touched by an update.

### Uninstall

Remove the shared Agent Skills links without touching their source checkout:

```bash
python3 ~/refinery/skills/instinct-prune/scripts/link_agent_skills.py \
  uninstall ~/refinery
```

To remove the copied Claude integration:

```bash
python3 ~/refinery/skills/instinct-prune/scripts/package_manifest.py \
  uninstall ~/refinery/package-manifest.json ~/refinery ~/.claude
```

Then remove the whole `SessionStart` object you added above — deleting only the `command`
line leaves an empty `hooks: []` array behind. Your corpus is left in place; delete
`~/.claude/homunculus/` yourself if you want it gone.

## Local configuration

Some tooling resolves cited paths against the projects you actually work in. Those names
stay on your machine: copy `skills/instinct-prune/scripts/local-projects.conf.example` to
`~/.claude/homunculus/local-projects.conf` and fill it in. The file is gitignored by
design, and the publish gate refuses to run without it.

Everything works without the file — path resolution falls back to generic discovery.

## Requirements

- An Agent Skills-compatible coding agent; Claude Code is required only for its hook/rule integration
- `bash` 3.2+, `python3` (≥ 3.9), standard POSIX tools; `gitleaks` only for the publish gate
- Developed and tested on macOS (bash 3.2). Linux should work; Windows means WSL.
  Full platform contract — what's assumed, and what happens when an assumption fails —
  in [PORTABILITY.md](PORTABILITY.md).

The installed runtime uses only Python's standard library. PyYAML is an optional,
test-only dependency used to give contributors an independent check that skill frontmatter is
valid YAML; it is never installed by the package manifest.

## Contributor validation

From a published Refinery checkout, create an isolated test environment and run:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-test.txt
python3 -m unittest discover -s tests -v
python3 validate_skills.py .
```

The validator uses PyYAML's safe loader and adds stricter skill-contract checks for duplicate
keys, required metadata, skill identity, known fields, and invocation-flag types. PyYAML is an
independent syntax check, not the normative parser for instinct records. The test suite also runs
the shipped canonical parser with site-packages disabled, preserving the zero-dependency runtime
contract.

## Content safety

The corpus is an injection surface: distill turns instinct prose into skills and rules,
so a hostile instinct file is a real threat model. The short version: bodies are quoted
data, imports are adversarial until a human reads them, every generated artifact passes
per-artifact human review, and hooks are propose-only, permanently. The full contract is
[CONTENT-SAFETY.md](CONTENT-SAFETY.md) and is restated inside the skills that read the
corpus.

## Status

v1. Export is one-way — this repo is generated from a working config, so please open an
issue before sending a PR, so we can agree where the change should actually live.

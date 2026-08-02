# refinery

Turn what you learn in a session into something the next session already knows.

A Claude Code plugin: four skills and one hook that capture lessons from real sessions,
keep them findable, retire the ones that stop being true, and promote the ones that keep
recurring into skills, rules, or hook proposals.

The loop is **learn → distill → improve**, and every step is human-started.

## What's in it

| Component | What it does |
|---|---|
| `instinct-analyze` | Capture: read a session, propose lessons, gate them before anything is written |
| `instinct-format` | The file contract every lesson obeys — frontmatter, triggers, evidence |
| `instinct-prune` | Retire: bucket the corpus, shortlist candidates, human-verify, archive transactionally |
| `instinct-distill` | Promote recurring lessons into skills, rules, commands, agents, or hook proposals |
| `hooks/surface-instincts.sh` | Session-start notice that the corpus exists, and how to search it |
| `rules/instincts.md` | The always-on rule that tells Claude when to consult the corpus |

## Honest expectations

**This ships the machinery, not the lessons.** The corpus is yours and starts empty.

**Distillation needs months of recurrence data.** The whole premise is that a lesson earns
promotion by recurring across many sessions — a pattern seen once is a coincidence. Running
`instinct-distill` on a two-week-old corpus will mostly tell you it has nothing to promote,
and that is the correct answer. Gates are deliberately not lowered for small corpora: a
stranger's first distillation becoming a bad always-on rule would poison every later session.

**Nothing is auto-invoked.** `instinct-prune` and `instinct-distill` are marked
`disable-model-invocation` — they archive files and rewrite config, so a human starts every
run. There is no background capture; the Stop-hook that once proposed lessons inline was
removed because it bypassed every verification gate.

## Install

```bash
git clone <this-repo> ~/refinery
cp -R ~/refinery/skills/instinct-* ~/.claude/skills/
cp ~/refinery/hooks/surface-instincts.sh ~/.claude/hooks/
cp ~/refinery/rules/instincts.md ~/.claude/rules/
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

### Update

Re-run the three `cp` lines. Your corpus lives in `~/.claude/homunculus/` and is never
touched by an update.

### Uninstall

```bash
rm -rf ~/.claude/skills/instinct-*
rm -f ~/.claude/hooks/surface-instincts.sh ~/.claude/rules/instincts.md
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

- Claude Code
- `bash`, `python3`, standard POSIX tools
- Developed and tested on macOS (bash 3.2). Linux should work; Windows is not yet
  characterized.

## Status

v1. Export is one-way — this repo is generated from a working config, so please open an
issue before sending a PR, so we can agree where the change should actually live.

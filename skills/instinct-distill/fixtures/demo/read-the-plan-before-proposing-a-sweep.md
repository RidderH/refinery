---
id: read-the-plan-before-proposing-a-sweep
trigger: "about to propose a bulk codemod |
          plan says do not do bulk drive-by migrations |
          out-of-scope section already answered this |
          migration-debt policy"
action: "Re-read the plan's own out-of-scope and migration-debt sections before proposing a bulk sweep — a rejection recorded there was already reviewed and signed off."
confidence: 0.7
evidence_count: 3
domain: workflow
source: "session-observation"
created: "2026-03-19"
updated: "2026-07-11"
last_checked: "2026-07-11"
version_pin: null
promoted_to: null
schema_version: 2
---

# The plan already rejected your sweep; go and read it

## Symptom

A proposal to "codemod the rest while we are in here", on a plan whose own text says
`DO NOT do bulk drive-by migrations`.

## What's actually happening

The out-of-scope section is written once, early, and never re-read. By the time the sweep
looks attractive, the reason it was rejected has fallen out of context.

## Do this

1. Grep the plan for `out of scope`, `migration-debt`, `lazy migration` before proposing any
   bulk change.
2. Revisit a recorded rejection only with documented evidence, never a silent override.

## Evidence (n=3, latest 2)

- **2026-07-11** `successful_recall` — this file was read before drafting the sweep proposal;
  the plan's migration-debt section was grepped first and the proposal was dropped.
- **2026-05-28** `successful_recall` — consulted during a refactor; the out-of-scope list
  named the exact directory the sweep would have touched, and the sweep was not proposed.

→ full history: no archive yet; the founding entry is below.

## Additional evidence

- **2026-03-19** `first_observation` — the founding observation: a bulk deprecation was
  proposed on a plan that had explicitly rejected it two sections earlier.

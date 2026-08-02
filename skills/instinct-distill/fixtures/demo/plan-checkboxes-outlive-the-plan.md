---
id: plan-checkboxes-outlive-the-plan
trigger: "plan file still has unticked boxes after the work shipped |
          stale plan document |
          checklist disagrees with the repo"
action: "Archive a plan the moment its last item lands — an unticked box in a shipped plan reads as outstanding work to the next session."
confidence: 0.3
evidence_count: 2
domain: workflow
source: "session-observation"
created: "2026-02-11"
updated: "2026-04-23"
last_checked: "2026-04-23"
version_pin: null
promoted_to: null
schema_version: 2
---

# A shipped plan with unticked boxes is a lie the next session believes

## Symptom

A session opens a plan file, sees three unticked items, and reimplements work that is already
merged.

## What's actually happening

The plan was never archived. Nothing in the repo distinguishes "not done" from "done and never
ticked", so the checklist outranks the code in the reader's mind.

## Do this

1. Move the plan under an archive directory in the same commit as its final item.
2. Never trust a checkbox against `git log` — the log is the record.

## Evidence (n=2, latest 2)

- **2026-04-23** — a shipped migration plan was picked up again three weeks later and half
  reimplemented. Entry predates the typed schema and has never been migrated.
- **2026-02-11** — first sighting on the web app's rollout plan. Also un-migrated.

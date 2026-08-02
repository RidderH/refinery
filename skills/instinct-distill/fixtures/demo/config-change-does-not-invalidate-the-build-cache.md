---
id: config-change-does-not-invalidate-the-build-cache
trigger: "config change ignored by the build |
          cache hit after editing a config file |
          stale bundle served from the build cache |
          build cache invalidation"
action: "Invalidate the build cache explicitly whenever a config file changes; nothing in the cache tracks config, so the stale entry stays valid forever."
confidence: 0.4
evidence_count: 3
domain: harness
source: "merge-2026-06-30"
created: "2026-01-08"
updated: "2026-06-30"
last_checked: "2026-06-30"
version_pin: null
promoted_to: null
schema_version: 2
---

# Nothing invalidates a build cache on your behalf

## Symptom

An edited config file has no effect on the next build. Deleting the cache directory by hand
makes the change appear, which reads as "the cache is broken".

## What's actually happening

Three separate lessons about cache invalidation were merged into this document on 2026-06-30.
Each arrived with its own founding observation, and each of those observations is the first
sighting of a *different* source lesson — the merged document inherits their evidence, never
their recurrence.

## Do this

1. Treat every config file as a cache input and declare it as one.
2. When merging lessons, re-type the inherited entries as `first_observation` — a five-source
   merge otherwise manufactures five recurrences on five distinct dates.
3. See [[build-cache-key-omits-the-config-hash]] for the cache-key half.

## Evidence (n=3, latest 2)

- **2026-06-30** `first_observation` — *(from docker-layer-cache-ignores-build-args)* the image
  rebuilt from a cached layer after a build arg changed.
- **2026-05-21** `first_observation` — *(from bundler-cache-ignores-env-files)* an edited env
  file left the emitted bundle unchanged on service-a.

→ full history: this document's three entries are listed above and below; no archive yet.

## Additional evidence

- **2026-01-08** `first_observation` — *(from test-runner-cache-ignores-config)* the test runner
  reused a cached transform after its config was rewritten.

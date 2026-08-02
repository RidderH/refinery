---
id: build-cache-key-omits-the-config-hash
trigger: "stale build output after a config change |
          cache hit on a config file the key does not cover |
          build cache key |
          rebuild produces the previous bundle"
action: "Put the config file's hash into the build cache key — a config change that is not in the key reads as a cache hit and serves the stale bundle."
confidence: 0.7
evidence_count: 5
domain: harness
source: "session-observation"
created: "2026-02-20"
updated: "2026-06-18"
last_checked: "2026-06-18"
version_pin: null
promoted_to: null
schema_version: 2
---

# The build cache key must cover every input, and config is an input

## Symptom

`build succeeded (cached)` immediately after editing the config, and the emitted bundle is
byte-identical to the previous run. No warning, no cache miss, no error.

## What's actually happening

The cache key is computed from the source tree only. The config file lives outside that tree,
so changing it moves nothing in the key. A cache hit is therefore correct behaviour for a key
that never saw the change — the bug is the key's coverage, not the cache.

## Do this

1. Hash the config file and fold it into the cache key before the first build runs.
2. When a build "succeeds instantly", print the key inputs — a cache hit you cannot explain
   is a key you have not enumerated.
3. See [[config-change-does-not-invalidate-the-build-cache]] for the invalidation half.

## Evidence (n=5, latest 2)

- **2026-06-18** `correction` — the fix is the key, not a `--no-cache` flag; forcing a full
  rebuild hid the defect for two weeks and doubled CI time.
- **2026-05-02** `repeated_failure` — same stale-bundle symptom on the web app, re-diagnosed
  from scratch as a "flaky cache" without opening this file.

→ full history: `build-cache-key-omits-the-config-hash.evidence.md`

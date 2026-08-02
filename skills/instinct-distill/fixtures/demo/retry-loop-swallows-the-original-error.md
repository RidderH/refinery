---
id: retry-loop-swallows-the-original-error
trigger: "retry exhausted with no cause |
          last error only, first error lost |
          exponential backoff hides the real failure |
          RetryError: all attempts failed"
action: "Capture the FIRST exception in a retry loop, not the last — the final attempt usually fails for a different, less informative reason."
confidence: 0.3
evidence_count: 2
domain: debugging
source: "session-observation"
created: "2026-04-05"
updated: "2026-07-02"
last_checked: "2026-07-02"
version_pin: null
promoted_to: null
schema_version: 2
---

# A retry loop reports the last failure, and the last failure is the wrong one

## Symptom

`RetryError: all attempts failed` with a final cause of `ConnectionResetError`, while the
first attempt failed with a `403` that explains everything.

## What's actually happening

The loop overwrites its `last_err` variable on every pass. By the time it raises, the
diagnostic attempt — the first one, against a warm connection — has been discarded.

## Do this

1. Record attempt 1's exception separately and attach it to the raised error.
2. Log every attempt at debug level; a single aggregated failure is not a trace.

## Evidence (n=2, latest 2)

- **2026-07-02** `?` — a retry wrapper on service-a surfaced only the final timeout; unclear
  from the transcript whether this file was consulted before the fix, so the outcome is not
  typeable and this file stays unverified.
- **2026-04-05** `repeated_failure` — the same swallowed-403 diagnosis was re-derived from
  scratch, a week after the loop was first written this way.

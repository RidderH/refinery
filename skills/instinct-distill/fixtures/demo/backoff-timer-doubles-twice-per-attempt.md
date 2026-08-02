---
id: backoff-timer-doubles-twice-per-attempt
trigger: "backoff delay grows faster than expected |
          retry sleeps 64s on attempt 4 |
          delay doubled in two places |
          exponential backoff wrong base"
action: "Double the backoff delay in exactly one place — a helper that doubles plus a caller that doubles gives 4^n, and the first three attempts look correct."
confidence: 0.4
evidence_count: 2
domain: debugging
source: "session-observation"
created: "2026-05-15"
updated: "2026-05-15"
last_checked: "2026-05-15"
version_pin: null
promoted_to: null
schema_version: 2
---

# Two places doubling the same delay is 4^n, and it hides until attempt 4

## Symptom

Attempt 4 sleeps 64 seconds where the config says 8. Attempts 1-3 (1s, 4s, 16s) look close
enough to plausible that nobody checks them.

## What's actually happening

The backoff helper returns `base * 2**n`, and the caller then multiplies its own accumulator
by 2 again. The error is multiplicative, so it is invisible at n=1 and obvious only past n=3.

## Do this

1. Assert the full delay sequence in a test, not just "the delay grew".
2. Keep the doubling in the helper and make the caller's accumulator read-only.

## Evidence (n=2, latest 2)

- **2026-05-15** `repeated_failure` — reintroduced during a refactor of the retry helper on
  the web app; the same 64s-on-attempt-4 symptom, re-diagnosed without opening this file.
- **2026-05-15** `repeated_failure` — found again later the same session in a second caller
  that kept its own accumulator. One session, written twice.

---
id: test-database-is-not-reset-between-suites
trigger: "second test run fails where the first passed |
          expect(count).toBe(before + 1) off by one |
          leftover rows from a previous suite |
          integration test passes only on a clean database"
action: "TRUNCATE in test setup, never teardown — a suite that cleans up after itself leaves the database dirty the moment a run crashes mid-test."
confidence: 0.5
evidence_count: 2
domain: testing
source: "session-observation"
created: "2026-02-14"
updated: "2026-07-21"
last_checked: "2026-07-21"
version_pin: null
promoted_to: null
schema_version: 2
---

# Reset the database before the test, not after it

## Symptom

The suite passes on a clean checkout and fails on the second run with an off-by-one row
count. Deleting the test database by hand makes it pass again, once.

## What's actually happening

Cleanup lives in teardown. Any run that crashes mid-test never reaches it, so the next run
starts against rows it did not create — and the failure surfaces in whichever test asserts a
count, not in the one that leaked.

## Do this

1. `TRUNCATE … CASCADE` in setup, then insert known fixtures.
2. For an idempotent endpoint you cannot reset, assert "exactly 1 row after N calls" against
   a real database rather than a delta.

## Evidence (n=2, latest 2)

- **2026-04-27** `repeated_failure` — reintroduced on service-a's suite during a fixture
  refactor; the cleanup moved back into `afterAll` and the second run failed again.
- **2026-02-14** `repeated_failure` — the web app's integration suite failed on every run
  after the first, re-diagnosed as a flaky assertion rather than a dirty database.

→ full history: `test-database-is-not-reset-between-suites.evidence.md`

---
id: schema-cache-serves-the-config-from-before-the-change
trigger: "column does not exist after a migration |
          stale schema cache |
          config change not visible to the API |
          NOTIFY pgrst, 'reload schema'"
action: "Reload the API's schema cache after a migration; a config or schema change is not picked up on its own and the cached shape is served indefinitely."
confidence: 0.6
evidence_count: 2
domain: supabase
source: "session-observation"
created: "2026-03-02"
updated: "2026-06-09"
last_checked: "2026-06-09"
version_pin: null
promoted_to: null
schema_version: 2
---

# The API caches your schema, and a migration does not tell it

## Symptom

`column "tenant_id" does not exist` from the REST layer, minutes after a migration that
plainly added it. `psql` sees the column.

## What's actually happening

The API process holds a cached schema built at boot. A migration changes the database and
nothing notifies the cache, so the stale shape is served until something reloads it.

## Do this

1. Emit `NOTIFY pgrst, 'reload schema'` at the end of any migration that changes shape.
2. When the database and the API disagree, suspect the cache before the migration.

## Evidence (n=2, latest 2)

- **2026-06-09** `repeated_failure` — the missing-column error was re-diagnosed as a failed
  migration on service-a; the migration had applied cleanly and the cache was stale.
- **2026-03-02** `first_observation` — first sighting, immediately after adding a column to
  the web app's tenants table.

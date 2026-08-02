# Evidence archive — test-database-is-not-reset-between-suites

Full history. The lesson lives in `test-database-is-not-reset-between-suites.md`; this file
exists so the pattern *across* entries stays visible.

**This archive is deliberately written in the UNSUPPORTED paragraph shape.** Every entry
below is a bare paragraph with no leading `-`, which is the fourth entry shape
`instinct-distill/SKILL.md` names as handled by nothing. The parser yields **zero** entries
for it.

That is the whole point of the fixture. The lesson's body excerpt parses two
`repeated_failure` entries on distinct dates and reads a clean `eligible` **on its own** —
its `evidence_count: 2` matches those two, so the declared-vs-counted self-check does not
fire and nothing else stands in the way. Meanwhile the real newest entry here is a
`successful_recall`: the value that DECLINES a cluster outright. A reader that quietly fell
back to the body would promote a lesson this record says is already working.

The frontmatter count is stale with respect to this file, and that is realistic rather than
sloppy: entries were appended here in the wrong shape and `evidence_count` was never bumped.
It is also load-bearing — bump it to 4 and the body-only verdict degrades to
`ineligible: parsed 2 of 4 declared entries`, which is a loud failure, so the fixture would
no longer demonstrate a *silent wrong pass*.

2026-07-21 `successful_recall` — the newest entry, and the one the body excerpt does not
carry. This file was read before writing a new integration suite for service-a; the reset was
put in setup from the start and the second run passed unchanged. The lesson is working as
intended and needs no promotion.

2026-06-03 `confirmation` — a second suite on the web app was observed to fail on exactly this
pattern without anyone consulting this file. The mechanism matched the description here.

2026-04-27 `repeated_failure` — reintroduced during a fixture refactor; the cleanup moved back
into `afterAll` and the second run failed again.

2026-02-14 `first_observation` — first sighting: the web app's integration suite failed on
every run after the first, and the leaked rows were traced to a crashed earlier run.

# Evidence archive — build-cache-key-omits-the-config-hash

Full history. The lesson lives in `build-cache-key-omits-the-config-hash.md`; this file
exists so the pattern *across* entries stays visible.

- **2026-06-18** `correction` — the fix is the key, not a `--no-cache` flag; forcing a full
  rebuild hid the defect for two weeks and doubled CI time.
- **2026-05-02** `repeated_failure` — same stale-bundle symptom on the web app, re-diagnosed
  from scratch as a "flaky cache" without opening this file.
- **2026-04-14** `confirmation` — service-a's pipeline showed the identical instant-success
  pattern after a config edit; nobody consulted this file, and the mechanism matched.
- **2026-03-11** `repeated_failure` — the config hash was added to the key, then dropped again
  during a refactor of the key builder; the stale bundle returned within one day.
- **2026-02-20** `first_observation` — first sighting: a config change to the web app's bundler
  produced `build succeeded (cached)` and an unchanged output file.

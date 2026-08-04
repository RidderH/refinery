# Evidence migration and parser limits

This reference holds the evidence-maintenance detail used while converting or repairing a corpus.
The main skill keeps the promotion procedure and safety checkpoints.

## Settled inventory rule

One incident is one evidence entry. `evidence_count` inventories observed incidents; it does not
count only gate-qualifying dates. Several failures in one session remain separate entries dated
that day. The recurrence gate independently deduplicates dates, so inventory detail does not
inflate distinct-date evidence.

Consequences:

- Split a bullet that narrates multiple incidents; preserve its prose.
- Repair a declared-versus-parsed shortfall by making incidents readable, not by lowering the
  declared number.
- Lower the number only when the claimed incident was never recorded anywhere.
- Treat folded-incident detection as human judgment until a measured heuristic exists.

## Parser and migration contract

`instinct_record.py` owns frontmatter and body structure. `build_index.py` owns typed evidence
outcomes. `migrate_evidence.py` brings legacy evidence to the indexed shape, and
`lint_evidence.py` fails loudly when declared incidents remain invisible.

The migration chooses entry boundaries from the document's own coarsest structure:

1. A dated `##` heading governs one entry containing its whole body.
2. A thematic `---` separates one entry block from the next.
3. Dated column-zero bullets are separate entries.
4. With none of those, a paragraph is an entry only when it leads with a date.

Undated bullets remain at column zero unless the preceding sentence unambiguously introduces a
list with a colon. Indenting an ambiguous bullet would delete an incident from the index.
Continuation paragraphs stay attached and byte-preserved. Preamble text before the first governed
entry is not evidence.

A date comes from the entry itself, or from one unambiguous ISO date in its governing heading.
Otherwise the block stays untouched and is counted as undatable. A date is never guessed.

## Archive rules

When `<id>.evidence.md` exists, it is the full record and the lesson body is only a latest-entry
excerpt. The archive has no `## Evidence` heading, so evidence readers scan its whole document.
An archive that parses zero entries is a hard stop; the body must not substitute for it.

## Known limits to check by hand

- Evidence may live under `## Additional evidence` or another heading matching
  `^##\s+[A-Za-z ]*Evidence\b`.
- Indented bullets are continuation prose, not entries.
- A paragraph or dated heading remains invisible to the index until migrated.
- A declared count can refer to incidents never written into any evidence section.
- The first ISO date in prose may describe a referenced release rather than the observation date.
- An archive preamble bullet may be either a real legacy incident or structural prose.
- An unknown outcome token blocks promotion and must be corrected, never dropped.

## Re-measure instead of copying old corpus numbers

Run the current scripts against the current corpus:

```bash
python3 scripts/lint_evidence.py <CORPUS_ROOT>
python3 scripts/migrate_evidence.py --report <files...>
python3 scripts/build_index.py --frontier <CORPUS_ROOT>
```

Review every `FABRICATION`, `STALE COUNT`, `UNPARSED`, undatable, and heading-dated row. A green
migration is not enough by itself: compare total readable incidents before and after, then run the
lint again. Only wire the lint as a blocking CI gate when the target corpus is actually green.

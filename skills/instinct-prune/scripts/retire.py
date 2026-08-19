#!/usr/bin/env python3
"""Phase D of `instinct-prune` (spec §5): perform one retirement transaction.

ARCHIVE-FIRST (decided-by-dependency 2026-07-31), on PruneTransaction:

    CLAIMED → ARCHIVED → CITATIONS_REPOINTED → MANIFEST_UPDATED
            → COMMITTING → COMMITTED

Intent is recorded BEFORE each effect (ledger contract), every effect is
idempotent, so `--resume` replays the last recorded step after a crash and
`--rollback` undoes any non-terminal transaction. Dry-run by default.

Two modes, two different MANIFEST shapes (spec §4/§5):
  --successor <name>   RULE_DUP retirement: citations repoint to the covering
                       rule; MANIFEST gets an ordinary `## → <name>` section.
                       <name> is the BARE rule stem or skill dir name
                       ('agents', 'claude-code-hooks-config') — never a path.
  --no-successor       ALL_DEAD retirement: citations are deliberately LEFT;
                       MANIFEST gets the `## → (no successor)` section — the
                       intent-marker dangling_links.py reads — including the
                       inbound-link hand-fix worklist.

This driver moves files and edits citers. It does NOT decide: run it only on
candidates Phase C has verified, and never on rank-1 candidates before ruling
R3 (evidence lineage) is made.

Provenance (optional, carried plan → committed record): --rank N --gate1 F
--gate2 F record WHICH shortlist row produced this candidate. Omit them for a
hand-picked retirement and the record says null — never a guessed rank.

Usage:
  retire.py <id>... (--successor <rule> | --no-successor) [--why "<text>"]
            [--date YYYY-MM-DD] [--root <personal-dir>] [--write-plan FILE]
            [--rank N] [--gate1 F] [--gate2 F]
  retire.py --apply-plan FILE [--apply]
  retire.py --resume <cluster_id> [--root ...] [--apply]
  retire.py --rollback <cluster_id> [--root ...] [--apply]
  retire.py --selftest
"""
import datetime
import hashlib
import json
import pathlib
import re
import sys

DISTILL_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent \
    / "instinct-distill/scripts"
sys.path.insert(0, str(DISTILL_SCRIPTS))
from ledger import (InvalidTransactionRecord, PruneTransaction, Store,  # noqa: E402
                    UnsafeSourceId, check_source_id, content_hash,
                    manifest_append, recover)
from runtime_context import RuntimeContext  # noqa: E402
FORMAT_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent.parent \
    / "instinct-format/scripts"
sys.path.insert(0, str(FORMAT_SCRIPTS))
from dangling_links import successor_ids  # noqa: E402

CORPUS = pathlib.Path.home() / ".claude/homunculus/instincts/personal"
PLAN_SCHEMA_VERSION = 1


def full_hash(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def plan_digest(payload):
    body = dict(payload)
    body.pop("plan_hash", None)
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def runtime_context(root):
    """Resolve all operational roots once, at the CLI boundary."""
    return RuntimeContext.from_corpus(root).as_dict()


def check_provenance(rank, flags):
    """Normalise the shortlist provenance carried into the plan. Both halves
    are optional — a retirement reached by hand has no shortlist row behind it
    and must record null rather than a guessed rank."""
    if rank is not None and (not isinstance(rank, int) or isinstance(rank, bool)):
        raise ValueError(f"rank must be an integer, got {rank!r}")
    if flags is None:
        return rank, None
    if not isinstance(flags, dict) or set(flags) != {"gate1", "gate2"} \
            or any(not isinstance(v, str) for v in flags.values()):
        raise ValueError(f"flags must be {{gate1, gate2}} strings, got {flags!r}")
    return rank, dict(flags)


def create_retirement_plan(root, sources, successor, why, date,
                           rank=None, flags=None):
    """Build the exact, reviewable retirement plan without performing effects."""
    rank, flags = check_provenance(rank, flags)
    root = pathlib.Path(root).resolve()
    for sid in sources:
        check_source_id(sid)
    if not sources or len(set(sources)) != len(sources):
        raise ValueError("sources must be a non-empty list without duplicates")
    date = check_retirement_date(date)
    context = runtime_context(root)
    if successor:
        vocab = successor_ids(pathlib.Path(context["package"]))
        if successor not in vocab:
            raise ValueError(f"successor {successor!r} is not a live rule or skill")
    missing = [sid for sid in sources
               if not (root / f"{sid}.md").is_file()
               or (root / f"{sid}.md").is_symlink()]
    if missing:
        raise ValueError(f"not in corpus: {missing}")

    archive_dir = pathlib.Path(context["archive"]) / date
    cluster_id = f"prune-{date}-{sources[0]}"
    citations = []
    if successor:
        for sid in sources:
            for citer in inbound_citers(root, sid, exclude=set(sources)):
                path = root / citer
                citations.append({
                    "file": str(path), "old": f"[[{sid}]]", "new": f"[[{successor}]]",
                    "sha256_before": full_hash(path),
                })
    moves = []
    for sid in sources:
        for suffix in (".md", ".evidence.md"):
            source = root / f"{sid}{suffix}"
            destination = archive_dir / source.name
            if source.is_symlink():
                raise ValueError(f"source must not be a symlink: {source}")
            if source.exists() and destination.exists():
                raise ValueError(f"archive collision: {source} and {destination} both exist")
            if source.is_file() and not source.is_symlink():
                moves.append({"source": str(source), "destination": str(destination),
                              "sha256_before": full_hash(source)})
    manifest_path = archive_dir / "MANIFEST.md"
    successor_subject = None
    if successor:
        rule = pathlib.Path(context["rules"]) / f"{successor}.md"
        skill = pathlib.Path(context["skills"]) / successor / "SKILL.md"
        successor_path = rule if rule.is_file() else skill
        successor_subject = {"path": str(successor_path),
                             "sha256_before": full_hash(successor_path)}
    payload = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "transaction_id": cluster_id,
        "context": context,
        "sources": list(sources),
        "successor": successor,
        "successor_subject": successor_subject,
        "reason": why,
        "retirement_date": date,
        # Provenance (T4): the shortlist rank and gate flags that produced this
        # candidate, so a committed retirement can be traced back to the gate
        # that flagged it. null when the retirement was reached by hand.
        "rank": rank,
        "flags": flags,
        "moves": moves,
        "citations": citations,
        "manifest": {
            "path": str(manifest_path),
            "sha256_before": full_hash(manifest_path) if manifest_path.is_file() else None,
            "block": build_manifest_block(root, sources, successor, why),
        },
    }
    payload["plan_hash"] = plan_digest(payload)
    return payload


def load_retirement_plan(path):
    try:
        payload = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"cannot read retirement plan: {e}") from e
    if not isinstance(payload, dict) or payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported or malformed retirement plan schema")
    expected_keys = {"schema_version", "transaction_id", "context", "sources", "successor",
                     "successor_subject", "rank", "flags",
                     "reason", "retirement_date", "moves", "citations", "manifest", "plan_hash"}
    if set(payload) != expected_keys:
        raise ValueError("retirement plan fields do not match schema")
    check_provenance(payload["rank"], payload["flags"])
    if not isinstance(payload.get("plan_hash"), str) or payload["plan_hash"] != plan_digest(payload):
        raise ValueError("retirement plan hash does not match its contents")
    context = payload.get("context")
    if not isinstance(context, dict) or set(context) != {
            "config", "corpus", "rules", "skills", "ledger", "claims", "archive", "package"}:
        raise ValueError("retirement plan context is incomplete")
    return payload


def apply_retirement_plan(payload, apply_):
    """Rebuild and compare the plan before the first effect, then execute it."""
    root = pathlib.Path(payload["context"]["corpus"])
    try:
        current = create_retirement_plan(
            root, payload["sources"], payload["successor"], payload["reason"],
            payload["retirement_date"], payload["rank"], payload["flags"])
    except (ValueError, UnsafeSourceId) as e:
        print(f"retire: plan precondition failed: {e}", file=sys.stderr)
        return 3
    if current != payload:
        print("retire: plan precondition failed: source, citation, destination, "
              "manifest, or context changed since review", file=sys.stderr)
        return 3
    if not apply_:
        print(json.dumps(payload, indent=2, sort_keys=True))
        print("VALIDATED ONLY — pass --apply with --apply-plan to perform effects")
        return 0
    return run(Store(root), root, payload["sources"], payload["successor"],
               payload["reason"], payload["retirement_date"], True,
               reviewed_plan=payload, rank=payload["rank"],
               flags=payload["flags"])


def check_retirement_date(value):
    """Return an exact real YYYY-MM-DD date string or raise ValueError."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"date must be exact YYYY-MM-DD, got {value!r}")
    try:
        parsed = datetime.date.fromisoformat(value)
    except ValueError as e:
        raise ValueError(f"invalid calendar date {value!r}") from e
    if parsed.isoformat() != value:
        raise ValueError(f"date must be canonical YYYY-MM-DD, got {value!r}")
    return value


def inbound_citers(root, sid, exclude):
    """Live lessons whose body links [[sid]] — the hand-fix worklist."""
    pat = re.compile(r"\[\[" + re.escape(sid) + r"\]\]")
    out = []
    for p in sorted(root.glob("*.md")):
        if p.stem in exclude or p.name.endswith(".evidence.md"):
            continue
        if pat.search(p.read_text()):
            out.append(p.name)
    return out


def build_manifest_block(root, sources, successor, why):
    """The section this transaction appends — spec §5 format, §4 for no-successor."""
    rows = []
    line_counts = []
    for sid in sources:
        p = root / f"{sid}.md"
        lines = len(p.read_text().splitlines()) if p.exists() else "?"
        line_counts.append(lines if isinstance(lines, int) else 0)
        rows.append(f"| `{sid}.md` | {lines} | {why or '—'} |")
    if successor:
        head = f"## → {successor}"
        cols = "| retired file | lines | contributed |"
    else:
        head = "## → (no successor)"
        cols = "| retired file | lines | why retired |"
    block = [head, "", cols, "|---|---|---|", *rows, ""]
    if successor:
        # Spec §5: MANIFEST needs a line-accounting sentence and the rationale
        # for why the retired sources were one lesson — without these the
        # table alone doesn't say what changed or justify collapsing them.
        total = sum(line_counts)
        block.append(f"{total} lines over {len(sources)} file(s) → "
                      f"carried by {successor}.")
        block.append(f"**Why they were one lesson.** {why or '—'}")
        block.append("")
    if not successor:
        for sid in sources:
            citers = inbound_citers(root, sid, exclude=set(sources))
            if citers:
                block.append(f"Inbound links deliberately left (fix by hand): "
                             f"{', '.join(f'`{c}`' for c in citers)} → `[[{sid}]]`")
        block.append("")
    block += ["## Restore", "```"]
    for sid in sources:
        block.append(f"mv <archive>/{sid}.md {root}/")
        block.append(f"mv <archive>/{sid}.evidence.md {root}/  # if present")
    block += ["```",
              "Restore restores source files only; it does not un-repoint "
              "citations — after COMMITTED that is a manual recovery."]
    return "\n".join(block)


# Each step: (re)record intent, then perform the idempotent effect.
def step_archive(tx, root, apply_):
    adir = pathlib.Path(tx.d["archive_dir"])
    if apply_:
        adir.mkdir(parents=True, exist_ok=True)
    for sid in tx.d["sources"]:
        for name in (f"{sid}.md", f"{sid}.evidence.md"):
            src, dst = root / name, adir / name
            print(f"  mv {name} -> {adir.name}/" + ("" if src.exists() else "  (absent)"))
            if apply_ and src.exists() and dst.exists():
                # A genuine replay after rename is `src absent, dst present`.
                # Both present is always a collision, even when the bytes are
                # equal: skipping it leaves the source live and used to allow
                # the transaction to report COMMITTED without retiring it.
                raise RuntimeError(
                    f"archive collision mid-transaction: {src} and {dst} "
                    f"both exist — refusing to skip")
            elif apply_ and src.exists() and not dst.exists():
                src.rename(dst)


def step_repoint(tx, apply_):
    for f, old, new in tx.d.get("citations") or []:
        fp = pathlib.Path(f)
        print(f"  repoint {fp.name}: {old} -> {new}")
        if apply_ and fp.exists() and old in fp.read_text():
            fp.write_text(fp.read_text().replace(old, new))


def step_manifest(tx, apply_):
    mp = tx.d["manifest_path"]
    print(f"  MANIFEST block -> {mp}")
    if apply_:
        manifest_append(mp, tx.cluster_id, tx.d["manifest_rows"][0])


def verify_archive_postconditions(tx, root):
    """Prove the archive effect before allowing COMMITTED.

    Preflight protects the start state; this protects the promised end state
    after every intervening effect and any concurrent filesystem change.
    """
    adir = pathlib.Path(tx.d["archive_dir"])
    expected = tx.d.get("archive_hashes") or {}
    for sid in tx.d["sources"]:
        check_source_id(sid)
        for index, name in enumerate((f"{sid}.md", f"{sid}.evidence.md")):
            src, dst = root / name, adir / name
            if src.exists() or src.is_symlink():
                raise RuntimeError(
                    f"archive postcondition failed: source still live: {src}")
            expected_hash = expected.get(name)
            # The lesson is mandatory. Evidence is optional unless the plan
            # recorded that it existed before the move.
            if index == 0 or expected_hash is not None:
                if not dst.is_file() or dst.is_symlink():
                    raise RuntimeError(
                        f"archive postcondition failed: destination missing or "
                        f"not a regular file: {dst}")
                if expected_hash is not None and content_hash(dst) != expected_hash:
                    raise RuntimeError(
                        f"archive postcondition failed: destination hash changed: {dst}")
    return True


def verify_citation_postconditions(tx, root):
    for filename, old, new in tx.d.get("citations") or []:
        path = pathlib.Path(filename)
        try:
            path.resolve().relative_to(pathlib.Path(root).resolve())
        except ValueError as exc:
            raise RuntimeError(f"citation postcondition escapes corpus: {path}") from exc
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"citation postcondition file missing or unsafe: {path}")
        text = path.read_text()
        if old in text or new not in text:
            raise RuntimeError(
                f"citation postcondition failed: expected {new!r} and no {old!r} in {path}")
    return True


def verify_manifest_postconditions(tx):
    path = pathlib.Path(tx.d["manifest_path"])
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"MANIFEST postcondition missing or unsafe: {path}")
    text = path.read_text()
    opening = f"<!-- tx:{tx.cluster_id} -->"
    closing = f"<!-- /tx:{tx.cluster_id} -->"
    block = tx.d["manifest_rows"][0]
    if text.count(opening) != 1 or text.count(closing) != 1 or block.rstrip() not in text:
        raise RuntimeError(f"MANIFEST postcondition failed for {tx.cluster_id}: {path}")
    return True


def run(store, root, sources, successor, why, date, apply_, reviewed_plan=None,
        rank=None, flags=None):
    try:
        rank, flags = check_provenance(rank, flags)
    except ValueError as e:
        print(f"retire: {e} — refusing", file=sys.stderr)
        return 2
    # Refuse an unsafe id BEFORE anything (transaction, archive dir, MANIFEST
    # path) is built from it — a source id becomes `root / f"{sid}.md"` and
    # `claims / sid` downstream, and pathlib's `/` silently discards `root`
    # for an absolute sid, escaping the corpus entirely.
    try:
        for sid in sources:
            check_source_id(sid)
    except UnsafeSourceId as e:
        print(f"retire: {e} — refusing", file=sys.stderr)
        return 2

    try:
        date = check_retirement_date(date)
    except ValueError as e:
        print(f"retire: {e} — refusing", file=sys.stderr)
        return 2

    cluster = f"prune-{date}-{sources[0]}"
    archive_root = root.parent / "Archive"
    adir = archive_root / date
    if adir.resolve().parent != archive_root.resolve():
        print(f"retire: archive destination escapes {archive_root}: {adir} — "
              f"refusing", file=sys.stderr)
        return 2
    mpath = adir / "MANIFEST.md"

    # A successor must be a bare rule stem or skill dir name — the exact
    # vocabulary dangling_links classifies as SUCCESSOR. A path form like
    # "rules/agents.md" commits fine and then every repointed link lands in
    # "never resolved" (found by the 2026-08-01 rehearsal). Fail closed.
    if successor:
        home = root.parent.parent.parent
        vocab = successor_ids(home)
        if not vocab:
            print(f"retire: no rules/ or skills/ under {home} — root must live "
                  f"at <home>/homunculus/instincts/personal", file=sys.stderr)
            return 4
        if successor not in vocab:
            print(f"retire: successor {successor!r} is not a rule stem or "
                  f"skill dir name (e.g. 'agents', not 'rules/agents.md') — "
                  f"refusing", file=sys.stderr)
            return 4

    missing = [s for s in sources
               if not (root / f"{s}.md").is_file()
               or (root / f"{s}.md").is_symlink()]
    if missing:
        print(f"retire: not in corpus: {missing} — refusing", file=sys.stderr)
        return 3
    unsafe_evidence = []
    for sid in sources:
        evidence = root / f"{sid}.evidence.md"
        if evidence.is_symlink() or (evidence.exists() and not evidence.is_file()):
            unsafe_evidence.append(str(evidence))
    if unsafe_evidence:
        print(f"retire: evidence source must be a regular file when present: "
              f"{unsafe_evidence} — refusing", file=sys.stderr)
        return 3
    claimed = store.claimed_ids() & set(sources)
    if claimed:
        print(f"retire: already claimed: {sorted(claimed)} — refusing", file=sys.stderr)
        return 3

    # ANY PRE-EXISTING archive destination paired with a live source must refuse
    # before the transaction opens — step_archive's rename is skip-on-exists,
    # so proceeding would leave the live source in place, keep the stale
    # archive bytes, and still let the transaction reach COMMITTED claiming
    # the retirement happened. Byte equality does not make this a replay: after
    # a successful rename the source is absent.
    if apply_:
        for sid in sources:
            for name in (f"{sid}.md", f"{sid}.evidence.md"):
                src, dst = root / name, adir / name
                if src.exists() and dst.exists():
                    print(f"retire: archive collision at {dst} while {src} is "
                          f"still live — refusing", file=sys.stderr)
                    return 3

    # Citations plan + manifest block are computed BEFORE anything moves, and
    # recorded in the ledger, so --resume needs no recomputation.
    triples = []
    if successor:
        for sid in sources:
            for citer in inbound_citers(root, sid, exclude=set(sources)):
                triples.append([str(root / citer), f"[[{sid}]]", f"[[{successor}]]"])
    block = build_manifest_block(root, sources, successor, why)
    archive_hashes = {}
    for sid in sources:
        for name in (f"{sid}.md", f"{sid}.evidence.md"):
            source_path = root / name
            if source_path.is_file() and not source_path.is_symlink():
                archive_hashes[name] = content_hash(source_path)

    if reviewed_plan is not None:
        try:
            if create_retirement_plan(root, sources, successor, why, date,
                                      rank, flags) != reviewed_plan:
                print("retire: reviewed plan changed immediately before transaction; refusing",
                      file=sys.stderr)
                return 3
        except (ValueError, UnsafeSourceId) as e:
            print(f"retire: reviewed plan is no longer applicable: {e}", file=sys.stderr)
            return 3

    print(f"{'APPLY' if apply_ else 'DRY RUN'}  cluster={cluster}")
    if not apply_:
        print(f"  would claim {sources}, archive to {adir}, "
              f"{'repoint ' + str(len(triples)) + ' citation(s)' if successor else 'leave citations (no successor)'},"
              f" append MANIFEST block, commit")
        print("\n--- MANIFEST block ---\n" + block)
        return 0

    tx = PruneTransaction.begin(store, cluster, sources)
    tx.claim()
    tx.record_plan(adir, triples, mpath, [block], archive_hashes=archive_hashes,
                   rank=rank, flags=flags)
    tx.archived(str(adir))
    step_archive(tx, root, apply_)
    tx.citations_repointed(triples)
    step_repoint(tx, apply_)
    try:
        verify_citation_postconditions(tx, root)
    except RuntimeError as e:
        print(f"retire: {e} — refusing next transition; resume or rollback required",
              file=sys.stderr)
        return 5
    tx.manifest_updated([block], manifest_path=mpath)
    step_manifest(tx, apply_)
    try:
        verify_manifest_postconditions(tx)
        verify_archive_postconditions(tx, root)
    except RuntimeError as e:
        print(f"retire: {e} — refusing COMMITTED; resume or rollback required",
              file=sys.stderr)
        return 5
    tx.commit(datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"COMMITTED {cluster}")
    return 0


def resume(store, root, cluster, apply_):
    try:
        tx = PruneTransaction.load(store, cluster)
    except InvalidTransactionRecord as e:
        print(f"retire: invalid persisted transaction: {e} — refusing",
              file=sys.stderr)
        return 3
    if tx is None:
        print(f"retire: no transaction {cluster!r}", file=sys.stderr)
        return 3
    step = tx.next_action()
    if step is None:
        print(f"{cluster}: already terminal ({tx.d['state']}) — nothing to do")
        return 0
    print(f"{'APPLY' if apply_ else 'DRY RUN'}  resume {cluster} from {step}")
    if not apply_:
        return 0
    if step == "ROLLING_BACK":
        tx.rollback("resumed rollback")
        print(f"ROLLED_BACK {cluster}")
        return 0
    # Replay the recorded step, then run the remainder in sequence order.
    order = ["CLAIMED", "ARCHIVED", "CITATIONS_REPOINTED", "MANIFEST_UPDATED",
             "COMMITTING"]
    if step == "DISCOVERED":
        step = "CLAIMED"  # nothing after begin() landed; replay from the top
    if step not in order:
        print(f"retire: cannot resume from {step!r}", file=sys.stderr)
        return 3
    for s in order[order.index(step):]:
        if s == "CLAIMED":
            tx.claim()
        elif s == "ARCHIVED":
            tx.archived(tx.d["archive_dir"])
            step_archive(tx, root, True)
        elif s == "CITATIONS_REPOINTED":
            tx.citations_repointed(tx.d.get("citations") or [])
            step_repoint(tx, True)
            try:
                verify_citation_postconditions(tx, root)
            except RuntimeError as e:
                print(f"retire: {e} — refusing next transition; resume or rollback required",
                      file=sys.stderr)
                return 5
        elif s == "MANIFEST_UPDATED":
            tx.manifest_updated(tx.d["manifest_rows"],
                                manifest_path=tx.d["manifest_path"])
            step_manifest(tx, True)
            try:
                verify_manifest_postconditions(tx)
            except RuntimeError as e:
                print(f"retire: {e} — refusing COMMITTED; resume or rollback required",
                      file=sys.stderr)
                return 5
        elif s == "COMMITTING":
            try:
                verify_citation_postconditions(tx, root)
                verify_manifest_postconditions(tx)
                verify_archive_postconditions(tx, root)
            except RuntimeError as e:
                print(f"retire: {e} — refusing COMMITTED; resume or rollback "
                      f"required", file=sys.stderr)
                return 5
            tx.commit(tx.d.get("watermark_value")
                      or datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(f"COMMITTED {cluster}")
    return 0


def main(argv):
    args = list(argv)
    sources, successor, why, date = [], None, "", None
    mode, cluster, apply_, root = "run", None, False, CORPUS
    write_plan, apply_plan = None, None
    rank, gate1, gate2 = None, None, None
    no_succ = False
    while args:
        a = args.pop(0)
        if a == "--successor":
            successor = args.pop(0)
        elif a == "--rank":
            rank = args.pop(0)
        elif a == "--gate1":
            gate1 = args.pop(0)
        elif a == "--gate2":
            gate2 = args.pop(0)
        elif a == "--no-successor":
            no_succ = True
        elif a == "--why":
            why = args.pop(0)
        elif a == "--date":
            date = args.pop(0)
        elif a == "--root":
            root = pathlib.Path(args.pop(0))
        elif a == "--apply":
            apply_ = True
        elif a == "--write-plan":
            write_plan = pathlib.Path(args.pop(0))
        elif a == "--apply-plan":
            apply_plan = pathlib.Path(args.pop(0))
        elif a in ("--resume", "--rollback"):
            mode, cluster = a.lstrip("-"), args.pop(0)
        elif a.startswith("-"):
            print(__doc__)
            return 2
        else:
            sources.append(a)
    if apply_plan is not None:
        # A reviewed plan is the sole authority. Mixing free-form identifiers,
        # roots, or decisions with it would quietly change what was approved —
        # provenance included: the rank the reviewer saw is the rank recorded.
        if sources or successor is not None or no_succ or why or date is not None \
                or root != CORPUS or write_plan is not None or mode != "run" \
                or rank is not None or gate1 is not None or gate2 is not None:
            print("retire: --apply-plan accepts only its plan file and optional --apply",
                  file=sys.stderr)
            return 2
        try:
            payload = load_retirement_plan(apply_plan)
        except ValueError as e:
            print(f"retire: {e}", file=sys.stderr)
            return 2
        return apply_retirement_plan(payload, apply_)
    store = Store(root)
    if mode == "resume":
        return resume(store, root, cluster, apply_)
    if mode == "rollback":
        try:
            tx = PruneTransaction.load(store, cluster)
        except InvalidTransactionRecord as e:
            print(f"retire: invalid persisted transaction: {e} — refusing",
                  file=sys.stderr)
            return 3
        if tx is None:
            print(f"retire: no transaction {cluster!r}", file=sys.stderr)
            return 3
        print(f"{'APPLY' if apply_ else 'DRY RUN'}  rollback {cluster} "
              f"from {tx.d['state']}")
        if apply_:
            print(tx.rollback("operator rollback"))
        return 0
    if not sources or (successor is None) == (not no_succ):
        print(__doc__)
        return 2
    if rank is not None:
        try:
            rank = int(rank)
        except ValueError:
            print(f"retire: --rank must be the integer shortlist rank, got "
                  f"{rank!r}", file=sys.stderr)
            return 2
    flags = ({"gate1": gate1 or "-", "gate2": gate2 or "-"}
             if (gate1 is not None or gate2 is not None) else None)
    if write_plan is not None:
        if apply_:
            print("retire: --write-plan cannot be combined with --apply", file=sys.stderr)
            return 2
        try:
            payload = create_retirement_plan(
                root, sources, successor, why,
                date or datetime.date.today().isoformat(), rank, flags)
        except (ValueError, UnsafeSourceId) as e:
            print(f"retire: cannot create plan: {e}", file=sys.stderr)
            return 3
        write_plan.parent.mkdir(parents=True, exist_ok=True)
        write_plan.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"WROTE {write_plan}  sha256={payload['plan_hash']}")
        return 0
    return run(store, root, sources, successor, why,
               date or datetime.date.today().isoformat(), apply_,
               rank=rank, flags=flags)


# ---------------------------------------------------------------- selftest
def _home(tmp):
    home = pathlib.Path(tmp)
    root = home / "homunculus/instincts/personal"
    root.mkdir(parents=True)
    (home / "rules").mkdir()
    (home / "rules/testing.md").write_text("the covering rule\n")
    (root / "dup.md").write_text("# dup lesson\nbody\n")
    (root / "dup.evidence.md").write_text("# Evidence archive — dup\n")
    (root / "dead.md").write_text("# dead lesson\ncites /nonexistent\n")
    (root / "citer.md").write_text("see [[dup]] and [[dead]]\n")
    return home, root


def selftest():
    import json
    import subprocess
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent
                           / "instinct-format/scripts"))
    import dangling_links as DL

    # --- successor retirement, end to end --------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        rc = run(st, root, ["dup"], "testing", "carried by rules/testing.md",
                 "2026-07-31", apply_=False)
        check("dry run exits 0", rc == 0)
        check("dry run moves nothing", (root / "dup.md").exists())
        rc = run(st, root, ["dup"], "testing", "carried by rules/testing.md",
                 "2026-07-31", apply_=True)
        adir = root.parent / "Archive/2026-07-31"
        check("apply exits 0 and archives lesson + evidence sibling",
              rc == 0 and (adir / "dup.md").exists()
              and (adir / "dup.evidence.md").exists()
              and not (root / "dup.md").exists())
        check("citer repointed to the covering rule",
              "[[testing]]" in (root / "citer.md").read_text())
        mf = (adir / "MANIFEST.md").read_text()
        check("MANIFEST block is transaction-marked",
              "<!-- tx:prune-2026-07-31-dup -->" in mf and "## → testing" in mf)
        check("claims released, watermark advanced, nothing open",
              st.claimed_ids() == set() and st.read_watermark() and
              recover(st) == [])
        _l, _a, retired, intent, succ, _n = DL.scan(home)
        check("dangling_links: repointed citer lands in SUCCESSOR bucket",
              "testing" in {t for t, _ in succ}
              and "dup" not in {t for t, _ in retired})
        rc = run(st, root, ["dead"], "rules/testing.md", "path form",
                 "2026-07-31", apply_=True)
        check("path-form successor refused (exit 4), nothing moved",
              rc == 4 and (root / "dead.md").exists())
        rc = run(st, root, ["dead"], "no-such-rule", "bogus successor",
                 "2026-07-31", apply_=True)
        check("unknown successor refused (exit 4), nothing moved",
              rc == 4 and (root / "dead.md").exists())

    # --- no-successor retirement: marker end to end ----------------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        rc = run(st, root, ["dead"], None, "every cited path gone",
                 "2026-07-31", apply_=True)
        adir = root.parent / "Archive/2026-07-31"
        mf = (adir / "MANIFEST.md").read_text()
        check("no-successor MANIFEST carries the intent-marker heading",
              rc == 0 and "## → (no successor)" in mf)
        check("inbound hand-fix worklist recorded",
              "citer.md" in mf and "[[dead]]" in mf)
        check("citations deliberately left",
              "[[dead]]" in (root / "citer.md").read_text())
        _l, _a, retired, intent, _s, _n = DL.scan(home)
        check("dangling_links: left link reads INTENT, not debt",
              "dead" in {t for t, _ in intent}
              and "dead" not in {t for t, _ in retired})

    # --- crash + resume, crash + rollback --------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        before = {p.name for p in root.glob("*.md")}
        adir = root.parent / "Archive/2026-07-31"
        # crash at the EARLIEST recoverable point: plan recorded, no effect yet
        tx = PruneTransaction.begin(st, "prune-2026-07-31-dup", ["dup"])
        tx.claim()
        tx.record_plan(adir,
                       [[str(root / "citer.md"), "[[dup]]", "[[testing]]"]],
                       adir / "MANIFEST.md",
                       [build_manifest_block(root, ["dup"], "testing", "x")])
        # crash here — resume must archive, repoint, append MANIFEST, commit
        st2 = Store(root)
        rc = resume(st2, root, "prune-2026-07-31-dup", apply_=True)
        check("resume finishes a transaction crashed right after the plan",
              rc == 0 and st2.claimed_ids() == set()
              and PruneTransaction.load(st2, "prune-2026-07-31-dup").d["state"]
              == "COMMITTED"
              and "[[testing]]" in (root / "citer.md").read_text()
              and "<!-- tx:prune-2026-07-31-dup -->"
              in (adir / "MANIFEST.md").read_text())

        # rollback a second, crashed-early transaction
        tx2 = PruneTransaction.begin(st2, "prune-2026-07-31-dead", ["dead"])
        tx2.claim()
        tx2.archived(str(adir))
        step_archive(tx2, root, True)
        check("fixture: dead.md is archived pre-rollback",
              not (root / "dead.md").exists())
        tx2.rollback("phase C rejected")
        check("rollback restores the archived source",
              (root / "dead.md").exists() and st2.claimed_ids() == set())
        check("corpus file set == before, minus the committed retirement",
              {p.name for p in root.glob("*.md")}
              == before - {"dup.md", "dup.evidence.md"})

    # --- source-id path escape refused before anything is touched ---------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        sentinel = pathlib.Path(tmp) / "victim.txt"
        sentinel.write_text("do not touch\n")
        before = {p.name for p in root.glob("*.md")}
        for bad_sources in (["dup", str(pathlib.Path(tmp) / "escaped")],
                             ["dup", "../evil"]):
            rc = run(st, root, bad_sources, "testing", "x",
                     "2026-07-31", apply_=True)
            check(f"unsafe id in {bad_sources} refused (exit 2)", rc == 2)
        check("nothing archived for the refused runs",
              not (root.parent / "Archive/2026-07-31").exists())
        check("corpus untouched by the refused runs",
              {p.name for p in root.glob("*.md")} == before)
        check("sentinel outside the corpus untouched",
              sentinel.read_text() == "do not touch\n")
        check("nothing claimed by the refused runs", st.claimed_ids() == set())
        # RED probe: neuter the id check in run() and confirm it was the
        # thing stopping the escape — a green refusal on live code proves
        # nothing if this call path never reaches check_source_id.
        _mod = sys.modules[__name__]
        original = _mod.check_source_id
        _mod.check_source_id = lambda sid: sid  # neuter
        try:
            rc = run(st, root, ["dup", "../evil"], "testing", "x",
                     "2026-07-31", apply_=True)
            neutered_proceeded = rc != 2
        finally:
            _mod.check_source_id = original
        check("RED probe: neutering check_source_id lets an unsafe id "
              "proceed past the refusal (proves the guard, when live, is "
              "what stops it)", neutered_proceeded)

    # `date` is also a path component (`Archive / date`) and must be parsed,
    # not trusted because the help text happens to say YYYY-MM-DD.
    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        st = Store(root)
        escaped_archive = pathlib.Path(tmp) / "outside-archive"
        rc = run(st, root, ["dup"], "testing", "x",
                 str(escaped_archive), apply_=True)
        check("absolute --date is rejected before path construction", rc == 2)
        check("bad date performs zero source/archive/ledger effects",
              (root / "dup.md").exists()
              and not (escaped_archive / "dup.md").exists()
              and st.open_transactions() == [])
        for bad_date in ("../2026-07-31", "2026/07/31", "2026-02-30",
                         "2026-7-1", "2026-07-31-extra"):
            check(f"invalid date {bad_date!r} rejected",
                  run(st, root, ["dup"], "testing", "x", bad_date,
                      apply_=False) == 2)
        check("valid leap-day date accepted",
              run(st, root, ["dup"], "testing", "x", "2024-02-29",
                  apply_=False) == 0)

    # Persisted input is still input. A legacy, corrupt, or hand-edited
    # transaction must be validated when loaded, before resume joins a source
    # id to any path. This CLI fixture starts at ARCHIVED because that is the
    # first replay state that performs the move immediately.
    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        sentinel = root.parent / "victim.md"
        sentinel.write_text("outside corpus sentinel\n")
        adir = root.parent / "Archive/2026-07-31"
        adir.mkdir(parents=True)
        ledger_dir = root / ".distill/ledger"
        ledger_dir.mkdir(parents=True)
        entry = {
            "cluster_id": "replay-unsafe", "state": "ARCHIVED",
            "sources": ["../victim"],
            "history": ["DISCOVERED", "CLAIMED", "ARCHIVED"],
            "artifact": None, "artifact_hash_before": None,
            "citations": [], "archive_dir": str(adir),
            "manifest_rows": ["fixture"],
            "manifest_path": str(adir / "MANIFEST.md"),
            "watermark_before": None, "reason": None,
        }
        ledger_path = ledger_dir / "replay-unsafe.json"
        ledger_path.write_text(json.dumps(entry))
        proc = subprocess.run(
            [sys.executable, str(pathlib.Path(__file__).resolve()),
             "--resume", "replay-unsafe", "--root", str(root), "--apply"],
            capture_output=True, text=True)
        after = json.loads(ledger_path.read_text())
        check("unsafe source in a persisted transaction is rejected by the CLI",
              proc.returncode == 3 and "unsafe source id" in proc.stderr
              and "Traceback" not in proc.stderr)
        check("rejected replay leaves the outside sentinel byte-identical",
              sentinel.is_file()
              and sentinel.read_text() == "outside corpus sentinel\n")
        check("rejected replay performs zero archive/MANIFEST/ledger effects",
              not (adir.parent / "victim.md").exists()
              and not (adir / "MANIFEST.md").exists()
              and after["state"] == "ARCHIVED")

    # --- archive collision: pre-existing destination with different bytes --
    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        st = Store(root)
        adir = root.parent / "Archive/2026-07-31"
        adir.mkdir(parents=True)
        (adir / "dup.md").write_bytes((root / "dup.md").read_bytes())
        rc = run(st, root, ["dup"], "testing", "x", "2026-07-31",
                 apply_=True)
        check("identical pre-existing archive destination is still a collision",
              rc == 3)
        check("identical collision cannot report retirement while source is live",
              (root / "dup.md").exists()
              and PruneTransaction.load(st, "prune-2026-07-31-dup") is None)

    # Preflight alone cannot prove completion: another effect or concurrent
    # actor can recreate a source after the move. Inject that mutation after
    # MANIFEST write and require the commit boundary to detect it.
    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        st = Store(root)
        original_step_manifest = globals()["step_manifest"]

        def resurrect_source(tx, apply_):
            original_step_manifest(tx, apply_)
            if apply_:
                (root / "dup.md").write_text("reappeared before commit\n")

        globals()["step_manifest"] = resurrect_source
        try:
            rc = run(st, root, ["dup"], "testing", "x", "2026-07-31",
                     apply_=True)
        finally:
            globals()["step_manifest"] = original_step_manifest
        tx = PruneTransaction.load(st, "prune-2026-07-31-dup")
        check("pre-COMMITTED invariant rejects a source that reappeared",
              rc == 5)
        check("failed completion assertion leaves an open recoverable transaction",
              tx is not None and tx.d["state"] == "MANIFEST_UPDATED"
              and st.claimed_ids() == {"dup"})

    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        st = Store(root)
        adir = root.parent / "Archive/2026-07-31"
        adir.mkdir(parents=True)
        (adir / "dup.md").write_text("# an older, DIFFERENT archived copy\n")
        live_before = (root / "dup.md").read_text()
        rc = run(st, root, ["dup"], "testing", "x", "2026-07-31", apply_=True)
        check("archive collision refused (exit 3)", rc == 3)
        check("live source still present after refusal", (root / "dup.md").exists())
        check("live source bytes unchanged", (root / "dup.md").read_text() == live_before)
        check("old archive bytes intact (not overwritten)",
              (adir / "dup.md").read_text() == "# an older, DIFFERENT archived copy\n")
        check("no COMMITTED ledger record for the refused cluster",
              recover(st) == [] and st.claimed_ids() == set())
        # RED probe: exercise step_archive directly with both src and dst
        # present and differing bytes, bypassing run()'s pre-flight entirely —
        # proves the fail-closed RuntimeError branch inside step_archive is
        # reachable on its own, not merely unreachable dead code.
        tx = PruneTransaction.begin(st, "prune-2026-07-31-dup-direct", ["dup"])
        tx.claim()
        tx.record_plan(adir, [], adir / "MANIFEST.md", ["x"])
        tx.archived(str(adir))
        try:
            step_archive(tx, root, True)
            step_archive_raised = False
        except RuntimeError:
            step_archive_raised = True
        check("RED probe: step_archive itself refuses a mid-transaction "
              "collision (fail-closed even if the pre-flight is bypassed)",
              step_archive_raised)
        tx.rollback("test cleanup")

    # --- MANIFEST successor block: line-accounting + rationale -------------
    with tempfile.TemporaryDirectory() as tmp:
        home, root = _home(tmp)
        block = build_manifest_block(root, ["dup"], "testing",
                                      "both covered the same failure mode")
        dup_lines = len((root / "dup.md").read_text().splitlines())
        check("MANIFEST successor block has the line-accounting sentence",
              f"{dup_lines} lines over 1 file(s) → carried by testing." in block)
        check("MANIFEST successor block has the Why-they-were-one-lesson line",
              "**Why they were one lesson.** both covered the same failure mode"
              in block)
        no_succ_block = build_manifest_block(root, ["dead"], None, "gone")
        check("no-successor block is unchanged (no line-accounting sentence)",
              "carried by" not in no_succ_block
              and "Why they were one lesson" not in no_succ_block)

    # --- machine-readable review plan -------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        plan_path = pathlib.Path(tmp) / "retirement-plan.json"
        rc = main(["dup", "--successor", "testing", "--why", "same lesson",
                   "--date", "2026-08-03", "--root", str(root),
                   "--write-plan", str(plan_path)])
        plan = load_retirement_plan(plan_path)
        check("write-plan creates a hashed versioned plan without effects",
              rc == 0 and plan["schema_version"] == 1
              and plan["plan_hash"] == plan_digest(plan)
              and (root / "dup.md").exists()
              and not (root / ".distill").exists())
        check("plan records all runtime roots and exact effect inputs",
              set(plan["context"]) == {"config", "corpus", "rules", "skills",
                                       "ledger", "claims", "archive", "package"}
              and plan["moves"] and plan["citations"]
              and plan["manifest"]["block"].startswith("## → testing"))
        check("apply-plan without --apply validates but performs no effects",
              main(["--apply-plan", str(plan_path)]) == 0
              and (root / "dup.md").exists())

        original = (root / "dup.md").read_text()
        (root / "dup.md").write_text(original + "changed after review\n")
        check("changed source invalidates the reviewed plan before any effect",
              main(["--apply-plan", str(plan_path), "--apply"]) == 3
              and (root / "dup.md").exists()
              and not (root / ".distill").exists())
        (root / "dup.md").write_text(original)
        check("apply-plan rejects mixed free-form identifiers",
              main(["--apply-plan", str(plan_path), "dup", "--apply"]) == 2)
        (root.parent.parent.parent / "rules/testing.md").write_text("changed successor\n")
        check("changed successor artifact invalidates the reviewed plan",
              main(["--apply-plan", str(plan_path), "--apply"]) == 3
              and (root / "dup.md").exists())
        (root.parent.parent.parent / "rules/testing.md").write_text("the covering rule\n")
        check("unchanged reviewed plan applies and commits",
              main(["--apply-plan", str(plan_path), "--apply"]) == 0
              and not (root / "dup.md").exists()
              and (root.parent / "Archive/2026-08-03/dup.md").exists())

    # --- provenance: which shortlist rank and gate flags led here (T4) -----
    # The 6 committed records before 2026-08-19 answer "what was retired and
    # why" but not "what flagged it" — so a retirement cannot be traced back
    # to the gate that produced it. Carried plan → transaction record; absent
    # on the older records by design (append-only history, no backfill).
    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        plan_path = pathlib.Path(tmp) / "plan.json"
        rc = main(["dup", "--successor", "testing", "--why", "same lesson",
                   "--date", "2026-08-03", "--root", str(root),
                   "--rank", "1", "--gate1", "RULE_DUP", "--gate2", "UNCITED",
                   "--write-plan", str(plan_path)])
        plan = load_retirement_plan(plan_path)
        check("plan carries the shortlist rank (RED)",
              rc == 0 and plan.get("rank") == 1)
        check("plan carries the gate flags (RED)",
              plan.get("flags") == {"gate1": "RULE_DUP", "gate2": "UNCITED"})
        check("provenance is inside the plan digest (tampering is rejected)",
              plan_digest(dict(plan, rank=3)) != plan["plan_hash"])
        check("reviewed plan with provenance applies and commits",
              main(["--apply-plan", str(plan_path), "--apply"]) == 0
              and not (root / "dup.md").exists())
        committed = json.loads(
            (root / ".distill/ledger/prune-2026-08-03-dup.json").read_text())
        check("committed transaction record carries rank + flags (RED)",
              committed["state"] == "COMMITTED" and committed.get("rank") == 1
              and committed.get("flags") == {"gate1": "RULE_DUP",
                                             "gate2": "UNCITED"})

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        # Provenance is optional: a retirement reached by hand, with no
        # shortlist row behind it, must still commit — recording null rather
        # than inventing a rank.
        check("retirement without provenance still commits",
              run(Store(root), root, ["dup"], "testing", "hand-picked",
                  "2026-08-03", True) == 0)
        committed = json.loads(
            (root / ".distill/ledger/prune-2026-08-03-dup.json").read_text())
        check("absent provenance is recorded as null, never guessed",
              committed.get("rank") is None and committed.get("flags") is None)

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        check("a non-integer --rank is refused before any effect",
              main(["dup", "--successor", "testing", "--why", "x", "--date",
                    "2026-08-03", "--root", str(root), "--rank", "high",
                    "--apply"]) == 2 and (root / "dup.md").exists())

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        plan = create_retirement_plan(root, ["dup"], "testing", "x", "2026-08-03")
        plan["reason"] = "tampered after hashing"
        tampered = pathlib.Path(tmp) / "tampered.json"
        tampered.write_text(json.dumps(plan))
        try:
            load_retirement_plan(tampered)
            rejected = False
        except ValueError:
            rejected = True
        check("plan-content tampering is rejected by its digest", rejected)

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        outside = pathlib.Path(tmp) / "outside.md"
        outside.write_text("outside")
        (root / "dup.md").unlink()
        (root / "dup.md").symlink_to(outside)
        try:
            create_retirement_plan(root, ["dup"], "testing", "x", "2026-08-03")
            symlink_rejected = False
        except ValueError:
            symlink_rejected = True
        check("mandatory lesson symlink is rejected while planning", symlink_rejected)
        check("rejected symlink plan leaves its target byte-identical",
              outside.read_text() == "outside")

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        st = Store(root)
        adir = root.parent / "Archive/2026-08-03"
        hashes = {"dup.md": content_hash(root / "dup.md"),
                  "dup.evidence.md": content_hash(root / "dup.evidence.md")}
        tx = PruneTransaction.begin(st, "prune-2026-08-03-dup", ["dup"])
        tx.claim()
        tx.record_plan(adir,
                       [[str(root / "citer.md"), "[[dup]]", "[[testing]]"]],
                       adir / "MANIFEST.md", ["fixture"], archive_hashes=hashes)
        tx.archived(str(adir))
        step_archive(tx, root, True)
        tx.citations_repointed(tx.d["citations"])
        (root / "citer.md").write_text("citation changed after intent\n")
        rc = resume(st, root, tx.cluster_id, apply_=True)
        loaded = PruneTransaction.load(st, tx.cluster_id)
        check("missing promised citation blocks the next transition and commit",
              rc == 5 and loaded.d["state"] == "CITATIONS_REPOINTED"
              and not (adir / "MANIFEST.md").exists())

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        outside = pathlib.Path(tmp) / "outside-evidence.md"
        outside.write_text("outside evidence")
        (root / "dup.evidence.md").unlink()
        (root / "dup.evidence.md").symlink_to(outside)
        rc = run(Store(root), root, ["dup"], "testing", "x", "2026-08-03", True)
        check("direct retirement rejects an evidence-archive symlink before effects",
              rc == 3 and (root / "dup.md").exists()
              and outside.read_text() == "outside evidence")

    with tempfile.TemporaryDirectory() as tmp:
        _home_dir, root = _home(tmp)
        st = Store(root)
        adir = root.parent / "Archive/2026-08-03"
        block = build_manifest_block(root, ["dup"], "testing", "x")
        hashes = {"dup.md": content_hash(root / "dup.md"),
                  "dup.evidence.md": content_hash(root / "dup.evidence.md")}
        tx = PruneTransaction.begin(st, "prune-2026-08-03-dup", ["dup"])
        tx.claim()
        tx.record_plan(adir,
                       [[str(root / "citer.md"), "[[dup]]", "[[testing]]"]],
                       adir / "MANIFEST.md", [block], archive_hashes=hashes)
        tx.archived(str(adir)); step_archive(tx, root, True)
        tx.citations_repointed(tx.d["citations"]); step_repoint(tx, True)
        tx.manifest_updated([block], manifest_path=adir / "MANIFEST.md"); step_manifest(tx, True)
        tx._record("COMMITTING", watermark_value="W")
        (root / "citer.md").write_text("corrupt after COMMITTING\n")
        (adir / "MANIFEST.md").unlink()
        rc = resume(st, root, tx.cluster_id, apply_=True)
        loaded = PruneTransaction.load(st, tx.cluster_id)
        check("COMMITTING replay rechecks citation and MANIFEST postconditions",
              rc == 5 and loaded.d["state"] == "COMMITTING"
              and st.claimed_ids() == {"dup"})

    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main(sys.argv[1:]))

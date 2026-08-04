#!/usr/bin/env python3
"""Durable transaction state for `instinct-distill`. Plan §1B.

One cluster is one transaction:

    DISCOVERED → CLAIMED → ARTIFACT_WRITTEN → CITATIONS_REPOINTED
               → ARCHIVED → MANIFEST_UPDATED → COMMITTING → COMMITTED
                          ↘ ROLLING_BACK → ROLLED_BACK
    (COMMITTING is forward-only: it carries the watermark value, so a crashed
    commit is finished by a no-argument commit() replay, never rolled back.)

This module owns **state, claims and the watermark snapshot** — never the effects.
`distill` performs the artifact write, the citation repoint, the archive move; it
records intent here *first*. So a recorded state means "intent durable, effect may or
may not have landed", which is exactly why every effect must be idempotent and why
recovery replays the last recorded step rather than the next one.

Recovery reads the LEDGER, never the filesystem. Inspecting files cannot distinguish
"not yet done" from "done and rolled back" — the ambiguity this file exists to remove.

Usage:
    python3 ledger.py --selftest        # crash injection at every transition
    python3 ledger.py --status [root]   # list open transactions and claims
"""
import hashlib
import json
import os
import pathlib
import re
import sys
import tempfile

# A source id becomes a path via `claims / sid`. pathlib's `/` DISCARDS the left
# operand when the right is absolute — `claims / "/tmp/victim"` is `/tmp/victim`,
# not an error — and ".." climbs out of the store just as normal filesystem
# traversal would. Every id must be checked BEFORE it meets a path operator.
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


class UnsafeSourceId(Exception):
    """A source id failed SAFE_ID — most often an absolute path ("/tmp/x", which
    pathlib's `/` operator silently substitutes for the whole claims path instead
    of raising) or a ".." segment. Raised before the id ever reaches `claims / sid`
    or `root / f"{sid}.md"`, because catching it after would mean the escape
    already happened."""


class InvalidTransactionRecord(Exception):
    """Persisted transaction data is malformed or unsafe to replay."""


def check_source_id(sid):
    """Refuse anything that is not a bare filename-safe token. Returns sid
    unchanged so callers can inline it: `for sid in map(check_source_id, sources)`."""
    if not isinstance(sid, str) or "/" in sid or "\\" in sid or ".." in sid \
            or not SAFE_ID.match(sid):
        raise UnsafeSourceId(f"unsafe source id: {sid!r}")
    return sid


def check_cluster_id(cluster_id):
    """Cluster IDs are persisted filenames and must obey the same safe-component rule."""
    try:
        return check_source_id(cluster_id)
    except UnsafeSourceId as e:
        raise InvalidTransactionRecord(f"unsafe cluster id: {cluster_id!r}") from e


def _within(path, root):
    try:
        pathlib.Path(path).resolve().relative_to(pathlib.Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def validate_transaction_data(data, cluster_id, store, sequence=None):
    """Validate every persisted field before recovery may interpret any effect path."""
    check_cluster_id(cluster_id)
    if not isinstance(data, dict):
        raise InvalidTransactionRecord(
            f"{cluster_id}: transaction record must be an object")
    required = {"cluster_id", "state", "sources", "history", "citations",
                "archive_dir", "manifest_rows"}
    missing = sorted(required - set(data))
    if missing:
        raise InvalidTransactionRecord(
            f"{cluster_id}: missing persisted field(s): {', '.join(missing)}")
    if data.get("cluster_id") != cluster_id:
        raise InvalidTransactionRecord(
            f"{cluster_id}: record cluster_id does not match its filename")

    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise InvalidTransactionRecord(
            f"{cluster_id}: sources must be a non-empty list")
    try:
        for sid in sources:
            check_source_id(sid)
    except UnsafeSourceId as e:
        raise InvalidTransactionRecord(f"{cluster_id}: {e}") from e
    if len(set(sources)) != len(sources):
        raise InvalidTransactionRecord(f"{cluster_id}: sources contain duplicates")

    allowed_states = set(FORWARD) | set(PRUNE_FORWARD) | set(ROLLBACK)
    if data.get("state") not in allowed_states:
        raise InvalidTransactionRecord(f"{cluster_id}: invalid state {data.get('state')!r}")
    history = data.get("history")
    if (not isinstance(history, list) or not history
            or any(not isinstance(state, str) or state not in allowed_states
                   for state in history)
            or history[0] != "DISCOVERED" or history[-1] != data["state"]):
        raise InvalidTransactionRecord(
            f"{cluster_id}: history must start at DISCOVERED and end at state")
    if sequence is not None:
        seq = list(sequence)
        for previous, current in zip(history, history[1:]):
            legal = (current == previous
                     or current == "ROLLING_BACK" and previous not in TERMINAL
                     and previous != "COMMITTING"
                     or current == "ROLLED_BACK" and previous == "ROLLING_BACK"
                     or previous in seq and current in seq
                     and seq.index(current) == seq.index(previous) + 1)
            if not legal:
                raise InvalidTransactionRecord(
                    f"{cluster_id}: illegal persisted transition {previous} → {current}")

    citations = data.get("citations")
    if not isinstance(citations, list):
        raise InvalidTransactionRecord(f"{cluster_id}: citations must be a list")
    for triple in citations:
        if (not isinstance(triple, list) or len(triple) != 3
                or any(not isinstance(item, str) for item in triple)):
            raise InvalidTransactionRecord(
                f"{cluster_id}: each citation must be a three-string list")
        if not _within(triple[0], store.root):
            raise InvalidTransactionRecord(
                f"{cluster_id}: citation path escapes corpus: {triple[0]}")

    for key, owned_root in (("artifact", store.root),
                            ("archive_dir", store.root.parent),
                            ("manifest_path", store.root.parent)):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not _within(value, owned_root)):
            raise InvalidTransactionRecord(
                f"{cluster_id}: {key} escapes its owned root: {value!r}")

    rows = data.get("manifest_rows")
    if not isinstance(rows, list) or any(not isinstance(row, str) for row in rows):
        raise InvalidTransactionRecord(f"{cluster_id}: manifest_rows must be strings")
    hashes = data.get("archive_hashes", {})
    expected_names = {f"{sid}{suffix}" for sid in sources
                      for suffix in (".md", ".evidence.md")}
    if (not isinstance(hashes, dict)
            or any(name not in expected_names or not isinstance(digest, str)
                   or not re.fullmatch(r"[0-9a-f]{16}", digest)
                   for name, digest in hashes.items())):
        raise InvalidTransactionRecord(
            f"{cluster_id}: archive_hashes contains an invalid name or digest")
    return data


FORWARD = [
    "DISCOVERED", "CLAIMED", "ARTIFACT_WRITTEN", "CITATIONS_REPOINTED",
    "ARCHIVED", "MANIFEST_UPDATED", "COMMITTING", "COMMITTED",
]
ROLLBACK = ["ROLLING_BACK", "ROLLED_BACK"]
TERMINAL = {"COMMITTED", "ROLLED_BACK"}

# 6b-3: prune's own sequence — archive-FIRST (spec §5, decided-by-dependency
# 2026-07-31) and no ARTIFACT_WRITTEN, because prune authors nothing.
PRUNE_FORWARD = [
    "DISCOVERED", "CLAIMED", "ARCHIVED", "CITATIONS_REPOINTED",
    "MANIFEST_UPDATED", "COMMITTING", "COMMITTED",
]


class IllegalTransition(Exception):
    """6b-3: a state recorded out of its class's sequence. The ledger used to
    validate nothing — a distill-shaped object accepted arbitrary method calls
    in any order, and next_action() would then replay whatever nonsense was
    last recorded. Raised BEFORE the write, so the bad state never persists."""


class LedgerWriteFailed(Exception):
    """Crash matrix row 5: a transaction that cannot record its own state must not
    proceed. Fail closed and LEAVE THE CLAIM — releasing it would expose sources to
    prune while the artifact may already exist."""


def _atomic_write(path, text):
    """Write-then-rename so a reader never sees a half-file."""
    tmp = None
    try:
        # mkstemp must be INSIDE the try: on a read-only ledger dir it raises
        # PermissionError, and an escaping OSError bypasses the fail-closed
        # contract entirely — the caller would see a crash, not a kept claim.
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent))
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as e:
        if tmp:
            pathlib.Path(tmp).unlink(missing_ok=True)
        raise LedgerWriteFailed(str(e)) from e


# --------------------------------------------------------------- MANIFEST identity
# 6b-2: a transaction's MANIFEST contribution is one contiguous block bracketed by
# HTML-comment markers. Markdown renders them invisible; append is replace-by-id so
# replay never duplicates; remove is by-id so rollback touches nothing but its own
# rows. All writes go through _atomic_write, so a partial block cannot exist on disk.
def _tx_marks(cluster_id):
    return f"<!-- tx:{cluster_id} -->", f"<!-- /tx:{cluster_id} -->"


def _strip_block(text, cluster_id):
    open_m, close_m = _tx_marks(cluster_id)
    out, skip = [], False
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s == open_m:
            skip = True
            continue
        if s == close_m:
            skip = False
            continue
        if not skip:
            out.append(line)
    return "".join(out)


def manifest_append(path, cluster_id, block_text):
    """Append (or replace) THIS transaction's block. Idempotent under replay."""
    path = pathlib.Path(path)
    text = path.read_text() if path.exists() else ""
    text = _strip_block(text, cluster_id)
    open_m, close_m = _tx_marks(cluster_id)
    if text and not text.endswith("\n"):
        text += "\n"
    _atomic_write(path, f"{text}{open_m}\n{block_text.rstrip()}\n{close_m}\n")


def manifest_remove(path, cluster_id):
    """Remove THIS transaction's block, nothing else. No-op when absent."""
    path = pathlib.Path(path)
    if not path.exists():
        return False
    text = path.read_text()
    new = _strip_block(text, cluster_id)
    if new == text:
        return False
    _atomic_write(path, new)
    return True


def content_hash(p):
    p = pathlib.Path(p)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


class Store:
    """Layout under <root>/.distill/ — ledger/, claims/, watermark.json"""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.base = self.root / ".distill"
        self.ledger = self.base / "ledger"
        self.claims = self.base / "claims"
        self.watermark = self.base / "watermark.json"

    def read_watermark(self):
        if not self.watermark.exists():
            return None
        return json.loads(self.watermark.read_text()).get("value")

    def _set_watermark(self, value):
        _atomic_write(self.watermark, json.dumps({"value": value}, indent=2))

    def claimed_ids(self):
        """Source ids currently claimed. `prune` treats these as INVISIBLE — not
        skipped-with-a-warning; a warning in a batch run is a line nobody reads."""
        if not self.claims.is_dir():
            return set()
        return {p.name for p in self.claims.iterdir()}

    def stale_claims(self):
        """6b-4: a claim is STALE iff its transaction is terminal or absent.
        The ledger is the clock — no run ids or timestamps: under the COMMITTING
        protocol a healthy claim always has an open transaction, so anything
        else is crash legacy (pre-2026-07-31) or a hand-edited ledger.
        Returns {source_id: cluster_id}; decides nothing, releases nothing."""
        if not self.claims.is_dir():
            return {}
        out = {}
        for p in self.claims.iterdir():
            cid = p.read_text().strip()
            entry = self.ledger / f"{cid}.json"
            if not entry.exists() or json.loads(entry.read_text())["state"] in TERMINAL:
                out[p.name] = cid
        return out

    def reclaim_stale(self):
        """Release stale claims. Safe by the definition above: no open
        transaction relies on them. Idempotent."""
        stale = self.stale_claims()
        for sid in stale:
            # Claim names come from disk listing (iterdir), not user input, but
            # a hand-edited ledger/claims dir is still an attack surface — check
            # before unlink rather than trust the source.
            check_source_id(sid)
            (self.claims / sid).unlink(missing_ok=True)
        return stale

    def open_transactions(self):
        if not self.ledger.is_dir():
            return []
        out = []
        for p in sorted(self.ledger.glob("*.json")):
            try:
                d = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError) as e:
                raise InvalidTransactionRecord(
                    f"{p.stem}: unreadable transaction record: {e}") from e
            validate_transaction_data(d, p.stem, self)
            if d["state"] not in TERMINAL:
                out.append(d)
        return out


class Transaction:
    SEQUENCE = FORWARD  # subclasses override; validation is per-class (6b-3)

    def __init__(self, store, cluster_id, data=None):
        check_cluster_id(cluster_id)
        self.store = store
        self.cluster_id = cluster_id
        self.path = store.ledger / f"{cluster_id}.json"
        self.d = data if data is not None else {
            "cluster_id": cluster_id, "state": "DISCOVERED", "sources": [],
            "history": [], "artifact": None, "artifact_hash_before": None,
            "citations": [], "archive_dir": None, "manifest_rows": [],
            "archive_hashes": {},
            "watermark_before": None, "reason": None,
        }

    # ---------------------------------------------------------------- lifecycle
    @classmethod
    def begin(cls, store, cluster_id, sources):
        sources = list(sources)
        for sid in sources:
            check_source_id(sid)
        tx = cls(store, cluster_id)
        tx.d["sources"] = sources
        tx._record("DISCOVERED")
        return tx

    @classmethod
    def load(cls, store, cluster_id):
        check_cluster_id(cluster_id)
        p = store.ledger / f"{cluster_id}.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise InvalidTransactionRecord(
                f"{cluster_id}: unreadable transaction record: {e}") from e
        validate_transaction_data(data, cluster_id, store, cls.SEQUENCE)
        return cls(store, cluster_id, data)

    def _legal(self, cur, new):
        if new == cur:
            return True  # idempotent re-record — how replay works
        if new == "ROLLING_BACK":
            return cur not in TERMINAL and cur != "COMMITTING"
        if new == "ROLLED_BACK":
            return cur == "ROLLING_BACK"
        seq = type(self).SEQUENCE
        return (cur in seq and new in seq
                and seq.index(new) == seq.index(cur) + 1)

    def _record(self, state, **payload):
        """Persist intent BEFORE the caller performs the effect. Validates the
        transition first (6b-3) — an illegal state must never reach disk, or
        next_action() would faithfully replay it."""
        if not self._legal(self.d["state"], state):
            raise IllegalTransition(
                f"{self.cluster_id}: {self.d['state']} → {state} is not a legal "
                f"transition for {type(self).__name__}")
        self.d.update(payload)
        self.d["state"] = state
        self.d["history"] = self.d["history"] + [state]
        _atomic_write(self.path, json.dumps(self.d, indent=2))

    # ---------------------------------------------------------------- forward
    def claim(self):
        """Snapshot the watermark, then claim every source. Order matters: the
        snapshot must be durable before anything becomes invisible to prune."""
        self._record("CLAIMED", watermark_before=self.store.read_watermark())
        for sid in self.d["sources"]:
            check_source_id(sid)
            _atomic_write(self.store.claims / sid, self.cluster_id)
        return self

    def artifact_written(self, path, hash_before):
        self._record("ARTIFACT_WRITTEN", artifact=str(path),
                     artifact_hash_before=hash_before)
        return self

    def citations_repointed(self, triples):
        self._record("CITATIONS_REPOINTED", citations=[list(t) for t in triples])
        return self

    def archived(self, archive_dir):
        self._record("ARCHIVED", archive_dir=str(archive_dir))
        return self

    def manifest_updated(self, rows, manifest_path=None):
        """Record the rows AND where they went. Without the path, rollback cannot
        find the rows to remove — which is exactly how hole 1 (6b-2) happened."""
        self._record("MANIFEST_UPDATED", manifest_rows=list(rows),
                     manifest_path=str(manifest_path) if manifest_path else None)
        return self

    def commit(self, watermark_value=None):
        """Watermark advances ONLY here (#3). Committed at any earlier state it
        silently skips this cluster on the next run.

        Two records bracket the effects (6b-1): COMMITTING — carrying the
        watermark value, so replay needs no caller state — before them,
        COMMITTED only after the last one. A crash anywhere inside leaves
        COMMITTING, which recover() reports and a no-argument commit() rolls
        FORWARD idempotently. Recording COMMITTED first left a terminal
        transaction still holding claims, invisible to recovery forever."""
        if watermark_value is None:
            watermark_value = self.d.get("watermark_value")
            if self.d["state"] != "COMMITTING" or watermark_value is None:
                raise RuntimeError(f"{self.cluster_id}: no watermark value — "
                                   "argument required outside COMMITTING replay")
        self._record("COMMITTING", watermark_value=watermark_value)
        self.store._set_watermark(watermark_value)
        self._release_claims()
        self._record("COMMITTED")
        return self

    # ---------------------------------------------------------------- rollback
    def rollback(self, reason=""):
        """Re-entrant: every undo below is idempotent, so a rollback that itself
        crashes is recovered by re-running ROLLING_BACK from the top."""
        if self.d["state"] == "COMMITTING":
            # Past the point of no return: the watermark may already have moved.
            # Recovery rolls FORWARD from here — a no-argument commit().
            raise RuntimeError(f"{self.cluster_id}: COMMITTING is forward-only — "
                               "finish with commit(), do not roll back")
        self._record("ROLLING_BACK", reason=reason)
        undone = {"artifact": False, "citations": 0, "archive": 0}

        art = self.d.get("artifact")
        if art:
            p = pathlib.Path(art)
            if self.d.get("artifact_hash_before") is None:
                # artifact did not exist before this transaction
                if p.exists():
                    p.unlink()
                    undone["artifact"] = True
            elif p.exists() and content_hash(p) != self.d["artifact_hash_before"]:
                undone["artifact"] = True  # caller restores bytes; we flag the need

        for f, old, new in reversed(self.d.get("citations") or []):
            fp = pathlib.Path(f)
            if fp.exists() and new in fp.read_text():
                fp.write_text(fp.read_text().replace(new, old))
                undone["citations"] += 1

        adir = self.d.get("archive_dir")
        if adir:
            ad = pathlib.Path(adir)
            for sid in self.d["sources"]:
                # Restore the lesson AND its sibling evidence archive. Restoring only
                # `<sid>.md` put the lesson back while leaving `<sid>.evidence.md` in the
                # pruned dir — and `build_index.record()` reads the archive in preference
                # to the body, so the rolled-back lesson came back with its history
                # amputated and no error anywhere.
                for name in (f"{sid}.md", f"{sid}.evidence.md"):
                    src = ad / name
                    dst = self.store.root / name
                    if src.exists() and not dst.exists():
                        src.rename(dst)
                        undone["archive"] += 1

        # 6b-2: remove THIS transaction's MANIFEST block — by marker, so another
        # transaction's rows are untouchable, and idempotently, so replay is safe.
        # Before this, rollback left MANIFEST history asserting a retirement that
        # did not happen (recovery hole 1).
        mp = self.d.get("manifest_path")
        if mp and manifest_remove(mp, self.cluster_id):
            undone["manifest"] = True
        else:
            undone.setdefault("manifest", False)

        # Watermark: only ever advanced at COMMITTED, so this is an ASSERT, not a
        # restore. If it moved, the protocol is broken and we want to know.
        assert self.store.read_watermark() == self.d.get("watermark_before"), (
            f"{self.cluster_id}: watermark moved before COMMITTED — protocol bug"
        )

        # Claims release BEFORE the terminal record (6b-1). Crash between the
        # two leaves ROLLING_BACK, which replays through the idempotent undos
        # above. The old order left a terminal ROLLED_BACK still holding claims
        # — recover() skips terminals, so the sources stayed hidden forever.
        # Releasing early is safe here and only here: the undos just restored
        # the sources, so there is nothing in flight left to protect.
        self._release_claims()
        self._record("ROLLED_BACK")
        return undone

    def _release_claims(self):
        for sid in self.d["sources"]:
            check_source_id(sid)
            (self.store.claims / sid).unlink(missing_ok=True)

    # ---------------------------------------------------------------- recovery
    def next_action(self):
        """The step to (re)execute. The LAST recorded state, not the next one —
        a recorded state means the intent is durable, not that the effect landed."""
        s = self.d["state"]
        if s in TERMINAL:
            return None
        if s == "ROLLING_BACK":
            return "ROLLING_BACK"
        return s


class PruneTransaction(Transaction):
    """Phase D of `instinct-prune` (spec §5): archive-first, nothing authored.
    Load crashed prune transactions with THIS class — Transaction.load would
    validate replays against distill's sequence and reject the archive-first
    order."""
    SEQUENCE = PRUNE_FORWARD

    def record_plan(self, archive_dir, citations, manifest_path, manifest_rows,
                    archive_hashes=None):
        """Persist the WHOLE retirement plan up front (a same-state re-record,
        legal by 6b-3). Without this, a crash before a step's own record left
        --resume unable to reconstruct intent — the plan lived only in the
        crashed process's memory."""
        self._record(self.d["state"], archive_dir=str(archive_dir),
                     citations=[list(t) for t in citations],
                     manifest_path=str(manifest_path),
                     manifest_rows=list(manifest_rows),
                     archive_hashes=dict(archive_hashes or {}))
        return self


def recover(store):
    """Every open transaction and the step each must replay."""
    return [(d["cluster_id"], Transaction(store, d["cluster_id"], d).next_action())
            for d in store.open_transactions()]


# -------------------------------------------------------------------- selftest
def _corpus(tmp):
    root = pathlib.Path(tmp) / "personal"
    root.mkdir(parents=True)
    for sid in ("src-a", "src-b"):
        (root / f"{sid}.md").write_text(f"# {sid}\n")
    (root / "other.md").write_text("see [[src-a]]\n")
    return root


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    # ---- crash injection at every forward transition -----------------------
    # [:-2] excludes COMMITTED (terminal) and COMMITTING — the latter has no
    # entry in `seq` (it is entered inside commit()) and is covered by the
    # dedicated 6b-1 probes below; letting it fall through this loop would
    # silently test MANIFEST_UPDATED while claiming to test COMMITTING.
    for stop in FORWARD[:-2]:
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp)
            st = Store(root)
            before = {p.name for p in root.glob("*.md")}
            tx = Transaction.begin(st, "c1", ["src-a", "src-b"])
            seq = [
                ("CLAIMED", lambda: tx.claim()),
                ("ARTIFACT_WRITTEN", lambda: tx.artifact_written(root / "topic.md", None)),
                ("CITATIONS_REPOINTED", lambda: tx.citations_repointed(
                    [[str(root / "other.md"), "[[src-a]]", "[[topic]]"]])),
                ("ARCHIVED", lambda: tx.archived(root / ".pruned-x")),
                ("MANIFEST_UPDATED", lambda: tx.manifest_updated(["src-a → topic"])),
            ]
            # DISCOVERED is the begin() state — it is not in `seq`, so guard it:
            # falling through the loop would run every step and silently test
            # MANIFEST_UPDATED while claiming to test DISCOVERED.
            if stop != "DISCOVERED":
                for name, fn in seq:
                    fn()
                    if name == stop:
                        break
            # --- crash here: new process, ledger only ---
            st2 = Store(root)
            rec = recover(st2)
            check(f"crash@{stop}: recovery names exactly one open tx, replaying {stop}",
                  rec == [("c1", stop)])
            check(f"crash@{stop}: watermark still unset",
                  st2.read_watermark() is None)
            # nothing is claimed until CLAIMED is recorded
            expect_claims = set() if stop == "DISCOVERED" else {"src-a", "src-b"}
            check(f"crash@{stop}: claims match state ({len(expect_claims)} held)",
                  st2.claimed_ids() == expect_claims)
            tx2 = Transaction.load(st2, "c1")
            tx2.rollback("crash test")
            check(f"crash@{stop}: rollback releases claims", st2.claimed_ids() == set())
            check(f"crash@{stop}: corpus file set restored",
                  {p.name for p in root.glob("*.md")} == before)
            check(f"crash@{stop}: terminal state readable",
                  Transaction.load(st2, "c1").d["state"] == "ROLLED_BACK")

    # ---- happy path --------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        tx = Transaction.begin(st, "c2", ["src-a"])
        tx.claim()
        check("claimed source is invisible to prune", "src-a" in st.claimed_ids())
        tx.artifact_written(root / "topic.md", None)
        tx.citations_repointed([])
        tx.archived(root / ".pruned-x")
        tx.manifest_updated([])
        check("watermark still unset one step before COMMITTED",
              st.read_watermark() is None)
        tx.commit("2026-07-29T00:00:00Z")
        check("watermark advances only at COMMITTED",
              st.read_watermark() == "2026-07-29T00:00:00Z")
        check("claims released on commit", st.claimed_ids() == set())
        check("committed tx no longer open", recover(st) == [])

    # ---- citation reversal + archive restore -------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        other = root / "other.md"
        tx = Transaction.begin(st, "c3", ["src-a"])
        tx.claim()
        art = root / "topic.md"
        art.write_text("# topic\n")
        tx.artifact_written(art, None)
        other.write_text(other.read_text().replace("[[src-a]]", "[[topic]]"))
        tx.citations_repointed([[str(other), "[[src-a]]", "[[topic]]"]])
        # A real source usually travels with its evidence archive. Restoring only
        # `<sid>.md` brought the lesson back with its history amputated — and since
        # `build_index.record()` prefers the archive, the amputation is exactly what the
        # gate would have read.
        (root / "src-a.evidence.md").write_text("# Evidence archive — src-a\n")
        arch = root / ".pruned-x"
        arch.mkdir()
        (root / "src-a.md").rename(arch / "src-a.md")
        (root / "src-a.evidence.md").rename(arch / "src-a.evidence.md")
        tx.archived(arch)
        check("pre-rollback: source archived and citation repointed",
              not (root / "src-a.md").exists() and "[[topic]]" in other.read_text())
        undone = tx.rollback("panel rejected")
        check("rollback reverses the citation", "[[src-a]]" in other.read_text())
        check("rollback restores the archived source", (root / "src-a.md").exists())
        check("rollback restores the sibling evidence archive too",
              (root / "src-a.evidence.md").exists())
        check("rollback removes an artifact this tx created", not art.exists())
        check("rollback counted its undos", undone["citations"] == 1
              and undone["archive"] == 2 and undone["artifact"])
        again = Transaction.load(st, "c3")
        again.d["state"] = "ROLLING_BACK"
        again.rollback("re-entrant")
        check("rollback is RE-ENTRANT (second pass is a no-op, not a crash)",
              (root / "src-a.md").exists() and "[[src-a]]" in other.read_text())

    # ---- 6b-1: the terminal-record -> claim-release window ------------------
    # A crash between recording a terminal state and releasing claims must NOT
    # produce a terminal transaction still holding claims: recover() skips
    # terminals, so that claim would hide its sources from prune forever. The
    # probe kills _release_claims and asserts the transaction stays VISIBLE.
    for path_name in ("commit", "rollback"):
        with tempfile.TemporaryDirectory() as tmp:
            root = _corpus(tmp)
            st = Store(root)
            tx = Transaction.begin(st, "c6", ["src-a"])
            tx.claim()
            tx.artifact_written(root / "topic.md", None)
            tx.citations_repointed([])
            tx.archived(root / ".pruned-x")
            tx.manifest_updated([])
            tx._release_claims = lambda: (_ for _ in ()).throw(OSError("crash"))
            try:
                if path_name == "commit":
                    tx.commit("W1")
                else:
                    tx.rollback("crash test")
            except Exception:
                pass
            st2 = Store(root)
            check(f"{path_name} crash before claim-release: claim still held",
                  st2.claimed_ids() == {"src-a"})
            check(f"{path_name} crash before claim-release: tx still OPEN to recovery",
                  recover(st2) != [])
            tx2 = Transaction.load(st2, "c6")
            check(f"{path_name} crash: recorded state is not terminal",
                  tx2.d["state"] not in TERMINAL)
            # roll forward / re-run: both paths must finish idempotently
            if path_name == "commit":
                tx2.commit()  # no argument: watermark value must come from the ledger
                check("replayed commit sets the recorded watermark",
                      st2.read_watermark() == "W1")
            else:
                tx2.rollback("replay")
            check(f"replayed {path_name} releases claims", st2.claimed_ids() == set())
            check(f"replayed {path_name} reaches terminal",
                  Transaction.load(st2, "c6").d["state"] in TERMINAL)
            check(f"replayed {path_name} leaves nothing open", recover(st2) == [])

    # ---- 6b-2: MANIFEST transaction identity ---------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        mf = root / ".pruned-x" / "MANIFEST.md"
        mf.parent.mkdir()
        pre = ("# Pruned\n\n<!-- tx:other -->\n## → other-topic\n"
               "| `src-z` | 5 | y |\n<!-- /tx:other -->\n")
        mf.write_text(pre)
        manifest_append(mf, "c8", "## → topic\n| `src-a` | 3 | x |")
        t = mf.read_text()
        check("append adds a bracketed block", "<!-- tx:c8 -->" in t and "src-a" in t)
        manifest_append(mf, "c8", "## → topic\n| `src-a` | 3 | x |")
        check("append is idempotent under replay (one block, not two)",
              mf.read_text().count("<!-- tx:c8 -->") == 1)
        check("remove of an absent id is a no-op", manifest_remove(mf, "c9") is False)
        tx = Transaction.begin(st, "c8", ["src-a"])
        tx.claim()
        tx.artifact_written(root / "topic.md", None)
        tx.citations_repointed([])
        tx.archived(root / ".pruned-x")
        tx.manifest_updated(["src-a → topic"], manifest_path=mf)
        tx.rollback("panel rejected")
        t = mf.read_text()
        check("rollback removes exactly this tx's MANIFEST rows", t == pre)
        check("another tx's rows survive untouched",
              "<!-- tx:other -->" in t and "src-z" in t)
        again = Transaction.load(st, "c8")
        again.d["state"] = "ROLLING_BACK"
        again.rollback("re-entrant")
        check("manifest removal is re-entrant", mf.read_text() == pre)

    # ---- 6b-1: COMMITTING is forward-only ------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        tx = Transaction.begin(st, "c7", ["src-a"])
        tx.claim()
        tx.d["state"] = "COMMITTING"  # as recovery would find it
        try:
            tx.rollback("must refuse")
            refused = False
        except RuntimeError:
            refused = True
        check("rollback from COMMITTING refuses (roll forward instead)", refused)

    # ---- 6b-3: validated transitions ----------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        tx = Transaction.begin(st, "c9", ["src-a"])
        tx.claim()
        try:
            tx.manifest_updated([])  # skips ARTIFACT_WRITTEN..ARCHIVED
            jumped = True
        except IllegalTransition:
            jumped = False
        check("distill: skipping states raises IllegalTransition", not jumped)
        check("the illegal record was NOT persisted",
              Transaction.load(st, "c9").d["state"] == "CLAIMED")

    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        # prune is archive-FIRST (spec §5, 2026-07-31) and authors nothing
        px = PruneTransaction.begin(st, "p1", ["src-a"])
        px.claim()
        try:
            px.artifact_written(root / "t.md", None)
            allowed = True
        except IllegalTransition:
            allowed = False
        check("prune: artifact_written is not in its sequence", not allowed)
        try:
            px.citations_repointed([])
            wrong_order = True
        except IllegalTransition:
            wrong_order = False
        check("prune: citations before archive violates archive-first", not wrong_order)
        px.archived(root / ".pruned-x")
        px.citations_repointed([])
        px.manifest_updated([])
        px.commit("W2")
        check("prune: archive-first sequence commits clean",
              PruneTransaction.load(st, "p1").d["state"] == "COMMITTED"
              and st.claimed_ids() == set())
        px2 = PruneTransaction.begin(st, "p2", ["src-b"])
        px2.claim()
        px2.archived(root / ".pruned-x")
        px2.rollback("mid-flight")
        check("prune: rollback from mid-sequence is legal",
              PruneTransaction.load(st, "p2").d["state"] == "ROLLED_BACK")

    # ---- 6b-4: stale-claim semantics — the ledger is the clock ---------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        live = Transaction.begin(st, "c10", ["src-a"])
        live.claim()
        # orphan: a claim naming a cluster with no ledger entry at all
        _atomic_write(st.claims / "src-b", "ghost-cluster")
        # crash legacy: terminal transaction still holding its claim, forged the
        # way the pre-COMMITTING protocol produced it (direct write, no _record)
        done = Transaction.begin(st, "c11", ["src-c"])
        done.claim()
        done.d["state"] = "COMMITTED"
        _atomic_write(done.path, json.dumps(done.d))
        stale = st.stale_claims()
        check("claim with an OPEN transaction is not stale", "src-a" not in stale)
        check("claim with no transaction is stale", stale.get("src-b") == "ghost-cluster")
        check("claim held by a terminal transaction is stale", stale.get("src-c") == "c11")
        reclaimed = st.reclaim_stale()
        check("reclaim releases exactly the stale claims",
              set(reclaimed) == {"src-b", "src-c"} and st.claimed_ids() == {"src-a"})
        check("reclaim is idempotent", st.reclaim_stale() == {})

    # ---- ledger write failure -> fail closed, keep the claim ---------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        tx = Transaction.begin(st, "c4", ["src-a"])
        tx.claim()
        st.ledger.chmod(0o500)  # read-only: next ledger write must fail
        try:
            tx.artifact_written(root / "topic.md", None)
            raised = False
        except LedgerWriteFailed:
            raised = True
        finally:
            st.ledger.chmod(0o700)
        check("ledger write failure raises LedgerWriteFailed", raised)
        check("failed ledger write LEAVES the claim (fail closed)",
              st.claimed_ids() == {"src-a"})

    # ---- watermark-moved assertion -----------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        tx = Transaction.begin(st, "c5", ["src-a"])
        tx.claim()
        st._set_watermark("MOVED-EARLY")  # simulate the protocol bug
        try:
            tx.rollback("x")
            caught = False
        except AssertionError:
            caught = True
        check("watermark moving before COMMITTED trips the assert", caught)

    # ---- source-id path escape (pathlib `/` discards root for an absolute
    # right side, so an unchecked sid can write/unlink outside the store) ----
    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        sentinel = pathlib.Path(tmp) / "victim.txt"
        sentinel.write_text("do not touch\n")
        for bad in ("/tmp/whatever", "../evil", "a/b", "..", ""):
            try:
                Transaction.begin(st, "cx", ["src-a", bad])
                raised = False
            except UnsafeSourceId:
                raised = True
            check(f"begin() refuses unsafe id {bad!r}", raised)
        check("no ledger entry was written for the refused id",
              not (st.ledger / "cx.json").exists())
        check("claims dir has nothing unexpected", st.claims.is_dir() is False
              or list(st.claims.iterdir()) == [])
        check("sentinel outside the store is untouched",
              sentinel.exists() and sentinel.read_text() == "do not touch\n")
        # claim() and _release_claims() are also reachable with a bad id via a
        # hand-edited ledger (Transaction.load bypasses begin()'s check), so
        # they must enforce independently, not rely on begin() alone.
        tx = Transaction(st, "cx2")
        tx.d["sources"] = ["/tmp/whatever"]
        try:
            tx.claim()
            raised = False
        except UnsafeSourceId:
            raised = True
        check("claim() refuses an unsafe id even bypassing begin()", raised)
        check("claim() wrote nothing outside the store",
              sentinel.read_text() == "do not touch\n")
        tx2 = Transaction(st, "cx3")
        tx2.d["sources"] = ["../evil"]
        try:
            tx2._release_claims()
            raised = False
        except UnsafeSourceId:
            raised = True
        check("_release_claims() refuses an unsafe id", raised)
        # RED probe: neuter check_source_id and confirm the guard actually
        # gates something — a green result on unmodified code proves nothing
        # if the call site never runs the check.
        # Patch THIS module's own namespace, not a re-import — running as
        # `python3 ledger.py` registers the file as `__main__`, so `import
        # ledger` would load a SECOND, distinct module object whose functions
        # Transaction.claim() never calls.
        _ledger_mod = sys.modules[__name__]
        original = _ledger_mod.check_source_id
        # Target an absolute path under the sandbox-writable tmp tree (not
        # literal /tmp) so the probe demonstrates the pathlib escape itself,
        # not an unrelated permission denial from writing outside the sandbox.
        escape_target = pathlib.Path(tmp) / "should-be-blocked"
        _ledger_mod.check_source_id = lambda sid: sid  # neuter
        try:
            tx3 = Transaction(st, "cx4")
            tx3.d["sources"] = [str(escape_target)]
            tx3.claim()
            neutered_wrote_outside = escape_target.exists()
        finally:
            _ledger_mod.check_source_id = original
            escape_target.unlink(missing_ok=True)
        check("RED probe: neutering check_source_id lets the escape happen "
              "(proves the guard, when live, is what stops it)",
              neutered_wrote_outside)

    with tempfile.TemporaryDirectory() as tmp:
        root = _corpus(tmp)
        st = Store(root)
        st.ledger.mkdir(parents=True)
        malformed = Transaction(st, "bad-origin").d
        malformed.update({"sources": ["src-a"], "state": "ARCHIVED",
                          "history": ["ARCHIVED"]})
        (st.ledger / "bad-origin.json").write_text(json.dumps(malformed))
        try:
            Transaction.load(st, "bad-origin")
            rejected = False
        except InvalidTransactionRecord:
            rejected = True
        check("persisted history must originate at DISCOVERED", rejected)

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if "--status" in args:
        rest = [a for a in args if a != "--status"]
        st = Store(rest[0] if rest else
                   pathlib.Path.home() / ".claude/homunculus/instincts/personal")
        print(f"open transactions: {recover(st)}")
        print(f"claimed (invisible to prune): {sorted(st.claimed_ids())}")
        print(f"stale claims (reclaimable): {st.stale_claims()}")
        print(f"watermark: {st.read_watermark()}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

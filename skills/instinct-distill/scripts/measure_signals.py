#!/usr/bin/env python3
"""Regenerate the evidence base for decision #6/#7. Plan §1D/§1E.

Those decisions rest on measured numbers — link recall 1.5%, same-domain 5.3x, full body
*worse* than trigger/action, the top_k curve. Recorded as prose in a plan, numbers rot and
nobody notices. Recorded as a command, they are re-checkable in one line.

This exists because three of this session's six measurement families were ad-hoc heredocs
that vanished with the session, including the one the whole re-ruling stands on. If you
re-tune any weight in `shortlist.py`, run this and `recall_test.py`, and update §1D.

    python3 measure_signals.py            # all three reports
    python3 measure_signals.py --signals  # true-pair vs random separation
    python3 measure_signals.py --sweep    # recall across top_k
    python3 measure_signals.py --formats  # evidence-format split of the LIVE corpus
    python3 measure_signals.py --selftest
"""
import pathlib
import random
import re
import statistics as st
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import shortlist as S          # noqa: E402
import recall_test as R        # noqa: E402

LIVE = pathlib.Path.home() / ".claude/homunculus/instincts/personal"
FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
TYPED = re.compile(r"^\s*[-*]\s+\*\*\d{4}-\d{2}-\d{2}\*\*", re.M)
EVSEC = re.compile(r"^##\s+[A-Za-z ]*Evidence\b", re.M | re.I)
SEED = 0                       # fixed: a moving baseline is not a baseline


def replay():
    """Ground-truth corpus + the true pairs a human actually merged."""
    truth = R.ground_truth(R.PRUNED / "MANIFEST.md")
    tmp = tempfile.mkdtemp()
    root, _, _ = R.build_corpus(R.PRUNED, tmp, set(truth))
    recs, _ = S.load(root)
    pairs = []
    for members in truth.values():
        m = [x for x in members if x in recs]
        for i in range(len(m)):
            for j in range(i + 1, len(m)):
                pairs.append((m[i], m[j]))
    return root, recs, pairs, truth


def signals():
    root, recs, pairs, _ = replay()
    body, title = {}, {}
    for i in recs:
        t = (root / f"{i}.md").read_text()
        m = FM.match(t)
        b = t[m.end():] if m else t
        body[i] = S.tokens(b)
        h = re.search(r"^#\s+(.*)$", b, re.M)
        title[i] = S.tokens(h.group(1)) if h else set()

    rnd = random.Random(SEED)
    ids = sorted(recs)
    rpairs = [tuple(rnd.sample(ids, 2)) for _ in range(3000)]

    print(f"SIGNAL SEPARATION — {len(pairs)} ground-truth pairs vs {len(rpairs)} random")
    print(f"  {'signal':<20}{'true':>9}{'random':>9}{'sep':>8}   {'>random p95':>11}")

    def row(name, fn):
        a = [fn(x, y) for x, y in pairs]
        b = [fn(x, y) for x, y in rpairs]
        mb = st.mean(b)
        sep = (st.mean(a) / mb) if mb else float("inf")
        thr = sorted(b)[int(.95 * len(b))]
        print(f"  {name:<20}{st.mean(a):>9.4f}{mb:>9.4f}{sep:>7.1f}x"
              f"{sum(1 for v in a if v > thr) / len(a):>11.0%}")

    row("trigger+action", lambda x, y: S.jaccard(recs[x]["tokens"], recs[y]["tokens"]))
    row("full body", lambda x, y: S.jaccard(body[x], body[y]))
    row("H1 title", lambda x, y: S.jaccard(title[x], title[y]))
    row("same domain", lambda x, y: 1.0 if recs[x]["domain"]
        and recs[x]["domain"] == recs[y]["domain"] else 0.0)

    # the link graph, reported as RECALL — the reading error that cost decision #6
    edges = set()
    idset = set(recs)
    for r in recs.values():
        for t in r["links"]:
            if t in idset and t != r["id"]:
                edges.add(frozenset((r["id"], t)))
    linked = sum(1 for x, y in pairs if frozenset((x, y)) in edges)
    print(f"\n  [[link]] graph: {linked}/{len(pairs)} true pairs linked = "
          f"{linked/len(pairs):.1%} RECALL")
    print(f"                  {linked}/{len(edges)} edges are true pairs = "
          f"{linked/len(edges):.1%} precision")
    print("  -> precision-when-present is NOT recall. Reading one as the other is what "
          "put links at weight 0.55.")


def sweep(ks=(2, 4, 8, 12, 20, 40)):
    _, recs, pairs, truth = replay()
    print(f"TOP-K SWEEP @ min_score=0.0 — {len(truth)} clusters")
    print(f"  {'k':>4}{'pair recall':>14}{'>=1 member':>12}")
    for k in ks:
        clusters, _ = S.build(recs, top_k=k, min_score=0.0)
        nb = {c["seed"]: {x["id"] for x in c["neighbours"]} for c in clusters}
        hits = sum(1 for x, y in pairs if y in nb.get(x, ())) \
            + sum(1 for x, y in pairs if x in nb.get(y, ()))
        tot = len(pairs) * 2
        cany = ctot = 0
        for members in truth.values():
            m = [x for x in members if x in recs]
            if len(m) < 2:
                continue
            ctot += 1
            if any(b in nb.get(a, ()) for a in m for b in m if a != b):
                cany += 1
        print(f"  {k:>4}{hits:>7}/{tot:<6}{hits/tot:>5.0%}{cany/ctot:>11.0%}")


def formats():
    files = sorted(p for p in LIVE.glob("*.md") if not p.name.endswith(".evidence.md"))
    spec = legacy = none = 0
    for p in files:
        t = p.read_text()
        m = FM.match(t)
        b = t[m.end():] if m else t
        if not EVSEC.search(b):
            none += 1
        elif TYPED.search(b):
            spec += 1
        else:
            legacy += 1
    arch = len(list(LIVE.glob("*.evidence.md")))
    print(f"EVIDENCE FORMAT SPLIT — live corpus, {len(files)} lessons")
    print(f"  spec-format (bold-date entries) : {spec}")
    print(f"  legacy prose bullets            : {legacy}")
    print(f"  no evidence section             : {none}")
    print(f"  sibling .evidence.md archives   : {arch}")


def selftest():
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    check("jaccard identical sets = 1", S.jaccard({"a", "b"}, {"a", "b"}) == 1.0)
    check("jaccard disjoint sets = 0", S.jaccard({"a"}, {"b"}) == 0.0)
    check("jaccard empty is 0, not a crash", S.jaccard(set(), {"a"}) == 0.0)
    # a planted signal must separate from noise, or the separation metric is meaningless
    rnd = random.Random(SEED)
    shared = {f"t{i}" for i in range(5)}
    true_like = [(shared | {f"x{rnd.randint(0,3)}"}, shared | {f"y{rnd.randint(0,3)}"})
                 for _ in range(200)]
    rand_like = [({f"a{rnd.randint(0,500)}" for _ in range(6)},
                  {f"a{rnd.randint(0,500)}" for _ in range(6)}) for _ in range(200)]
    ts = st.mean(S.jaccard(a, b) for a, b in true_like)
    rs = st.mean(S.jaccard(a, b) for a, b in rand_like)
    check("planted signal separates from noise", ts > rs * 3)
    check("fixed seed makes the baseline reproducible",
          random.Random(SEED).random() == random.Random(SEED).random())
    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    want = [a for a in args if a in ("--signals", "--sweep", "--formats")] or \
        ["--signals", "--sweep", "--formats"]
    for i, w in enumerate(want):
        if i:
            print()
        {"--signals": signals, "--sweep": sweep, "--formats": formats}[w]()
    return 0


if __name__ == "__main__":
    sys.exit(main())

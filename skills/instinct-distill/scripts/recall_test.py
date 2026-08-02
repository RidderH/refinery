#!/usr/bin/env python3
"""Measure shortlist RECALL against human-curated ground truth. Plan §1.

"It proposed a cluster" proves one example. This answers the question that matters:
of the clusters a human actually made, how many would `shortlist.py` have surfaced?

Ground truth is `Archive/2026-07-28/MANIFEST.md` — 56 clusters over 218 retired sources,
each `## → <target>` block listing the sources merged into it.

Method: reconstruct the corpus as it stood the moment BEFORE those merges (see
`build_corpus` — replaying the 218 sources alone deletes the link graph and measures a
corpus that never existed), run the real `shortlist.load`/`build`, and ask whether each
cluster's members surface as each other's neighbours. Merge *targets* are excluded: they
are the answers and did not exist pre-merge.

Reported by cluster size, never as one average: size-2 clusters have one pair to find and
size-5 have twenty, and a flat mean hides that.

`>=1 member` is the metric that matters. Distill's flow is shortlist → **model judges the
partition, reading files in full** → gate. Once two members of a true cluster land in front
of the judge together, it can assemble the rest by reading. Pair recall is the stricter
proxy; `all members` is near-zero by construction and is not a target.

This harness is the gate on decision #6/#7. Re-run it after changing any weight in
`shortlist.py`; the 2026-07-29 re-ruling moved it from 2% to 29% pair / 80% cluster.

Usage:
    python3 recall_test.py [--top-k 12] [--min-score 0.0] [--verbose]
"""
import pathlib
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import shortlist as S  # noqa: E402

PRUNED = pathlib.Path.home() / ".claude/homunculus/instincts/Archive/2026-07-28"


def ground_truth(manifest):
    """{target: [source ids]} for every `## → target` block."""
    text = manifest.read_text()
    out = {}
    for block in re.split(r"^## → ", text, flags=re.M)[1:]:
        target = block.split("\n")[0].strip()
        srcs = re.findall(r"^\|\s*`([^`]+)`", block, re.M)
        if srcs:
            out[target] = srcs
    return out


LIVE = pathlib.Path.home() / ".claude/homunculus/instincts/personal"


def build_corpus(pruned, tmp, targets):
    """Reconstruct the corpus AS IT WAS the moment before the 2026-07-28 merges.

    Not just the 218 retired sources. At merge time they coexisted with everything
    that was never merged, and their [[links]] resolved against that full set — the
    link graph is 0.55 of the pair score, so replaying the sources ALONE deletes the
    dominant signal and measures a corpus that never existed. Measured: 10 resolving
    edges in isolation vs 232 live.

    So: 218 sources + today's `personal/` MINUS the 56 merge targets. The targets are
    the answers and did not exist before the merge; including them would leak.
    """
    root = pathlib.Path(tmp) / "personal"
    root.mkdir(parents=True)
    n_src = n_ctx = 0
    for p in pruned.glob("*.md"):
        if p.stem == "MANIFEST":
            continue
        shutil.copy2(p, root / p.name)
        n_src += 1
    for p in LIVE.glob("*.md"):
        if p.name.endswith(".evidence.md") or p.stem in targets:
            continue
        if (root / p.name).exists():
            continue
        shutil.copy2(p, root / p.name)
        n_ctx += 1
    return root, n_src, n_ctx


def measure(top_k=12, min_score=0.0, verbose=False):
    truth = ground_truth(PRUNED / "MANIFEST.md")
    with tempfile.TemporaryDirectory() as tmp:
        root, n_src, n_ctx = build_corpus(PRUNED, tmp, set(truth))
        records, malformed = S.load(root)
        clusters, edges = S.build(records, top_k=top_k, min_score=min_score)
        nb = {c["seed"]: [x["id"] for x in c["neighbours"]] for c in clusters}

        print(f"replay corpus: {n_src} retired sources + {n_ctx} never-merged context "
              f"= {n_src + n_ctx} files, {len(records)} parsed, "
              f"{len(malformed)} malformed, {edges} resolving edges")
        if malformed:
            print(f"  malformed (excluded, so recall below is a CEILING): {malformed}")

        by_size = {}
        absent_total = 0
        details = []
        for target, srcs in sorted(truth.items()):
            present = [s for s in srcs if s in records]
            absent_total += len(srcs) - len(present)
            if len(present) < 2:
                continue          # cannot measure a pair that isn't there
            pairs = hits = 0
            for a in present:
                for b in present:
                    if a == b:
                        continue
                    pairs += 1
                    if b in nb.get(a, []):
                        hits += 1
            size = len(present)
            rec = by_size.setdefault(size, {"pairs": 0, "hits": 0, "clusters": 0,
                                            "any": 0, "full": 0})
            rec["pairs"] += pairs
            rec["hits"] += hits
            rec["clusters"] += 1
            rec["any"] += 1 if hits else 0
            rec["full"] += 1 if hits == pairs else 0
            details.append((target, size, hits, pairs))

        print(f"\nsources named by MANIFEST but not on disk: {absent_total} "
              f"(merge targets live in personal/ — expected)")
        print(f"\nRECALL @ top-k={top_k}, min-score={min_score}")
        print(f"{'size':>5} {'clusters':>9} {'pair recall':>13} "
              f"{'>=1 member':>11} {'all members':>12}")
        tp = th = tc = ta = tf = 0
        for size in sorted(by_size):
            r = by_size[size]
            tp += r["pairs"]; th += r["hits"]; tc += r["clusters"]
            ta += r["any"]; tf += r["full"]
            print(f"{size:>5} {r['clusters']:>9} "
                  f"{r['hits']:>6}/{r['pairs']:<6} {r['hits']/r['pairs']:>5.0%} "
                  f"{r['any']/r['clusters']:>10.0%} {r['full']/r['clusters']:>11.0%}")
        if tc:
            print(f"{'ALL':>5} {tc:>9} {th:>6}/{tp:<6} {th/tp:>5.0%} "
                  f"{ta/tc:>10.0%} {tf/tc:>11.0%}")

        if verbose:
            print("\nworst clusters (fewest pairs recovered):")
            for t, s, h, p in sorted(details, key=lambda d: (d[2] / d[3], -d[1]))[:12]:
                print(f"  {h}/{p}  size={s}  {t}")
        return th / tp if tp else 0.0


def main():
    args = sys.argv[1:]
    verbose = "--verbose" in args
    args = [a for a in args if a != "--verbose"]

    def opt(name, default, cast):
        if name in args:
            i = args.index(name)
            v = cast(args[i + 1])
            del args[i:i + 2]
            return v
        return default

    if not (PRUNED / "MANIFEST.md").exists():
        print(f"ground truth not found: {PRUNED}/MANIFEST.md", file=sys.stderr)
        return 1
    measure(opt("--top-k", 12, int), opt("--min-score", 0.0, float), verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())

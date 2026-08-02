#!/usr/bin/env python3
"""Mechanically shortlist candidate clusters for `instinct-distill`. Plan §1, decision #6/#7.

MECHANICAL. NO JUDGMENT. This emits *candidates* — which ones are really one lesson is the
model's call, made afterwards against the full files.

**PARTITION, THEN JUDGE** (decision #6/#7, re-ruled 2026-07-29 on measured evidence).

`domain` is the partition boundary; comparison happens only inside it. Within a partition,
trigger/action overlap ranks, and a [[link]] is a tiebreak bonus. A cross-domain pair is
admitted only when the author linked it.

The earlier design scored links at 0.55 and domain at 0.05, on the reading of
`instinct-format/MERGING.md:33` — *"an author already asserting a relationship is the
strongest signal"*. Measured against 56 human-made clusters, that is true of **precision when
a link exists** and false as a claim about **recall**: only 5 of 342 ground-truth pairs are
linked (1.5%), while 63% share a domain. It scored 2% pair recall. `recall_test.py` is the
harness; re-run it after touching any weight here.

**Only RESOLVING links count.** A dangling target would be a phantom cluster member no
downstream judgment can dismiss, because there is no file to read.

**Claimed files are invisible** (#14). A source held by an in-flight transaction must not be
shortlisted into a second cluster.

Usage:
    python3 shortlist.py [--top-k 12] [--min-score 0.0] [--json] [ROOT]
    python3 shortlist.py --selftest
"""
import json
import pathlib
import re
import sys

DEFAULT_ROOT = pathlib.Path.home() / ".claude/homunculus/instincts/personal"

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)
LINK = re.compile(r"\[\[([^\]]+)\]\]")
WORD = re.compile(r"[a-z0-9_.\-/]+")

# Deliberately small. An aggressive stoplist throws away the literal error text and command
# names that make a trigger findable in the first place.
STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "on", "for", "with", "that",
    "this", "not", "but", "be", "are", "was", "you", "your", "its", "at", "by", "as", "if",
    "from", "then", "than", "so", "do", "does", "did", "has", "have", "had", "will", "would",
    "can", "could", "should", "when", "what", "which", "who", "why", "how", "one", "only",
}

# Weights rewritten 2026-07-29 after the recall harness falsified the original scheme.
#
# WAS: W_LINK 0.55, W_LEX 0.40, W_DOMAIN 0.05 -> 2% pair recall against 56 human-made
# clusters. Measured causes: only 5 of 342 ground-truth pairs are [[linked]] (1.5%), while
# 63% share a `domain` — the strongest discriminator carried the smallest weight.
#
# NOW: `domain` is the PARTITION, not a score term. Candidates are only compared inside a
# domain, so the judge sees a scoped set instead of 357 one-liners (still honouring #6's
# "not a flat scan"). Within a partition, trigger/action overlap ranks and a [[link]] is a
# TIEBREAK — precise when present (it survives as a bonus), useless for recall (it no
# longer decides membership).
W_LEX, W_LINK_BONUS = 0.85, 0.15
# A cross-domain pair is admitted ONLY if the author linked it — measured to rescue 4 of
# the 127 cross-domain true pairs. Small, but it is the only cross-partition evidence there is.
CROSS_DOMAIN_NEEDS_LINK = True


def tokens(text):
    return {t for t in WORD.findall(text.lower()) if t not in STOP and len(t) > 2}


def parse(p):
    t = p.read_text()
    m = FM.match(t)
    if not m:
        return None
    fm, body = m.group(1), t[m.end():]

    def field(name):
        mm = re.search(rf"^{name}:\s*(.*?)$", fm, re.M)
        if not mm:
            return ""
        val = mm.group(1).strip().strip('"')
        # trigger:/action: wrap across lines; gather the indented continuation
        tail = re.search(rf"^{name}:.*?\n((?:[ \t]+\S.*\n)*)", fm, re.M | re.S)
        if tail:
            val += " " + " ".join(l.strip().strip('"') for l in tail.group(1).splitlines())
        return val.strip()

    ec = re.search(r"^evidence_count:\s*(\d+)", fm, re.M)
    return {
        "id": p.stem,
        "domain": field("domain"),
        "trigger": field("trigger"),
        "action": field("action"),
        "evidence_count": int(ec.group(1)) if ec else 0,
        "links": LINK.findall(body) + LINK.findall(fm),
        "tokens": tokens(field("trigger") + " " + field("action")),
    }


def claimed(root):
    """Source ids held by an in-flight transaction (#14). Read directly rather than
    importing ledger.py so this stays runnable standalone."""
    d = pathlib.Path(root) / ".distill" / "claims"
    return {p.name for p in d.iterdir()} if d.is_dir() else set()


def load(root):
    """Returns (records, malformed). A file whose frontmatter will not parse is NEVER
    silently dropped: it vanishes from the candidate pool *and* takes its links out of
    the graph, so the shortlist silently shrinks and reads as complete. Same contract as
    `migrate_evidence.py` and §1A step 6 — name the file, never skip it."""
    root = pathlib.Path(root)
    skip = claimed(root)
    out, malformed = {}, []
    for p in sorted(root.glob("*.md")):
        if p.name.endswith(".evidence.md") or p.stem in skip:
            continue
        rec = parse(p)
        if rec:
            out[rec["id"]] = rec
        else:
            malformed.append(p.name)
    return out, malformed


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def eligible(x, y, linked):
    """May this pair be compared at all? Domain is the partition boundary."""
    if x["domain"] and x["domain"] == y["domain"]:
        return True
    return linked if CROSS_DOMAIN_NEEDS_LINK else True


def score_pair(x, y, linked):
    """Rank WITHIN a partition. No domain term — same-domain is a precondition here,
    not a bonus, so scoring it again would just add a constant to every pair."""
    return W_LEX * jaccard(x["tokens"], y["tokens"]) + W_LINK_BONUS * (1.0 if linked else 0.0)


def build(records, top_k=8, min_score=0.0):
    """Returns (clusters, n_edges). One cluster per seed, neighbours drawn from the seed's
    own domain plus any cross-domain file the author explicitly linked.

    `min_score` defaults to 0.0 on purpose: inside a partition the judge is the filter, and
    a threshold tuned on lexical overlap was what suppressed 98% of true pairs before.
    """
    ids = set(records)
    # RESOLVING links only — a dangling target cannot be a cluster member.
    edges = set()
    for r in records.values():
        for t in r["links"]:
            if t in ids and t != r["id"]:
                edges.add(frozenset((r["id"], t)))

    by_domain = {}
    for r in records.values():
        by_domain.setdefault(r["domain"], []).append(r["id"])

    clusters = []
    for cid, rec in sorted(records.items()):
        # candidate pool = own partition + linked cross-domain files
        pool = set(by_domain.get(rec["domain"], []))
        for e in edges:
            if cid in e:
                pool |= set(e)
        pool.discard(cid)

        scored = []
        for oid in pool:
            other = records[oid]
            linked = frozenset((cid, oid)) in edges
            if not eligible(rec, other, linked):
                continue
            s = score_pair(rec, other, linked)
            if s >= min_score:
                scored.append({"id": oid, "score": round(s, 4), "linked": linked,
                               "cross_domain": other["domain"] != rec["domain"]})
        if not scored:
            continue
        scored.sort(key=lambda d: (-d["score"], d["id"]))
        clusters.append({
            "seed": cid,
            "domain": rec["domain"],
            "partition_size": len(by_domain.get(rec["domain"], [])),
            "evidence_count": rec["evidence_count"],
            "neighbours": scored[:top_k],
            "top_score": scored[0]["score"],
        })
    clusters.sort(key=lambda c: (-c["top_score"], c["seed"]))
    return clusters, len(edges)


def main():
    args = [a for a in sys.argv[1:]]
    if "--selftest" in args:
        return selftest()
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]

    def opt(name, default, cast):
        if name in args:
            i = args.index(name)
            v = cast(args[i + 1])
            del args[i:i + 2]
            return v
        return default

    top_k = opt("--top-k", 12, int)
    min_score = opt("--min-score", 0.0, float)
    root = pathlib.Path(args[0]) if args else DEFAULT_ROOT

    records, malformed = load(root)
    clusters, n_edges = build(records, top_k, min_score)
    if as_json:
        print(json.dumps({"clusters": clusters, "malformed": malformed}, indent=2))
        return 1 if malformed else 0
    held = claimed(root)
    print(f"candidates={len(records)}  resolving-link-edges={n_edges}  "
          f"clusters={len(clusters)}  claimed-excluded={len(held)}")
    for c in clusters[:25]:
        nb = ", ".join(f"{n['id']}{'*' if n['linked'] else ''}:{n['score']}"
                       for n in c["neighbours"])
        print(f"  {c['seed']}  [{c['domain']}] -> {nb}")
    if len(clusters) > 25:
        print(f"  … {len(clusters) - 25} more (use --json for all)")
    if malformed:
        print(f"\nMALFORMED — excluded from the graph, so the shortlist above is "
              f"INCOMPLETE ({len(malformed)}):", file=sys.stderr)
        for n in malformed:
            print(f"  {n}", file=sys.stderr)
        return 1
    return 0


# -------------------------------------------------------------------- selftest
def selftest():
    import tempfile
    ok = True

    def check(label, cond):
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        ok = ok and cond

    def write(root, ident, domain, trigger, action, body=""):
        (root / f"{ident}.md").write_text(
            f"---\nid: {ident}\ntrigger: \"{trigger}\"\naction: \"{action}\"\n"
            f"domain: {domain}\nevidence_count: 2\n---\n\n# {ident}\n{body}\n")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        write(root, "alpha", "harness", "hook exit 2 blocked write",
              "read the allowlist first", "see [[beta]] and [[ghost-target]]")
        write(root, "beta", "harness", "hook exit 2 blocked write again",
              "read the allowlist before opining")
        write(root, "gamma", "supabase", "rls policy denies select",
              "check the policy path")
        (root / "alpha.evidence.md").write_text("archive, not a lesson\n")

        recs, bad = load(root)
        check("evidence archives are not candidates", set(recs) == {"alpha", "beta", "gamma"})
        check("clean corpus reports no malformed files", bad == [])

        clusters, n_edges = build(recs, top_k=4, min_score=0.05)
        check("dangling [[ghost-target]] creates no edge", n_edges == 1)
        by_seed = {c["seed"]: c for c in clusters}
        nb_alpha = {n["id"] for n in by_seed["alpha"]["neighbours"]}
        check("linked pair alpha–beta is shortlisted", "beta" in nb_alpha)
        check("ghost target never appears as a neighbour",
              all("ghost-target" not in {n["id"] for n in c["neighbours"]} for c in clusters))
        linked_beta = [n for n in by_seed["alpha"]["neighbours"] if n["id"] == "beta"][0]
        check("link flag set on the linked neighbour", linked_beta["linked"] is True)
        # NOT `not unlinked or ...` — gamma is genuinely absent at this threshold, so the
        # short-circuit made that assertion vacuously true. It passed while testing nothing.
        check("unrelated cross-domain file is not shortlisted at all",
              "gamma" not in nb_alpha)
        # Domain is a PARTITION BOUNDARY, not a score term: an unlinked cross-domain file
        # is excluded structurally, so no threshold can admit it. (This replaces an
        # assertion written for the pre-2026-07-29 weighting, where domain was worth 0.05
        # and gamma could surface by lexical luck.)
        floor, _ = build(recs, top_k=99, min_score=0.0)
        g = [n for n in {c["seed"]: c for c in floor}["alpha"]["neighbours"]
             if n["id"] == "gamma"]
        check("cross-domain file stays excluded even at threshold 0, top-k 99", g == [])

        # …unless the author linked it. That is the only cross-partition evidence there is.
        (root / "delta.md").write_text(
            '---\nid: delta\ntrigger: "unrelated text"\naction: "unrelated"\n'
            'domain: supabase\nevidence_count: 2\n---\n\n# delta\nsee [[alpha]]\n')
        recs_x, _ = load(root)
        cx, _ = build(recs_x, top_k=99, min_score=0.0)
        d = [n for n in {c["seed"]: c for c in cx}["alpha"]["neighbours"]
             if n["id"] == "delta"]
        check("linked cross-domain file IS admitted, flagged cross_domain",
              len(d) == 1 and d[0]["cross_domain"] and d[0]["linked"])
        (root / "delta.md").unlink()

        # link edge must dominate: strip the link, alpha–beta should drop in score
        (root / "alpha.md").write_text(
            (root / "alpha.md").read_text().replace("see [[beta]] and [[ghost-target]]", ""))
        recs2, _ = load(root)
        c2, e2 = build(recs2, top_k=4, min_score=0.05)
        s2 = [n for n in {c["seed"]: c for c in c2}["alpha"]["neighbours"]
              if n["id"] == "beta"][0]["score"]
        check("removing the link lowers the pair score", e2 == 0 and s2 < linked_beta["score"])

        # #14 — claimed sources are invisible
        (root / ".distill" / "claims").mkdir(parents=True)
        (root / ".distill" / "claims" / "beta").write_text("c1")
        recs3, _ = load(root)
        check("claimed source excluded from candidates", "beta" not in recs3)
        c3, _ = build(recs3, top_k=4, min_score=0.05)
        check("claimed source never appears as a neighbour either",
              all("beta" not in {n["id"] for n in c["neighbours"]} for c in c3))

        # A malformed file must be NAMED, never silently dropped — it also takes its
        # links out of the graph, so the shortlist shrinks and still reads as complete.
        (root / "wrecked.md").write_text("# no frontmatter\nsee [[alpha]]\n")
        recs4, bad4 = load(root)
        check("malformed file is reported by name", bad4 == ["wrecked.md"])
        check("malformed file is not silently a candidate", "wrecked" not in recs4)

    print("\nSELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

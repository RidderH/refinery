#!/usr/bin/env python3
"""Explicit filesystem context shared by Refinery CLI boundaries."""

import dataclasses
import pathlib
import sys
import tempfile


@dataclasses.dataclass(frozen=True)
class RuntimeContext:
    config: pathlib.Path
    corpus: pathlib.Path
    rules: pathlib.Path
    skills: pathlib.Path
    ledger: pathlib.Path
    claims: pathlib.Path
    archive: pathlib.Path
    package: pathlib.Path

    @classmethod
    def from_corpus(cls, corpus):
        corpus = pathlib.Path(corpus).resolve()
        package = corpus.parent.parent.parent
        return cls(
            config=package / "homunculus/local-projects.conf",
            corpus=corpus,
            rules=package / "rules",
            skills=package / "skills",
            ledger=corpus / ".distill/ledger",
            claims=corpus / ".distill/claims",
            archive=corpus.parent / "Archive",
            package=package,
        )

    def as_dict(self):
        return {field.name: str(getattr(self, field.name))
                for field in dataclasses.fields(self)}


def selftest():
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        one = RuntimeContext.from_corpus(pathlib.Path(first) / "homunculus/instincts/personal")
        two = RuntimeContext.from_corpus(pathlib.Path(second) / "homunculus/instincts/personal")
        ok = (one.corpus != two.corpus and one.ledger != two.ledger
              and str(first) not in " ".join(two.as_dict().values())
              and str(second) not in " ".join(one.as_dict().values()))
        print("  PASS  two runtime contexts have no path cross-talk" if ok
              else "  FAIL  runtime contexts overlap")
        print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else 2)

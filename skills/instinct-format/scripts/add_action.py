#!/usr/bin/env python3
"""Insert an authored `action:` line into instinct frontmatter, mechanically and atomically.

Authoring the line is judgment work and stays with the author; this script only places it, so
the placement can't drift file to file. It goes immediately after the `trigger:` block (or after
`id:` when there is no trigger), which is where SKILL.md's Skim tier expects it.

Input: TSV on stdin, one `<id>\\t<action text>` per line. Blank lines and #-comments ignored.

    printf '%s\\t%s\\n' my-instinct 'Do the thing, because the other thing lies.' |
        python3 add_action.py

Validation runs over the WHOLE batch before anything is written — one bad row writes no files,
so a partly-applied batch is not a state this can reach. Rejects: unknown id, a file that already
has `action:`, an empty action, a literal `"` (it would break the YAML quoting — use backticks or
single quotes), and any embedded newline (the line must stay greppable on one line).

Usage: python3 add_action.py [--dry] [--dir DIR] < batch.tsv
       python3 add_action.py --selftest
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from trigger_wrap import trigger_block  # noqa: E402  — same placement rules, one definition

DEFAULT_ROOT = pathlib.Path.home() / ".claude/homunculus/instincts/personal"


def plan_one(root, ident, action):
    """Return (path, new_lines) or raise ValueError describing why this row can't be applied."""
    if not action.strip():
        raise ValueError("empty action text")
    if '"' in action:
        raise ValueError('action contains a literal " — use backticks or single quotes')
    if "\n" in action:
        raise ValueError("action spans a newline — it must stay one greppable line")

    path = root / f"{ident}.md"
    if not path.is_file():
        raise ValueError(f"no such file: {path.name}")

    lines = path.read_text().splitlines()
    if any(l.startswith("action:") for l in lines):
        raise ValueError("already has an action: line")

    block = trigger_block(lines)
    if block:
        after = block[-1][0]  # 1-indexed line number of the trigger block's last line
    else:
        idx = [i for i, l in enumerate(lines) if l.startswith("id:")]
        if not idx:
            raise ValueError("no trigger: and no id: — cannot place action:")
        after = idx[0] + 1

    new = lines[:after] + [f'action: "{action.strip()}"'] + lines[after:]
    return path, new


def apply(root, rows, dry=False):
    planned, errors = [], []
    for ident, action in rows:
        try:
            planned.append(plan_one(root, ident, action))
        except ValueError as e:
            errors.append(f"{ident}: {e}")

    if errors:
        print(f"REFUSED — {len(errors)} bad row(s), nothing written:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    for path, new in planned:
        if not dry:
            path.write_text("\n".join(new) + "\n")
    print(f"{'would insert' if dry else 'inserted'} action: into {len(planned)} file(s)")
    return 0


def read_rows(text):
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ident, _, action = line.partition("\t")
        rows.append((ident.strip(), action))
    return rows


def selftest():
    fixture = (
        '---\nid: sample\n'
        'trigger: "first phrase |\n'
        '          second phrase"\n'
        'confidence: 0.5\n---\n\n# Title\n'
    )
    no_trigger = '---\nid: bare\nconfidence: 0.5\n---\n\n# Title\n'
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "sample.md").write_text(fixture)
        (root / "bare.md").write_text(no_trigger)

        apply(root, [("sample", "Do the thing."), ("bare", "Do the other thing.")])
        got = (root / "sample.md").read_text().splitlines()
        if got[4] != 'action: "Do the thing."':
            print(f"FAIL: action not placed after the trigger block (got line 5: {got[4]!r})")
            ok = False
        else:
            print("ok: placed directly after a multi-line trigger block")
        bare = (root / "bare.md").read_text().splitlines()
        if bare[2] != 'action: "Do the other thing."':
            print(f"FAIL: no-trigger placement wrong (got line 3: {bare[2]!r})")
            ok = False
        else:
            print("ok: placed after id: when there is no trigger")

        # Each refusal must block the WHOLE batch, leaving the good row unwritten.
        for label, rows in (
            ("already has action:", [("sample", "Again.")]),
            ("unknown id", [("bare", "fine")] and [("nope", "x")]),
            ('literal "', [("bare", 'say "hi"')]),
            ("empty action", [("bare", "   ")]),
        ):
            before = (root / "bare.md").read_text()
            rc = apply(root, rows)
            if rc == 0:
                print(f"FAIL: batch with {label} was not refused")
                ok = False
            elif (root / "bare.md").read_text() != before:
                print(f"FAIL: {label} refused but a file still changed")
                ok = False
            else:
                print(f"ok: refused — {label}")

        rc = apply(root, [("bare", "second action")])
        if rc == 0:
            print("FAIL: second action: on an already-actioned file was accepted")
            ok = False
        else:
            print("ok: refused — idempotency (no double action:)")

    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    dry = "--dry" in args
    root = DEFAULT_ROOT
    if "--dir" in args:
        root = pathlib.Path(args[args.index("--dir") + 1])
    if not root.is_dir():
        print(f"FATAL: {root} is not a directory", file=sys.stderr)
        return 2
    rows = read_rows(sys.stdin.read())
    if not rows:
        print("FATAL: no rows on stdin", file=sys.stderr)
        return 2
    return apply(root, rows, dry=dry)


if __name__ == "__main__":
    sys.exit(main())

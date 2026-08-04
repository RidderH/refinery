#!/usr/bin/env python3
"""Canonical parser for the supported instinct Markdown/frontmatter contract.

This is deliberately *not* a general YAML parser. It accepts the small subset the
instinct format documents, reports typed diagnostics for everything else, and has no
third-party runtime dependency. Consumers should use this module instead of scraping
frontmatter independently.
"""

import ast
import dataclasses
import datetime
import json
import pathlib
import re
import sys


DOMAINS = {
    "harness", "workflow", "testing", "git", "supabase", "data", "nextjs",
    "frontend", "infra", "debugging", "security", "code-style",
}
KNOWN_FIELDS = {
    "schema_version", "id", "trigger", "action", "confidence", "evidence_count",
    "domain", "source", "created", "updated", "last_checked", "version_pin",
    "promoted_to", "cites", "status",
}
REQUIRED_FIELDS = {
    "schema_version", "id", "trigger", "action", "confidence", "evidence_count",
    "domain", "source", "created", "updated",
}
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(.*))?$")
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: int = 0
    field: str = ""

    def as_dict(self):
        return dataclasses.asdict(self)


class InstinctFormatError(ValueError):
    def __init__(self, diagnostics):
        self.diagnostics = list(diagnostics)
        super().__init__("; ".join(d.message for d in self.diagnostics))


@dataclasses.dataclass
class InstinctRecord:
    values: dict
    body: str
    sections: tuple
    kind: str
    cites_present: bool = False
    cites: list = dataclasses.field(default_factory=list)
    diagnostics: tuple = ()

    def has_section(self, name):
        wanted = name.casefold()
        return any(title.casefold() == wanted for _, title in self.sections)

    def as_dict(self):
        return {
            "kind": self.kind,
            "frontmatter": self.values,
            "sections": [{"level": level, "title": title}
                         for level, title in self.sections],
            "cites_present": self.cites_present,
            "cites": self.cites,
            "diagnostics": [d.as_dict() for d in self.diagnostics],
        }


def _strip_comment(value):
    """Strip a YAML-style comment occurring outside quotes/brackets."""
    quote, escaped, depth = None, False, 0
    for i, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif char == "#" and depth == 0 and (i == 0 or value[i - 1].isspace()):
            return value[:i].rstrip()
    return value.rstrip()


def _quoted_complete(value):
    value = value.lstrip()
    if not value or value[0] not in "'\"":
        return True
    quote, escaped = value[0], False
    for char in value[1:]:
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif char == quote:
            return True
    return False


def _scalar(value, line, field):
    value = _strip_comment(value).strip()
    if value in {"", "null", "Null", "NULL", "~"}:
        return None
    if value[0] in "'\"":
        if not _quoted_complete(value):
            raise InstinctFormatError([
                Diagnostic("unterminated_quote", "quoted scalar is not closed", line, field)
            ])
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            raise InstinctFormatError([
                Diagnostic("invalid_quote", "quoted scalar uses unsupported syntax", line, field)
            ])
        if not isinstance(parsed, str):
            raise InstinctFormatError([
                Diagnostic("invalid_scalar", "expected a string scalar", line, field)
            ])
        return parsed
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    if value.startswith(('[', '{')) or value.endswith((']', '}')):
        raise InstinctFormatError([
            Diagnostic("unsupported_scalar", "unsupported collection-shaped scalar", line, field)
        ])
    return value


def _split_inline_list(value, line):
    clean = _strip_comment(value).strip()
    if not (clean.startswith("[") and clean.endswith("]")):
        raise InstinctFormatError([
            Diagnostic("invalid_cites", "cites must be a closed inline list or block list",
                       line, "cites")
        ])
    inner = clean[1:-1].strip()
    if not inner:
        return []
    items, start, quote, escaped = [], 0, None, False
    for i, char in enumerate(inner):
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == ",":
            items.append(inner[start:i])
            start = i + 1
        elif char in "[]{}":
            raise InstinctFormatError([
                Diagnostic("nested_cites", "nested cites values are not supported", line, "cites")
            ])
    if quote:
        raise InstinctFormatError([
            Diagnostic("unterminated_quote", "quoted cites entry is not closed", line, "cites")
        ])
    items.append(inner[start:])
    return [_cite_item(item, line) for item in items]


def _cite_item(value, line):
    item = _scalar(value, line, "cites")
    if not isinstance(item, str) or not item.strip():
        raise InstinctFormatError([
            Diagnostic("invalid_cite", "each cites entry must be a non-empty path string",
                       line, "cites")
        ])
    return item.strip()


def _validate(values, path, require_complete):
    diagnostics = []
    ident = values.get("id")
    if ident is not None and (not isinstance(ident, str) or not SAFE_ID.fullmatch(ident)):
        diagnostics.append(Diagnostic("unsafe_id", "id must be a safe lowercase path stem",
                                      field="id"))
    if path is not None and ident is not None and pathlib.Path(path).suffix == ".md":
        if pathlib.Path(path).stem != ident:
            diagnostics.append(Diagnostic("id_filename_mismatch",
                                          "id must equal the Markdown filename stem", field="id"))
    for field in sorted(set(values) - KNOWN_FIELDS):
        diagnostics.append(Diagnostic("unknown_field", f"unknown field: {field}", field=field))
    if "schema_version" in values and values["schema_version"] != 2:
        diagnostics.append(Diagnostic("schema_version", "schema_version must be 2",
                                      field="schema_version"))
    for field in ("trigger", "action", "domain", "source", "created", "updated"):
        if field in values and (not isinstance(values[field], str) or not values[field].strip()):
            diagnostics.append(Diagnostic("field_type", f"{field} must be a non-empty string",
                                          field=field))
    if "confidence" in values and (
            not isinstance(values["confidence"], (int, float))
            or not 0.0 <= values["confidence"] <= 0.9):
        diagnostics.append(Diagnostic("confidence", "confidence must be a number from 0 to 0.9",
                                      field="confidence"))
    if "evidence_count" in values and (
            not isinstance(values["evidence_count"], int) or values["evidence_count"] < 0):
        diagnostics.append(Diagnostic("evidence_count", "evidence_count must be a non-negative integer",
                                      field="evidence_count"))
    domain = values.get("domain")
    if domain is not None and domain not in DOMAINS:
        diagnostics.append(Diagnostic("unknown_domain", f"domain is not in the closed vocabulary: {domain}",
                                      field="domain"))
    for field in ("created", "updated", "last_checked"):
        value = values.get(field)
        if value is None:
            continue
        try:
            if datetime.date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except (TypeError, ValueError):
            diagnostics.append(Diagnostic("invalid_date", f"{field} must be YYYY-MM-DD or null",
                                          field=field))
    for field in ("version_pin", "promoted_to"):
        value = values.get(field)
        if value is not None and not isinstance(value, str):
            diagnostics.append(Diagnostic("field_type", f"{field} must be a string or null",
                                          field=field))
    if require_complete:
        for field in sorted(REQUIRED_FIELDS - set(values)):
            diagnostics.append(Diagnostic("missing_field", f"required field is missing: {field}",
                                          field=field))
        if "status" in values:
            diagnostics.append(Diagnostic("deprecated_field", "status is not part of schema v2",
                                          field="status"))
    if diagnostics:
        raise InstinctFormatError(diagnostics)


def parse_text(text, path=None, require_complete=False):
    """Parse supported instinct Markdown into one typed record or raise diagnostics.

    A document with no leading frontmatter is returned as ``kind='legacy'``. A document
    that starts frontmatter but cannot finish or parse it is malformed and always raises.
    """
    if not (text.startswith("---\n") or text.startswith("---\r\n")):
        sections = tuple((len(m.group(1)), m.group(2).strip()) for m in HEADING.finditer(text))
        return InstinctRecord({}, text, sections, "legacy")

    lines = text.splitlines(keepends=True)
    closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
    if closing is None:
        raise InstinctFormatError([
            Diagnostic("frontmatter_boundary", "frontmatter has no closing ---", 1)
        ])
    fm_lines = [line.rstrip("\r\n") for line in lines[1:closing]]
    body = "".join(lines[closing + 1:])
    values, seen, diagnostics = {}, {}, []
    cites_present, cites = False, []
    i = 0
    while i < len(fm_lines):
        raw, lineno = fm_lines[i], i + 2
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw[:1].isspace():
            diagnostics.append(Diagnostic("unexpected_indent",
                                          "indented value has no owning field", lineno))
            i += 1
            continue
        match = KEY.match(raw)
        if not match:
            diagnostics.append(Diagnostic("invalid_field", "frontmatter line is not a key/value",
                                          lineno))
            i += 1
            continue
        field, value = match.group(1), match.group(2) or ""
        if field in seen:
            diagnostics.append(Diagnostic("duplicate_key", f"duplicate key: {field}",
                                          lineno, field))
            i += 1
            continue
        seen[field] = lineno

        if field == "cites":
            cites_present = True
            clean = _strip_comment(value).strip()
            try:
                if clean.startswith("["):
                    cites = _split_inline_list(value, lineno)
                elif clean:
                    raise InstinctFormatError([
                        Diagnostic("invalid_cites", "cites must be a list, not a scalar",
                                   lineno, field)
                    ])
                else:
                    block = []
                    j = i + 1
                    while j < len(fm_lines) and fm_lines[j][:1].isspace():
                        item_line = fm_lines[j]
                        item = re.match(r"^[ \t]+-[ \t]+(.+?)\s*$", item_line)
                        if not item:
                            raise InstinctFormatError([
                                Diagnostic("invalid_cites", "cites block must contain only list items",
                                           j + 2, field)
                            ])
                        block.append(_cite_item(item.group(1), j + 2))
                        j += 1
                    if not block:
                        raise InstinctFormatError([
                            Diagnostic("invalid_cites", "empty cites must be written as cites: []",
                                       lineno, field)
                        ])
                    cites = block
                    i = j - 1
                values[field] = list(cites)
            except InstinctFormatError as exc:
                diagnostics.extend(exc.diagnostics)
        else:
            parts = [value.strip()]
            if parts[0].lstrip().startswith(("'", '"')) and not _quoted_complete(parts[0]):
                j = i + 1
                while j < len(fm_lines) and fm_lines[j][:1].isspace():
                    parts.append(fm_lines[j].strip())
                    if _quoted_complete(" ".join(parts)):
                        break
                    j += 1
                i = j
            try:
                values[field] = _scalar(" ".join(parts), lineno, field)
            except InstinctFormatError as exc:
                diagnostics.extend(exc.diagnostics)
        i += 1

    if diagnostics:
        raise InstinctFormatError(diagnostics)
    _validate(values, path, require_complete)
    sections = tuple((len(m.group(1)), m.group(2).strip()) for m in HEADING.finditer(body))
    if require_complete:
        titles = [title.casefold() for _, title in sections]
        missing_sections = []
        for required in ("symptom", "what's actually happening", "do this"):
            if required not in titles:
                missing_sections.append(required)
        if not any(title.startswith("evidence") for title in titles):
            missing_sections.append("evidence")
        if missing_sections:
            raise InstinctFormatError([
                Diagnostic("missing_section", f"required body section is missing: {name}")
                for name in missing_sections
            ])
    complete = REQUIRED_FIELDS.issubset(values)
    return InstinctRecord(values, body, sections, "complete" if complete else "partial",
                          cites_present, cites)


def parse_file(path, require_complete=False):
    path = pathlib.Path(path)
    return parse_text(path.read_text(), path, require_complete=require_complete)


def _cli(args):
    if not args:
        print("usage: instinct_record.py [--json|--gate0|--cites-state|--cites] FILE",
              file=sys.stderr)
        return 2
    mode = next((a for a in args if a.startswith("--")), "--json")
    named = [a for a in args if not a.startswith("--")]
    if len(named) != 1:
        print("exactly one FILE is required", file=sys.stderr)
        return 2
    try:
        rec = parse_file(named[0], require_complete=(mode == "--check"))
    except (OSError, InstinctFormatError) as exc:
        if mode in {"--json", "--check"} and isinstance(exc, InstinctFormatError):
            print(json.dumps({"valid": False,
                              "diagnostics": [d.as_dict() for d in exc.diagnostics]}))
        elif mode == "--cites-state":
            print("INVALID")
        elif mode == "--gate0":
            print("FORMAT_INVALID")
        else:
            print(f"invalid instinct: {exc}", file=sys.stderr)
        return 2
    if mode == "--check":
        print("OK: complete instinct record")
    elif mode == "--gate0":
        action = isinstance(rec.values.get("action"), str) and bool(rec.values["action"].strip())
        symptom = rec.has_section("Symptom")
        print("CONVERTED" if action and symptom else "PARTIAL" if action or symptom else "OLD_FORMAT")
    elif mode == "--cites-state":
        print("ABSENT" if not rec.cites_present else "EMPTY" if not rec.cites else "LIST")
    elif mode == "--cites":
        if not rec.cites_present:
            return 3
        print("\n".join(rec.cites))
    elif mode == "--json":
        payload = rec.as_dict()
        payload["valid"] = True
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2
    return 0


def selftest():
    valid = '''---
schema_version: 2
id: retry-with-backoff
trigger: "429 from the API |
          quota exhausted"
action: 'Retry with jitter.' # why
confidence: 0.8
evidence_count: 2
domain: testing
source: session
created: 2026-08-03
updated: 2026-08-03
last_checked: null
version_pin: null
promoted_to: null
cites: ["project/src/api.py", '.claude/rules/safe.md'] # curated
---

# Retry with backoff

## Symptom

The API returns 429.

## What's actually happening

The service is throttling callers.

## Do this

Retry with bounded jitter.

## Evidence (n=2, latest 2)

- **2026-08-03** `repeated_failure` — synthetic observation.
'''
    block = valid.replace(
        'cites: ["project/src/api.py", \'.claude/rules/safe.md\'] # curated',
        'cites:\n  - "project/src/api.py"\n  - \'.claude/rules/safe.md\'')
    checks = []

    rec = parse_text(valid, pathlib.Path("retry-with-backoff.md"), require_complete=True)
    checks.extend([
        ("multiline quoted scalar", rec.values["trigger"] == "429 from the API | quota exhausted"),
        ("comment outside quotes", rec.values["action"] == "Retry with jitter."),
        ("inline cites", rec.cites == ["project/src/api.py", ".claude/rules/safe.md"]),
        ("body section discovery", rec.has_section("Symptom")),
    ])
    rec2 = parse_text(block, pathlib.Path("retry-with-backoff.md"), require_complete=True)
    checks.append(("block cites agree with inline cites", rec2.cites == rec.cites))

    malformed = {
        "missing closing frontmatter": "---\nid: x\n",
        "duplicate key": "---\nid: x\nid: y\n---\n",
        "unterminated inline list": "---\nid: x\ncites: [\n---\n",
        "scalar cites": "---\nid: x\ncites: nope\n---\n",
        "mapping cites": "---\nid: x\ncites:\n  path: nope\n---\n",
    }
    for label, source in malformed.items():
        try:
            parse_text(source, pathlib.Path("x.md"))
        except InstinctFormatError:
            checks.append((label, True))
        else:
            checks.append((label, False))

    for label, source in {
        "unsupported schema version": "---\nid: x\nschema_version: 99\n---\n",
        "unknown field": "---\nid: x\nmystery: yes\n---\n",
        "numeric action": "---\nid: x\naction: 99\n---\n## Symptom\n",
        "invalid date": "---\nid: x\nupdated: 2026-02-30\n---\n",
    }.items():
        try:
            parse_text(source, pathlib.Path("x.md"))
        except InstinctFormatError:
            checks.append((label, True))
        else:
            checks.append((label, False))

    for label, source in {
        "filename mismatch": valid.replace("id: retry-with-backoff", "id: other"),
        "unsafe id": valid.replace("id: retry-with-backoff", "id: ../escape"),
        "closed domain": valid.replace("domain: testing", "domain: made-up"),
    }.items():
        try:
            parse_text(source, pathlib.Path("retry-with-backoff.md"), require_complete=True)
        except InstinctFormatError:
            checks.append((label, True))
        else:
            checks.append((label, False))

    legacy = parse_text("# Old lesson\n\nNo frontmatter yet.\n")
    checks.append(("legacy document is explicit, not malformed", legacy.kind == "legacy"))
    try:
        parse_text("---\nid: partial\ncites: []\n---\n", pathlib.Path("partial.md"),
                   require_complete=True)
    except InstinctFormatError:
        checks.append(("strict completion rejects partial records", True))
    else:
        checks.append(("strict completion rejects partial records", False))
    crlf = parse_text("---\r\nid: crlf\r\ncites: []\r\n---\r\n## Symptom\r\n",
                      pathlib.Path("crlf.md"))
    checks.append(("CRLF frontmatter is parsed, not downgraded to legacy",
                   crlf.kind == "partial" and crlf.has_section("Symptom")))
    check_confidence = valid.replace("confidence: 0.8", "confidence: 0.9")
    checks.append(("documented confidence cap 0.9 is accepted",
                   parse_text(check_confidence, pathlib.Path("retry-with-backoff.md"),
                              require_complete=True).values["confidence"] == 0.9))
    try:
        parse_text(valid.replace("confidence: 0.8", "confidence: 1.0"),
                   pathlib.Path("retry-with-backoff.md"), require_complete=True)
    except InstinctFormatError:
        checks.append(("confidence above 0.9 is rejected", True))
    else:
        checks.append(("confidence above 0.9 is rejected", False))

    ok = True
    for label, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else _cli(sys.argv[1:]))

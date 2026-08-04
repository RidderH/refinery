#!/usr/bin/env python3
"""Validate Refinery skill frontmatter with the optional PyYAML test dependency."""

import pathlib
import re
import sys

try:
    import yaml
except ImportError:  # Keep the distributed runtime independent of this test tool.
    yaml = None


ALLOWED_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
    "argument-hint",
    "disable-model-invocation",
    "user-invocable",
    "model",
    "context",
    "agent",
    "hooks",
}


if yaml is not None:
    class UniqueKeySafeLoader(yaml.SafeLoader):
        """SafeLoader variant that refuses ambiguous duplicate mapping keys."""


    def _construct_unique_mapping(loader, node, deep=False):
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "merge keys are not supported in skill frontmatter",
                    key_node.start_mark,
                )
        loader.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "mapping keys must be scalar values",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key: {key}",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping


    UniqueKeySafeLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        _construct_unique_mapping,
    )


def _frontmatter(path):
    lines = path.read_text().splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing opening frontmatter delimiter")
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError("missing closing frontmatter delimiter") from exc
    return "\n".join(lines[1:closing])


def _safe_load_unique(source):
    loader = UniqueKeySafeLoader(source)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


def _validate_metadata(frontmatter, directory_name):
    if not all(isinstance(key, str) for key in frontmatter):
        raise ValueError("frontmatter keys must be strings")
    unsupported = sorted(set(frontmatter) - ALLOWED_KEYS)
    if unsupported:
        raise ValueError(f"unsupported frontmatter key: {unsupported[0]}")
    for key in ("name", "description"):
        if key not in frontmatter:
            raise ValueError(f"missing required key: {key}")
    name = frontmatter["name"]
    if (
        not isinstance(name, str)
        or len(name) > 64
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
    ):
        raise ValueError("name must be lowercase kebab-case and at most 64 characters")
    if name != directory_name:
        raise ValueError(f"name must match directory {directory_name!r}")
    description = frontmatter["description"]
    if not isinstance(description, str) or not description.strip():
        raise ValueError("description must be a non-empty string")
    for key in ("disable-model-invocation", "user-invocable"):
        if key in frontmatter and not isinstance(frontmatter[key], bool):
            raise ValueError(f"{key} must be a boolean")


def main(args):
    if len(args) != 1:
        print("usage: validate_skills.py PACKAGE_ROOT", file=sys.stderr)
        return 2
    if yaml is None:
        print(
            "PyYAML is required for contributor validation; install requirements-test.txt",
            file=sys.stderr,
        )
        return 2

    root = pathlib.Path(args[0]).resolve()
    skill_dirs = sorted(path for path in root.glob("skills/instinct-*") if path.is_dir())
    if not skill_dirs:
        print(f"no Refinery skills found under {root}", file=sys.stderr)
        return 2

    failed = False
    for skill_dir in skill_dirs:
        path = skill_dir / "SKILL.md"
        relative = path.relative_to(root)
        if not path.is_file():
            print(f"{relative}: missing", file=sys.stderr)
            failed = True
            continue
        try:
            frontmatter = _safe_load_unique(_frontmatter(path))
            if not isinstance(frontmatter, dict):
                raise ValueError("frontmatter must be a mapping")
            _validate_metadata(frontmatter, path.parent.name)
        except yaml.YAMLError as exc:
            print(f"{relative}: invalid YAML: {exc}", file=sys.stderr)
            failed = True
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"{relative}: invalid frontmatter: {exc}", file=sys.stderr)
            failed = True

    if failed:
        return 1
    print(f"VALIDATION PASSED: {len(skill_dirs)} Refinery skills")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

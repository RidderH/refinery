#!/usr/bin/env python3
"""Validate and apply Refinery's versioned package ownership manifest."""

import glob
import json
import pathlib
import shutil
import sys
import tempfile


FIELDS = {"source", "destination", "type", "mode", "ownership", "replace", "install"}
TYPES = {"directory_glob", "file", "metadata"}


class ManifestError(ValueError):
    pass


def safe_relative(value, label):
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"{label} must be a non-empty POSIX relative path")
    path = pathlib.PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ManifestError(f"{label} escapes its root: {value!r}")
    return value


def load_manifest(path):
    try:
        data = json.loads(pathlib.Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"schema_version", "managed"}:
        raise ManifestError("manifest must contain exactly schema_version and managed")
    if data["schema_version"] != 1 or not isinstance(data["managed"], list):
        raise ManifestError("unsupported manifest schema")
    seen = set()
    for index, entry in enumerate(data["managed"]):
        if not isinstance(entry, dict) or set(entry) != FIELDS:
            raise ManifestError(f"managed[{index}] fields do not match schema")
        safe_relative(entry["source"], "source")
        safe_relative(entry["destination"], "destination")
        if entry["type"] not in TYPES or entry["mode"] not in {"preserve", "executable"}:
            raise ManifestError(f"managed[{index}] has unsupported type or mode")
        if entry["ownership"] not in {"managed", "package"}:
            raise ManifestError(f"managed[{index}] has unsupported ownership")
        expected_replace = "replace_matches" if entry["type"] == "directory_glob" else "replace"
        if entry["replace"] != expected_replace:
            raise ManifestError(
                f"managed[{index}].replace must be {expected_replace!r} for its type")
        if not isinstance(entry["install"], bool):
            raise ManifestError(f"managed[{index}].install must be boolean")
        if entry["install"] != (entry["ownership"] == "managed"):
            raise ManifestError(
                f"managed[{index}] install/ownership values disagree")
        key = (entry["source"], entry["destination"])
        if key in seen:
            raise ManifestError(f"duplicate manifest mapping: {key}")
        seen.add(key)
    return data


def selected_sources(manifest, package_root):
    package_root = pathlib.Path(package_root).resolve()
    selected = []
    for entry in manifest["managed"]:
        pattern = str(package_root / entry["source"])
        matches = sorted(pathlib.Path(p) for p in glob.glob(pattern))
        if not matches:
            raise ManifestError(f"manifest source matched nothing: {entry['source']}")
        if entry["type"] == "directory_glob" and any(not p.is_dir() for p in matches):
            raise ManifestError(f"directory_glob matched a non-directory: {entry['source']}")
        if entry["type"] in {"file", "metadata"} and (
                len(matches) != 1 or not matches[0].is_file()):
            raise ManifestError(f"file source is not exactly one file: {entry['source']}")
        selected.append((entry, matches))
    return selected


def _assert_no_symlink(path, root):
    """Refuse a managed path if any component at/below CONFIG_ROOT is a symlink."""
    path, root = pathlib.Path(path), pathlib.Path(root)
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ManifestError(f"managed destination escapes config root: {path}") from exc
    current = root
    if current.is_symlink():
        raise ManifestError(f"config root is a symlink: {current}")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError(f"managed destination traverses a symlink: {current}")


def _remove_owned(entry, matches, config_root):
    target = config_root / entry["destination"]
    _assert_no_symlink(target, config_root)
    if entry["type"] == "directory_glob":
        target.mkdir(parents=True, exist_ok=True)
        # Ownership comes from the stable manifest pattern, not only CURRENT
        # source matches. This removes a whole skill deleted in the new release.
        owned_names = sorted(target.glob(pathlib.PurePosixPath(entry["source"]).name))
        for owned in owned_names:
            _assert_no_symlink(owned, config_root)
            if owned.is_dir():
                shutil.rmtree(owned)
            elif owned.exists() or owned.is_symlink():
                owned.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()


def apply_manifest(manifest, package_root, config_root, action):
    if action not in {"install", "update", "uninstall"}:
        raise ManifestError(f"unknown action: {action}")
    package_root = pathlib.Path(package_root).resolve()
    raw_config_root = pathlib.Path(config_root).expanduser().absolute()
    if raw_config_root.is_symlink():
        raise ManifestError(f"config root is a symlink: {raw_config_root}")
    config_root = raw_config_root.resolve()
    if action == "uninstall":
        # Removal is driven by the manifest's stable ownership patterns. It
        # must remain possible after package source files have been damaged or
        # partially deleted; no uninstall effect reads those sources.
        installed = [(entry, []) for entry in manifest["managed"] if entry["install"]]
    else:
        selected = selected_sources(manifest, package_root)
        installed = [(entry, matches) for entry, matches in selected if entry["install"]]
    if action in {"update", "uninstall"}:
        for entry, matches in installed:
            _remove_owned(entry, matches, config_root)
    if action == "uninstall":
        return
    for entry, matches in installed:
        target = config_root / entry["destination"]
        _assert_no_symlink(target, config_root)
        if entry["type"] == "directory_glob":
            target.mkdir(parents=True, exist_ok=True)
            for source in matches:
                destination = target / source.name
                _assert_no_symlink(destination, config_root)
                shutil.copytree(source, destination, dirs_exist_ok=False)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(matches[0], target)
            if entry["mode"] == "executable":
                target.chmod(target.stat().st_mode | 0o111)


def selftest():
    ok = True

    def check(label, condition):
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")
        ok = ok and bool(condition)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        package, config = root / "package", root / "home/.claude"
        (package / "skills/instinct-one").mkdir(parents=True)
        (package / "skills/instinct-one/SKILL.md").write_text("one")
        (package / "hooks").mkdir()
        (package / "hooks/surface-instincts.sh").write_text("hook")
        (package / "rules").mkdir()
        (package / "rules/instincts.md").write_text("rule")
        (package / "README.md").write_text("metadata")
        manifest_data = {"schema_version": 1, "managed": [
            {"source": "skills/instinct-*", "destination": "skills",
             "type": "directory_glob", "mode": "preserve", "ownership": "managed",
             "replace": "replace_matches", "install": True},
            {"source": "hooks/surface-instincts.sh", "destination": "hooks/surface-instincts.sh",
             "type": "file", "mode": "executable", "ownership": "managed",
             "replace": "replace", "install": True},
            {"source": "rules/instincts.md", "destination": "rules/instincts.md",
             "type": "file", "mode": "preserve", "ownership": "managed",
             "replace": "replace", "install": True},
            {"source": "README.md", "destination": "README.md", "type": "metadata",
             "mode": "preserve", "ownership": "package", "replace": "replace",
             "install": False},
        ]}
        manifest_path = package / "package-manifest.json"
        manifest_path.write_text(json.dumps(manifest_data))
        manifest = load_manifest(manifest_path)
        apply_manifest(manifest, package, config, "install")
        check("install copies every managed mapping",
              (config / "skills/instinct-one/SKILL.md").is_file()
              and (config / "hooks/surface-instincts.sh").is_file()
              and (config / "rules/instincts.md").is_file())
        integration_data = {"schema_version": 1, "managed": [
            {"source": "hooks/surface-instincts.sh", "destination": "hooks/surface-instincts.sh",
             "type": "file", "mode": "executable", "ownership": "managed",
             "replace": "replace", "install": True},
            {"source": "rules/instincts.md", "destination": "rules/instincts.md",
             "type": "file", "mode": "preserve", "ownership": "managed",
             "replace": "replace", "install": True},
        ]}
        integration_path = package / "integration-manifest.json"
        integration_path.write_text(json.dumps(integration_data))
        integration = load_manifest(integration_path)
        integration_config = root / "home/.another-agent"
        apply_manifest(integration, package, integration_config, "install")
        check("integration manifest installs only the portable hook and instructions",
              (integration_config / "hooks/surface-instincts.sh").is_file()
              and (integration_config / "rules/instincts.md").is_file()
              and not (integration_config / "skills").exists())
        (config / "skills/instinct-one/obsolete").write_text("old")
        apply_manifest(manifest, package, config, "update")
        check("update reconciles owned directories and removes obsolete files",
              not (config / "skills/instinct-one/obsolete").exists())
        removed_upstream = config / "skills/instinct-removed"
        removed_upstream.mkdir()
        (removed_upstream / "user-data").write_text("obsolete managed component")
        apply_manifest(manifest, package, config, "update")
        check("update removes whole managed components absent upstream",
              not removed_upstream.exists())
        check("executable mode is enforced from the manifest",
              bool((config / "hooks/surface-instincts.sh").stat().st_mode & 0o111))
        corpus = config / "homunculus/instincts/personal/lesson.md"
        corpus.parent.mkdir(parents=True)
        corpus.write_text("private")
        (package / "README.md").unlink()
        try:
            apply_manifest(manifest, package, config, "uninstall")
            gutted_uninstall = True
        except ManifestError:
            gutted_uninstall = False
        check("uninstall works when non-installed package metadata is missing",
              gutted_uninstall
              and not (config / "skills/instinct-one").exists()
              and not (config / "hooks/surface-instincts.sh").exists())
        check("uninstall never touches the personal corpus", corpus.read_text() == "private")
        (package / "README.md").write_text("metadata")
        if not gutted_uninstall:
            apply_manifest(manifest, package, config, "uninstall")

        bad = dict(manifest_data)
        bad["managed"] = [dict(manifest_data["managed"][0], destination="../escape")]
        bad_path = root / "bad.json"
        bad_path.write_text(json.dumps(bad))
        try:
            load_manifest(bad_path)
            rejected = False
        except ManifestError:
            rejected = True
        check("unsafe manifest destinations fail closed", rejected)

        external = root / "external"
        external.mkdir()
        (external / "user-data").write_text("must survive")
        skills = config / "skills"
        if skills.is_dir():
            skills.rmdir()
        skills.symlink_to(external, target_is_directory=True)
        try:
            apply_manifest(manifest, package, config, "update")
            symlink_rejected = False
        except ManifestError:
            symlink_rejected = True
        check("symlinked managed parent is rejected before external effects",
              symlink_rejected and (external / "user-data").read_text() == "must survive")
    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(args):
    if args == ["--selftest"]:
        return selftest()
    if len(args) != 4 or args[0] not in {"install", "update", "uninstall"}:
        print("usage: package_manifest.py install|update|uninstall MANIFEST PACKAGE_ROOT CONFIG_ROOT",
              file=sys.stderr)
        return 2
    try:
        manifest = load_manifest(args[1])
        apply_manifest(manifest, args[2], args[3], args[0])
    except ManifestError as exc:
        print(f"package manifest: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

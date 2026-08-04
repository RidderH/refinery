#!/usr/bin/env python3
"""Safely link Refinery's canonical skills into an agent skills directory."""

import os
import pathlib
import sys
import tempfile


class LinkError(ValueError):
    pass


def discover_skills(package_root):
    package_root = pathlib.Path(package_root).expanduser().resolve()
    skills_root = package_root / "skills"
    if not skills_root.is_dir() or skills_root.is_symlink():
        raise LinkError(f"skills directory is missing or is a symlink: {skills_root}")

    skills = []
    for skill in sorted(skills_root.glob("instinct-*")):
        if skill.is_symlink() or not skill.is_dir():
            raise LinkError(f"skill source must be a real directory: {skill}")
        if not (skill / "SKILL.md").is_file():
            raise LinkError(f"skill source has no SKILL.md: {skill}")
        skills.append(skill.resolve())
    if not skills:
        raise LinkError(f"no Refinery skills found under {skills_root}")
    return skills


def _lexical_link_target(link):
    raw = pathlib.Path(os.readlink(link))
    if not raw.is_absolute():
        raw = link.parent / raw
    return pathlib.Path(os.path.abspath(raw))


def _prepare_target_root(target_root):
    target_root = pathlib.Path(target_root).expanduser().absolute()
    if target_root.is_symlink():
        raise LinkError(f"agent skills directory must not be a symlink: {target_root}")
    if target_root.exists() and not target_root.is_dir():
        raise LinkError(f"agent skills destination is not a directory: {target_root}")
    return target_root


def _classify_target(target, source):
    if target.is_symlink():
        return "owned" if _lexical_link_target(target) == source else "foreign-link"
    if target.exists():
        return "foreign-path"
    return "missing"


def install(package_root, target_root):
    skills = discover_skills(package_root)
    target_root = _prepare_target_root(target_root)

    conflicts = []
    for source in skills:
        target = target_root / source.name
        state = _classify_target(target, source)
        if state not in {"missing", "owned"}:
            conflicts.append(f"{target} ({state})")
    if conflicts:
        raise LinkError(
            "refusing to replace existing agent skill paths:\n" + "\n".join(conflicts)
        )

    target_root.mkdir(parents=True, exist_ok=True)
    created = []
    for source in skills:
        target = target_root / source.name
        if not target.is_symlink():
            target.symlink_to(source, target_is_directory=True)
            created.append(target)
    return created


def uninstall(package_root, target_root):
    skills = discover_skills(package_root)
    target_root = _prepare_target_root(target_root)

    conflicts = []
    for source in skills:
        target = target_root / source.name
        state = _classify_target(target, source)
        if state not in {"missing", "owned"}:
            conflicts.append(f"{target} ({state})")
    if conflicts:
        raise LinkError(
            "refusing to remove agent skill paths not owned by this checkout:\n"
            + "\n".join(conflicts)
        )

    removed = []
    for source in skills:
        target = target_root / source.name
        if target.is_symlink():
            target.unlink()
            removed.append(target)
    return removed


def status(package_root, target_root):
    skills = discover_skills(package_root)
    target_root = _prepare_target_root(target_root)
    return [
        (source.name, _classify_target(target_root / source.name, source))
        for source in skills
    ]


def selftest():
    ok = True

    def check(label, condition):
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")
        ok = ok and bool(condition)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        package = root / "refinery"
        target = root / ".agents/skills"
        for name in ("instinct-analyze", "instinct-format"):
            skill = package / "skills" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")

        created = install(package, target)
        check("install creates one symlink per canonical skill", len(created) == 2)
        check(
            "links point to the canonical package directories",
            all(path.is_symlink() and _lexical_link_target(path).is_dir() for path in created),
        )
        check("install is idempotent", install(package, target) == [])

        foreign = target / "instinct-format"
        foreign.unlink()
        foreign.mkdir()
        try:
            install(package, target)
            rejected_foreign = False
        except LinkError:
            rejected_foreign = True
        check("install refuses an existing non-link skill", rejected_foreign)
        check(
            "preflight prevents a partial rewrite of existing links",
            (target / "instinct-analyze").is_symlink(),
        )

        foreign.rmdir()
        foreign.symlink_to(
            (package / "skills/instinct-format").resolve(), target_is_directory=True
        )
        removed = uninstall(package, target)
        check("uninstall removes only links owned by this checkout", len(removed) == 2)
        check("uninstall is idempotent", uninstall(package, target) == [])

    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(args):
    if args == ["--selftest"]:
        return selftest()
    if len(args) not in {2, 3} or args[0] not in {"install", "uninstall", "status"}:
        print(
            "usage: link_agent_skills.py install|uninstall|status "
            "PACKAGE_ROOT [AGENT_SKILLS_DIR]",
            file=sys.stderr,
        )
        return 2

    action, package_root = args[:2]
    target_root = (
        args[2]
        if len(args) == 3
        else pathlib.Path.home() / ".agents/skills"
    )
    try:
        if action == "install":
            changed = install(package_root, target_root)
            for path in changed:
                print(f"linked {path}")
            if not changed:
                print("all Refinery skills are already linked")
        elif action == "uninstall":
            changed = uninstall(package_root, target_root)
            for path in changed:
                print(f"unlinked {path}")
            if not changed:
                print("no Refinery links were installed")
        else:
            for name, state in status(package_root, target_root):
                print(f"{name}\t{state}")
    except (LinkError, OSError) as exc:
        print(f"agent skill linker: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

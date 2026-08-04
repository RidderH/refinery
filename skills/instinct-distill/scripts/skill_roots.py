#!/usr/bin/env python3
"""Configure canonical skill storage and managed agent discovery links.

The Agent Skills format is portable; discovery directories are host-specific.
Refinery therefore writes each promoted skill once under ``canonical_skills_root``
and creates per-skill symlinks in any configured ``agent_skill_roots``.

Usage:
    python3 skill_roots.py configure shared|codex|claude [CONFIG_PATH]
    python3 skill_roots.py configure-custom CANONICAL_ROOT [AGENT_ROOT ...]
    python3 skill_roots.py setup shared|codex|claude PACKAGE_ROOT [CONFIG_PATH]
    python3 skill_roots.py setup-custom PACKAGE_ROOT CANONICAL_ROOT [AGENT_ROOT ...]
    python3 skill_roots.py resolve [CONFIG_PATH]
    python3 skill_roots.py link SKILL_DIR [CONFIG_PATH]
    python3 skill_roots.py unlink SKILL_DIR [CONFIG_PATH]
    python3 skill_roots.py --selftest
"""

import importlib.util
import json
import os
import pathlib
import sys
import tempfile


SCHEMA_VERSION = 1
PROFILES = {"shared", "codex", "claude"}


class RootError(ValueError):
    pass


def _home_path(home=None):
    return pathlib.Path(home) if home is not None else pathlib.Path.home()


def default_config_path(home=None, env=None):
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME")
    if base:
        return pathlib.Path(base).expanduser().absolute() / "refinery/skill-roots.json"
    return _home_path(home).expanduser().absolute() / ".config/refinery/skill-roots.json"


def profile_config(profile, home=None, codex_home=None):
    if profile not in PROFILES:
        raise RootError(f"unknown profile {profile!r}; choose shared, codex, or claude")
    home = _home_path(home).expanduser().absolute()
    if profile == "shared":
        canonical = home / ".agents/skills"
        agent_roots = [home / ".claude/skills"]
    elif profile == "codex":
        raw_codex = codex_home or os.environ.get("CODEX_HOME") or home / ".codex"
        canonical = pathlib.Path(raw_codex).expanduser().absolute() / "skills"
        agent_roots = []
    else:
        canonical = home / ".claude/skills"
        agent_roots = []
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_skills_root": str(canonical),
        "agent_skill_roots": [str(path) for path in agent_roots],
    }


def _absolute_root(value, label):
    if not isinstance(value, str) or not value:
        raise RootError(f"{label} must be a non-empty absolute path")
    path = pathlib.Path(value).expanduser()
    if not path.is_absolute():
        raise RootError(f"{label} must be absolute: {value!r}")
    return path.absolute()


def _overlap(left, right):
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def validate_config(data):
    if not isinstance(data, dict) or set(data) != {
            "schema_version", "canonical_skills_root", "agent_skill_roots"}:
        raise RootError(
            "config must contain exactly schema_version, canonical_skills_root, "
            "and agent_skill_roots")
    if data["schema_version"] != SCHEMA_VERSION:
        raise RootError(f"unsupported skill-root schema: {data['schema_version']!r}")
    canonical = _absolute_root(data["canonical_skills_root"], "canonical_skills_root")
    raw_agents = data["agent_skill_roots"]
    if not isinstance(raw_agents, list):
        raise RootError("agent_skill_roots must be a list")
    agents = [_absolute_root(value, f"agent_skill_roots[{index}]")
              for index, value in enumerate(raw_agents)]
    if len(set(agents)) != len(agents):
        raise RootError("agent_skill_roots contains duplicates")
    for root in [canonical] + agents:
        if root.is_symlink():
            raise RootError(f"configured skill root must not be a symlink: {root}")
        if root.exists() and not root.is_dir():
            raise RootError(f"configured skill root is not a directory: {root}")
    for root in agents:
        if _overlap(canonical, root):
            raise RootError(f"canonical and agent skill roots overlap: {canonical} / {root}")
    for index, left in enumerate(agents):
        for right in agents[index + 1:]:
            if _overlap(left, right):
                raise RootError(f"agent skill roots overlap: {left} / {right}")
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_skills_root": str(canonical),
        "agent_skill_roots": [str(path) for path in agents],
    }


def _config_file(path=None, home=None, env=None):
    env = os.environ if env is None else env
    selected = path if path is not None else env.get("REFINERY_SKILL_CONFIG")
    return (pathlib.Path(selected).expanduser().absolute() if selected is not None
            else default_config_path(home=home, env=env))


def write_config(data, path=None, home=None, env=None):
    data = validate_config(data)
    target = _config_file(path, home=home, env=env)
    if target.is_symlink():
        raise RootError(f"config path must not be a symlink: {target}")
    if target.exists() and not target.is_file():
        raise RootError(f"config path is not a file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=".skill-roots.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps(data, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        pathlib.Path(temporary).unlink(missing_ok=True)
        raise
    return target


def load_config(path=None, home=None, env=None):
    env = os.environ if env is None else env
    target = _config_file(path, home=home, env=env)
    if target.is_symlink():
        raise RootError(f"config path must not be a symlink: {target}")
    if target.exists():
        try:
            data = json.loads(target.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RootError(f"cannot read skill-root config: {exc}") from exc
    else:
        home_path = _home_path(home).expanduser().absolute()
        data = {
            "schema_version": SCHEMA_VERSION,
            "canonical_skills_root": str(home_path / ".agents/skills"),
            "agent_skill_roots": [],
        }
    if env.get("REFINERY_SKILLS_ROOT"):
        data["canonical_skills_root"] = str(
            pathlib.Path(env["REFINERY_SKILLS_ROOT"]).expanduser().absolute())
    if "REFINERY_AGENT_SKILL_ROOTS" in env:
        raw = env["REFINERY_AGENT_SKILL_ROOTS"]
        data["agent_skill_roots"] = [
            str(pathlib.Path(item).expanduser().absolute())
            for item in raw.split(os.pathsep) if item
        ]
    return validate_config(data)


def _lexical_link_target(link):
    raw = pathlib.Path(os.readlink(link))
    if not raw.is_absolute():
        raw = link.parent / raw
    return pathlib.Path(os.path.abspath(raw))


def _skill_source(skill_dir, config):
    config = validate_config(config)
    source = pathlib.Path(skill_dir).expanduser().absolute()
    canonical = pathlib.Path(config["canonical_skills_root"])
    if source.parent != canonical:
        raise RootError(f"skill must be a direct child of canonical_skills_root: {source}")
    if source.is_symlink() or not source.is_dir():
        raise RootError(f"canonical skill must be a real directory: {source}")
    if not (source / "SKILL.md").is_file():
        raise RootError(f"canonical skill has no SKILL.md: {source}")
    return source, config


def planned_links(skill_dir, config):
    source, config = _skill_source(skill_dir, config)
    records = []
    conflicts = []
    for raw_root in config["agent_skill_roots"]:
        root = pathlib.Path(raw_root)
        target = root / source.name
        if target.is_symlink():
            if _lexical_link_target(target) == source:
                records.append([str(target), str(source), True])
            else:
                conflicts.append(f"{target} (foreign-link)")
        elif target.exists():
            conflicts.append(f"{target} (foreign-path)")
        else:
            records.append([str(target), str(source), False])
    if conflicts:
        raise RootError(
            "refusing to replace existing agent skill paths:\n" + "\n".join(conflicts))
    return records


def link_skill(skill_dir, config):
    records = planned_links(skill_dir, config)
    created = []
    try:
        for link_text, source_text, existed in records:
            if existed:
                continue
            link = pathlib.Path(link_text)
            source = pathlib.Path(source_text)
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(source, target_is_directory=True)
            created.append(link)
    except OSError as exc:
        expected_source = pathlib.Path(skill_dir).expanduser().absolute()
        for link in reversed(created):
            if link.is_symlink() and _lexical_link_target(link) == expected_source:
                link.unlink()
        raise RootError(f"could not create discovery links: {exc}") from exc
    return created


def unlink_skill(skill_dir, config):
    source, config = _skill_source(skill_dir, config)
    removable = []
    conflicts = []
    for raw_root in config["agent_skill_roots"]:
        target = pathlib.Path(raw_root) / source.name
        if target.is_symlink():
            if _lexical_link_target(target) == source:
                removable.append(target)
            else:
                conflicts.append(f"{target} (foreign-link)")
        elif target.exists():
            conflicts.append(f"{target} (foreign-path)")
    if conflicts:
        raise RootError(
            "refusing to remove agent skill paths not owned by Refinery:\n"
            + "\n".join(conflicts))
    for target in removable:
        target.unlink()
    return removable


def _load_linker(package_root):
    path = (pathlib.Path(package_root).expanduser().resolve()
            / "skills/instinct-prune/scripts/link_agent_skills.py")
    if not path.is_file():
        raise RootError(f"Refinery core-skill linker not found: {path}")
    spec = importlib.util.spec_from_file_location("refinery_link_agent_skills", path)
    module = importlib.util.module_from_spec(spec)
    # This executes a helper from the user's checkout. Do not leave __pycache__
    # there: a later manifest-driven install must never copy generated files.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _setup_config(config, package_root, config_target):
    config = validate_config(config)
    config_target = pathlib.Path(config_target).expanduser().absolute()
    # Validate the config destination before any link is created.
    if config_target.is_symlink() or (config_target.exists() and not config_target.is_file()):
        raise RootError(f"invalid config destination: {config_target}")
    linker = _load_linker(package_root)
    roots = [config["canonical_skills_root"]] + config["agent_skill_roots"]
    conflicts = []
    for root in roots:
        for name, state in linker.status(package_root, root):
            if state not in {"missing", "owned"}:
                conflicts.append(f"{pathlib.Path(root) / name} ({state})")
    if conflicts:
        raise RootError(
            "setup found existing agent skill conflicts:\n" + "\n".join(conflicts))
    created = []
    try:
        for root in roots:
            created.extend(linker.install(package_root, root))
        write_config(config, config_target)
    except Exception as exc:
        package_skills = pathlib.Path(package_root).expanduser().resolve() / "skills"
        for link in reversed(created):
            expected = (package_skills / link.name).resolve()
            if link.is_symlink() and _lexical_link_target(link) == expected:
                link.unlink()
        if isinstance(exc, linker.LinkError):
            raise RootError(str(exc)) from exc
        raise
    return config_target, created


def setup(profile, package_root, config_path=None, home=None, codex_home=None):
    config = profile_config(profile, home=home, codex_home=codex_home)
    return _setup_config(config, package_root, _config_file(config_path, home=home))


def setup_custom(package_root, canonical_root, agent_roots=None, config_path=None,
                 home=None):
    config = {
        "schema_version": SCHEMA_VERSION,
        "canonical_skills_root": str(pathlib.Path(canonical_root).expanduser().absolute()),
        "agent_skill_roots": [
            str(pathlib.Path(root).expanduser().absolute()) for root in (agent_roots or [])
        ],
    }
    return _setup_config(config, package_root, _config_file(config_path, home=home))


def selftest():
    ok = True

    def check(label, condition):
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")
        ok = ok and bool(condition)

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        home = root / "home"
        shared = profile_config("shared", home=home)
        check("shared stores once under .agents and adapts Claude",
              shared["canonical_skills_root"] == str(home / ".agents/skills")
              and shared["agent_skill_roots"] == [str(home / ".claude/skills")])
        codex = profile_config("codex", home=home, codex_home=root / "custom-codex")
        check("Codex profile respects CODEX_HOME",
              codex["canonical_skills_root"] == str(root / "custom-codex/skills")
              and codex["agent_skill_roots"] == [])
        claude = profile_config("claude", home=home)
        check("Claude-only profile writes directly to Claude discovery",
              claude["canonical_skills_root"] == str(home / ".claude/skills"))

        config_path = root / "config/skill-roots.json"
        write_config(shared, config_path)
        check("configuration round-trips", load_config(config_path) == shared)
        overridden = load_config(config_path, env={
            "REFINERY_SKILLS_ROOT": str(root / "override"),
            "REFINERY_AGENT_SKILL_ROOTS": str(root / "one") + os.pathsep + str(root / "two"),
        })
        check("environment overrides both configured root classes",
              overridden["canonical_skills_root"] == str(root / "override")
              and overridden["agent_skill_roots"] == [str(root / "one"), str(root / "two")])

        canonical = pathlib.Path(shared["canonical_skills_root"])
        skill = canonical / "learned-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: learned-skill\n---\n")
        created = link_skill(skill, shared)
        target = home / ".claude/skills/learned-skill"
        check("managed discovery link points at canonical bytes",
              created == [target] and target.is_symlink()
              and _lexical_link_target(target) == skill)
        check("managed discovery linking is idempotent", link_skill(skill, shared) == [])

        other = canonical / "other-skill"
        other.mkdir()
        (other / "SKILL.md").write_text("---\nname: other-skill\n---\n")
        foreign = home / ".claude/skills/other-skill"
        foreign.mkdir()
        try:
            link_skill(other, shared)
            rejected_foreign = False
        except RootError:
            rejected_foreign = True
        check("foreign discovery paths fail before replacement",
              rejected_foreign and foreign.is_dir() and not foreign.is_symlink())
        check("unlink removes only an owned link",
              unlink_skill(skill, shared) == [target] and not target.exists())

        two_agents = dict(shared)
        second_agent = root / "second-agent/skills"
        two_agents["agent_skill_roots"] = [
            str(home / ".claude/skills"), str(second_agent)]
        link_skill(skill, two_agents)
        second_target = second_agent / "learned-skill"
        second_target.unlink()
        second_target.mkdir()
        try:
            unlink_skill(skill, two_agents)
            rejected_partial_unlink = False
        except RootError:
            rejected_partial_unlink = True
        check("unlink preflights every root before removing any owned link",
              rejected_partial_unlink and target.is_symlink())
        second_target.rmdir()
        unlink_skill(skill, two_agents)

        package = root / "refinery"
        for name in ("instinct-analyze", "instinct-distill", "instinct-prune"):
            package_skill = package / "skills" / name
            package_skill.mkdir(parents=True)
            (package_skill / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        linker_source = pathlib.Path(__file__).resolve().parents[2] / "instinct-prune/scripts/link_agent_skills.py"
        linker_target = package / "skills/instinct-prune/scripts/link_agent_skills.py"
        linker_target.parent.mkdir(parents=True)
        linker_target.write_bytes(linker_source.read_bytes())

        setup_conflict = home / ".claude/skills/instinct-distill"
        setup_conflict.mkdir(parents=True)
        try:
            setup("shared", package, root / "setup/config.json", home=home)
            rejected_setup_conflict = False
        except RootError:
            rejected_setup_conflict = True
        check("setup preflights every root before creating any links",
              rejected_setup_conflict
              and not (home / ".agents/skills/instinct-analyze").exists())
        setup_conflict.rmdir()

        setup_config, setup_links = setup(
            "shared", package, root / "setup/config.json", home=home)
        check("one setup call links core skills into both supported discovery roots",
              len(setup_links) == 6
              and (home / ".agents/skills/instinct-analyze").is_symlink()
              and (home / ".claude/skills/instinct-analyze").is_symlink())
        check("setup persists the resolved profile",
              load_config(setup_config) == profile_config("shared", home=home))
        check("setup leaves no generated bytecode in the package",
              not list(package.rglob("__pycache__"))
              and not list(package.rglob("*.pyc")))

        custom_root = root / "custom/canonical"
        custom_agents = [root / "custom/codex", root / "custom/claude"]
        custom_config, custom_links = setup_custom(
            package, custom_root, custom_agents, root / "custom/config.json", home=home)
        check("custom setup links every core skill into every configured root",
              len(custom_links) == 9
              and all((skill_root / "instinct-distill").is_symlink()
                      for skill_root in [custom_root] + custom_agents))
        check("custom setup persists its exact roots",
              load_config(custom_config)["agent_skill_roots"]
              == [str(path) for path in custom_agents])

        symlink_config = root / "linked-config.json"
        real_config = root / "real-config.json"
        real_config.write_text("{}")
        symlink_config.symlink_to(real_config)
        try:
            write_config(shared, symlink_config)
            rejected_config_link = False
        except RootError:
            rejected_config_link = True
        check("configuration refuses a symlink destination", rejected_config_link)

    print("SELFTEST PASSED" if ok else "SELFTEST FAILED")
    return 0 if ok else 1


def main(args):
    if args == ["--selftest"]:
        return selftest()
    try:
        if args and args[0] == "configure" and len(args) in {2, 3}:
            config = profile_config(args[1])
            path = write_config(config, args[2] if len(args) == 3 else None)
            print(path)
            return 0
        if args and args[0] == "configure-custom" and len(args) >= 2:
            config = {
                "schema_version": SCHEMA_VERSION,
                "canonical_skills_root": str(pathlib.Path(args[1]).expanduser().absolute()),
                "agent_skill_roots": [
                    str(pathlib.Path(value).expanduser().absolute()) for value in args[2:]
                ],
            }
            print(write_config(config))
            return 0
        if args and args[0] == "setup" and len(args) in {3, 4}:
            path, links = setup(args[1], args[2], args[3] if len(args) == 4 else None)
            print(f"configured {path}")
            for link in links:
                print(f"linked {link}")
            return 0
        if args and args[0] == "setup-custom" and len(args) >= 3:
            path, links = setup_custom(args[1], args[2], args[3:])
            print(f"configured {path}")
            for link in links:
                print(f"linked {link}")
            return 0
        if args and args[0] == "resolve" and len(args) in {1, 2}:
            print(json.dumps(load_config(args[1] if len(args) == 2 else None), indent=2))
            return 0
        if args and args[0] in {"link", "unlink"} and len(args) in {2, 3}:
            config = load_config(args[2] if len(args) == 3 else None)
            changed = (link_skill(args[1], config) if args[0] == "link"
                       else unlink_skill(args[1], config))
            for path in changed:
                print(("linked " if args[0] == "link" else "unlinked ") + str(path))
            return 0
    except (RootError, OSError) as exc:
        print(f"Refinery skill roots: {exc}", file=sys.stderr)
        return 2
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

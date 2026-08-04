#!/usr/bin/env python3
"""Public-CLI regressions for the optional skill validation tooling."""

import pathlib
import subprocess
import sys
import tempfile
import unittest


PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1]
VALIDATOR = PACKAGE_ROOT / "validate_skills.py"
SOURCE_ROOT = (
    PACKAGE_ROOT
    if (PACKAGE_ROOT / "skills").is_dir()
    else PACKAGE_ROOT.parent.parent
)


class ValidateSkillsCliTests(unittest.TestCase):
    def run_validator(self, package_root):
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(package_root)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_valid_skill_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-valid"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-valid\n"
                "description: A complete valid skill fixture.\n"
                "disable-model-invocation: true\n"
                "---\n\n"
                "# Valid\n"
            )

            result = self.run_validator(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("VALIDATION PASSED: 1 Refinery skills", result.stdout)

    def test_shipped_refinery_skills_pass(self):
        result = self.run_validator(SOURCE_ROOT)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VALIDATION PASSED: 4 Refinery skills", result.stdout)

    def test_malformed_yaml_frontmatter_fails_with_the_skill_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-broken"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-broken\n"
                "description: \"unterminated\n"
                "---\n\n"
                "# Broken\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("skills/instinct-broken/SKILL.md", result.stderr)
            self.assertIn("invalid YAML", result.stderr)

    def test_duplicate_frontmatter_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-duplicate"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-duplicate\n"
                "name: overwritten\n"
                "description: Duplicate keys are ambiguous.\n"
                "---\n\n"
                "# Duplicate\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate key: name", result.stderr)

    def test_yaml_merge_keys_receive_an_explicit_unsupported_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-merge"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-merge\n"
                "description: Merge keys are outside the skill contract.\n"
                "metadata: &defaults\n"
                "  x: 1\n"
                "compatibility:\n"
                "  <<: *defaults\n"
                "  x: 2\n"
                "---\n\n"
                "# Merge key\n"
            )

            result = self.run_validator(root)

            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "merge keys are not supported in skill frontmatter",
                result.stderr,
            )

    def test_required_skill_metadata_cannot_be_omitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-incomplete"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-incomplete\n"
                "---\n\n"
                "# Incomplete\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing required key: description", result.stderr)

    def test_declared_name_must_match_the_skill_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-actual"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-other\n"
                "description: The name points at the wrong skill.\n"
                "---\n\n"
                "# Mismatch\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "name must match directory 'instinct-actual'", result.stderr
            )

    def test_description_must_remain_a_nonempty_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-typed"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-typed\n"
                "description: yes\n"
                "---\n\n"
                "# Implicit YAML type\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("description must be a non-empty string", result.stderr)

    def test_unknown_frontmatter_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-typo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-typo\n"
                "description: A valid description.\n"
                "descripton: This misspelling must not be ignored.\n"
                "---\n\n"
                "# Typo\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported frontmatter key: descripton", result.stderr)

    def test_non_string_keys_do_not_crash_or_abort_sibling_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            mixed = root / "skills/instinct-a-mixed"
            mixed.mkdir(parents=True)
            (mixed / "SKILL.md").write_text(
                "---\n"
                "name: instinct-a-mixed\n"
                "description: Mixed key types must fail cleanly.\n"
                "1: numeric key\n"
                "zzz: string key\n"
                "---\n\n"
                "# Mixed keys\n"
            )
            sibling = root / "skills/instinct-z-sibling"
            sibling.mkdir()
            (sibling / "SKILL.md").write_text(
                "---\n"
                "name: instinct-z-sibling\n"
                "---\n\n"
                "# Missing description\n"
            )

            result = self.run_validator(root)

            self.assertEqual(result.returncode, 1)
            self.assertIn("frontmatter keys must be strings", result.stderr)
            self.assertIn("missing required key: description", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_invocation_flags_must_be_booleans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-manual"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-manual\n"
                "description: A human starts this workflow.\n"
                "disable-model-invocation: \"true\"\n"
                "---\n\n"
                "# Manual workflow\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("disable-model-invocation must be a boolean", result.stderr)

    def test_missing_test_dependency_has_an_actionable_error(self):
        result = subprocess.run(
            [sys.executable, "-S", str(VALIDATOR), str(SOURCE_ROOT)],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("install requirements-test.txt", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_shipped_parser_runs_without_site_packages(self):
        parser = (
            SOURCE_ROOT
            / "skills/instinct-format/scripts/instinct_record.py"
        )

        result = subprocess.run(
            [sys.executable, "-S", str(parser), "--selftest"],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("SELFTEST PASSED", result.stdout)

    def test_every_discovered_skill_directory_requires_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            valid = root / "skills/instinct-valid"
            valid.mkdir(parents=True)
            (valid / "SKILL.md").write_text(
                "---\n"
                "name: instinct-valid\n"
                "description: A complete control fixture.\n"
                "---\n\n"
                "# Valid\n"
            )
            (root / "skills/instinct-missing").mkdir()

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("skills/instinct-missing/SKILL.md: missing", result.stderr)

    def test_skill_name_must_be_lowercase_kebab_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            skill = root / "skills/instinct-Bad"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\n"
                "name: instinct-Bad\n"
                "description: Matching the directory is not sufficient.\n"
                "---\n\n"
                "# Invalid name\n"
            )

            result = self.run_validator(root)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("name must be lowercase kebab-case", result.stderr)

    def test_malformed_yaml_classes_fail_without_tracebacks(self):
        cases = {
            "multiple documents": (
                b"---\nname: instinct-invalid\n"
                b"description: Multiple documents are unsupported.\n"
                b"--- # starts another YAML document\nother: value\n---\n",
                "invalid YAML",
            ),
            "unsafe Python tag": (
                b"---\nname: instinct-invalid\n"
                b"description: Unsafe tags are unsupported.\n"
                b"metadata: !!python/object/apply:os.system [echo]\n---\n",
                "invalid YAML",
            ),
            "non-UTF8 bytes": (
                b"---\nname: instinct-invalid\n"
                b"description: invalid byte follows: \xff\n---\n",
                "invalid frontmatter",
            ),
            "complex mapping key": (
                b"---\nname: instinct-invalid\n"
                b"description: Complex keys are unsupported.\n"
                b"? [one, two]\n: value\n---\n",
                "mapping keys must be scalar values",
            ),
            "unterminated frontmatter": (
                b"---\nname: instinct-invalid\n"
                b"description: The closing delimiter is absent.\n",
                "missing closing frontmatter delimiter",
            ),
            "trailing-space delimiter": (
                b"---\nname: instinct-invalid\n"
                b"description: The delimiter must be exact.\n---  \n",
                "missing closing frontmatter delimiter",
            ),
            "implicitly typed key": (
                b"---\nname: instinct-invalid\n"
                b"description: YAML resolves this key to false.\n"
                b"no: value\n---\n",
                "frontmatter keys must be strings",
            ),
        }
        for label, (content, diagnostic) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                skill = root / "skills/instinct-invalid"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_bytes(content)

                result = self.run_validator(root)

                self.assertEqual(result.returncode, 1)
                self.assertIn(diagnostic, result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_empty_skill_set_has_an_actionable_usage_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_validator(pathlib.Path(tmp))

            self.assertEqual(result.returncode, 2)
            self.assertIn("no Refinery skills found", result.stderr)


if __name__ == "__main__":
    unittest.main()

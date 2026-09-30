"""Contract tests for the distributable Skill package.

These tests deliberately check durable invariants rather than prose wording. The
runtime is allowed to evolve as long as activation, references, safety rules,
portability, and the public CLI remain intact.
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SKILL_MD = SKILL / "SKILL.md"
MI = SKILL / "scripts" / "mi.py"
OPENAI_YAML = SKILL / "agents" / "openai.yaml"


def read(p):
    return p.read_text(encoding="utf-8")


class TestFrontmatter(unittest.TestCase):
    def test_frontmatter_present_and_named(self):
        text = read(SKILL_MD)
        self.assertTrue(text.startswith("---\n"), "SKILL.md must open with YAML frontmatter")
        fm = text.split("---\n")[1]
        self.assertRegex(fm, r"(?m)^name:\s*mastery-interrogator\s*$")
        self.assertRegex(fm, r"(?m)^description:\s*.+")
        keys = re.findall(r"(?m)^([A-Za-z_][\w-]*):", fm)
        self.assertEqual(keys, ["name", "description"], "frontmatter should only expose name + description")

    def test_skill_md_stays_a_control_plane_not_an_encyclopedia(self):
        self.assertLess(len(read(SKILL_MD)), 24000,
                        "SKILL.md must stay lean; depth belongs in references/")

    def test_description_has_activation_signals(self):
        fm = read(SKILL_MD).split("---\n")[1]
        desc = re.search(r"description:\s*(.+)", fm).group(1)
        for token in ("苏格拉底", "软件项目", "论文", "数学建模", "追问"):
            self.assertIn(token, desc)


class TestPackageLayout(unittest.TestCase):
    def test_required_ui_metadata_exists(self):
        self.assertTrue(OPENAI_YAML.exists(), "agents/openai.yaml is required")
        text = read(OPENAI_YAML)
        self.assertIn("interface:", text)
        self.assertIn("display_name:", text)
        self.assertIn("short_description:", text)

    def test_agents_directory_contains_ui_metadata_not_role_prompts(self):
        role_prompts = sorted((SKILL / "agents").glob("*.md"))
        self.assertEqual(role_prompts, [],
                         "role instructions belong in references/; agents/ is reserved for UI metadata")

    def test_no_hardcoded_install_location(self):
        banned = "/var/minis/skills/mastery-interrogator"
        for p in [SKILL_MD] + sorted((SKILL / "references").glob("*.md")):
            self.assertNotIn(banned, read(p), "%s hard-codes an installation path" % p.relative_to(SKILL))

    def test_no_stray_files(self):
        for p in SKILL.rglob("*"):
            self.assertNotIn(p.name, (".DS_Store",), "stray file: %s" % p)


class TestReferences(unittest.TestCase):
    def test_every_skill_reference_exists(self):
        text = read(SKILL_MD)
        refs = set(re.findall(r"`((?:references|scripts|templates|examples)/[\w\-./]+\.\w+)`", text))
        self.assertTrue(refs, "SKILL.md should point at its resources")
        for r in refs:
            self.assertTrue((SKILL / r).exists(), "missing referenced file: %s" % r)

    def test_reference_files_are_substantive(self):
        for p in sorted((SKILL / "references").glob("*.md")):
            self.assertGreater(len(read(p)), 900, "%s looks like a stub" % p.name)

    def test_reference_cross_links_resolve(self):
        refs = SKILL / "references"
        for p in sorted(refs.glob("*.md")):
            for target in re.findall(r"`([\w\-]+\.md)`", read(p)):
                if target == p.name:
                    continue
                if target == "SKILL.md":
                    dest = SKILL / target
                else:
                    dest = refs / target
                self.assertTrue(dest.exists(), "%s links to missing %s" % (p.name, target))


class TestNonNegotiableRules(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILL_MD)

    def test_dialogue_driven_growth_is_the_top_rule(self):
        self.assertIn("树必须由对话长出来", self.text)
        self.assertIn("Seed Tree", self.text)
        self.assertIn("discovered_from", self.text)

    def test_one_primary_question_per_turn_including_exam(self):
        self.assertRegex(self.text, r"每轮只问一个主要问题")
        self.assertIn("考试也按题逐轮", self.text)

    def test_idk_requires_prerequisite_first(self):
        self.assertIn("用户说“不知道”时，不先给答案", self.text)
        self.assertIn("最小充分前置", self.text)

    def test_return_stack_and_exact_reprobe_are_required(self):
        self.assertIn("return_stack", self.text)
        self.assertIn("--reprobe-of", self.text)
        self.assertIn("original_question_id", self.text)

    def test_no_self_report_mastery_question(self):
        self.assertIn("懂了吗", self.text)

    def test_mastery_needs_two_unprompted_novel_transfers(self):
        self.assertIn("transfer:pass", self.text)
        self.assertIn("两次", self.text)
        self.assertIn("新颖", self.text)
        self.assertIn("无提示", self.text)

    def test_state_machine_is_authoritative(self):
        for token in ("answer", "next", "validate", "doctor", "mi.py"):
            self.assertIn(token, self.text)


class TestScriptContract(unittest.TestCase):
    def test_script_runs_and_has_all_public_commands(self):
        p = subprocess.run([sys.executable, str(MI), "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        for cmd in ("init", "ask", "answer", "teach", "drill", "return", "next", "where",
                    "render", "status", "resume", "validate", "doctor", "audit", "challenge",
                    "review", "skip", "source", "log", "export", "maintain", "projects",
                    "refresh", "node", "edge"):
            self.assertIn(cmd, p.stdout, "missing command: %s" % cmd)

    def test_read_commands_fail_as_json_not_traceback(self):
        for cmd in ("next", "validate", "doctor", "status", "render", "where", "projects"):
            p = subprocess.run([sys.executable, str(MI), "--home", "/nonexistent-home", cmd],
                               capture_output=True, text=True)
            json.loads(p.stdout)
            self.assertNotIn("Traceback", p.stderr)

    def test_seed_templates_are_valid_json(self):
        for p in sorted((SKILL / "templates" / "workspace").glob("seed-*.json")):
            data = json.loads(read(p))
            self.assertIn("note", data, "%s must declare that it is only a hypothesis" % p.name)
            self.assertGreaterEqual(len(data["nodes"]), 4)


if __name__ == "__main__":
    unittest.main()

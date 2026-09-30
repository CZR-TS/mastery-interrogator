"""State follows the project: <project>/.mastery/<name>/.

These tests pin the resolution order, because getting it wrong means either
writing into a random directory or silently ignoring the user's project.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mi_harness import MI  # noqa: E402


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mi-root-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # a clean environment: the legacy MASTERY_HOME must not leak in
        self.env = {k: v for k, v in os.environ.items()
                    if k not in ("MASTERY_HOME", "MASTERY_STATE_DIR", "MASTERY_ROOT")}

    def mi(self, *args, cwd=None, env=None, expect=0):
        e = dict(self.env)
        e.update(env or {})
        p = subprocess.run([sys.executable, str(MI)] + [str(a) for a in args],
                           capture_output=True, text=True, cwd=str(cwd or self.tmp),
                           env=e, timeout=120)
        try:
            data = json.loads(p.stdout)
        except Exception:
            data = {"raw": p.stdout, "err": p.stderr}
        if expect is not None:
            self.assertEqual(p.returncode, expect,
                             "cmd %r -> exit %s\n%s\n%s" % (args, p.returncode, p.stdout[:800], p.stderr[:400]))
        return data

    def project(self, name="proj", marker="pyproject.toml"):
        d = self.tmp / name
        (d / "src" / "deep").mkdir(parents=True)
        if marker:
            (d / marker).write_text("", encoding="utf-8")
        return d


class TestResolution(WorkspaceCase):
    def test_root_defaults_to_project_local_mastery(self):
        proj = self.project()
        d = self.mi("init", "--mode", "project", "--seed", "project", cwd=proj)
        self.assertEqual(d["root"], str(proj))
        self.assertEqual(d["dir"], str(proj / ".mastery" / "proj"))
        self.assertTrue((proj / ".mastery" / "proj" / "tree.json").exists())
        # and the slug defaulted to the directory name
        self.assertEqual(d["project"], "proj")

    def test_relative_root_is_honoured(self):
        proj = self.project()
        d = self.mi("--root", str(proj), "init", "--seed", "none", "--project", "x", cwd=self.tmp)
        self.assertTrue((proj / ".mastery" / "x" / "tree.json").exists())

    def test_commands_find_the_workspace_from_a_subdirectory(self):
        proj = self.project()
        self.mi("init", "--seed", "project", cwd=proj)
        d = self.mi("status", cwd=proj / "src" / "deep")
        self.assertTrue(d["ok"], d)

    def test_explicit_root_works_from_anywhere(self):
        proj = self.project()
        self.mi("init", "--seed", "project", cwd=proj)
        d = self.mi("--root", str(proj), "validate")
        self.assertTrue(d["ok"], d)

    def test_no_workspace_is_an_error_not_a_global_fallback(self):
        empty = self.tmp / "empty"
        empty.mkdir()
        d = self.mi("status", cwd=empty, expect=2)
        self.assertFalse(d["ok"])
        self.assertIn("no workspace found", d["error"])
        self.assertIn("--root", d["error"])

    def test_state_dir_overrides_the_project(self):
        proj = self.project()
        shared = self.tmp / "shared"
        self.mi("init", "--seed", "project", cwd=proj)
        d = self.mi("--state-dir", str(shared), "init", "--project", "other",
                     "--seed", "none")
        self.assertTrue((shared / "other" / "tree.json").exists())
        self.assertFalse((proj / ".mastery" / "other").exists())

    def test_mastery_state_dir_env_var(self):
        proj = self.project()
        shared = self.tmp / "shared2"
        self.mi("init", "--seed", "none", "--project", "via-env",
                 env={"MASTERY_STATE_DIR": str(shared)})
        self.assertTrue((shared / "via-env" / "tree.json").exists())

    def test_legacy_mastery_home_is_ignored_and_reported(self):
        proj = self.project()
        legacy = self.tmp / "legacy"
        legacy.mkdir()
        d = self.mi("init", "--seed", "none", cwd=proj,
                     env={"MASTERY_HOME": str(legacy)})
        self.assertIn(".mastery", d["dir"])
        self.assertIn("MASTERY_HOME", d["display"], "the retired variable must be reported, not silently ignored")
        self.assertFalse((legacy / "proj").exists())

    def test_mastery_root_env_var(self):
        proj = self.project()
        d = self.mi("init", "--seed", "none", cwd=self.tmp,
                     env={"MASTERY_ROOT": str(proj)})
        self.assertTrue((proj / ".mastery").is_dir())


class TestGitignore(WorkspaceCase):
    def test_hint_when_no_gitignore(self):
        proj = self.project()
        d = self.mi("init", "--seed", "none", cwd=proj)
        self.assertIn("gitignore", d["display"])

    def test_flag_appends_the_entry_once(self):
        proj = self.project()
        (proj / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
        self.mi("init", "--seed", "none", cwd=proj, )
        self.mi("init", "--seed", "none", cwd=proj, expect=2)  # already exists
        self.mi("init", "--force", "--gitignore", "--seed", "none", cwd=proj)
        body = (proj / ".gitignore").read_text(encoding="utf-8")
        self.assertEqual(body.count(".mastery/"), 1)
        self.assertIn("__pycache__/", body)

    def test_no_hint_when_already_ignored(self):
        proj = self.project()
        (proj / ".gitignore").write_text(".mastery/\n", encoding="utf-8")
        d = self.mi("init", "--seed", "none", cwd=proj)
        self.assertNotIn("建议加进 .gitignore", d["display"])

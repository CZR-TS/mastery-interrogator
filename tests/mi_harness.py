"""Fast test harness for scripts/mi.py.

Most tests call ``main()`` in-process so the suite is cheap enough to run on every
edit. Tests whose purpose is process restart / crash semantics can call
``mi_subprocess`` explicitly.
"""

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
MI = SKILL / "scripts" / "mi.py"

_spec = importlib.util.spec_from_file_location("mastery_interrogator_mi", MI)
MI_MODULE = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(MI_MODULE)


class MICase(unittest.TestCase):
    """Base class: a fresh explicit state directory per test."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="mi-test-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.project = "t"

    def _argv(self, args, project=None, home=None):
        argv = ["--home", str(home or self.home)]
        if project:
            argv += ["--project", project]
        argv += [str(a) for a in args]
        return argv

    @staticmethod
    def _decode(stdout, stderr, rc):
        try:
            data = json.loads(stdout)
        except Exception:
            data = {"ok": False, "raw_stdout": stdout, "raw_stderr": stderr}
        data["_rc"] = rc
        return data

    def mi(self, *args, project=None, expect=0, home=None):
        """Run the CLI contract in-process for speed."""
        argv = self._argv(args, project=project, home=home)
        out, err = io.StringIO(), io.StringIO()
        rc = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                MI_MODULE.main(argv)
            except SystemExit as exc:
                rc = int(exc.code or 0)
        stdout, stderr = out.getvalue(), err.getvalue()
        data = self._decode(stdout, stderr, rc)
        if expect is not None:
            self.assertEqual(
                rc, expect,
                "command %r expected exit %s got %s\n%s\n%s"
                % (args, expect, rc, stdout[:2000], stderr[:800]))
        return data

    def mi_subprocess(self, *args, project=None, expect=0, home=None, env=None):
        """Run a real fresh Python process for restart/crash integration tests."""
        cmd = [sys.executable, str(MI)] + self._argv(args, project=project, home=home)
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, env=env)
        data = self._decode(p.stdout, p.stderr, p.returncode)
        if expect is not None:
            self.assertEqual(
                p.returncode, expect,
                "command %r expected exit %s got %s\n%s\n%s"
                % (args, expect, p.returncode, p.stdout[:2000], p.stderr[:800]))
        return data

    def ok(self, *args, **kw):
        kw.setdefault("project", self.project)
        d = self.mi(*args, **kw)
        self.assertTrue(d.get("ok"), d)
        return d

    def blocked(self, *args, **kw):
        kw.setdefault("project", self.project)
        d = self.mi(*args, expect=3, **kw)
        self.assertFalse(d.get("ok"))
        self.assertTrue(d.get("violations"), d)
        return d

    def init(self, mode="project", seed="project", objective="弄懂这个对象", project=None):
        self.project = project or self.project
        return self.ok("init", "--project", self.project, "--mode", mode,
                       "--seed", seed, "--objective", objective)

    # -- helpers used by several cases
    def node(self, nid):
        tree = json.loads((self.home / self.project / "tree.json").read_text(encoding="utf-8"))
        for k, v in tree["nodes"].items():
            if k == nid or v["name"] == nid:
                return v
        return None

    def state(self):
        return json.loads((self.home / self.project / "state.json").read_text(encoding="utf-8"))

    def nodes(self):
        return json.loads((self.home / self.project / "tree.json").read_text(encoding="utf-8"))["nodes"]

    def history(self):
        p = self.home / self.project / "history.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]

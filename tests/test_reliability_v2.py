"""Reliability regressions added in v2.

These tests cover failure modes that are easy to miss in happy-path tutoring:
crash recovery, concurrent writers, schema migration, hint-attempt leakage,
existing-node drills, stale project facts, and user-directed skips.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mi_harness import MICase, MI  # noqa: E402


class TestCrashSafety(MICase):
    def test_crashed_multifile_command_is_rolled_back_on_next_open(self):
        self.init(seed="none", objective="original")
        ws = self.home / self.project
        code = r'''
import importlib.util, os, pathlib
mi = pathlib.Path(os.environ["MI_PATH"])
spec = importlib.util.spec_from_file_location("mi_crash", mi)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
st = mod.Store(os.environ["MI_HOME"], os.environ["MI_PROJECT"])
state = st.state(); state["objective"] = "BROKEN"; st.put_state(state)
st.event("crash_probe", note="this append must roll back too")
os._exit(91)
'''
        env = os.environ.copy()
        env.update({"MI_PATH": str(MI), "MI_HOME": str(self.home), "MI_PROJECT": self.project})
        p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 91)
        self.assertTrue((ws / ".mi-txn" / "manifest.json").exists())
        # The interrupted write reached disk; recovery must restore it, not merely
        # ignore the journal.
        self.assertEqual(json.loads((ws / "state.json").read_text(encoding="utf-8"))["objective"], "BROKEN")

        d = self.mi_subprocess("status", project=self.project)
        self.assertTrue(d.get("ok"), d)
        self.assertEqual(self.state()["objective"], "original")
        self.assertFalse((ws / ".mi-txn").exists())
        self.assertFalse(any(h.get("event") == "crash_probe" for h in self.history()))

    def test_concurrent_writer_is_rejected_instead_of_racing(self):
        self.init(seed="none")
        code = r'''
import importlib.util, os, pathlib, time
mi = pathlib.Path(os.environ["MI_PATH"])
spec = importlib.util.spec_from_file_location("mi_hold", mi)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
st = mod.Store(os.environ["MI_HOME"], os.environ["MI_PROJECT"])
print("LOCKED", flush=True)
time.sleep(0.8)
st.release_lock()
'''
        env = os.environ.copy()
        env.update({"MI_PATH": str(MI), "MI_HOME": str(self.home), "MI_PROJECT": self.project})
        holder = subprocess.Popen([sys.executable, "-c", code], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: holder.poll() is None and holder.kill())
        self.assertEqual(holder.stdout.readline().strip(), "LOCKED")

        env2 = os.environ.copy(); env2["MI_LOCK_TIMEOUT"] = "0.1"
        d = self.mi_subprocess("status", project=self.project, expect=2, env=env2)
        self.assertIn("workspace is busy", d.get("error", ""))
        holder.wait(timeout=3)
        if holder.stdout:
            holder.stdout.close()
        if holder.stderr:
            holder.stderr.close()
        self.assertTrue(self.mi_subprocess("status", project=self.project).get("ok"))


class TestSchemaAndAttempts(MICase):
    def test_v1_workspace_migrates_to_schema2(self):
        self.init(seed="none")
        ws = self.home / self.project
        (ws / "meta.json").unlink()
        for name in ("state.json", "tree.json"):
            p = ws / name
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("workspace_schema", None)
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        d = self.mi_subprocess("status", project=self.project)
        self.assertTrue(d.get("ok"), d)
        meta = json.loads((ws / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["workspace_schema"], 2)
        self.assertEqual(self.state()["workspace_schema"], 2)


    def test_v1_manual_discovered_nodes_and_unanchored_evidence_migrate_safely(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        ws = self.home / self.project
        # Emulate a v1 workspace: direct nodes used to be labeled discovered,
        # while answer records could lack a question id.
        (ws / "meta.json").unlink()
        tree = json.loads((ws / "tree.json").read_text(encoding="utf-8"))
        tree.pop("workspace_schema", None)
        tree["nodes"]["A"]["origin"] = "discovered"
        tree["nodes"]["A"]["discovered_from"] = None
        (ws / "tree.json").write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        state = json.loads((ws / "state.json").read_text(encoding="utf-8"))
        state.pop("workspace_schema", None)
        (ws / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        (ws / "evidence.json").write_text(json.dumps({"A": [{"kind": "why", "result": "pass"}]}), encoding="utf-8")

        self.assertTrue(self.mi_subprocess("status", project=self.project).get("ok"))
        self.assertEqual(self.node("A")["origin"], "manual")
        evidence = json.loads((ws / "evidence.json").read_text(encoding="utf-8"))
        self.assertTrue(evidence["A"][0]["orphan"])

    def test_new_question_resets_old_hint_chain(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        self.ok("ask", "--node", "A", "--text", "第一问？")
        self.ok("teach", "--node", "A", "--level", "1", "--text", "提示一")
        self.ok("answer", "--text", "还是不知道", "--idk")
        self.assertEqual(self.state()["hint_chain"]["levels"], [1])

        self.ok("ask", "--node", "A", "--text", "换一个独立问题？")
        self.assertEqual(self.state()["hint_chain"], {})
        self.blocked("teach", "--node", "A", "--level", "2", "--text", "不能继承旧提示")

    def test_skip_consumes_outstanding_question_on_that_node(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        self.ok("ask", "--node", "A", "--text", "这个问题？")
        self.ok("skip", "--node", "A", "--reason", "用户暂时不想学")
        self.assertIsNone(self.state().get("pending_question"))
        self.assertNotEqual(self.ok("next")["action"], "wait")


class TestGraphAndSources(MICase):
    def test_existing_prerequisite_does_not_get_reparented(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        self.ok("node", "add", "--id", "B", "--name", "B")
        self.ok("node", "add", "--id", "B-child", "--name", "B-child", "--parent", "B")
        self.ok("ask", "--node", "A", "--text", "A 为什么依赖 B？")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "A", "--prereq-id", "B", "--reason", "B 是回答 A 的前置")
        self.assertEqual(self.node("B")["parent"], "root")
        self.assertEqual(self.node("B-child")["parent"], "B")
        edges = json.loads((self.home / self.project / "edges.json").read_text(encoding="utf-8"))["edges"]
        self.assertTrue(any(e["from"] == "A" and e["to"] == "B" and e["type"] == "depends_on" for e in edges))

    def test_source_hash_detects_same_size_same_mtime_change(self):
        root = Path(tempfile.mkdtemp(prefix="mi-object-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        src = root / "fact.txt"
        src.write_text("AAAA", encoding="utf-8")
        st0 = src.stat()

        self.mi("--root", str(root), "init", "--project", self.project,
                "--mode", "project", "--seed", "none", project=None)
        self.ok("source", "add", "--ref", "fact.txt", "--kind", "project_fact")
        self.assertEqual(self.ok("doctor")["warnings"], [])

        src.write_text("BBBB", encoding="utf-8")  # same size
        os.utime(src, ns=(st0.st_atime_ns, st0.st_mtime_ns))  # same timestamp too
        d = self.ok("doctor")
        self.assertTrue(any("source changed" in w for w in d["warnings"]), d)


class TestTextInput(MICase):
    def test_multiline_text_file_avoids_shell_quoting(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        q = self.home / "question.txt"
        q.write_text("解释这段现象：\n为什么 A 会影响 B？", encoding="utf-8")
        d = self.ok("ask", "--node", "A", "--text-file", str(q), "--kind", "why")
        self.assertEqual(d["kind"], "why")
        self.assertIn("为什么 A 会影响 B", self.state()["pending_question"]["text"])


class TestLogArchives(MICase):
    def test_rotated_question_archive_keeps_evidence_anchor_valid(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        q = self.ok("ask", "--node", "A", "--text", "为什么 A？", "--kind", "why")
        self.ok("answer", "--text", "因为机制如此", "--evidence", "why:pass")
        ws = self.home / self.project
        qp = ws / "questions.jsonl"
        base = qp.read_text(encoding="utf-8").splitlines()
        # Force the anchored question into the archived prefix while keeping
        # syntactically valid filler records.
        filler = [json.dumps({"type": "note", "turn": i, "text": "x"}, ensure_ascii=False)
                  for i in range(4100)]
        qp.write_text("\n".join(base + filler) + "\n", encoding="utf-8")
        self.ok("maintain")
        d = self.mi("validate", project=self.project, expect=0)
        self.assertEqual(d["violations"], [], d)
        archives = sorted((ws / "archive").glob("questions-*.jsonl"))
        self.assertTrue(archives)
        self.assertIn(q["question_id"], archives[0].read_text(encoding="utf-8"))

    def test_archive_number_never_overwrites_a_surviving_gap(self):
        self.init(seed="none")
        ws = self.home / self.project
        arch = ws / "archive"
        arch.mkdir(exist_ok=True)
        (arch / "questions-001.jsonl").write_text("{}\n", encoding="utf-8")
        sentinel = arch / "questions-003.jsonl"
        sentinel.write_text('{"sentinel":true}\n', encoding="utf-8")
        qp = ws / "questions.jsonl"
        qp.write_text("\n".join(json.dumps({"type": "note", "turn": i}) for i in range(4100)) + "\n", encoding="utf-8")
        self.ok("maintain")
        self.assertEqual(sentinel.read_text(encoding="utf-8"), '{"sentinel":true}\n')
        self.assertTrue((arch / "questions-004.jsonl").exists())

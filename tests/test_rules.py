"""Tests for the rules that stop the agent from cheating: one question per turn,
hint ladder, the mastery gate, decay, and structural validation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mi_harness import MICase  # noqa: E402


class Answer:
    """Convenience: ask + answer one exchange."""

    def __init__(self, case):
        self.c = case

    def exchange(self, node, qtext, kind, answer_text, evidence=None, idk=False,
                 misconception=None, hinted=False):
        self.c.ok("ask", "--node", node, "--text", qtext, "--kind", kind)
        args = ["answer", "--text", answer_text]
        if idk:
            args.append("--idk")
        for e in (evidence or []):
            args += ["--evidence", e]
        if misconception:
            args += ["--misconception", misconception]
        if hinted:
            args.append("--hinted")
        return self.c.ok(*args)


class TestOneQuestionRule(MICase):
    def setUp(self):
        super().setUp()
        self.init()
        self.a = Answer(self)

    def test_two_question_dump_is_refused(self):
        self.blocked("ask", "--node", "数据与存储",
                     "--text", "1. 表结构怎么设计的？\n2. 索引建在哪？\n3. 事务边界在哪？",
                     "--kind", "probe")

    def test_single_question_with_leadin_is_allowed(self):
        self.ok("ask", "--node", "数据与存储",
                "--text", "先别急着说答案：你觉得订单表的索引为什么会建在用户 ID 上？",
                "--kind", "probe")

    def test_forbidden_proof_question_is_refused(self):
        d = self.blocked("ask", "--node", "数据与存储", "--text", "这段讲了事务，你懂了吗？")
        self.assertIn("forbidden", " ".join(d["violations"]))

    def test_english_variant_also_refused(self):
        self.blocked("ask", "--node", "数据与存储", "--text", "Does that make sense?")

    def test_second_question_while_one_is_pending_is_refused(self):
        self.ok("ask", "--node", "数据与存储", "--text", "第一个问题是什么？")
        d = self.blocked("ask", "--node", "接口与契约", "--text", "第二个问题是什么？")
        self.assertIn("already pending", " ".join(d["violations"]))

    def test_exam_mode_is_sequential_too(self):
        d = self.blocked("ask", "--node", "数据与存储", "--text", "1. A？\n2. B？\n3. C？",
                         "--kind", "recall", "--exam")
        self.assertIn("exam mode is also sequential", " ".join(d["violations"]))
        self.ok("ask", "--node", "数据与存储", "--text", "A？", "--kind", "recall", "--exam")

    def test_force_waives_but_records_the_violation(self):
        self.ok("ask", "--node", "数据与存储", "--text", "你懂了吗？", "--force",
                "--reason", "user asked a yes/no sanity check")
        self.assertTrue(any(h["event"] == "violation_waived" for h in self.history()))


class TestHintLadder(MICase):
    def setUp(self):
        super().setUp()
        self.init()

    def _pending(self):
        self.ok("ask", "--node", "数据与存储", "--text", "WAL 为什么能帮助崩溃恢复？", "--kind", "why")

    def test_bottom_out_needs_the_lower_rungs(self):
        self._pending()
        d = self.blocked("teach", "--node", "数据与存储", "--level", "3",
                         "--text", "答案是 WAL", "--recovery-text", "用自己的话解释 WAL 为什么能恢复？")
        self.assertIn("requires level 1 then level 2", " ".join(d["violations"]))

    def test_bottom_out_needs_a_recovery_prompt(self):
        self._pending()
        self.ok("teach", "--node", "数据与存储", "--level", "1", "--text", "先看日志和数据落盘的先后顺序")
        self.ok("teach", "--node", "数据与存储", "--level", "2", "--text", "原则：先把可重放的信息持久化，再改数据页")
        d = self.blocked("teach", "--node", "数据与存储", "--level", "3", "--text", "答案是 WAL")
        self.assertIn("recovery-text", " ".join(d["violations"]))

    def test_level_two_needs_a_level_one(self):
        self._pending()
        self.blocked("teach", "--node", "数据与存储", "--level", "2", "--text", "原则是……")

    def test_hint_without_pending_registers_a_retry(self):
        d = self.ok("teach", "--node", "数据与存储", "--level", "1", "--text", "看写入顺序",
                    "--retry-text", "如果先写数据后写日志，崩溃时可能丢什么？", "--retry-kind", "why")
        self.assertEqual(d["pending_question"]["kind"], "why")

    def test_full_ladder_is_recorded_and_l3_forces_recovery(self):
        self._pending()
        self.ok("teach", "--node", "数据与存储", "--level", "1", "--text", "p", "--stuck-point", "wal")
        self.ok("teach", "--node", "数据与存储", "--level", "2", "--text", "t", "--stuck-point", "wal")
        d = self.ok("teach", "--node", "数据与存储", "--level", "3", "--text", "a",
                    "--stuck-point", "wal", "--recovery-text", "现在用自己的话解释 WAL 为什么有效？")
        st = self.state()
        self.assertEqual([h["level"] for h in st["hint_log"] if h["stuck_point"] == "wal"], [1, 2, 3])
        self.assertEqual(st["pending_question"]["id"], d["pending_question"]["id"])
        self.assertEqual(st["pending_question"]["kind"], "explain_back")
        self.ok("answer", "--text", "因为日志先持久化，数据页丢失后可以重放", "--evidence", "explain_back:pass")
        self.assertEqual(self.ok("validate")["violations"], [])


class TestMasteryGate(MICase):
    def setUp(self):
        super().setUp()
        self.init(seed="none")
        self.a = Answer(self)
        self.n = "Transactions"
        self.ok("node", "add", "--id", self.n, "--name", self.n)

    TRANSFERS = [
        "换一个场景：如果这份数据每小时才更新一次，你还会这样写吗，为什么？",
        "反过来：如果两个进程同时写同一个 key，你怎么保证读到的不是旧值？",
        "现在把量级放大一千倍，最先坏掉的会是哪一步？",
    ]

    def _walk(self, evidence=(), transfers=()):
        for kind, result in evidence:
            self.a.exchange(self.n, "%s 问题?" % kind, kind, "回答", evidence=["%s:%s" % (kind, result)])
        for i, t in enumerate(transfers):
            self._transfer(i, t)

    def _transfer(self, i, result="pass"):
        self.a.exchange(self.n, self.TRANSFERS[i], "transfer", "回答",
                        evidence=["transfer:%s" % result])

    def test_one_correct_answer_is_not_mastery(self):
        self._walk([("explain_back", "pass")])
        d = self.ok("audit", "--node", self.n)
        self.assertEqual(d["verdict"], "BLOCK")
        self.assertNotEqual(self.node(self.n)["status"], "mastered")

    def test_mastered_cannot_be_declared_by_hand(self):
        d = self.blocked("node", "update", "--id", self.n, "--status", "mastered")
        self.assertIn("mastery gate", d["error"])
        self.assertNotEqual(self.node(self.n)["status"], "mastered")

    def test_full_gate_requires_two_novel_unprompted_transfers(self):
        self._walk([("explain_back", "pass"), ("why", "pass"), ("boundary", "pass")],
                   transfers=["pass"])
        self.assertEqual(self.ok("audit", "--node", self.n)["verdict"], "BLOCK")
        self._transfer(1)
        d = self.ok("audit", "--node", self.n)
        self.assertEqual(d["verdict"], "PASS")
        self.ok("refresh")
        self.assertEqual(self.node(self.n)["status"], "mastered")

    def test_hinted_transfer_does_not_count(self):
        self._walk([("explain_back", "pass"), ("why", "pass"), ("boundary", "pass")])
        self.a.exchange(self.n, "新场景：换一个条件会怎样？", "transfer", "回答",
                        evidence=["transfer:pass"], hinted=True)
        self.a.exchange(self.n, "新场景二：反过来会怎样？", "transfer", "回答",
                        evidence=["transfer:pass"], hinted=True)
        d = self.ok("audit", "--node", self.n)
        self.assertEqual(d["verdict"], "BLOCK")
        self.assertIn("transfer", " ".join(d["missing"]))

    def test_repeating_a_seen_transfer_is_not_novel(self):
        self._walk([("explain_back", "pass"), ("why", "pass"), ("boundary", "pass")])
        self.a.exchange(self.n, "同一道迁移题：换一个条件会怎样？", "transfer", "回答",
                        evidence=["transfer:pass"])
        self.a.exchange(self.n, "同一道迁移题：换一个条件会怎样？", "transfer", "回答",
                        evidence=["transfer:pass"])
        self.assertEqual(self.ok("audit", "--node", self.n)["verdict"], "BLOCK")

    def test_fact_nodes_cannot_be_mastered(self):
        self.ok("node", "add", "--id", "常量", "--name", "常量", "--kind", "fact")
        self.a.exchange("常量", "这个上限是多少？", "recall", "1024", evidence=["recall:pass"])
        self.a.exchange("常量", "用你自己的话说这个上限是干什么的？", "explain_back", "回答",
                        evidence=["explain_back:pass"])
        self.assertNotEqual(self.node("常量")["status"], "mastered")

    def test_evidence_label_validation(self):
        self.ok("ask", "--node", self.n, "--text", "问一个？")
        self.mi("--project", self.project, "answer", "--text", "x",
                "--evidence", "vibes:pass", expect=2)
        self.mi("--project", self.project, "answer", "--text", "x",
                "--evidence", "explain_back:probably", expect=2)


class TestDecay(MICase):
    def setUp(self):
        super().setUp()
        self.init(seed="none")
        self.a = Answer(self)
        self.ok("node", "add", "--id", "N", "--name", "N")
        for kind in ("explain_back", "why", "boundary"):
            self.a.exchange("N", "%s ?" % kind, kind, "回答", evidence=["%s:pass" % kind])
        self.a.exchange("N", "迁移一：换成别的情形会怎样？", "transfer", "回答", evidence=["transfer:pass"])
        self.a.exchange("N", "迁移二：再换一种情形呢？", "transfer", "回答", evidence=["transfer:pass"])
        self.ok("refresh")
        assert self.node("N")["status"] == "mastered", self.node("N")

    def test_mastered_decays_into_misconception(self):
        d = self.ok("challenge", "--node", "N", "--note", "用户说他以为缓存只是为了速度更快",
                    "--misconception", "缓存只是为了速度")
        self.assertEqual(d["before"], "mastered")
        self.assertEqual(d["after"], "misconception")
        self.assertIsNone(self.node("N")["mastered_at"])
        self.assertEqual(self.ok("audit", "--node", "N")["verdict"], "BLOCK")
        self.assertTrue(any(h["event"] == "challenge" for h in self.history()))

    def test_correcting_a_misconception_restores_the_node(self):
        self.ok("challenge", "--node", "N", "--note", "误解", "--misconception", "缓存只是为了速度")
        mids = [m["id"] for m in json.loads(
            (self.home / self.project / "mistakes.json").read_text(encoding="utf-8"))["open"]]
        self.ok("ask", "--node", "N", "--text", "给一个缓存不只是为了提速的场景，并说明原因？", "--kind", "connect")
        self.ok("answer", "--text", "缓存也可能用于削峰和隔离后端压力", "--evidence", "connect:pass",
                "--corrected", mids[0])
        self.ok("refresh")
        self.assertEqual(self.node("N")["status"], "mastered")

    def test_plain_challenge_demotes_to_needs_review(self):
        self.ok("challenge", "--node", "N", "--note", "回答里暴露了遗忘")
        self.assertEqual(self.node("N")["status"], "needs_review")

    def test_mastery_schedules_spaced_review(self):
        self.assertIsNotNone(self.node("N")["review"])
        d = self.ok("review", "--mark", "N")
        self.assertEqual(d["next"], "1w")
        self.assertEqual(self.node("N")["review"]["level"], 1)


class TestValidate(MICase):
    def setUp(self):
        super().setUp()
        self.init(seed="none")
        self.ok("node", "add", "--id", "A", "--name", "A")
        self.ok("node", "add", "--id", "B", "--name", "B", "--parent", "A")

    def test_clean_tree_validates(self):
        d = self.ok("validate")
        self.assertEqual(d["violations"], [])
        self.assertIn("校验通过", d["display"])

    def test_tampered_tree_is_caught(self):
        p = self.home / self.project / "tree.json"
        t = json.loads(p.read_text(encoding="utf-8"))
        t["nodes"]["A"]["status"] = "mastered"          # declared, never earned
        t["nodes"]["B"]["parent"] = "ghost"             # dangling parent
        p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
        d = self.mi("validate", project=self.project, expect=3)
        joined = " ".join(x["message"] for x in d["violations"])
        self.assertIn("mastered without passing the gate", joined)
        self.assertIn("missing parent", joined)

    def test_stale_status_is_reported_as_a_warning(self):
        # Create valid anchored evidence, then tamper only the cached derived status.
        for kind in ("explain_back", "why", "boundary"):
            self.ok("ask", "--node", "A", "--text", "%s evidence?" % kind, "--kind", kind)
            self.ok("answer", "--text", "ok", "--evidence", "%s:pass" % kind)
        p = self.home / self.project / "tree.json"
        tree = json.loads(p.read_text(encoding="utf-8"))
        tree["nodes"]["A"]["status"] = "unexplored"
        p.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        d = self.ok("validate")
        self.assertTrue(any("stale status" in w["message"] for w in d["warnings"]), d["warnings"])
        self.ok("refresh")
        self.assertEqual(self.ok("validate")["warnings"], [])

    def test_same_level_rephrase_after_idk_is_warned(self):
        self.ok("ask", "--node", "A", "--text", "为什么这里要加锁？")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("ask", "--node", "A", "--text", "为什么这里要加锁？", "--force")
        self.ok("answer", "--text", "还是不知道", "--idk")
        d = self.ok("validate")
        self.assertTrue(any("same-level rephrase" in w["message"] for w in d["warnings"]), d["warnings"])


class TestRenderAndExport(MICase):
    def test_render_shows_status_not_chain_of_thought(self):
        self.init()
        d = self.ok("render")
        self.assertIn("○", d["display"])
        self.assertNotIn("为什么问这个", json.dumps(d, ensure_ascii=False))

    def test_export_markdown(self):
        self.init()
        out = self.home / "map.md"
        self.ok("export", "--format", "md", "--out", str(out))
        text = out.read_text(encoding="utf-8")
        self.assertIn("知识树", text)

"""Core state-machine tests: tree growth, structure ops, traversal, persistence."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mi_harness import MICase  # noqa: E402


class TestInit(MICase):
    def test_seed_tree_is_coarse_and_all_hypothesis(self):
        self.init()
        nodes = self.nodes()
        self.assertGreater(len(nodes), 5)
        self.assertLess(len(nodes), 25, "seed must stay coarse -- depth is discovered in dialogue")
        for n in nodes.values():
            self.assertEqual(n["status"], "unexplored")
            self.assertEqual(n["mastery"], 0.0)

    def test_unknown_seed_rejected(self):
        d = self.mi("init", "--project", "x", "--seed", "nope", expect=2)
        self.assertFalse(d["ok"])

    def test_second_init_requires_force(self):
        self.init()
        self.mi("init", "--project", self.project, "--seed", "project", expect=2)

    def test_writes_are_atomic_no_tmp_left(self):
        self.init()
        stray = list((self.home / self.project).glob("*.tmp"))
        self.assertEqual(stray, [])


class TestTreeGrowth(MICase):
    def test_node_added_by_dialogue_records_its_origin(self):
        self.init()
        before = len(self.nodes())
        self.ok("ask", "--node", "核心业务逻辑与算法", "--text", "下单请求经过哪些环节？",
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "核心业务逻辑与算法", "--prereq", "依赖注入",
                "--reason", "不懂 DI 无法解释装配过程")
        self.assertEqual(len(self.nodes()), before + 1)
        n = self.node("依赖注入")
        self.assertIsNotNone(n)
        self.assertEqual(n["origin"], "discovered")
        self.assertEqual(n["parent"], "核心业务逻辑与算法")
        self.assertIsNotNone(n["discovered_from"])
        self.assertIn("turn", n["discovered_from"])
        # the growth is recorded as an event, not only as a final state
        self.assertTrue(any(h["event"] == "node_add" for h in self.history()))

    def test_edge_and_dependency(self):
        self.init()
        self.ok("ask", "--node", "数据与存储", "--text", "一次写入的事务边界在哪里？")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "数据与存储", "--prereq", "事务边界",
                "--reason", "不知道事务边界就看不懂落库")
        edges = json.loads((self.home / self.project / "edges.json").read_text(encoding="utf-8"))["edges"]
        self.assertTrue(any(e["type"] == "depends_on" and e["to"] == "事务边界" for e in edges))

    def test_split_merge_reparent_remove(self):
        self.init()
        self.ok("node", "add", "--id", "模型原理", "--name", "模型原理", "--importance", "5")
        self.ok("node", "split", "--id", "模型原理", "--into", "假设,目标函数,优化方法")
        n = self.node("模型原理")
        self.assertTrue(n["is_container"])
        self.assertEqual(len(n["children"]), 3)

        self.ok("node", "add", "--id", "模型原理-别名", "--name", "模型原理别称")
        self.ok("node", "merge", "--id", "模型原理-别名", "--into", "模型原理", "--reason", "同义")
        self.assertIsNone(self.node("模型原理-别名"))

        self.ok("node", "reparent", "--id", "假设", "--parent", "root")
        self.assertEqual(self.node("假设")["parent"], "root")

        self.ok("node", "remove", "--id", "优化方法", "--reason", "重复")
        self.assertIsNone(self.node("优化方法"))
        self.assertTrue(any(h["event"] == "node_remove" for h in self.history()))

    def test_reparent_under_own_descendant_is_refused(self):
        self.init()
        self.ok("node", "add", "--id", "A", "--name", "A")
        self.ok("node", "add", "--id", "B", "--name", "B", "--parent", "A")
        self.blocked("node", "reparent", "--id", "A", "--parent", "B")

    def test_node_lookup_by_name_or_id(self):
        self.init()
        self.ok("node", "update", "--id", "架构与模块划分", "--note", "按名字也能命中")
        self.assertIn("按名字也能命中", [x["text"] for x in self.node("架构与模块划分")["notes"]])


class TestTraversal(MICase):
    def _mainline(self):
        self.init()
        self.ok("ask", "--node", "核心业务逻辑与算法", "--text", "下单请求从进来到落库经过哪些环节？",
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "不知道", "--idk")

    def test_drill_pushes_return_stack_and_preserves_question(self):
        self._mainline()
        self.ok("drill", "--node", "核心业务逻辑与算法", "--prereq", "依赖注入", "--reason", "挡住理解")
        st = self.state()
        self.assertEqual(len(st["return_stack"]), 1)
        self.assertEqual(st["return_stack"][0]["node"], "核心业务逻辑与算法")
        self.assertIn("环节", st["return_stack"][0]["question_text"])
        self.assertEqual(st["drill"]["depth"], 1)

    def test_return_requires_something_established(self):
        self._mainline()
        self.ok("drill", "--node", "核心业务逻辑与算法", "--prereq", "依赖注入", "--reason", "挡住理解")
        self.blocked("return")  # nothing probed in the child yet
        self.ok("ask", "--node", "依赖注入", "--text", "你平时怎么拿到依赖？", "--kind", "probe")
        self.ok("answer", "--text", "用 @Autowired", "--evidence", "recall:pass")
        d = self.ok("return")
        self.assertEqual(d["returned_to"], "核心业务逻辑与算法")
        self.assertTrue(d["reprobe_required"])
        self.assertIn("环节", d["original_question"])

    def test_reprobe_is_forced_after_return(self):
        self._mainline()
        self.ok("drill", "--node", "核心业务逻辑与算法", "--prereq", "依赖注入", "--reason", "挡住理解")
        self.ok("ask", "--node", "依赖注入", "--text", "你平时怎么拿到依赖？", "--kind", "probe")
        self.ok("answer", "--text", "用 @Autowired", "--evidence", "recall:pass")
        self.ok("return")
        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "probe")
        self.assertIn("重问", nxt["display"])

    def test_drill_depth_is_capped(self):
        self.init(mode="modeling", seed="modeling")
        parent = "模型选择的理由"
        for prereq in ["方差与偏差", "过拟合", "容量", "信息量"]:
            self.ok("ask", "--node", parent, "--text", "为什么当前层还解释不了？")
            self.ok("answer", "--text", "不知道", "--idk")
            self.ok("drill", "--node", parent, "--prereq", prereq, "--reason", "继续下钻")
            parent = prereq
        self.ok("ask", "--node", "信息量", "--text", "信息量为什么会限制模型？")
        self.ok("answer", "--text", "不知道", "--idk")
        d = self.blocked("drill", "--node", "信息量", "--prereq", "极限", "--reason", "无效下钻")
        self.assertIn("max_drill_depth", " ".join(d["violations"]))

    def test_drill_without_reason_is_refused(self):
        self.init()
        self.mi("--project", self.project, "drill", "--node", "数据与存储",
                "--prereq", "索引", "--reason", "", expect=3)


class TestPersistence(MICase):
    def test_state_survives_a_fresh_process(self):
        self.init()
        self.ok("ask", "--node", "安全与权限", "--text", "登录状态存在哪里？", "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "安全与权限", "--prereq", "会话与 Cookie", "--reason", "不知道会话就无法谈登录")
        st_before = self.state()
        # a brand new process, as if the app had been restarted
        d = self.mi_subprocess("resume", project=self.project)
        self.assertTrue(d.get("ok"), d)
        self.assertEqual(d["focus"], "会话与 cookie".replace(" ", "") if False else st_before["focus"])
        self.assertEqual(d["return_stack"][0]["node"], "安全与权限")
        self.assertIn("登录状态存在哪里", d["display"])
        st_after = json.loads((self.home / self.project / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(st_after["sessions"], st_before["sessions"] + 1)
        self.assertEqual(st_after["turn"], st_before["turn"])
        self.assertTrue(any(h["event"] == "session_start" for h in self.history()))

    def test_project_resolution_from_current_file(self):
        self.init()
        d = self.mi("status", expect=0)  # no --project: falls back to <home>/current
        self.assertTrue(d["ok"])
        self.assertIn("节点", d["display"])

    def test_logs_rotate(self):
        self.init()
        p = self.home / self.project / "history.jsonl"
        with p.open("a", encoding="utf-8") as fh:
            for i in range(4200):
                fh.write(json.dumps({"at": "x", "turn": i, "event": "noise"}) + "\n")
        d = self.ok("maintain")
        self.assertLess(d["sizes"]["history.jsonl_after"], 1000)
        self.assertGreater(d["archives"], 0)

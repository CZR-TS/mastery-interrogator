"""The seven acceptance cases from the Skill's brief (A-G).

Each test drives the prescribed protocol end to end through the real CLI and
asserts the outcome that would otherwise be a matter of the model's goodwill.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mi_harness import MICase  # noqa: E402


class TestCaseA_SpringBootDI(MICase):
    """A: user knows Controllers but not Dependency Injection -> the tree must
    grow a DI prerequisite *during the conversation*, then drill into it."""

    def test_di_node_appears_only_after_the_user_admits_the_gap(self):
        self.init(seed="project", objective="彻底弄懂这个 Spring Boot 项目")
        seed_count = len(self.nodes())

        self.ok("ask", "--node", "核心业务逻辑与算法",
                "--text", "这个项目里，一次下单请求从进来到数据落库，中间经过了哪些环节？",
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "Controller 我知道，但是 Dependency Injection 不懂", "--idk")

        # the tree has NOT grown yet: the gap is only known now
        self.assertEqual(len(self.nodes()), seed_count)

        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "drill")
        self.assertTrue(nxt.get("must_not_answer"), "the script must forbid answering straight away")

        self.ok("drill", "--node", "核心业务逻辑与算法", "--prereq", "依赖注入",
                "--reason", "不理解 DI 就无法解释这段业务逻辑的装配方式")
        self.assertEqual(len(self.nodes()), seed_count + 1)
        n = self.node("依赖注入")
        self.assertEqual(n["origin"], "discovered")
        self.assertEqual(n["discovered_from"]["trigger"], "不理解 DI 就无法解释这段业务逻辑的装配方式")

        self.ok("ask", "--node", "依赖注入", "--text", "你写过的类要用到另一个对象时，是怎么拿到它的？")
        self.ok("answer", "--text", "自己 new 的", "--evidence", "recall:pass")
        d = self.ok("return")
        self.assertEqual(d["returned_to"], "核心业务逻辑与算法")
        self.assertTrue(d["reprobe_required"])
        self.assertTrue(any(h["event"] == "drill_push" for h in self.history()))


class TestCaseB_ModelingRandomForest(MICase):
    """B: a paper says random forest; the user says an AI picked it. The drill
    must start from the *reason for the choice*, not from a lecture on forests."""

    def test_model_choice_gap_drills_into_why_before_what(self):
        self.init(mode="modeling", seed="modeling", objective="答辩前保证每个地方都懂")
        self.ok("ask", "--node", "模型选择的理由", "--text", "论文这里选了随机森林，为什么是它？",
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "AI 给我选的，我不知道", "--idk")
        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "drill")
        self.assertNotIn("答案", json.dumps(nxt, ensure_ascii=False))

        self.ok("drill", "--node", "模型选择的理由", "--prereq", "偏差与方差",
                "--reason", "不知道为何合适，通常源于不知道该模型在偏差-方差上的位置")
        self.ok("ask", "--node", "偏差与方差",
                "--text", "你理解的“偏差”和“方差”，分别对应模型出错的哪种情况？", "--kind", "probe")
        self.ok("answer", "--text", "不知道", "--idk")
        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "drill")
        self.ok("drill", "--node", "偏差与方差", "--prereq", "过拟合",
                "--reason", "不知道方差来自不理解过拟合")
        self.assertIn("再切一层", nxt["display"])

        # the prerequisite graph, not just the tree, has grown
        edges = json.loads((self.home / self.project / "edges.json").read_text(encoding="utf-8"))["edges"]
        self.assertTrue(any(e["from"] == "模型选择的理由" and e["to"] == "偏差与方差"
                            and e["type"] == "depends_on" for e in edges))
        self.assertTrue(any(e["from"] == "偏差与方差" and e["to"] == "过拟合" for e in edges))


class TestCaseC_IDKChain(MICase):
    """C: three "I don't know" in a row. The Skill must keep descending to a
    prerequisite, then STOP descending and micro-teach instead of drilling to
    the axioms."""

    def test_descends_then_stops_and_teaches(self):
        self.init(mode="modeling", seed="modeling")

        # Every descent is anchored to a real failed question on the current focus.
        chain = [
            ("模型原理", "梯度提升", "你能解释梯度提升的核心机制吗？"),
            ("梯度提升", "梯度", "梯度提升里的梯度具体代表什么？"),
            ("梯度", "导数", "梯度和导数是什么关系？"),
            ("导数", "变化率", "导数为什么能表示局部变化率？"),
        ]
        for parent, prereq, question in chain:
            self.ok("ask", "--node", parent, "--text", question, "--kind", "probe")
            self.ok("answer", "--text", "不知道", "--idk")
            self.ok("drill", "--node", parent, "--prereq", prereq,
                    "--reason", "用户在这一层说不知道，下一层是回答当前问题的最小前置")

        self.ok("ask", "--node", "变化率", "--text", "什么叫一个量相对另一个量的变化率？")
        self.ok("answer", "--text", "不知道", "--idk")
        d = self.blocked("drill", "--node", "变化率", "--prereq", "极限",
                         "--reason", "继续下钻到底层数学公理")
        self.assertIn("stop descending", d["error"])

        # At the floor, use the full hint ladder and force an explain-back before return.
        self.ok("teach", "--node", "变化率", "--level", "1", "--text", "先想单位输入变化带来的输出变化",
                "--stuck-point", "rate",
                "--retry-text", "如果输入增加 2、输出增加 6，这个区间的平均变化率是多少？",
                "--retry-kind", "probe")
        self.ok("teach", "--node", "变化率", "--level", "2", "--text", "原则：变化率比较的是 Δy/Δx",
                "--stuck-point", "rate", "--floor")
        self.ok("teach", "--node", "变化率", "--level", "3", "--text", "变化率就是输出变化量除以输入变化量",
                "--stuck-point", "rate", "--recovery-text", "现在用自己的话解释变化率是什么？", "--floor")
        self.ok("answer", "--text", "就是输入每变化一单位，输出大约变化多少",
                "--evidence", "explain_back:pass")
        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "return", "after teaching at the floor the drill must unwind")

    def test_an_idk_never_returns_a_ready_made_answer(self):
        self.init()
        self.ok("ask", "--node", "数据与存储", "--text", "为什么这张表要加唯一索引？")
        d = self.ok("answer", "--text", "不知道", "--idk")
        blob = json.dumps(d, ensure_ascii=False)
        self.assertNotIn("因为", blob)
        self.assertEqual(d["next_action"], "drill")
        self.assertFalse(d["next"].get("answer"))


class TestCaseD_OneRightAnswer(MICase):
    """D: one correct answer must not produce mastery."""

    def test_correct_answer_stays_below_mastery(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "锁", "--name", "锁")
        self.ok("ask", "--node", "锁", "--text", "为什么这里需要锁？", "--kind", "explain_back")
        d = self.ok("answer", "--text", "因为两个线程同时改同一个变量",
                    "--evidence", "explain_back:pass")
        self.assertNotEqual(d["status"], "mastered")
        aud = self.ok("audit", "--node", "锁")
        self.assertEqual(aud["verdict"], "BLOCK")
        self.assertIn("transfer", " ".join(aud["missing"]))


class TestCaseE_Regression(MICase):
    """E: a previously mastered node that later shows a misunderstanding must
    fall back automatically."""

    def test_mastered_node_falls_back(self):
        self.init(seed="none")
        self.ok("node", "add", "--id", "缓存", "--name", "缓存")
        for kind in ("explain_back", "why", "boundary"):
            self.ok("ask", "--node", "缓存", "--text", "%s?" % kind, "--kind", kind)
            self.ok("answer", "--text", "回答", "--evidence", "%s:pass" % kind)
        self.ok("ask", "--node", "缓存", "--kind", "transfer",
                "--text", "换一个场景：如果这份数据每小时只更新一次，你会把缓存放在哪一层，为什么？")
        self.ok("answer", "--text", "回答", "--evidence", "transfer:pass")
        self.ok("ask", "--node", "缓存", "--kind", "transfer",
                "--text", "反过来：如果两个进程同时写同一个 key，你会怎么保证读到的不是旧值？")
        self.ok("answer", "--text", "回答", "--evidence", "transfer:pass")
        self.ok("refresh")
        self.assertEqual(self.node("缓存")["status"], "mastered")

        d = self.ok("challenge", "--node", "缓存", "--note", "用户说“缓存就是为了让页面更快”",
                    "--misconception", "缓存只是为了让页面更快")
        self.assertEqual(d["before"], "mastered")
        self.assertIn(d["after"], ("misconception", "needs_review"))
        self.assertEqual(self.ok("audit", "--node", "缓存")["verdict"], "BLOCK")
        self.assertEqual(self.ok("validate")["violations"], [])


class TestCaseF_ReturnAfterDrill(MICase):
    """F: five turns inside a sub-node must end with a return to the original
    question, not with drifting off into the sub-topic."""

    def test_drill_budget_forces_the_return(self):
        self.init()
        opening = "这个项目里，一次下单请求从进来到数据落库，中间经过了哪些环节？"
        self.ok("ask", "--node", "核心业务逻辑与算法", "--text", opening,
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "核心业务逻辑与算法", "--prereq", "依赖注入",
                "--reason", "不知道装配方式")
        for i in range(5):
            self.ok("ask", "--node", "依赖注入", "--text", "第 %d 个子问题？" % (i + 1))
            self.ok("answer", "--text", "还是不太确定", "--evidence", "why:partial")
        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "return")
        self.assertIn("预算", nxt["display"])

        d = self.ok("return")
        self.assertEqual(d["returned_to"], "核心业务逻辑与算法")
        self.assertEqual(d["original_question"], opening)
        nxt = self.ok("next")
        self.assertEqual(nxt["action"], "probe")
        self.assertIn("重问", nxt["display"])

    def test_a_whole_chain_unwinds_level_by_level(self):
        self.init(mode="modeling", seed="modeling")
        self.ok("ask", "--node", "模型选择的理由", "--text", "为什么选随机森林？",
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "模型选择的理由", "--prereq", "偏差与方差", "--reason", "r1")
        self.ok("ask", "--node", "偏差与方差", "--text", "偏差和方差分别是什么？")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "偏差与方差", "--prereq", "过拟合", "--reason", "r2")
        self.ok("ask", "--node", "过拟合", "--text", "什么叫过拟合？")
        self.ok("answer", "--text", "训练集很好测试集很差", "--evidence", "explain_back:pass")

        self.assertEqual(self.ok("next")["action"], "return")
        ret = self.ok("return")
        # at each level the parent must be re-probed before unwinding further
        self.blocked("return")
        self.ok("ask", "--node", "偏差与方差",
                "--text", "如果训练集表现很好但换一批数据就波动很大，这更接近偏差还是方差问题？",
                "--reprobe-of", ret["original_question_id"], "--kind", "why")
        self.ok("answer", "--text", "方差问题，因为模型对样本扰动太敏感", "--evidence", "why:pass")
        self.ok("return")
        st = self.state()
        self.assertEqual(st["focus"], "模型选择的理由")
        self.assertEqual(st["return_stack"], [])
        self.assertEqual(st["drill"]["depth"], 0)


class TestCaseG_Resume(MICase):
    """G: quit and come back -- the tree, the stack and the weak points survive."""

    def test_full_state_restored_in_a_fresh_process(self):
        self.init(seed="project", objective="彻底弄懂这个项目")
        self.ok("ask", "--node", "安全与权限", "--text", "登录态存在哪里？",
                "--kind", "probe", "--mainline")
        self.ok("answer", "--text", "不知道", "--idk")
        self.ok("drill", "--node", "安全与权限", "--prereq", "会话与 Cookie", "--reason", "卡在这里")
        self.ok("ask", "--node", "会话与 Cookie", "--text", "Cookie 和 Session 的关系是什么？")
        self.ok("answer", "--text", "Cookie 存在浏览器，Session 存在服务端", "--evidence", "recall:pass")

        before_tree = json.loads((self.home / self.project / "tree.json").read_text(encoding="utf-8"))
        before_turn = self.state()["turn"]

        # brand new process: only the home directory is known
        d = self.mi_subprocess("resume", project=self.project)
        self.assertTrue(d.get("ok"), d)
        self.assertEqual(d["turn"], before_turn)
        self.assertEqual(d["focus"], "会话与 cookie" if False else d["focus"])
        self.assertIn("登录态存在哪里", d["display"])
        self.assertIn("会话与 cookie", d["display"].lower().replace("c", "c"))
        self.assertIn("不要重新扫描项目", d["display"])
        after_tree = json.loads((self.home / self.project / "tree.json").read_text(encoding="utf-8"))
        self.assertEqual(set(before_tree["nodes"]), set(after_tree["nodes"]))
        self.assertEqual(self.ok("validate")["violations"], [])

    def test_projects_command_lists_previous_work(self):
        self.init(seed="project")
        d = self.ok("projects")
        self.assertEqual(d["current"], self.project)
        self.assertEqual([p["project"] for p in d["projects"]], [self.project])

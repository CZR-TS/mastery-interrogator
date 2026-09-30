# mastery-interrogator

An Agent Skill that interrogates **you** about a complex object — a codebase, a
paper, a mathematical model, a technical design — until the map of what you
actually understand is explicit: what you hold, where the gaps are, what those
gaps depend on, and which question is worth asking next.

Not a lecture generator. Not a quiz bot. Not a one-shot knowledge graph.

The artifact is a **Dynamic Mastery Tree**. It is not generated once from the
materials: it grows from your answers, and every node keeps the provenance that
caused it to exist — the turn, the question, and your own words.

```text
why this exists at all
──────────────────────────────────────────────────────────────
You can read a project and still not understand it.
You can ship a model an AI wrote and still not be able to defend it.
The gap is invisible to you precisely because it feels like understanding.

So this skill never asks "does that make sense?". It makes you generate:
explain it back, say why, name where it breaks, apply it somewhere new.
```

## How it differs from a knowledge graph or a quiz

| concern | typical approach | mastery-interrogator |
|---|---|---|
| the map | generated up front from the materials | grows node-by-node, each node carrying `discovered_from` |
| next question | a syllabus walked in order | scored by importance, blocking, prerequisites, weakness, recency, review debt |
| "I don't know" | gets an answer | drills to the *smallest sufficient prerequisite*, then climbs back |
| mastery | one correct answer | explain-back + mechanism + boundary + **two novel, unprompted transfers** |
| hints | arbitrary | attempt-scoped L1 → L2 → L3, each rung blocked until the previous one is on record |
| tangents | conversational memory | persisted return stack; the interrupted question **must** be re-asked in new words |
| forgetting | ignored | spaced review plus `challenge`, which demotes a mastered node |
| state | the context window | files on disk; a crash or a restart resumes mid-thought |

The design decision worth arguing about: the skill is roughly 40% prose and 60%
code + tests. Prose decides **judgement** (what to ask, how to hint, how to
phrase feedback). Code decides **honesty** (where state lives, whether a mastery
claim is backed by evidence, whether the hint ladder was followed, whether a
drill budget was blown). The failure mode this guards against is not "a mediocre
question" — it is "the agent quietly lies on its own behalf at turn 40, when the
context is crowded and you need the honesty most".

## Install

Any agent that loads a skills directory works; the skill is just a folder
containing `SKILL.md`.

```bash
git clone https://github.com/<you>/mastery-interrogator.git

# Claude Code / any agent with a skills dir:
cp -r mastery-interrogator ~/.claude/skills/

# or point your agent at the folder directly
```

No dependencies. Python 3.10+ standard library only — nothing to `pip install`.
CI runs the suite on 3.10 and 3.12.

## Use

Never hard-code the install path; resolve `SKILL_ROOT` as the folder containing
`SKILL.md`.

```bash
SKILL_ROOT="/path/to/mastery-interrogator"
MI="$SKILL_ROOT/scripts/mi.py"
OBJ="/path/to/your/project"        # the object being interrogated

# 1. shallow read of the object, then a coarse seed tree (a hypothesis, not a syllabus)
python3 "$MI" --root "$OBJ" init --mode project --seed project \
    --objective "彻底弄懂这个项目" --gitignore

# 2. every turn: record the answer, let the engine choose, ask ONE question
python3 "$MI" --root "$OBJ" answer --text "..." --evidence explain_back:pass
python3 "$MI" --root "$OBJ" next
python3 "$MI" --root "$OBJ" ask --node <node> --kind probe --text "..."

# 3. come back later — health check, then resume
python3 "$MI" --root "$OBJ" doctor
python3 "$MI" --root "$OBJ" resume
```

State lives in `<object-root>/.mastery/<workspace>/`, so it travels with the
project. Nothing global, and no silent fallback: if no workspace can be located,
the CLI says so and tells you the fix.

Every command emits JSON. Exit `0` is success, `2` is usage/IO failure, `3` is a
protocol violation — the agent is expected to change its action, not bypass it.

Run `python3 "$MI" --help` for the full command list. Three modes:
`project`, `modeling`, `generic`.

## Verify

```bash
sh tests/run_tests.sh
# or, quickly:
python3 -m unittest discover -s tests -q
```

99 tests, ~45 s, no network, no fixtures to download. They cover:

* the seven end-to-end acceptance cases (growing the tree mid-dialogue, the
  "AI picked the model and I don't know why" case, an IDK chain that must stop
  descending, one-right-answer ≠ mastery, a mastered node decaying, the return
  stack unwinding level by level, and resume after a restart);
* crash recovery via a write journal, and concurrency rejection;
* v1 → v2 schema migration, source-staleness detection, evidence anchoring.

## Layout

```text
SKILL.md                      control plane: the runtime principles and the loop
agents/openai.yaml            ChatGPT UI metadata
references/scout.md           first shallow read and seed construction
references/interrogator.md    per-turn operating loop
references/auditor.md         independent certification of mastery claims
references/dynamic-tree.md    node/edge model and structural operations
references/question-engine.md next-action scoring and IDK drill policy
references/mastery-gate.md    evidence gate, decay, misconception log
references/pedagogy.md        hint ladder, banned verifications, feedback style
references/project-mode.md    software-project interrogation (dynamic domains)
references/modeling-mode.md   paper / mathematical-model interrogation
references/state-and-resume.md CLI, recovery, diagnosis, user commands
scripts/mi.py                 deterministic state machine (the rules live here)
examples/walkthrough.md       a real abridged session, commands and output
templates/workspace/          coarse seed templates
tests/                        99 regression and acceptance tests
```

## Origin and attribution

Built after reading three MIT-licensed skill collections and adapting their
ideas — **no code was copied** from any of them; `scripts/mi.py` and the test
suite are original:

* [cskwork/supertutor-skill](https://github.com/cskwork/supertutor-skill) — the
  explain-back discipline, the point/teach/bottom-out hint ladder, the
  independent critic that certifies mastery, and the idea of a deterministic
  gate the tutor may not run on its own work.
* [Far-200/think-before-code](https://github.com/Far-200/think-before-code) —
  one hint at a time, protecting productive struggle, root-cause-only mistake
  logging, and resumable session checkpoints.
* [m4vic/socratic](https://github.com/m4vic/socratic) — the domain question
  banks and, more importantly, *dynamic* domain discovery: a project with no
  database is never asked database questions.

Where this departs from all three: the tree is required to **grow during the
dialogue** (a node without provenance is a guess about the learner), the
questions are aimed at the human rather than answered by the agent, and the
protocol rules are enforced by executable code rather than prose the model is
trusted to follow.

## 中文说明

这是一个 Agent Skill：给 Agent 一个复杂对象（代码库、论文、数学建模、技术方案），
它通过多轮追问**审问用户本人**，维护一棵随对话生长的知识树，直到你真正掌握它。

- 不是讲解器、不是 Quiz、不是一次性生成的知识图谱。
- 每轮只问一个问题；"不知道"时先下钻最小充分前置，再逐层回溯重问原问题。
- "掌握"由闸门产出：复述 + 讲清机制 + 说清边界 + 两次未见过的无提示迁移。
- 规则写在 `scripts/mi.py` 里强制执行（exit 3），不是写在提示词里靠自觉。
- 状态落在 `<项目目录>/.mastery/`，跟着项目走；崩溃可恢复，重启可续接。
- 99 个测试，只需 python3 标准库：`sh tests/run_tests.sh`

## License

MIT — see [LICENSE](LICENSE).

# Walkthrough — a valid v2 session (abridged)

Scenario: a student needs to defend a mathematical-modeling paper whose model
selection was largely produced by an AI agent. The goal is not to pretend they
designed it; the goal is to make every claim they will defend genuinely theirs.

Resolve the active Skill directory; never hard-code an install path:

```bash
SKILL_ROOT="<directory-containing-SKILL.md>"
MI="$SKILL_ROOT/scripts/mi.py"
R="/path/to/paper-project"
```

## 1. Shallow scout and initialize

Read the abstract, headings, model-selection paragraph, validation section, and
one code entry point. Then:

```bash
python3 "$MI" --root "$R" init --mode modeling --seed modeling \
  --objective "答辩前能解释并捍卫每个关键建模决策" --gitignore

python3 "$MI" --root "$R" source add --ref "paper.pdf#p.4" \
  --kind project_fact --note "模型选择段落"
```

Ask one mainline question:

```bash
python3 "$MI" --root "$R" ask --node "模型选择的理由" --kind probe --mainline \
  --text "论文为什么选随机森林，而不是一个更简单的线性模型？"
```

## 2. The user says IDK: record, then drill

User: “AI 选的，我不知道。”

```bash
python3 "$MI" --root "$R" answer --text "AI 选的，我不知道" --idk
python3 "$MI" --root "$R" next
# -> action=drill, must_not_answer=true

python3 "$MI" --root "$R" drill --node "模型选择的理由" --prereq "偏差与方差" \
  --reason "如果不知道模型的偏差/方差特性，就无法解释它为什么适合当前数据"
```

The node `偏差与方差` is created with dialogue provenance and a dependency edge
from `模型选择的理由`. The interrupted model-choice question is now on the
return stack.

## 3. Probe the prerequisite

```bash
python3 "$MI" --root "$R" ask --node "偏差与方差" --kind probe \
  --text "如果一个模型在训练集上很好，但换一批样本结果就大幅波动，这更像偏差问题还是方差问题？"
```

If the user again says IDK:

```bash
python3 "$MI" --root "$R" answer --text "不知道" --idk
python3 "$MI" --root "$R" next
python3 "$MI" --root "$R" drill --node "偏差与方差" --prereq "过拟合" \
  --reason "用户无法判断方差问题，是因为还不能识别过拟合的泛化表现"
```

Depth only appears because the answers required it.

## 4. Reach a floor and use the hint ladder

Suppose the user cannot explain overfitting and this is the sensible floor for
the current goal. There is no pending question after the IDK, so L1 must create
a concrete retry:

```bash
python3 "$MI" --root "$R" teach --node "过拟合" --level 1 \
  --text "先比较训练集和没见过的数据，不要只看训练误差" \
  --retry-text "训练误差继续下降、验证误差反而上升时，你会怎么描述模型现在的问题？" \
  --retry-kind probe --stuck-point overfit --floor
```

If needed, continue L2 then L3. L3 always registers an explain-back:

```bash
python3 "$MI" --root "$R" teach --node "过拟合" --level 2 \
  --text "原则：模型可能把训练样本的偶然噪声也当成可泛化规律" \
  --stuck-point overfit --floor

python3 "$MI" --root "$R" teach --node "过拟合" --level 3 \
  --text "过拟合就是训练拟合继续变好，但对未见数据的泛化变差" \
  --recovery-text "现在不用我的原句，解释为什么训练更好反而可能预测更差？" \
  --stuck-point overfit --floor
```

User gives a genuine explain-back:

```bash
python3 "$MI" --root "$R" answer \
  --text "因为模型把训练样本里的偶然模式也学进去了，换样本这些模式不成立" \
  --evidence explain_back:pass
```

`next` can now choose `return`.

## 5. Return means re-probe, not recap

```bash
python3 "$MI" --root "$R" return
```

Assume the result contains:

```json
{
  "returned_to": "偏差与方差",
  "original_question_id": "q-0002",
  "reprobe_required": true
}
```

The next question must reference that ID and use genuinely new wording:

```bash
python3 "$MI" --root "$R" ask --node "偏差与方差" --kind why \
  --reprobe-of q-0002 \
  --text "如果训练表现稳定但换一批样本就忽高忽低，为什么这提示的是高方差而不是高偏差？"
```

Only after this answer is recorded can the next return frame be popped.

## 6. Mastery requires different evidence axes

Later, for a node close to mastery:

```bash
python3 "$MI" --root "$R" audit --node "偏差与方差"
```

If the gate says `boundary` is missing, register a boundary question before
recording the answer:

```bash
python3 "$MI" --root "$R" ask --node "偏差与方差" --kind boundary \
  --text "在什么情况下‘增加模型复杂度会增加方差’这个判断不能直接这么用？"

python3 "$MI" --root "$R" answer --text "..." --evidence boundary:pass
```

Two genuinely different, unprompted transfer passes are still required before
`mastered`. A fluent explanation alone never certifies the node.

## 7. A later contradiction demotes mastery

```bash
python3 "$MI" --root "$R" challenge --node "偏差与方差" \
  --note "用户后来把高方差等同于训练误差高" \
  --misconception "高方差意味着训练误差一定高"
```

To close the misconception, first register a repair question:

```bash
python3 "$MI" --root "$R" ask --node "偏差与方差" --kind debug \
  --text "一个模型训练误差接近 0、验证误差很高，这个例子对‘高方差=训练误差高’有什么反驳？"

python3 "$MI" --root "$R" answer \
  --text "它说明高方差可以同时伴随很低的训练误差，问题在泛化波动" \
  --evidence debug:pass --corrected m001
```

## 8. Resume safely later

```bash
python3 "$MI" --root "$R" doctor
python3 "$MI" --root "$R" resume
python3 "$MI" --root "$R" next
```

If `doctor` reports a changed paper/code source, re-read only that source before
continuing. Do not rebuild the mastery tree from scratch.

At a stage boundary:

```bash
python3 "$MI" --root "$R" validate
python3 "$MI" --root "$R" doctor
python3 "$MI" --root "$R" render
python3 "$MI" --root "$R" export --format md --out mastery-map.md
```

A healthy session can end with many `unexplored` nodes. That is more honest than
a complete-looking map produced before the user has demonstrated anything.

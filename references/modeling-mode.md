# Modelling mode — interrogating a paper, a model, or a competition solution

Goal: the user can defend every sentence of the work — in particular under the
questions a defence committee actually asks. Not "can recite the paper": can
*justify it* and say where it breaks.

## Step 1 — scout (once, shallow)

Read the abstract and the headings; find the model-selection sentence, the
assumption list, the equation block, and the validation section; open the code
at the point where the model is fitted. Map paper sections to code:
`mi.py source add --ref "paper.pdf#p.4" --kind project_fact`.

Seed with `mi.py init --mode modeling --seed modeling`. It gives 15 buckets; the
real structure is grown in dialogue.

## The interrogation axes

For each axis, the question that separates *reciting* from *understanding*:

| axis | recite-level | defence-level (ask this) |
|---|---|---|
| 问题定义 | 题目的字面要求 | "评价标准是什么？如果换一个评价标准，你的做法还合适吗？" |
| 抽象与简化 | 列了假设 | "这条假设如果不成立，结论会往哪个方向偏？" |
| 变量定义 | 符号表 | "为什么这个量是决策变量而不是参数？谁在优化谁？" |
| 目标函数 | 公式念一遍 | "为什么这里是这个损失？换成另一个损失，解会怎么变？" |
| 模型选择 | 名字 | "为什么是它而不是另一个？当时的数据特征是怎么支持这个选择的？" |
| 模型原理 | 模型的用途 | "它的机制是什么？每一步在修正什么？" |
| 公式推导 | 抄公式 | "这个式子是怎么来的？中间那一步为什么可以这么做？" |
| 参数 | 取值表 | "这个参数控制什么？它变大时结果会怎么动？" |
| 求解算法 | 调了库 | "为什么要用这个方法解？它一定能收敛吗？不收敛会怎样？" |
| 代码实现 | 能跑 | "论文里的第 3 步对应哪几行代码？" |
| 结果解释 | 读数字 | "这个数在现实里意味着什么？量级合理吗？" |
| 验证 | 有验证章节 | "你怎么知道结果是对的？如果数据换一批，结论还稳吗？" |
| 灵敏度 | 有图 | "哪个输入的扰动最敏感？为什么是它？" |
| 局限 | 有一句话 | "模型在什么情况下会失效？你会怎么改？" |
| 对比 | 没写 | "如果换成另一种模型，结论会变吗？变多少？" |

## Prerequisite descent in modelling

Modelling gaps usually sit under a *named technique*: the paper says PCA, the
user does not know it; you do not lecture on PCA — you ask what they know, and
descend until you find a floor.

```
PCA → 特征值 → 矩阵 → 线性变换      (descend until the user can produce something)
XGBoost → Boosting → 残差 → 梯度 → 导数
随机森林 → 方差/偏差 → 过拟合 → 训练误差 vs 泛化误差
```

Each level down is a `mi.py drill --node X --prereq NAME --reason "..."`. The
`--reason` must be about *this* work: "不知道方差就说不清为什么一百棵树比一棵树稳".
Depth cap and turn budget apply — do not descend into 高等数学 unless the user's
own words show the wall is there.

## The "AI wrote it" case (very common, treat as normal)

The user's honest answer to "why this model?" is often "AI 选的". That is not a
dead end and not a confession. It is the highest-value starting point:

1. Do not ask what they wanted to do; ask what the artifact *does*.
2. Start from the observable: "这个模型接收什么、输出什么？"
3. Then the justification: "如果只用线性回归，这篇论文的结论会变成什么样？"
4. Then the mechanism: descend into why the chosen model behaves that way.
5. Record the honest deficit: `answer --idk`, then drill.

The purpose is not to make the user pretend they designed it. It is to make them
able to defend it.

## Defence simulation (use sparingly, on request)

When the user asks to be examined (`--exam` is then legitimate), ask the three
questions a committee asks, one at a time, and score them as transfer evidence:

* "为什么这样建模，而不是另一种？"
* "你的假设里哪一条最危险？去掉它会怎样？"
* "你的结论在什么范围内成立？"

A defence answer only counts when it survives a follow-up "为什么?".

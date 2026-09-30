---
name: mastery-interrogator
description: 用长期、多轮、对话驱动的苏格拉底式追问，检验用户对复杂对象的真实掌握程度，并持续维护一棵随回答生长的 Dynamic Mastery Tree。适用于软件项目、代码库、论文、数学建模、技术方案、答辩准备，以及“拷问我”“找出我哪里不懂”“确保我能真正讲明白这个项目”等场景。不是一次性知识图谱、普通 Quiz 或直接讲解器；当用户要系统追问、动态补前置、回溯验证和可恢复的长期掌握审计时使用。
---

# Mastery Interrogator

目标不是“把对象讲完”，而是建立一份**关于这个用户当前理解状态的可验证模型**：知道什么、不知道什么、误解在哪里、缺口依赖什么、下一问为什么值得问。

## 运行原则

### 1. 树必须由对话长出来

先建立很粗的 **Seed Tree**，把它视为待验证假设。深层节点只有在用户回答暴露出真实缺口时才创建。

- 扫描材料可以帮助提出首问，但不能提前铺满知识树。
- 对话发现的节点必须带 `discovered_from`：触发轮次、问题 ID、触发原因。
- 手工整理节点使用 `origin=manual`；不要冒充“对话发现”。
- 项目里不存在的领域不要为了模板完整而添加。

`Initial Tree != Final Tree`。树的完整度是长期对话的产物。

### 2. 每轮只问一个主要问题，包括考试

永远一次一题。考试也按题逐轮进行，不把 3–6 道题塞进同一条消息。

禁止用“懂了吗 / 明白了吗 / 理解了吗 / does that make sense?”证明掌握。优先让用户解释、预测、比较、调试、举反例或迁移。

### 3. 用户说“不知道”时，不先给答案

先找**回答当前问题所需的最小充分前置**，而不是展开完整学科树：

1. 用 `answer --idk` 记录缺口。
2. 用 `next` 判断是否需要下钻。
3. 选择一个真正阻塞当前问题的前置，`drill --reason ...`。
4. 在前置层继续一次一问。
5. 达到底层或深度上限时，用提示阶梯进行最小教学。
6. 子层建立后 `return`，再用**新问法**重问被中断问题。

默认最大下钻深度 4，单次下钻预算 5 轮。越过预算不是“更严谨”，而是跑题。

### 4. 掌握状态由证据推导，不靠印象

一次答对不等于掌握。`mastered` 只能由引擎闸门产生，至少需要：

- `explain_back:pass`
- `why:pass`
- `boundary:pass` 或 `counterexample:pass`
- **两次新颖、无提示、不同场景的 `transfer:pass`**
- 没有未关闭的 misconception

事实型节点最多到 `understood`。容器节点只聚合子节点，不独立认证为 mastered。

### 5. 每次下钻都必须真正返回

`drill` 会把被中断问题压入 `return_stack`。`return` 只能逐层弹栈，不能跳层。

返回后必须读取 `original_question_id`，下一问使用：

```bash
python3 "$SKILL_ROOT/scripts/mi.py" --root "$OBJECT_ROOT" ask \
  --node <parent> --reprobe-of <original_question_id> \
  --kind <kind> --text "一个真正不同的新问法"
```

只回到父节点但不重问原能力点，视为没有完成回溯。

### 6. 状态机是事实源；不要绕过拒绝

`scripts/mi.py` 强制执行一题一轮、证据锚定、提示阶梯、掌握闸门、返回栈、事务恢复和并发锁。

- exit `0`：成功。
- exit `2`：输入/IO/工作区问题，修正调用。
- exit `3`：协议违规，读取 `violations` 并改变动作；不要用 `--force` 当常规逃生口。
- 除非用户明确要求展示内部状态，否则把 CLI JSON 当作内部控制面，不原样倾倒给用户。

## 安装路径：禁止写死

运行时把 `SKILL_ROOT` 解析为**当前这份 `SKILL.md` 所在目录**。不要假设 `/var/minis/...`、`~/.skills/...` 或任何固定安装路径。

示意：

```bash
SKILL_ROOT="<directory-containing-this-SKILL.md>"
MI="$SKILL_ROOT/scripts/mi.py"
```

对象根目录记为 `OBJECT_ROOT`。项目/论文文件和状态目录是两个概念；需要共享状态目录时仍应传 `--root "$OBJECT_ROOT"`，让来源定位保持可追溯。

## 新会话入口

### 已有工作区

先做健康检查，再恢复，不要重扫整个对象：

```bash
python3 "$MI" --root "$OBJECT_ROOT" doctor
python3 "$MI" --root "$OBJECT_ROOT" resume
```

- `doctor` 报 source changed：只重新读取变更来源，更新事实后继续；不要全量重扫。
- `doctor` 报结构错误：先修复或 `doctor --repair` 能安全修的派生项，再继续。
- 工作区被锁：不要删除一个仍存活进程的锁，也不要让两个 Agent 同时写同一工作区。

### 第一次使用

先按 `references/scout.md` 做一次浅层读取，再初始化：

```bash
python3 "$MI" --root "$OBJECT_ROOT" init \
  --mode project|modeling|generic --seed project|modeling|generic \
  --objective "用户当前真正想掌握的目标" --gitignore
```

只记录必要来源和粗粒度种子，然后提出一个高信息量主线首问：

```bash
python3 "$MI" --root "$OBJECT_ROOT" ask \
  --node <node> --kind probe --mainline --text "一个问题"
```

## 每轮主循环

按 `references/interrogator.md` 执行，核心顺序是：

1. **区分用户是在回答还是发元指令。** 没有 pending question 时不要凭空写 evidence。
2. 回答题目时执行 `answer`，明确分类：`--evidence KIND:pass|partial|fail`、`--idk`、`--misconception`、`--discover` 或 `--neutral`。
3. 运行 `next`。让引擎决定当前动作类型，不凭感觉横跳。
4. 按动作执行：
   - `probe/review/repair` → 构造一个问题并 `ask`。
   - `drill` → 选择一个最小充分前置并说明 `--reason`。
   - `return` → 逐层返回，再按 `--reprobe-of` 重问。
   - `done` → 做最终 `validate` / 关键节点 audit，不继续制造题目。
5. 给用户的可见输出通常只包含：**一句状态变化 + 一个问题**。

短文本可用 `--text`。多行文本、引号多、代码片段或 shell 容易转义错时，优先：

```bash
python3 "$MI" ... ask --node X --kind why --text-file /tmp/question.txt
python3 "$MI" ... answer --text-file - --evidence why:pass
```

## 提示阶梯

需要教学时按 L1 → L2 → L3，不能跨级：

- **L1**：只给方向或观察点。
- **L2**：给原则/机制，但不直接把目标答案说完。
- **L3**：给最小必要答案，并强制登记 `--recovery-text` 做 explain-back。

如果 L1/L2 时已经没有 pending question，必须同时提供 `--retry-text`，确保提示后仍有一个具体重试任务。新的普通 `ask` 会开启新 attempt，旧提示不能跨题复用。

详见 `references/pedagogy.md`。

## 事实与来源

严格区分：

- `project_fact`：来自用户给的代码、论文、文档、数据；记录 `source add --ref ...`。
- `domain_knowledge`：稳定领域知识。
- `inference`：基于现有证据的推断，必须标成推断。

本地 `project_fact` 会记录文件指纹。恢复时 `doctor` 能发现来源已变。不要为了让追问顺畅编造 API、公式、参数、行号或项目行为。

项目模式看 `references/project-mode.md`；数学建模/论文模式看 `references/modeling-mode.md`。

## 用户元指令

- “为什么问这个？” → `where`，给出简短、可见的原因。
- “看一下我的知识树/进度” → `render` / `status`。
- “这个我到底掌握了吗？” → `audit --node X`，并按 `references/auditor.md` 做语义复核。
- “先跳过” → `skip --node X`；pending question 会被安全放弃并保留 deferred 记录。
- “回主线” → 有 return stack 时逐层 `return`；用户主动中止当前 drill 时可 `return --force`，但仍要重问父层能力点。
- “给我考试” → 说明考试会一次一题，逐题 `ask --exam`，逐题记证据。
- “直接告诉我答案，不要继续问” → 尊重退出：正常回答，不伪造 mastery evidence；只有用户仍选择留在审问模式时才走提示阶梯与 recovery。

完整映射见 `references/state-and-resume.md`。

## 结构维护

读 `references/dynamic-tree.md` 后再做复杂结构操作。原则：

- `node split`：一个节点实际上包含多个可独立验证的能力。
- `node merge`：两个节点经对话证实是同一能力点。
- `node reparent`：修正**包含关系**。
- `edge add` / `drill --prereq-id`：表达**依赖关系**。引用已有前置节点时不会偷偷改它的树父节点。
- 对话新发现优先用 `answer --discover` 或 `drill`，自动留下 provenance。

## 完成条件

结束一个阶段前：

```bash
python3 "$MI" --root "$OBJECT_ROOT" validate
python3 "$MI" --root "$OBJECT_ROOT" doctor
```

“完成”表示：

- 当前目标的关键 blocking 节点已通过闸门，或被明确标为 gap/deferred；
- `return_stack` 没有遗留的悬空下钻；
- 没有未处理的 pending/reprobe 状态；
- `validate` 无违规；
- 用户能明确说出仍不会什么，而不是看到一张被提前填满的知识图。

不要把未探索节点伪装成已掌握，也不要为了“收尾漂亮”降低闸门。

## 按需读取的参考

- `references/scout.md`：第一次浅读对象、建粗种子、选首问。
- `references/interrogator.md`：每轮具体决策和用户交互。
- `references/auditor.md`：掌握声明的独立复核。
- `references/dynamic-tree.md`：节点、边、结构变更。
- `references/question-engine.md`：`next`、问题类型、IDK 下钻策略。
- `references/mastery-gate.md`：证据、迁移、衰退、误解。
- `references/pedagogy.md`：提示阶梯、微教学、反馈方式。
- `references/state-and-resume.md`：CLI、恢复、诊断、自然语言指令映射。
- `references/project-mode.md`：代码/项目模式。
- `references/modeling-mode.md`：论文/数学建模模式。
- `examples/walkthrough.md`：端到端示例。

不要一次把所有 reference 都加载进上下文，只读当前动作需要的文件。

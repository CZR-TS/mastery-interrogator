# Dynamic Mastery Tree — data model and structural operations

The tree is a working model of **this user's understanding**, not a syllabus of
the subject. It is deliberately incomplete and changes as dialogue produces new
evidence.

Two structures coexist:

- the **tree** expresses containment / decomposition: one concept or capability
  sits inside another;
- the **edge graph** expresses cross-cutting relations such as `depends_on`.
  Prerequisites do not have to be tree children.

Keeping these separate is important: reusing an existing prerequisite must not
silently move its whole subtree under the current node.

## Workspace files

| file | purpose |
|---|---|
| `meta.json` | workspace schema and engine metadata |
| `tree.json` | nodes, parent/children, derived status/mastery |
| `edges.json` | prerequisite and semantic relations |
| `state.json` | current focus, pending/re-probe state, return stack, hints |
| `evidence.json` | question-anchored evidence used by mastery calculation |
| `mistakes.json` | open/closed misconceptions |
| `sources.json` | artifact/domain/inference provenance |
| `questions.jsonl` | append-only question/answer/teach transcript |
| `history.jsonl` | structural/state events |

`maintain` or `doctor --repair` rotates oversized JSONL logs. Normal appends do
not scan the whole log.

## Node schema

Representative node:

```json
{
  "id": "boosting",
  "name": "Boosting",
  "description": "逐步加入弱学习器修正当前误差的机制",
  "kind": "concept",
  "origin": "discovered",
  "discovered_from": {
    "turn": 12,
    "question_id": "q-0012",
    "trigger": "用户无法解释 XGBoost 中后一棵树在修正什么"
  },
  "parent": "model-principle",
  "children": [],
  "importance": 4,
  "blocking": true,
  "tags": ["model"],
  "sources": [],
  "is_container": false,
  "manual_status": null,
  "asked": 2,
  "idks": 1,
  "fails": 0,
  "mastery": 0.38,
  "status": "partially_understood",
  "gate": "open",
  "gate_missing": ["why: ...", "transfer: 0/2 ..."]
}
```

### Origin

- `seed` — coarse hypothesis created at initialization;
- `manual` — deliberate human/agent organization not claimed to be discovered
  from a user's answer;
- `discovered` — created by dialogue and therefore requires `discovered_from`;
- `split` / `merged` — structural curation provenance.

Use `answer --discover` or `drill` for dialogue discoveries because they attach
turn/question provenance automatically. Direct `node add` defaults to `manual`.

## Derived status

Statuses are reports, not knobs:

- `unexplored` — no question/evidence yet;
- `probing` — a question on the node is pending;
- `gap` — it has been asked but no positive evidence is established;
- `learning` — it was just taught and still needs user production;
- `partially_understood` — some positive evidence exists but evidence is still
  incomplete;
- `understood` — evidence score is strong enough for practical understanding,
  but the full mastery gate has not passed;
- `mastered` — the full gate passes;
- `misconception` — an open misconception exists;
- `needs_review` — explicit/due review state;
- `deferred` — user asked to skip it.

Do not infer gate semantics from a numeric status threshold. `mastered` is the
only certification and is governed by `references/mastery-gate.md`.

A `fact` node cannot be mastered because there is no meaningful transfer gate.
A container created by `split` aggregates its children and is never independently
certified mastered; when all children are understood/mastered it reports
`understood`.

## Structural commands

Examples omit the common `--root` for readability:

```bash
mi.py node add --name "A deliberate category" --parent root
mi.py node split --id model-principle --into "assumptions,objective,optimization"
mi.py node merge --id duplicate --into canonical --reason "same capability"
mi.py node reparent --id child --parent better-parent --reason "containment was wrong"
mi.py node remove --id irrelevant --reason "not part of current goal"
mi.py edge add --from A --to B --type depends_on --strength required --reason "..."
```

Use each operation for a distinct reason:

- **add**: manual organization that is useful but not a dialogue discovery;
- **split**: one node cannot be tested with one coherent question;
- **merge**: two nodes repeatedly measure the same capability;
- **reparent**: the containment/decomposition hierarchy is wrong;
- **remove**: duplicate, false, or out of scope;
- **edge `depends_on`**: understanding one capability genuinely requires another.

### Drilling into a prerequisite

`drill` is both traversal and dependency recording:

```bash
mi.py drill --node xgboost --prereq "Boosting" \
  --reason "without Boosting the user cannot explain what successive trees correct"
```

For a new prerequisite, the engine creates a dialogue-provenance node. For an
existing prerequisite:

```bash
mi.py drill --node A --prereq-id B --reason "B blocks A"
```

The engine adds/updates `A depends_on B` and moves focus to B **without
re-parenting B**. This preserves the tree as containment rather than turning it
into a history of traversal.

Every drill also pushes the exact interrupted question onto `return_stack`.
Nested frames unwind one level at a time.

## Growth rule

Create depth only when dialogue forces it:

> A deep node should exist because the user exposed a gap, named a concept that
> now matters, or a current node proved too broad to test coherently.

The seed tree is allowed to contain coarse `seed` hypotheses without
`discovered_from`; deep `discovered` nodes are not.

Examples of healthy growth:

```text
模型选择的理由
  user: “随机森林是 AI 选的，我不知道为什么”
  -> discover/drill: 偏差与方差

偏差与方差
  user: “方差我也说不清”
  -> drill: 过拟合
```

Examples of unhealthy growth:

- generating `随机森林 → Bagging → bootstrap → probability → set theory` before
  the user has answered anything;
- adding every framework dependency from a manifest as a mastery node;
- using `reparent` merely because a prerequisite was visited from another node;
- keeping a node only because it appeared in the original seed after the object
  shows it is irrelevant.

A good tree is curated: it grows, splits, merges, moves and deletes. “More nodes”
is not a quality metric.

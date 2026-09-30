# Mastery gate, evidence, decay

Mastery is a claim about a person, and the only honest inputs are things that
person produced. So mastery is computed from `evidence.json`, and the gate is
enforced by the script, not by your judgement in the moment.

## Evidence kinds

| kind | what the user actually did |
|---|---|
| `recall` | reproduced a fact, value, or name |
| `explain_back` | explained it in their own words, no notes, no jargon they cannot unpack |
| `why` | gave the causal mechanism, not the label or the formula |
| `boundary` | said where it stops being true / what it does not cover |
| `counterexample` | produced or recognised an instance that breaks it |
| `compare` | distinguished it from a neighbouring concept on a relevant axis |
| `debug` | found the flaw in a wrong solution or a wrong claim |
| `modify` | predicted what happens when a condition changes |
| `connect` | tied it to its parent/neighbours in the tree |
| `transfer` | solved a *novel* instance of the same principle |

`--hinted` (or a recent stronger hint recorded by the current attempt) discounts the
record: a hinted win counts half, and **never** counts toward the transfer
requirement.

## Scoring

```
weight = {recall .15, explain_back .25, why .20, boundary .15, counterexample .15,
          compare .10, connect .10, debug .10, modify .10}   (best result per kind)
raw    = Σ weight × (pass=1.0 | partial=0.5, halved when hinted)
score  = raw / 1.30
mastery= score + 0.15 × min(#unprompted novel transfers, 2)     (capped at 1.0)
```

A single non-transfer evidence item contributes at most about 0.19 to the normalized score.
More importantly, **no single answer can satisfy the gate**: mastery requires distinct evidence axes and two novel unprompted transfers.

## The gate

`mastered` requires **all** of:

1. `explain_back` passed — in their own words;
2. `why` passed — the mechanism;
3. `boundary` or `counterexample` passed — they know where it fails;
4. **two `transfer` passes, each `novel` and `unprompted`**;
5. no open misconception on the node;
6. `kind != fact` (a bare constant has nothing to transfer; it caps at
   `understood`).

`mi.py audit --node X` prints the verdict and the missing items. `mi.py
validate` fails the workspace if any node claims `mastered` without the gate —
including a node you edited by hand.

### What counts as a novel transfer

* **Novel** = different surface: new numbers, new context, new direction, new
  representation. Re-asking a worked example tests recall of an answer.
* **Unprompted** = no hint ladder was used to get there.
* **Correct** = right answer *and* right reason.

The script checks novelty by comparing the new transfer question against every
question previously asked on that node (fuzzy match): a re-worded repeat is
recorded with `novel: false` and refused by the gate.

### How to construct a transfer

Change exactly one surface dimension, hold the mechanism fixed:

| vary | example (node: 随机森林降低方差) |
|---|---|
| context | 从房价预测换到故障分类 |
| direction | 从"更多树会怎样"换到"只剩一棵树会怎样" |
| regime | 从常规样本量换到样本极少的情形 |
| representation | 从文字描述换到一张新画的图 / 一段新代码 |

## Decay

Mastery is not permanent, and the tree must be allowed to say so.

* **Spaced review.** The moment a node becomes `mastered`, a review is
  scheduled at 1 day; `mi.py review --mark NODE` advances the interval
  1d → 1w → 1m → 3m. When a due date passes and the node was understood, its
  status becomes `needs_review` and the engine prefers it over new material.
* **Contradiction.** If a later answer contradicts a mastered node:
  `mi.py challenge --node X --note "..." [--misconception "the wrong belief"]`.
  With `--misconception` the node drops to `misconception`; without it, to
  `needs_review`. `mastered_at` is cleared, the event is logged, and the audit
  blocks until the node is re-established.
* **Re-establishment** is the same cheap path as first time: new evidence, gate
  re-checked. Correcting a logged misconception (`answer --corrected m001`)
  closes it and lets the node recover.

## The mistake / misconception log

`mistakes.json` keeps what the user got wrong and *why*, which is usually more
valuable than what they got right. Record a misconception only when you can
state the belief in the user's own terms:

```json
{ "id": "m001", "node": "gradient-descent",
  "belief": "梯度就是下降最快的方向",
  "corrected": false, "recheck_required": true,
  "source_turn": 12, "note": "用户在自己举的例子中把方向与负梯度混为一谈" }
```

Rules:

* **Never invent a psychological explanation.** If the user says "I don't know
  why I said that", the record says that.
* **A misconception outlives the turn it appeared in.** It is rechecked later —
  `recheck_required` means "ask about this again in a couple of sessions".
* **Repair is not correction.** Do not answer the misconception; build a
  concrete case whose consequence contradicts the belief and let the user
  notice (see `pedagogy.md`, elenchus). Only then close it.

## Reporting the verdict to the user

Report against evidence, never with encouragement:

> "你已经能解释它的作用，也说了它在哪里不成立；但还没说明为什么这里不能换成普通线性回归，
> 所以这个节点暂时是 partially_understood，还差一次迁移验证。"

`mi.py audit --node X` output is already in that voice — quote it rather than
softening it.

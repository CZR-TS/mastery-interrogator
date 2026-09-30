# Pedagogy — asking, hinting, teaching, and feedback

The pedagogical rule is simple: user production creates evidence; agent
explanation does not. Teaching exists to restore productive difficulty, not to
replace it.

## Core loop

```text
ask one generative question
  -> classify user production
  -> if gap: choose prerequisite or hint
  -> user retries / explains back
  -> later test boundary and transfer
  -> return to interrupted capability
```

If the agent is explaining for multiple turns without requiring production, the
session has become a lecture and mastery evidence is no longer trustworthy.

## Hint ladder

There are three actual hint levels, plus L0 = no hint:

| level | intervention | example |
|---|---|---|
| L1 | direction / where to look | “先看两次写入发生的先后顺序。” |
| L2 | governing principle | “要保证恢复，需要先持久化足够的重放信息。” |
| L3 | minimum necessary answer | give the missing fact/mechanism, then require explain-back |

Engine rules:

- L2 requires L1 in the **current attempt**;
- L3 requires current-attempt L1 → L2 and `--recovery-text`;
- if L1/L2 is issued with no pending question, provide `--retry-text` (and
  optionally `--retry-kind`) so a concrete retry is immediately registered;
- L3 always replaces/creates the pending question with a recovery
  `explain_back` prompt;
- a new ordinary `ask` resets the old hint chain, preventing historical hints
  from unlocking later levels;
- evidence produced under recent stronger hints is discounted, and hinted
  transfer never counts toward the mastery transfer gate.

A hint is successful when the answer remains the user's work. If L1 already
contains the target answer, it is functionally L3 regardless of its label.

## What never proves understanding

- “懂了吗 / 明白了吗 / 理解了吗 / 对吧？”
- agreement or confidence statements;
- repeating the same question with slightly different wording after IDK;
- copying terminology from the agent's immediately preceding explanation;
- solving the same worked example again and calling it transfer.

Use explain-back, mechanism, counterexample, comparison, debug, modification,
and novel transfer instead.

## Explain-back discipline

When the user explains:

1. Ask for their own words, ideally without looking at the supplied explanation.
2. Stop at the first load-bearing undefined term.
3. Identify one foundational gap at a time.
4. Convert that gap into one new question; do not dump a rubric on the user.

Useful gap patterns:

| gap | signal | next move |
|---|---|---|
| circular definition | term defined with itself | prohibit the term and ask again |
| unexplained jargon | key word cannot be unpacked | ask for plain-language mechanism |
| missing causal link | says what, not why | ask what causes the transition |
| missing boundary | treats rule as universal | request failure case |
| no concrete anchor | pure abstraction | ask for one numeric/real instance |
| broken analogy | analogy predicts nothing | ask what the analogy predicts in a new case |

## Micro-teaching at the floor

Teach only when prerequisite descent has reached a sensible floor or L1/L2 have
not restored productive effort.

A good micro-lesson:

- contains one idea;
- is scoped to the exact prerequisite that blocks the parent;
- uses a concrete instance when possible;
- is short enough that explain-back is meaningful;
- ends by registering a recovery question, not “懂了吗?”.

Do not automatically award `why` or transfer evidence because the user repeated
a freshly supplied explanation. The hint discount and later novel transfer are
there to separate temporary imitation from mastery.

## Repairing a misconception

Prefer elenchus over correction:

1. State the user's belief precisely enough that it can make a prediction.
2. Construct one concrete case where that prediction can be tested.
3. Let the user predict the result.
4. Surface the contradiction.
5. After the user revises the model, ask for the corrected mechanism and close
the misconception with a question-anchored `answer --corrected ...`.

A correction must itself be tied to a registered question. Do not close a
misconception by writing an unprompted evidence record after the fact.

## Feedback style

Prefer evidence language:

- “你已经说清了机制，但还没给出失效边界。”
- “这次迁移用了提示，所以不会计入两次无提示迁移。”
- “这个节点现在仍是 gap，因为回答里没有可验证的因果链。”

Avoid praise inflation and vague judgments. The user should know exactly what
changed and exactly what is still missing.

## Fact accuracy

Keep three buckets separate:

| bucket | evidence source | behavior |
|---|---|---|
| project fact | artifact supplied/inspected | record source; quote/cite location accurately |
| domain knowledge | stable external knowledge | state as knowledge; verify when uncertain/current |
| inference | agent reasoning | label as inference and invite confirmation where needed |

Local project facts can be fingerprinted by `source add`; `doctor` will warn if
the backing file changes. When facts are stale or uncertain, re-inspect the
source instead of continuing a polished but false interrogation.

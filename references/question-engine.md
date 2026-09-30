# Question engine — choosing the one question worth asking

`mi.py next` is the authoritative action selector. Use it after recording the
user's reply and required structure changes. Do not maintain a competing
“mental state machine” in prose.

## Decision order

The engine roughly applies this priority:

1. **wait** — an unanswered question is already pending;
2. **re-probe after return** — the interrupted parent capability must be tested
   again before anything else;
3. **forced return** — the drill budget is exhausted;
4. **repair** — a mainline misconception is open;
5. **inside a drill** — continue to a real unmet prerequisite or return upward;
6. **review** — a due spaced-review item;
7. **free selection** — choose the highest-value eligible node by importance,
   blocking status, mainline relevance, evidence weakness, recency and unmet
   prerequisites.

When a node already has substantial evidence but its mastery gate is still open,
`next` examines the *actual missing axis*. It can request `explain_back`, `why`,
`boundary/counterexample`, or `transfer`; it does not blindly spam transfer
questions merely because the score is high.

Relevant output fields include:

- `action`
- `node` / `node_name`
- `suggested_kind`
- `required_evidence_kind`
- `reprobe_of`
- `prereq_candidates`
- `why[]`

Use them.

## Selection policy

The score favors:

- high-importance nodes;
- `gap`, misconception, review, and freshly taught nodes;
- blocking and mainline nodes;
- low current mastery;
- repeated genuine failure.

It penalizes:

- asking the same node again too soon;
- over-deep branches;
- dependents whose required prerequisites are still unmet.

This keeps the dialogue on load-bearing gaps without bouncing between whatever
concept sounds interesting in the latest message.

## Choosing the question kind

| situation | useful kind | question shape |
|---|---|---|
| unexplored / gap | `probe` | “walk me through what happens” |
| just taught | `explain_back` | reproduce the mechanism in own words |
| mechanism missing | `why` | explain causal chain |
| scope missing | `boundary` / `counterexample` | say where it fails |
| decision node | `compare` | why X rather than Y |
| wrong belief | `debug` / `counterexample` | predict consequence of the belief |
| review | `recall` then mechanism | retrieve before re-teaching |
| gate needs transfer | `transfer` | same principle, genuinely new surface case |

One answer may demonstrate more than the question's nominal kind, but only
record evidence the user's actual content supports.

## IDK and prerequisite drill

IDK means “locate the smallest level I can stand on,” not “give me the answer.”

Protocol:

1. `answer --idk`.
2. `next`.
3. If action is `drill`, choose one prerequisite that actually blocks the
current question.
4. `drill --reason` must articulate that dependency.
5. Ask one question at the prerequisite.
6. Repeat only when the new answer proves another lower prerequisite is needed.
7. At depth/budget floor, use the hint ladder instead of manufacturing a deeper
academic chain.
8. Establish something on the child, `return`, then re-probe the exact parent
capability with `--reprobe-of` and a genuinely different formulation.

### Anchoring requirement

A drill must be anchored to a real question on the current focus. The engine
uses the pending question, the immediately last answered question on that node,
or the mainline opening question. Off-focus and unanchored drills are refused
unless an explicit exceptional `--force` is used.

This prevents a common failure where the agent sees a related concept and starts
a tangent that was never caused by the user's answer.

## Cognitive-load controls

- One primary question per turn, including exam mode.
- One current node visible unless the user asks for the map.
- Default max drill depth: 4.
- Default drill budget: 5 turns.
- A hint should shrink the problem, not reveal five new concepts.
- After a long drill, visible output should be one state sentence and the
re-probed parent question, not a recap lecture.

## Diagnosing bad questions

| symptom | likely issue | correction |
|---|---|---|
| repeated IDK | question is above the user's current level | drill one real prerequisite or use a concrete instance |
| fluent but vague answer | recognition/jargon rather than production | ask mechanism, boundary, debug or transfer |
| repeated same-level wording after IDK | no information gain | descend or hint; `validate` can warn on near-repeat |
| session drifts sideways | traversal not anchored | inspect `where`, `return_stack`, and opening question |
| every node becomes “blocking” | score has been gamed | reserve blocking for true prerequisites to the objective |
| agent keeps teaching | it is treating uncertainty as a request for exposition | return to ask → evidence → next |

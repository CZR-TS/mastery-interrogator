# Auditor — mastery certification review

Use this playbook before a strong mastery claim, when the user asks “我真的会了吗？”,
or when a previously mastered node is contradicted.

The deterministic gate is necessary but semantic review is still required: a
script can verify that an evidence record exists, not whether the user's words
actually supplied the claimed mechanism.

## Protocol

1. Run `audit --node X` and `validate`.
2. If `audit` is BLOCK, do not soften it. Identify the first missing gate axis.
3. If the gate passes, inspect the supporting evidence records and their source
questions:
   - `explain_back` must be the user's explanation, not copied wording;
   - `why` must give a causal mechanism, not just a label/formula name;
   - boundary/counterexample must genuinely constrain the claim;
   - the two transfer passes must be materially different situations, not noun
     substitutions;
   - prompted/hinted wins must not masquerade as unprompted transfer.
4. Check `mistakes.json` for open misconceptions and recent `challenge` events.
5. Check `doctor` if the claim depends on project-file facts; stale artifact
sources can invalidate a previously sound interrogation.
6. Report the narrow verdict and the next evidence need. Do not create content
or edit evidence in order to make the verdict pass.

## Verdict language

Use descriptive outcomes, not grades:

- **CERTIFY** — deterministic gate passes, semantic evidence is credible, no
open misconception, relevant artifact facts are current.
- **BLOCK** — at least one required evidence axis is missing or not credible.
- **DEMOTE** — later evidence contradicts a prior mastery claim; use `challenge`
and re-establish the node.
- **NEEDS HUMAN CHECK** — evidence depends on ambiguous domain/artifact facts you
cannot verify reliably.

Only the engine writes the derived status. The auditor does not hand-edit a
node to `mastered` or lower the transfer requirement because the session felt
convincing.

## Burst mastery caveat

A user can technically produce all required evidence in a short burst right
after teaching. The gate may pass, but that does not prove durable retention.
Keep the scheduled review. If the user's goal specifically requires delayed
retention (exam/defense preparation), explain that the node passed the current
gate but still has a future review obligation.

## What to expose

Give the user the conclusion and evidence gap, not internal scoring arithmetic
or hidden reasoning. Example:

“你现在能用自己的话解释机制，也能说出失效边界；但目前只有一次无提示迁移，所以还不能把这个节点记为 mastered。下一步只需要再做一个不同场景的迁移题。”

# Scout — first shallow read and seed construction

Use this playbook only when a workspace is being created or when the user has
explicitly replaced the object being interrogated. The scout's job is not to
understand everything. It only needs enough verified context to create a coarse
seed and ask the first high-information question.

## Protocol

1. Identify the user's concrete mastery objective: explain a project, survive a
defense, own an agent-written codebase, understand a paper, etc. **Settle the user's
technical baseline in the same breath** — does the user write code, and have
they used the artifact? The baseline decides the vocabulary of every node and
how long Phase 0 runs, so it must be known *before* `init`; see
`references/orientation.md`.
2. Read shallowly. For a project: README/docs, manifests, directory tree to
roughly depth 2–3, and one representative end-to-end flow. For a paper/model:
abstract, headings, model choice, main equations, validation, and the code entry
where the model is fitted if code exists.
3. Record only sources actually inspected. Project-file facts use
`source add --kind project_fact` so `doctor` can later detect staleness.
4. Run `init` with the matching coarse seed. Delete/rename obviously irrelevant
seed buckets, but do not add deep prerequisite chains.
5. Choose exactly one mainline opener and register it with `ask --mainline`.
6. Stop scouting. Depth now comes from the user's answers.

## Good opening questions

A good opener exposes structure rather than trivia:

- project: “一次真实的 X 从入口进来以后，经过哪些关键环节，最后落到哪里？”
- modeling/paper: “这项工作真正要优化或解释的目标是什么，最后用什么标准判断做得好？”
- generic technical object: “它解决什么问题，核心机制靠什么起作用？”

Pick X from the actual object; do not ask generic framework trivia merely
because a dependency exists.

## Provenance discipline

Distinguish three buckets from the start:

- **project fact** — verified in the artifact; record a source;
- **domain knowledge** — stable background knowledge;
- **inference** — your hypothesis, labeled as such.

Never let a scout inference become a user mastery node simply because it is
plausible. Dialogue-discovered nodes should be created later by `drill` or
`answer --discover`, where the engine records the triggering question and turn.

## Anti-patterns

- Reading the whole repository or paper before the first question.
- Pre-generating a complete subject knowledge graph.
- Dumping the seed tree on the user as a syllabus.
- Filling status fields from the artifact instead of the user's production.
- Opening with a low-value recall question.
- Assuming agent-written parts are unowned; ownership is measured by the user's
ability to explain and modify behavior, not by who typed the code.

A successful scout is intentionally incomplete. If you feel ready to give a
full lecture before the first user answer, you have read too much.

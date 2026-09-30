# Workspace layout (created by `mi.py init`)

One directory per object being interrogated. Nothing here is a lecture
artifact: everything is *learner state*.

```
<project>/.mastery/<name>/
  tree.json        nodes: parent/child, status, mastery, importance, origin
  edges.json       depends_on / related / contrasts_with / example_of / applies_in
  state.json       turn, focus, opening_question, return_stack, drill budget, hint log
  evidence.json    per-node evidence records -- the ONLY input to mastery
  mistakes.json    misconceptions (open/closed) with recheck flags
  sources.json     provenance: project_fact | domain_knowledge | inference
  questions.jsonl  append-only Q/A/teach transcript (debugging + resume)
  history.jsonl    append-only structural events (add/split/merge/reparent/decay)
  archive/         rotated logs
  session.md       human-readable digest (rewritten by `export`)
```

`<project>` is the project directory you pointed `--root` at (or that `mi.py`
discovered by walking up from the current directory), so the learner state sits
next to the code / the paper instead of in a hidden global store. Use
`--state-dir DIR` (or `$MASTERY_STATE_DIR`) when the project directory is
read-only or you deliberately want a central location.

## Rules the files encode

* **Status is derived, never declared.** `status` is a function of evidence +
  open misconceptions + review due dates. You cannot write `mastered`; only
  evidence can produce it (`mi.py audit --node X` shows what is missing).
* **Mastery decays.** `review.due_at` lapses into `needs_review`; a
  contradiction (`mi.py challenge`) demotes a mastered node immediately.
* **Every structural change is an event.** `history.jsonl` keeps the reasoning
  trail; `validate` re-checks the invariants at any time.
* **Deleting is a first-class operation.** Nodes that turn out to be duplicates,
  wrong, or unnecessary are removed with a reason, and the event survives.

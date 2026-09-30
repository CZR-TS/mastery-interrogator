# Tests

Run the complete verifier from any directory:

```sh
sh tests/run_tests.sh
```

The suite is intentionally fast enough to run after every state-machine edit. Most
CLI tests call the engine in-process; crash, restart, and lock tests use real child
processes. The current package has **90+ regression/acceptance tests** rather than a
brittle fixed-count promise.

| file | what it protects |
|---|---|
| `test_state_machine.py` | tree growth, structure operations, traversal, persistence, log maintenance |
| `test_rules.py` | one-question rule, hint attempts, mastery gate, decay, validation |
| `test_cases.py` | end-to-end acceptance scenarios for projects and modeling |
| `test_workspace_location.py` | workspace resolution, project-local state, legacy env handling |
| `test_reliability_v2.py` | crash rollback, concurrency, migration, source staleness, archive anchors, multiline input |
| `test_skill_contract.py` | package layout, trigger metadata, references, portability, CLI surface |

## Guarantees exercised

The tests verify that the tree grows from dialogue rather than being fully generated
up front; `I don't know` leads to a prerequisite drill; every drill unwinds through
an anchored re-probe; one correct answer cannot produce mastery; transfer evidence
must be novel/unprompted; exams remain one-question-per-turn; state survives process
restart; concurrent writers are rejected; interrupted multi-file writes roll back;
source changes can be detected; and manual/tampered mastery is caught.

A green suite proves the **protocol and persistence machinery** are internally
consistent. It cannot prove that an LLM's chosen question is pedagogically excellent
or that a semantic evidence label is honest. Use `references/auditor.md` for that
second, model-level review.

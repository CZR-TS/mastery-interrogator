# State, resume, commands, debugging

The deterministic engine owns persistent state. Treat its files as a small local
database: use the CLI to mutate them; do not hand-edit them during normal use.

## Workspace location

Default layout:

```text
<object-root>/.mastery/
├── current
└── <workspace>/
    ├── meta.json
    ├── tree.json
    ├── edges.json
    ├── state.json
    ├── evidence.json
    ├── mistakes.json
    ├── sources.json
    ├── questions.jsonl
    ├── history.jsonl
    ├── session.md
    └── archive/
```

Resolution order:

1. `--state-dir DIR` / `--home DIR`
2. `MASTERY_STATE_DIR`
3. `--root DIR` or `MASTERY_ROOT` → `DIR/.mastery/`
4. nearest ancestor containing `.mastery/`
5. otherwise error; `init` may create a project-local workspace

`MASTERY_HOME` is retired. It is deliberately ignored and reported so old
configuration cannot silently redirect state.

When `--state-dir` is used for shared state, also pass `--root <object-root>` if
project-file sources matter. `state.json.object_root` is what lets `doctor`
resolve and fingerprint those files later.

## Resolve the Skill path, do not hard-code it

At runtime, `SKILL_ROOT` means the directory containing the active `SKILL.md`:

```bash
SKILL_ROOT="<resolved skill directory>"
MI="$SKILL_ROOT/scripts/mi.py"
```

Never assume a particular installation directory.

## First use

```bash
python3 "$MI" --root "$OBJECT_ROOT" init \
  --mode project|modeling|generic \
  --seed project|modeling|generic \
  --objective "..." --gitignore
```

Then record only the sources actually inspected and ask one mainline question.

## Resume an existing workspace

Use both commands once at session entry:

```bash
python3 "$MI" --root "$OBJECT_ROOT" doctor
python3 "$MI" --root "$OBJECT_ROOT" resume
```

`doctor` checks operational health: schema, required files, traversal pointers,
source freshness, temporary files and oversized logs. `resume` increments the
session counter and reconstructs the current focus, return stack, pending thread
and next action. Do not rescan the object just because the chat restarted.

If `doctor` says a source changed, re-read that source before relying on facts
from it. If it reports only stale derived fields, `doctor --repair` can refresh
safe derived state and rotate logs.

## Command protocol

Every command returns JSON and one of three exit classes:

| exit | meaning | response |
|---|---|---|
| `0` | success | continue |
| `2` | usage, IO, missing object/workspace, busy lock | correct the call/environment |
| `3` | protocol violation | read `violations[]`; change the action |

Do not turn `--force` into a generic retry mechanism. It exists for explicit
user-directed exceptions such as abandoning an unresolved drill.

### Questions and answers

```bash
python3 "$MI" --root "$OBJECT_ROOT" ask \
  --node NODE --kind probe --text "one question"

python3 "$MI" --root "$OBJECT_ROOT" answer \
  --text "user answer" --evidence why:pass

python3 "$MI" --root "$OBJECT_ROOT" answer --text "不知道" --idk
```

An answer normally requires a pending registered question. This prevents the
agent from manufacturing evidence after the fact. `--orphan` exists only for
explicit import/legacy repair, and orphan evidence never contributes to mastery.

For multiline text, code, quotes, or shell-sensitive content:

```bash
python3 "$MI" ... ask --node NODE --kind why --text-file /tmp/q.txt
python3 "$MI" ... answer --text-file - --evidence why:pass
```

Exactly one of `--text` and `--text-file` may be used.

### Structure and prerequisites

```bash
python3 "$MI" ... drill --node NODE --prereq "NEW PREREQ" --reason "why it blocks"
python3 "$MI" ... drill --node NODE --prereq-id EXISTING_ID --reason "why it blocks"
python3 "$MI" ... node split --id NODE --into "part A,part B"
python3 "$MI" ... node merge --id DUP --into CANONICAL --reason "same concept"
python3 "$MI" ... node reparent --id CHILD --parent PARENT --reason "containment fix"
python3 "$MI" ... edge add --from A --to B --type depends_on --strength required --reason "..."
```

Containment and prerequisites are separate. `--prereq-id` creates/updates the
dependency edge and moves focus to the existing prerequisite; it does **not**
re-parent that existing concept in the tree.

Direct `node add` is `origin=manual` by default. Dialogue-discovered nodes should
normally be created by `answer --discover` or `drill`, which attach provenance
automatically.

### Return and re-probe

```bash
python3 "$MI" ... return
```

The result includes `original_question_id`. The next parent-level question must
be a genuinely new formulation and must reference the interrupted question:

```bash
python3 "$MI" ... ask --node PARENT --reprobe-of q-0012 \
  --kind why --text "new formulation of the same capability"
```

Frames cannot be skipped. Nested drills unwind one level at a time.

### Hint ladder

```bash
# If a question is still pending, L1/L2 attach to it.
python3 "$MI" ... teach --node NODE --level 1 --text "pointer" --stuck-point key
python3 "$MI" ... teach --node NODE --level 2 --text "principle" --stuck-point key

# If no question is pending, L1/L2 must create a concrete retry.
python3 "$MI" ... teach --node NODE --level 1 --text "pointer" \
  --retry-text "retry question" --retry-kind probe --stuck-point key

# L3 always creates a mandatory explain-back question.
python3 "$MI" ... teach --node NODE --level 3 --text "minimal answer" \
  --recovery-text "explain it back in your own words" --stuck-point key
```

Hint chains belong to the current attempt. A new ordinary `ask` clears the old
chain, so yesterday's L1 cannot unlock today's L3.

### Decision, visibility, audit

```bash
python3 "$MI" ... next
python3 "$MI" ... where
python3 "$MI" ... status
python3 "$MI" ... render [--full]
python3 "$MI" ... audit --node NODE
python3 "$MI" ... validate
python3 "$MI" ... doctor [--repair]
```

`next` is the action selector. Use its `action`, `node`, `suggested_kind`,
`required_evidence_kind`, `reprobe_of`, and `why[]` fields rather than inventing
a parallel state machine in prose.

`validate` checks semantic/structural invariants such as unsupported mastery,
dangling/cyclic tree links, unanchored evidence, duplicate dependency edges,
return-stack integrity and illegal hint sequences.

`doctor` checks operational integrity and source freshness. Run both before a
final “everything is clean” claim.

## Crash recovery and concurrency

Each command is a transaction across all canonical workspace files. Before the
first mutation the engine creates a `.mi-txn` recovery journal. Normal success
commits by removing it; a rule failure rolls back immediately; a hard process
crash leaves the journal so the next open restores the pre-command files and
truncates append-only logs to their previous sizes.

A `.mi.lock` file prevents concurrent writers. Never manually delete a lock
owned by a live process. A dead-process lock is detected and reclaimed. If the
CLI reports `workspace is busy`, stop launching parallel mutations against that
workspace.

Workspace schema is recorded in `meta.json`. v1 workspaces migrate losslessly to
schema 2 on open. A future/newer schema is refused rather than guessed at.

## Sources

```bash
python3 "$MI" ... source add --ref "src/x.py#L10-L30" --kind project_fact --node NODE
python3 "$MI" ... source add --ref "standard theorem" --kind domain_knowledge
python3 "$MI" ... source add --ref "reasoning note" --kind inference
```

Local `project_fact` files record size, modification time, and SHA-256. `doctor`
warns if they disappear or change, including same-size changes whose timestamps
were preserved.

## Natural-language user commands

| user intent | engine action |
|---|---|
| “为什么问这个？” | `where` |
| “看树/看进度” | `render` / `status` |
| “这个我掌握了吗？” | `audit --node X` + auditor semantic check |
| “先跳过这个” | `skip --node X`; outstanding question on X is safely abandoned |
| “回到主线” | `return`; use `return --force` only if user explicitly abandons unresolved child |
| “重新考我 X” | select X, ask one new item; use `--exam` if this is an exam session |
| “给我一次考试” | decide a short sequence internally, then ask **one exam item per turn** |
| “暂停” | stop asking; state is already persisted |
| “继续” | `doctor`, `resume`, then `next` |
| “导出” | `export --format md|json [--out PATH]` |

## Maintenance

```bash
python3 "$MI" ... log --tail 40
python3 "$MI" ... export --format md --out mastery-map.md
python3 "$MI" ... maintain
python3 "$MI" ... projects
```

Log rotation is explicit (`maintain` or `doctor --repair`) so normal per-turn
appends stay O(1) instead of rereading an ever-growing JSONL file.

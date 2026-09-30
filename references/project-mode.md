# Project mode — interrogating a software project

Goal: the user ends up able to explain, justify, defend and modify the project —
including the parts a coding agent wrote for them.

Note the inversion against `m4vic/socratic`: that Skill interrogates the *design*
and asks the user only for authority decisions. Here the questions go to the
**user**, because the object of measurement is the user's own understanding. You
may still read the code to answer your own questions — but never let a
project-inspection answer fill a gap that was supposed to be the user's.

## Step 1 — scout before the first question (once)

Read, in this order, and keep it shallow:

1. `README` / docs / entry points — what is this for, who uses it
2. the build/manifest files — language, frameworks, dependencies, versions
3. the directory tree at depth 2–3 — where the moving parts are
4. one representative end-to-end path (a request, a command, a job)
5. the diff, if the user is asking about a change

Then build the seed with `mi.py init --mode project --seed project` and record
what you read:

```bash
mi.py source add --ref "README.md" --kind project_fact --note "项目目标与运行方式"
mi.py source add --ref "src/main/java/..." --kind project_fact
```

Rules:

* **The seed tree is a hypothesis.** Say so once, in one line, and move on.
* **Do not read the whole codebase.** You need enough to ask the *first* good
  question, not a mental model. Depth comes from the dialogue.
* **Never open with a quiz on the seed tree.** Open with the question whose
  answer tells you the most: usually "walk me through what happens when X".

## Step 2 — dynamic domain detection

Start with two mandatory domains: **purpose/scope** and **the main flow**. Then
add a domain only when the project or the dialogue shows its signal. Re-run the
scan after each answer: if the answer mentions OAuth, add an authentication
branch; if it mentions a queue, add one.

| signal in project or in the user's answer | domain to interrogate |
|---|---|
| HTTP handlers, controllers, components, pages | frontend / presentation |
| services, jobs, queues, domain logic | backend / business logic |
| database, schema, ORM, cache, migrations | data & storage |
| endpoints, SDKs, webhooks, integrations, OAuth | API & contracts |
| auth, permissions, secrets, external input, public surface | security |
| Dockerfile, CI/CD, cloud, scaling | infrastructure & deployment |
| cron, metrics, logging, alerts | operations & observability |
| tests, fixtures, mocks, flakiness | testing & quality |
| LLM calls, prompts, RAG, tool use | AI/LLM layer |
| mobile, offline, permissions, store release | mobile |
| CLI, error messages, onboarding, user-visible states | product/UX |
| latency budgets, indexes, token cost, cloud bill | performance & cost |
| personal/regulated data, licensing, compliance | compliance |
| long-lived, team-owned, handover risk | maintenance |
| **absence** of a domain (no tests, no auth, no migration story) | ask why it is absent — usually the sharpest question available |

Each domain is a **question source**, not a checklist to run. Pick the one or two
questions per domain that this project's actual behaviour makes load-bearing.
A project with no database must never be asked database questions.

## Question shapes that work in project mode

| what you want to know | ask |
|---|---|
| does the user own the mental model of the flow | "请求进来之后经过哪些环节，最后写进了哪里？" |
| module boundaries | "这个模块为什么不直接调用那个模块，中间这层的存在是为了什么？" |
| design justification | "为什么这里用 X 而不是 Y？当时的约束是什么？" |
| data model reasoning | "这张表为什么这么设计？如果并发写会怎样？" |
| failure behaviour | "依赖挂了会怎样？这个重试是幂等的吗？" |
| security surface | "谁有权做这件事？这个判断写在哪里？" |
| testing judgement | "这些测试为什么测这个而不测那个？" |
| deployment reality | "改一行代码到线上生效，中间发生了什么？" |
| **AI-written parts** | "这段是别人/Agent 写的——但跑起来之后，数据在这里变成了什么？" |
| limits | "如果量级涨十倍，第一处会坏在哪？" |

Note the last-but-one row: never ask "is this yours?". Ownership is irrelevant;
**behavioural understanding is the whole question.** A user who can trace the
data through an agent-written module owns it; a user who wrote it by hand but
cannot explain it does not.

## Common gap patterns (where to expect `gap`)

* the framework does the work and the user does not know how (DI, ORM magic,
  serialization, middleware order);
* the "glue" between components (who calls whom, in what order, with what data);
* anything that only runs in production (config precedence, migrations, deploy);
* anything the user copied from a tutorial or an agent;
* concurrency, idempotency, retries, and timeouts;
* the *reasons* — the user knows what the code does but not why it is this way.

These are where the first real `drill`s usually happen.

## What "done" looks like in project mode

The user can, without notes:

* describe the system's purpose and its main flow in their own words;
* explain the container/dependency structure and why those boundaries exist;
* trace one non-trivial request end to end, including failure paths;
* justify the three or four decisions that carry the design, and say what the
  alternative would have cost;
* identify what they still do not know, and where they would look.

Leave the rest unexplored, honestly marked.

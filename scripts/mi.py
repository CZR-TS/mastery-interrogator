#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mi.py -- Mastery Interrogator state machine (v2.0.0).

Single source of truth for a Dynamic Mastery Tree.

The agent is REQUIRED to drive this script every turn. The script -- not the
model's narrative -- owns:

  * node status and mastery scores   (derived from evidence, never declared)
  * the mastery gate                 (a node cannot be marked mastered by fiat)
  * the traversal/return stack       (so a drill always comes back)
  * hint-ladder legality             (no premature bottom-out, no same-level retry)
  * the one-question rule            (multi-question dumps and "got it?" are refused)
  * structural invariants            (validate) and the mastery audit (audit)

Every command writes JSON to stdout. Exit codes:
  0 ok     2 usage/IO error     3 rule violation (read "violations")

State follows the project. Default location: <project>/.mastery/
  <project>/.mastery/current     last used workspace name in this project
  <project>/.mastery/<project>/
      tree.json        nodes + parent/child structure
      edges.json       dependency / relation graph
      state.json       traversal state: focus, opening question, return stack
      evidence.json    per-node evidence records (the mastery gate reads this)
      mistakes.json    misconceptions + mistake log
      sources.json     where facts came from (project_fact | domain | inference)
      questions.jsonl  append-only Q/A transcript (debug + resume)
      history.jsonl    append-only structural + state change events
      archive/         rotated logs
      session.md       rendered human-readable digest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path

VERSION = "2.0.0"
WORKSPACE_SCHEMA = 2
LOCK_TIMEOUT_SECONDS = float(os.environ.get("MI_LOCK_TIMEOUT", "3.0"))

_ACTIVE_STORES = []

# ---------------------------------------------------------------- constants

STATUSES = [
    "unexplored", "probing", "gap", "learning", "partially_understood",
    "understood", "mastered", "misconception", "needs_review", "deferred",
]

# Evidence kinds -- how the user proved something, not how it felt.
EVIDENCE_KINDS = [
    "recall", "explain_back", "why", "boundary", "counterexample",
    "compare", "debug", "modify", "connect", "transfer",
]

# Weight of each kind toward the mastery score. Only the BEST result per kind
# counts (an old failure is not punished forever), except `transfer`, where the
# two best unprompted passes count separately.
EVIDENCE_WEIGHT = {
    "recall": 0.15, "explain_back": 0.25, "why": 0.20, "boundary": 0.15,
    "counterexample": 0.15, "compare": 0.10, "connect": 0.10,
    "debug": 0.10, "modify": 0.10,
}
EVIDENCE_MAX = sum(EVIDENCE_WEIGHT.values())  # 1.30

EDGE_TYPES = ["depends_on", "related", "contrasts_with", "example_of", "applies_in"]
NODE_KINDS = ["concept", "decision", "method", "artifact", "fact", "practice"]

# Questions that cannot prove understanding. Refused by `ask`.
FORBIDDEN_PATTERNS = [
    r"懂了吗", r"懂了么", r"懂了没", r"明白了吗", r"明白了么", r"理解了吗",
    r"理解了么", r"清楚了吗", r"对不对[？?]", r"是吧[？?]", r"对吧[？?]",
    r"do you understand", r"does (that|this) make sense", r"got it\?",
    r"make sense\?", r"am i clear\?",
]

# Markers that betray a multi-question dump when combined with >=2 question marks.
LIST_MARKERS = [r"^\s*\d+[.)、]", r"^\s*[-*]\s", r"^\s*[①②③④⑤⑥⑦⑧⑨⑩]"]

DEFAULT_DRILL_BUDGET = 5     # turns allowed inside one drill before forced return
DEFAULT_MAX_DRILL_DEPTH = 4  # hard floor: stop descending, teach instead
REVIEW_INTERVALS = ["1d", "1w", "1m", "3m"]
LOG_MAX_LINES = 4000
LOG_KEEP_LINES = 800

SKILL_DIR = Path(__file__).resolve().parent.parent

# Where the state lives. The default is now PROJECT-LOCAL: <project>/.mastery/.
# A shared directory is only used when the caller asks for one explicitly
# (--state-dir or MASTERY_STATE_DIR). There is no silent global fallback: if no
# workspace can be located, mi.py fails with the exact fix.
STATE_DIR_NAME = ".mastery"

# Markers that identify "this directory is the project".
PROJECT_MARKERS = [
    ".git", "pyproject.toml", "package.json", "pom.xml", "build.gradle",
    "build.gradle.kts", "go.mod", "Cargo.toml", "CMakeLists.txt", "Makefile",
    "requirements.txt", "composer.json", "Gemfile", "mix.exs", "pubspec.yaml",
]


# ---------------------------------------------------------------- utilities

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_ts(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def find_state_dir(start: Path, explicit: str | None = None):
    """Resolve the state root.

    Order: explicit --state-dir/MASTERY_STATE_DIR  ->  <--root>/.mastery  ->
    nearest ancestor of cwd that already holds a .mastery  ->  error (on read)
    or nearest ancestor that looks like a project (on write).

    Returns (state_dir: Path|None, project_dir: Path|None, how: str).
    """
    if explicit:
        d = Path(explicit).expanduser()
        return d, None, "--state-dir / MASTERY_STATE_DIR"
    root = None
    here = Path(start).resolve()
    d = Path(os.environ.get("MASTERY_ROOT", "")).expanduser() if os.environ.get("MASTERY_ROOT") else None
    if d:
        return d / STATE_DIR_NAME, d, "$MASTERY_ROOT"
    for cand in [here] + list(here.parents):
        if (cand / STATE_DIR_NAME).is_dir():
            return cand / STATE_DIR_NAME, cand, "found %s up the tree" % STATE_DIR_NAME
    return None, None, ""


def guess_project_dir(start: Path):
    """For `init`: where should a brand-new .mastery go?"""
    here = Path(start).resolve()
    for cand in [here] + list(here.parents):
        if (cand / STATE_DIR_NAME).is_dir():
            return cand, "existing %s" % STATE_DIR_NAME
    for cand in [here] + list(here.parents):
        for m in PROJECT_MARKERS:
            if (cand / m).exists():
                return cand, "project marker %s" % m
    return here, "current directory (no marker found)"


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff]+", "-", (name or "").strip(), flags=re.UNICODE)
    s = s.strip("-").lower()
    return s[:48] or "n"


def norm_text(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", s)


def similar(a: str, b: str, threshold: float = 0.90) -> bool:
    """True when two questions are effectively the same question.

    Length guard: a long question and a short question that share a template are
    not the same question, even if the character ratio looks high.
    """
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) / max(len(a), len(b)) < 0.85:
        return False
    return SequenceMatcher(None, a, b).ratio() >= threshold


def question_marks(text: str) -> int:
    return text.count("?") + text.count("？")


def load_text_arg(inline, file_arg, label="text") -> str:
    """Read user/model text robustly without forcing shell quoting.

    --text-file - reads stdin; otherwise it reads UTF-8 from the supplied path.
    Exactly one of inline/file is allowed.
    """
    if inline is not None and file_arg:
        die("use either --%s or --%s-file, not both" % (label, label))
    if file_arg:
        if file_arg == "-":
            value = sys.stdin.read()
        else:
            try:
                value = Path(file_arg).read_text(encoding="utf-8")
            except Exception as e:
                die("cannot read --%s-file %s: %s" % (label, file_arg, e))
    else:
        value = inline
    if value is None or not str(value).strip():
        die("empty %s (use --%s or --%s-file PATH; PATH may be '-')" % (label, label, label))
    return str(value).strip()


def _finish_active_stores(success: bool):
    """Commit or roll back every Store opened by this command, then release locks."""
    global _ACTIVE_STORES
    stores, _ACTIVE_STORES = list(_ACTIVE_STORES), []
    for store in reversed(stores):
        try:
            if success:
                store.commit()
            else:
                store.rollback()
        finally:
            store.release_lock()


def emit(obj, code: int = 0):
    # Commands are transactions: a rule/usage failure leaves the workspace exactly
    # as it was before the command started. A crash leaves a recovery journal that
    # the next Store open rolls back automatically.
    _finish_active_stores(code == 0)
    obj.setdefault("ok", code == 0)
    obj["version"] = VERSION
    obj["schema"] = WORKSPACE_SCHEMA
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.exit(code)


def fail(violations, message="rule violation", code=3, **extra):
    if isinstance(violations, str):
        violations = [violations]
    out = {"ok": False, "error": message, "violations": violations}
    out.update(extra)
    emit(out, code)


def die(message, code=2):
    emit({"ok": False, "error": message}, code)


# ---------------------------------------------------------------- store

class Store:
    """One workspace directory per project with command-level crash recovery.

    JSON writes are individually atomic, but a command often touches several files.
    The transaction journal below makes the *command* atomic: before the first
    mutation we remember previous file contents/sizes, and a failed/crashed command
    is rolled back on the next open. A lightweight lock prevents concurrent agents
    from racing the same workspace.
    """

    def __init__(self, home: str, project: str):
        self.home = Path(home)
        self.project = project
        self.dir = self.home / project
        self._cache = {}
        self._lock_path = self.dir / ".mi.lock"
        self._lock_owned = False
        self._txn_dir = self.dir / ".mi-txn"
        self._txn_manifest = {"schema": 1, "files": {}, "appends": {}, "created_paths": []}
        self._txn_started = False
        if not self.dir.is_dir():
            die("no such project: %s (%s)" % (project, self.dir))
        self._acquire_lock()
        self._recover_if_needed()
        _ACTIVE_STORES.append(self)
        self._ensure_schema()

    def _ensure_schema(self):
        """Migrate older workspaces conservatively; reject future schemas safely."""
        meta_path = self.path("meta.json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                die("corrupt meta.json: %s" % e)
            schema = int(meta.get("workspace_schema", 1))
            if schema > WORKSPACE_SCHEMA:
                die("workspace schema %d is newer than this engine supports (%d); update the Skill first"
                    % (schema, WORKSPACE_SCHEMA))
            if schema == WORKSPACE_SCHEMA:
                return
        else:
            meta = {"workspace_schema": 1}
            schema = 1

        # v1 -> v2. Old releases used origin=discovered for direct/manual node
        # creation and permitted unanchored evidence. Preserve those records but
        # stop them from masquerading as dialogue-grounded mastery in v2.
        if schema < 2:
            meta.update({
                "workspace_schema": 2,
                "engine_version": VERSION,
                "migrated_at": now_iso(),
            })
            self.write("meta.json", meta)
            if self.path("state.json").exists():
                st = self.read("state.json", {})
                st["workspace_schema"] = 2
                st.setdefault("hint_chain", {})
                st.setdefault("last_answered_question", None)
                self.put_state(st)
            if self.path("tree.json").exists():
                tr = self.read("tree.json", {})
                tr["workspace_schema"] = 2
                for n in tr.get("nodes", {}).values():
                    if n.get("origin") == "discovered":
                        df = n.get("discovered_from")
                        if not isinstance(df, dict) or not df.get("question_id") or df.get("turn") is None or not df.get("trigger"):
                            n["origin"] = "manual"
                            n["discovered_from"] = None
                self.put_tree(tr)
            if self.path("evidence.json").exists():
                evidence = self.read("evidence.json", {})
                changed = False
                for items in evidence.values():
                    for item in items:
                        if item.get("kind") not in ("challenge",) and not item.get("question_id"):
                            item["orphan"] = True
                            changed = True
                if changed:
                    self.put_evidence(evidence)

    # -- locking ----------------------------------------------------------
    def _pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return True

    def _acquire_lock(self):
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        payload = json.dumps({"pid": os.getpid(), "at": now_iso()}, ensure_ascii=False)
        while True:
            try:
                fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                self._lock_owned = True
                return
            except FileExistsError:
                stale = False
                try:
                    data = json.loads(self._lock_path.read_text(encoding="utf-8"))
                    stale = not self._pid_alive(int(data.get("pid") or 0))
                except Exception:
                    try:
                        stale = (time.time() - self._lock_path.stat().st_mtime) > 30
                    except Exception:
                        stale = False
                if stale:
                    try:
                        self._lock_path.unlink()
                        continue
                    except FileNotFoundError:
                        continue
                    except Exception:
                        pass
                if time.monotonic() >= deadline:
                    try:
                        holder = self._lock_path.read_text(encoding="utf-8")[:200]
                    except Exception:
                        holder = "unknown"
                    # This Store is not registered yet, so no rollback is needed.
                    die("workspace is busy: %s (lock=%s). Another agent/process may be using it; "
                        "retry after that command finishes." % (self.dir, holder))
                time.sleep(0.05)

    def release_lock(self):
        if not self._lock_owned:
            return
        try:
            self._lock_path.unlink()
        except FileNotFoundError:
            pass
        self._lock_owned = False

    # -- transaction journal --------------------------------------------
    def _manifest_path(self):
        return self._txn_dir / "manifest.json"

    def _save_manifest(self):
        self._txn_dir.mkdir(parents=True, exist_ok=True)
        p = self._manifest_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._txn_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    def _begin_txn(self):
        if self._txn_started:
            return
        if self._txn_dir.exists():
            # Should have been handled on open; defensive recovery keeps us safe.
            self._recover_if_needed()
        self._txn_manifest = {
            "schema": 1,
            "engine_version": VERSION,
            "pid": os.getpid(),
            "started_at": now_iso(),
            "files": {},
            "appends": {},
            "created_paths": [],
        }
        self._txn_dir.mkdir(parents=True, exist_ok=True)
        self._save_manifest()
        self._txn_started = True

    def _backup_file(self, name: str):
        self._begin_txn()
        if name in self._txn_manifest["files"]:
            return
        p = self.path(name)
        rec = {"existed": p.exists()}
        if p.exists():
            b = self._txn_dir / "backup" / name
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, b)
            rec["backup"] = str(Path("backup") / name)
        self._txn_manifest["files"][name] = rec
        self._save_manifest()

    def _record_append(self, name: str):
        self._begin_txn()
        if name in self._txn_manifest["appends"] or name in self._txn_manifest["files"]:
            return
        p = self.path(name)
        self._txn_manifest["appends"][name] = {
            "existed": p.exists(),
            "size": p.stat().st_size if p.exists() else 0,
        }
        self._save_manifest()


    def _record_created_path(self, path: Path):
        """Record a non-canonical side effect so rollback can remove it."""
        self._begin_txn()
        try:
            rel = str(path.relative_to(self.dir))
        except ValueError:
            return
        created = self._txn_manifest.setdefault("created_paths", [])
        if rel not in created:
            created.append(rel)
            self._save_manifest()

    def _recover_if_needed(self):
        manifest = self._manifest_path()
        if not manifest.exists():
            if self._txn_dir.exists():
                shutil.rmtree(self._txn_dir, ignore_errors=True)
            return
        try:
            m = json.loads(manifest.read_text(encoding="utf-8"))
        except Exception:
            # An unreadable journal is safer to preserve than silently discard.
            # This Store is not registered yet during startup recovery, so release
            # the lock explicitly before exiting (important for embedded callers).
            self.release_lock()
            die("recovery journal is corrupt: %s. Preserve the workspace and inspect this directory manually."
                % self._txn_dir)
        # Restore fully rewritten files.
        for name, rec in m.get("files", {}).items():
            p = self.path(name)
            if rec.get("existed"):
                b = self._txn_dir / rec.get("backup", "")
                if not b.exists():
                    self.release_lock()
                    die("recovery backup missing for %s in %s" % (name, self._txn_dir))
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(b, p)
            else:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
        # Undo append-only log writes by truncating back to the pre-command size.
        for name, rec in m.get("appends", {}).items():
            if name in m.get("files", {}):
                continue
            p = self.path(name)
            if rec.get("existed"):
                if p.exists():
                    with p.open("r+b") as fh:
                        fh.truncate(int(rec.get("size", 0)))
            else:
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
        # Remove side-effect files created during the interrupted command (for
        # example a rotated log archive). Canonical JSON/log restoration above
        # is not enough if these are left behind.
        for rel in reversed(m.get("created_paths", [])):
            cp = self.dir / rel
            try:
                if cp.is_dir():
                    shutil.rmtree(cp)
                else:
                    cp.unlink()
            except FileNotFoundError:
                pass
        shutil.rmtree(self._txn_dir, ignore_errors=True)
        self._cache.clear()

    def commit(self):
        if self._txn_started or self._txn_dir.exists():
            shutil.rmtree(self._txn_dir, ignore_errors=True)
        self._txn_started = False

    def rollback(self):
        if self._txn_started or self._txn_dir.exists():
            self._recover_if_needed()
        self._txn_started = False

    # -- paths
    def path(self, name: str) -> Path:
        return self.dir / name

    # -- json files
    def read(self, name: str, default):
        if name in self._cache:
            return json.loads(json.dumps(self._cache[name]))
        p = self.path(name)
        if not p.exists():
            return json.loads(json.dumps(default))
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            die("corrupt %s: %s" % (name, e))
        self._cache[name] = data
        return json.loads(json.dumps(data))

    def write(self, name: str, data):
        self._backup_file(name)
        self._cache.pop(name, None)
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)

    # -- append-only logs
    def _rotate(self, name: str):
        p = self.path(name)
        if not p.exists():
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) <= LOG_MAX_LINES:
            return
        self._backup_file(name)
        arch = self.dir / "archive"
        arch.mkdir(exist_ok=True)
        stem = name.split(".")[0]
        # Never derive the next archive id from file count: gaps after cleanup
        # would otherwise overwrite a surviving higher-numbered archive.
        used = []
        for q in arch.glob(stem + "-*.jsonl"):
            try:
                used.append(int(q.stem.rsplit("-", 1)[1]))
            except Exception:
                pass
        idx = (max(used) + 1) if used else 1
        keep = lines[-LOG_KEEP_LINES:]
        old = lines[:-LOG_KEEP_LINES]
        archive_file = arch / ("%s-%03d.jsonl" % (stem, idx))
        self._record_created_path(archive_file)
        archive_file.write_text("\n".join(old) + "\n", encoding="utf-8")
        p.write_text("\n".join(keep) + "\n", encoding="utf-8")

    def append(self, name: str, obj: dict):
        self._record_append(name)
        self._cache.pop(name, None)
        p = self.path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        # Rotation is intentionally not performed on every append. Long-session
        # performance used to degrade O(n) because the whole log was reread for
        # each event. `maintain`/`doctor --repair` rotates explicitly instead.

    def records(self, name: str, include_archives: bool = False):
        """Read a JSONL stream, optionally including its rotated archives."""
        paths = []
        if include_archives:
            stem = name.split(".")[0]
            arch = self.dir / "archive"
            if arch.exists():
                def archive_key(q):
                    try:
                        return int(q.stem.rsplit("-", 1)[1])
                    except Exception:
                        return -1
                paths.extend(sorted(arch.glob(stem + "-*.jsonl"), key=archive_key))
        paths.append(self.path(name))
        out = []
        for p in paths:
            if not p.exists():
                continue
            for line_no, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if not ln.strip():
                    continue
                try:
                    out.append(json.loads(ln))
                except Exception:
                    out.append({"_invalid_jsonl": True, "_file": str(p), "_line": line_no})
        return out

    def tail(self, name: str, n: int = 20):
        p = self.path(name)
        if not p.exists():
            return []
        # Bounded reverse-read keeps resume/novelty checks fast even after long sessions.
        block = 8192
        data = b""
        with p.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()
            while pos > 0 and data.count(b"\n") <= n:
                take = min(block, pos)
                pos -= take
                fh.seek(pos)
                data = fh.read(take) + data
        lines = data.decode("utf-8", errors="replace").splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return out

    def event(self, kind: str, **payload):
        rec = {"at": now_iso(), "turn": self.state().get("turn", 0), "event": kind}
        rec.update(payload)
        self.append("history.jsonl", rec)

    # -- convenience loaders
    def tree(self):
        return self.read("tree.json", {"project": self.project, "root": "root", "nodes": {}})

    def state(self):
        return self.read("state.json", {})

    def edges(self):
        return self.read("edges.json", {"edges": []})

    def evidence(self):
        return self.read("evidence.json", {})

    def mistakes(self):
        return self.read("mistakes.json", {"open": [], "closed": []})

    def put_tree(self, t):
        self.write("tree.json", t)

    def put_state(self, s):
        s["updated_at"] = now_iso()
        self.write("state.json", s)

    def put_edges(self, e):
        self.write("edges.json", e)

    def put_evidence(self, e):
        self.write("evidence.json", e)

    def put_mistakes(self, m):
        self.write("mistakes.json", m)


# ---------------------------------------------------------------- tree ops

def get_node(tree, nid):
    return tree["nodes"].get(nid)


def resolve_node(store, key):
    """Find a node by exact id, case-insensitive id, or name. Returns None."""
    if not key:
        return None
    tree = store.tree()
    if key in tree["nodes"]:
        return key
    low = key.strip().lower()
    for nid, n in tree["nodes"].items():
        if nid.lower() == low or (n.get("name") or "").strip().lower() == low:
            return nid
    for nid, n in tree["nodes"].items():
        if low and low in (n.get("name") or "").lower():
            return nid
    return None


def node_depths(tree):
    """Recompute depth for every node from the root."""
    root = tree.get("root")
    seen, stack = set(), [(root, 0)]
    while stack:
        nid, d = stack.pop()
        n = tree["nodes"].get(nid)
        if not n or nid in seen:
            continue
        seen.add(nid)
        n["depth"] = d
        for c in n.get("children", []):
            stack.append((c, d + 1))
    return seen


def unique_id(tree, name):
    base = slugify(name)
    if base not in tree["nodes"]:
        return base
    i = 2
    while "%s-%d" % (base, i) in tree["nodes"]:
        i += 1
    return "%s-%d" % (base, i)


def make_node(nid, name, parent, **kw):
    node = {
        "id": nid,
        "name": name,
        "description": kw.get("description", ""),
        "kind": kw.get("kind", "concept"),
        "origin": kw.get("origin", "seed"),
        "discovered_from": kw.get("discovered_from"),
        "parent": parent,
        "children": [],
        "importance": int(kw.get("importance", 3)),
        "blocking": bool(kw.get("blocking", False)),
        "tags": list(kw.get("tags", [])),
        "sources": list(kw.get("sources", [])),
        "notes": [],
        "is_container": False,
        "manual_status": None,
        "asked": 0,
        "idks": 0,
        "fails": 0,
        "last_asked_turn": None,
        "last_teach_turn": None,
        "mastery": 0.0,
        "status": "unexplored",
        "gate": "open",
        "mastered_at": None,
        "review": None,
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    return node


def attach(tree, child_id, parent_id):
    p = get_node(tree, parent_id)
    if p is None:
        die("parent not found: %s" % parent_id)
    if child_id not in p["children"]:
        p["children"].append(child_id)


def detach(tree, child_id):
    n = get_node(tree, child_id)
    if not n:
        return
    p = get_node(tree, n.get("parent"))
    if p and child_id in p["children"]:
        p["children"].remove(child_id)


def descendants(tree, nid):
    out, stack = [], [nid]
    while stack:
        cur = stack.pop()
        n = get_node(tree, cur)
        if not n:
            continue
        for c in n.get("children", []):
            out.append(c)
            stack.append(c)
    return out


def subtree_nodes(tree, nid):
    return [nid] + descendants(tree, nid)


# ---------------------------------------------------------------- evidence model

def node_evidence(ev, nid):
    return ev.get(nid, [])


def best_by_kind(ev, nid):
    out = {}
    for e in node_evidence(ev, nid):
        if e.get("orphan"):
            continue
        if e.get("result") not in ("pass", "partial"):
            continue
        w = 1.0 if e["result"] == "pass" else 0.5
        if e.get("hinted"):
            w *= 0.5  # a hinted win counts for less -- never for the transfer gate
        k = e["kind"]
        if k == "transfer":
            continue
        out[k] = max(out.get(k, 0.0), w)
    return out


def unprompted_transfers(ev, nid):
    return [e for e in node_evidence(ev, nid)
            if not e.get("orphan") and e["kind"] == "transfer" and e.get("result") == "pass"
            and not e.get("hinted") and e.get("novel", True)]


def open_misconceptions(store, nid):
    m = store.mistakes()
    return [x for x in m["open"] if x["node"] == nid]


def compute_mastery(store, nid):
    ev = store.evidence()
    tree = store.tree()
    node = get_node(tree, nid)
    if not node:
        return 0.0
    b = best_by_kind(ev, nid)
    raw = sum(EVIDENCE_WEIGHT.get(k, 0.0) * v for k, v in b.items())
    score = min(raw / EVIDENCE_MAX, 1.0)
    score += 0.15 * min(len(unprompted_transfers(ev, nid)), 2)
    if open_misconceptions(store, nid):
        score = min(score, 0.40)
    return round(min(score, 1.0), 2)


def gate_status(store, nid):
    """Return (passed, missing[]) for the full mastery gate.

    Mastery needs proof on five different axes and at least two unprompted
    novel transfers. One correct answer is structurally incapable of passing.
    """
    ev = store.evidence()
    tree = store.tree()
    node = get_node(tree, nid) or {}
    b = best_by_kind(ev, nid)
    missing = []
    if b.get("explain_back", 0) < 1.0:
        missing.append("explain_back: explain it in your own words")
    if b.get("why", 0) < 1.0:
        missing.append("why: give the causal mechanism, not the label")
    if max(b.get("boundary", 0), b.get("counterexample", 0)) < 1.0:
        missing.append("boundary/counterexample: say where it stops being true")
    n_tr = len(unprompted_transfers(ev, nid))
    if n_tr < 2:
        missing.append("transfer: %d/2 unprompted passes on unseen cases" % n_tr)
    if open_misconceptions(store, nid):
        missing.append("open misconception must be resolved first")
    if node.get("kind") == "fact":
        missing.append("fact nodes cap at understood (nothing to transfer)")
    return (len(missing) == 0), missing


def derive_status(store, nid, tree=None, mastery=None):
    """Status is derived from evidence, never declared. Returns a string."""
    tree = tree if tree is not None else store.tree()
    state = store.state()
    node = get_node(tree, nid)
    if node is None:
        return "unexplored"
    if node.get("manual_status") == "deferred":
        return "deferred"
    if node.get("manual_status") == "needs_review":
        return "needs_review"
    if open_misconceptions(store, nid):
        return "misconception"
    rv = node.get("review")
    if rv and rv.get("due_at") and node.get("mastery", 0) >= 0.75:
        due = parse_ts(rv["due_at"])
        if due and due <= datetime.now(timezone.utc).astimezone():
            return "needs_review"
    pending = state.get("pending_question")
    if pending and pending.get("node") == nid:
        return "probing"
    if node.get("is_container"):
        kids = [c for c in node.get("children", []) if get_node(tree, c)]
        if kids:
            st = [get_node(tree, c)["status"] for c in kids]
            if any(s in ("gap", "misconception") for s in st):
                return "gap"
            if all(s in ("understood", "mastered") for s in st):
                # Containers aggregate children; they are never independently certified.
                return "understood"
            if any(s in ("understood", "mastered", "partially_understood", "learning") for s in st):
                return "partially_understood"
            return "unexplored"
    passed, _ = gate_status(store, nid)
    m = compute_mastery(store, nid) if mastery is None else mastery
    if passed:
        return "mastered"
    if m >= 0.60:
        return "understood"
    positive = any(e.get("result") in ("pass", "partial")
                   for e in store.evidence().get(nid, []))
    if positive:
        return "partially_understood"
    lt = node.get("last_teach_turn")
    if lt is not None and state.get("turn", 0) - lt <= 2:
        return "learning"
    if node.get("asked", 0) > 0:
        return "gap"
    return "unexplored"


def refresh(store, nid=None):
    """Recompute mastery + status (bottom-up unless nid given) and persist."""
    tree = store.tree()
    node_depths(tree)
    ids = list(tree["nodes"].keys()) if nid is None else subtree_nodes(tree, nid) + [nid]
    order = sorted(set(ids), key=lambda i: -get_node(tree, i).get("depth", 0))
    for i in order:
        n = get_node(tree, i)
        if not n:
            continue
        n["mastery"] = compute_mastery(store, i)
        passed, missing = gate_status(store, i)
        n["gate"] = "passed" if passed else "open"
        n["gate_missing"] = missing
        n["status"] = derive_status(store, i, tree, n["mastery"])
        if n["status"] == "mastered":
            if not n.get("mastered_at"):
                n["mastered_at"] = now_iso()
            if not n.get("review") and not n.get("is_container"):
                n["review"] = {"interval": "1d", "level": 0,
                               "last_reviewed": now_iso(),
                               "due_at": (datetime.now(timezone.utc).astimezone()
                                          + timedelta(days=1)).isoformat(timespec="seconds")}
        else:
            n["mastered_at"] = None
        n["updated_at"] = now_iso()
    store.put_tree(tree)
    return tree


# ---------------------------------------------------------------- rendering

SYMBOL = {
    "mastered": "✓", "understood": "●", "partially_understood": "◐",
    "learning": "◔", "probing": "◌", "gap": "✗", "misconception": "⚠",
    "needs_review": "↻", "deferred": "⏸", "unexplored": "○",
}


def render_tree(store, full=False, max_depth=99):
    tree = store.tree()
    state = store.state()
    lines = []

    def walk(nid, prefix, depth, is_last):
        n = get_node(tree, nid)
        if not n:
            return
        conn = "" if depth == 0 else ("└── " if is_last else "├── ")
        sym = SYMBOL.get(n["status"], "?")
        mark = ""
        if nid == state.get("focus"):
            mark = "   ← 当前"
        if n.get("is_container"):
            mark += "  [容器]"
        lines.append("%s%s%s %s (%.2f)%s" % (prefix, conn, sym, n["name"], n.get("mastery", 0), mark))
        if depth >= max_depth:
            return
        kids = n.get("children", [])
        for i, c in enumerate(kids):
            walk(c, prefix + ("" if depth == 0 else ("    " if is_last else "│   ")),
                 depth + 1, i == len(kids) - 1)

    walk(tree.get("root"), "", 0, True)
    body = "\n".join(lines)

    weak = [n for n in tree["nodes"].values()
            if n["status"] in ("gap", "misconception", "needs_review")
            or (n["status"] == "partially_understood" and n.get("blocking"))]
    weak.sort(key=lambda n: (n["status"] != "misconception", -n["importance"]))
    wlines = ["- %s %s (%s, ×%d 问)" % (SYMBOL.get(n["status"], "?"), n["name"], n["status"], n.get("asked", 0))
              for n in weak[:8]]
    legend = "✓ 掌握  ● 理解  ◐ 部分理解  ◔ 学习中  ◌ 提问中  ✗ 缺口  ⚠ 误解  ↻ 待复习  ⏸ 跳过  ○ 未探索"
    out = {"tree": body, "legend": legend, "weak_nodes": wlines, "counts": status_counts(tree)}
    out["display"] = body + "\n\n" + legend + (
        "\n\n最该补的:\n" + "\n".join(wlines) if wlines else "")
    return out


def status_counts(tree):
    c = {}
    for n in tree["nodes"].values():
        c[n["status"]] = c.get(n["status"], 0) + 1
    return c


# ---------------------------------------------------------------- next-question engine

def unmet_prereqs(store, nid):
    """depends_on edges pointing at nodes that are not yet understood."""
    tree = store.tree()
    ed = store.edges()["edges"]
    out = []
    for e in ed:
        if e["from"] == nid and e["type"] == "depends_on":
            dep = get_node(tree, e["to"])
            if dep and dep["status"] not in ("understood", "mastered", "deferred"):
                if e.get("strength", "required") == "required" or dep["status"] in ("gap", "misconception"):
                    out.append(dep)
    return out


def score_node(store, nid, ctx):
    tree = store.tree()
    n = get_node(tree, nid)
    if not n or n["status"] == "deferred":
        return None
    s, why = 0.0, []

    base = 2.0 * n["importance"] / 5.0
    s += base
    why.append("+%.2f 重要性 %d/5" % (base, n["importance"]))

    if n["status"] == "misconception":
        s += 2.0
        why.append("+2.00 存在未修正的误解")
    elif n["status"] == "gap":
        s += 2.0
        why.append("+2.00 已确认的缺口")
    elif n["status"] == "needs_review":
        s += 1.2
        why.append("+1.20 待复习（含回退风险）")
    elif n["status"] == "learning":
        s += 0.8
        why.append("+0.80 刚讲过，需要复述验证")
    elif n["status"] == "unexplored" and n.get("depth", 0) <= 2:
        s += 0.6
        why.append("+0.60 浅层未探索，信息增益高")

    if n.get("blocking") and n["status"] not in ("understood", "mastered"):
        s += 1.5
        why.append("+1.50 标记为 blocking（卡住上层）")

    if nid in ctx["mainline"]:
        s += 1.5
        why.append("+1.50 在当前主线上")

    weak = (1.0 - n.get("mastery", 0.0))
    s += weak
    why.append("+%.2f 掌握度缺口" % weak)

    if n.get("asked", 0) >= 2 and n["status"] in ("gap", "misconception", "partially_understood"):
        s += 1.0
        why.append("+1.00 反复失败（薄弱点）")

    if n["status"] in ("gap", "misconception", "partially_understood", "learning") \
            and (n.get("last_asked_turn") or -1) >= ctx["turn"] - 1:
        s += 3.0
        why.append("+3.00 刚刚在问这个节点，先把这条线走完（不要换话题）")
    elif nid in ctx["recent"]:
        s -= 1.2
        why.append("-1.20 最近刚问过，避免原地打转")

    if n.get("depth", 0) > ctx["max_depth"]:
        s -= 1.0
        why.append("-1.00 超过下钻预算")

    blocked = unmet_prereqs(store, nid)
    if blocked:
        s -= 3.0
        why.append("-3.00 有未满足的必需前置: %s" % ", ".join(d["name"] for d in blocked))

    return {"node": nid, "name": n["name"], "score": round(s, 2),
            "status": n["status"], "importance": n["importance"],
            "why": why, "blocked_by": [d["id"] for d in blocked]}


def decide_next(store):
    tree = store.tree()
    state = store.state()
    ctx = {
        "mainline": set([state.get("focus")] + [e["node"] for e in (state.get("return_stack") or [])]
                        + [(state.get("opening_question") or {}).get("node")])
        - {None},
        "recent": set(state.get("recent_nodes", [])[-3:]),
        "max_depth": state.get("max_drill_depth", DEFAULT_MAX_DRILL_DEPTH),
        "turn": state.get("turn", 0),
    }

    info = {
        "focus": state.get("focus"),
        "opening_question": state.get("opening_question"),
        "return_stack": (state.get("return_stack") or []),
        "drill": (state.get("drill") or {}),
    }

    # 1. an unanswered question is outstanding -> the ball is in the user's court
    if state.get("pending_question"):
        return dict(info, action="wait", node=state["pending_question"]["node"],
                    why=["已提出问题，等待用户回答"], display="等待回答，不要重复提问。")

    # 1b. we just came back from a drill: re-probe the original question FIRST
    rp = state.get("reprobe_pending")
    if rp and not state.get("pending_question"):
        return dict(info, action="probe", node=rp["node"], reprobe_of=rp.get("of"),
                    why=["刚从一个下钻里回来，必须先重问被中断的原问题（换一种问法，不要复述已讲内容）"],
                    display="回到主线：用新的问法重新问「%s」，用户的回答才算这条主线的证据。"
                            % (rp.get("text") or ""))

    # 2. drill budget exhausted -> the drill MUST come back (no rabbit holes)
    drill = (state.get("drill") or {})
    if drill.get("depth", 0) > 0 and drill.get("turns", 0) >= state.get("drill_budget", DEFAULT_DRILL_BUDGET):
        tgt = state["return_stack"][-1]["node"] if state.get("return_stack") else state.get("focus")
        return dict(info, action="return", node=tgt,
                    why=["下钻已用 %d 轮，达到预算，必须回到上一层的原问题" % drill.get("turns", 0)],
                    display="下钻预算用尽：回到被中断的原问题，并用新问法重新提问。")

    # 3. open misconceptions on the mainline outrank everything
    mitems = [m for m in store.mistakes()["open"] if m["node"] in ctx["mainline"]]
    if mitems:
        return dict(info, action="repair", node=mitems[0]["node"],
                    why=["主线节点上存在未修正的误解：%s" % mitems[0]["belief"]],
                    misconception=mitems[0],
                    display="先做误解修复：不要直接纠正，构造一个能暴露矛盾的具体场景。")

    # 4. inside a drill -> either go deeper or come back, never sideways
    stack = (state.get("return_stack") or [])
    if stack and drill.get("depth", 0) > 0:
        cur = get_node(tree, state.get("focus"))
        if cur and cur["status"] in ("gap", "misconception", "learning"):
            cands = [score_node(store, c, ctx) for c in _drill_candidates(store, cur["id"])]
            cands = [c for c in cands if c]
            cands.sort(key=lambda c: -c["score"])
            if cands:
                return dict(info, action="drill", node=cands[0]["node"], candidates=cands[:3],
                            why=["当前子节点仍未建立（%s），继续下钻到最小充分前置" % cur["name"]],
                            display="继续往下一层：只看一个真正卡住理解的前置概念。")
            if cur.get("idks", 0) >= 1 and drill.get("depth", 0) < ctx["max_depth"] \
                    and not drill.get("floor_node"):
                return dict(info, action="drill", node=cur["id"], candidates=[],
                            prereq_candidates=[], must_not_answer=True,
                            why=["用户在「%s」上说了不知道，但树里没有更深的前置节点" % cur["name"]],
                            display="树里没有更小的前置了：要么用 `drill --node %s --prereq <更小的概念> "
                                    "--reason ...` 再切一层，要么判定已经到底层——用 `teach --level 2/3 "
                                    "--floor` 讲最小必要的一块，然后用 `return` 往回走。" % cur["id"])
        return dict(info, action="return", node=stack[-1]["node"],
                    why=["子节点已达到可用水平（%s）" % (cur["name"] if cur else "?")],
                    display="回到上一层的原问题（换一种问法重新验证，不要复述已讲内容）。")

    # 5. review queue
    now = datetime.now(timezone.utc).astimezone()
    due = [n for n in tree["nodes"].values()
           if n.get("review") and parse_ts(n["review"]["due_at"]) and parse_ts(n["review"]["due_at"]) <= now
           and n["status"] != "deferred"]
    if due:
        due.sort(key=lambda n: parse_ts(n["review"]["due_at"]))
        return dict(info, action="review", node=due[0]["id"],
                    why=["间距复习到期（%s）" % due[0]["review"]["interval"]],
                    display="先做一次旧知识抽查（%s），再回到主线。" % due[0]["name"])

    # 6. otherwise: free choice by score over the whole tree
    cands = [score_node(store, nid, ctx) for nid in tree["nodes"]]
    cands = [c for c in cands if c]
    cands.sort(key=lambda c: -c["score"])
    if not cands:
        return dict(info, action="done", node=None, why=["没有可问的节点"], display="知识树已清空。")
    top = cands[0]
    n = get_node(tree, top["node"])
    extra = {}
    if n["status"] == "gap" and (n.get("idks", 0) > 0 or n.get("fails", 0) > 0):
        pri = [p["id"] for p in unmet_prereqs(store, n["id"])]
        pri = [score_node(store, i, ctx) for i in pri]
        action = "drill"
        extra["prereq_candidates"] = [c for c in pri if c]
        extra["must_not_answer"] = True
        if extra["prereq_candidates"]:
            disp = ("用户在「%s」上卡住了：不要给答案，斜向下钻到最小充分前置（候选见 "
                    "prereq_candidates）。" % n["name"])
        else:
            disp = ("用户在「%s」上卡住了，且树里还没有可用的前置节点：用 "
                    "`mi.py drill --node %s --prereq <前置概念> --reason \"为什么它真的挡住当前目标\"` "
                    "先把这个前置概念挂进树，再单独问它。" % (n["name"], n["id"]))
    elif n.get("gate") == "open" and n.get("mastery", 0) >= 0.35:
        missing = n.get("gate_missing") or []
        if any(x.startswith("explain_back") for x in missing):
            need = "explain_back"
        elif any(x.startswith("why") for x in missing):
            need = "why"
        elif any(x.startswith("boundary/counterexample") for x in missing):
            need = "boundary"
        elif any(x.startswith("transfer") for x in missing):
            need = "transfer"
        else:
            need = None
        if need == "transfer":
            action = "transfer"
            disp = "证据闸门当前缺迁移：给一个真正未见过的新场景，不要重复旧题。"
        elif need:
            action = "probe"
            disp = "证据闸门当前最先缺的是 %s；下一问只补这一项，不要继续刷迁移题。" % need
        else:
            action, disp = "probe", "问当前得分最高的节点。"
        if need:
            extra["required_evidence_kind"] = need
    else:
        action, disp = "probe", "问当前得分最高的节点。"
    return dict(info, action=action, node=top["node"], candidates=cands[:5],
                why=top["why"], display=disp, **extra)


def _drill_candidates(store, nid):
    """What to descend into: unmet prereqs first, then children, then depends_on."""
    tree = store.tree()
    out = [p["id"] for p in unmet_prereqs(store, nid)]
    n = get_node(tree, nid)
    if n:
        out += [c for c in n.get("children", []) if get_node(tree, c)["status"] != "mastered"]
    for e in store.edges()["edges"]:
        if e["from"] == nid and e["type"] == "depends_on" and e["to"] not in out:
            out.append(e["to"])
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- resume / where

def resume_brief(store):
    s = store.state()
    tree = store.tree()
    st = decide_next(store)
    weak = [n for n in tree["nodes"].values()
            if n["status"] in ("gap", "misconception", "needs_review")]
    weak.sort(key=lambda n: (n["status"] != "misconception", -n["importance"], -n["asked"]))
    stack = s.get("return_stack", [])
    lines = []
    lines.append("项目: %s   模式: %s" % (s.get("project"), s.get("mode")))
    lines.append("目标: %s" % s.get("objective"))
    lines.append("轮次: 第 %d 问 | 累计会话 %d 次 | 上次活动 %s"
                 % (s.get("turn", 0), s.get("sessions", 1), s.get("updated_at")))
    oq = s.get("opening_question")
    if oq:
        nm = get_node(tree, oq["node"])
        lines.append("未完成的主线问题: 「%s」(%s)" % (oq["text"], nm["name"] if nm else oq["node"]))
    if stack:
        chain = " → ".join((get_node(tree, e["node"])["name"]
                            if get_node(tree, e["node"]) else e["node"]) for e in stack)
        lines.append("下钻路径(需回溯): %s" % chain)
    cur = s.get("focus")
    if cur and get_node(tree, cur):
        n = get_node(tree, cur)
        lines.append("当前焦点: %s [%s %.2f]" % (n["name"], n["status"], n["mastery"]))
    if weak:
        lines.append("薄弱点: " + "; ".join("%s(%s)" % (n["name"], n["status"]) for n in weak[:6]))
    pend = s.get("pending_question")
    if pend:
        lines.append("上一问未答: 「%s」" % pend["text"])
    lines.append("下一步: %s" % st["display"])
    ctx = [r for r in store.tail("questions.jsonl", 6) if r.get("type") == "answer"]
    if ctx:
        lines.append("最近一次回答: 「%s」" % ctx[-1]["text"][:120])
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI commands

def cmd_init(a):
    home = Path(a.home)
    project_dir = Path(a.object_root) if getattr(a, "object_root", None) else (Path(a.home).parent if a.home_is_local else None)
    if not getattr(a, "project", None):
        base = (project_dir or Path.cwd()).name
        a.project = slugify(base) or "project"
    pdir = home / a.project
    if pdir.exists() and not a.force:
        die("project already exists: %s (use --force to recreate)" % pdir)
    pdir.mkdir(parents=True, exist_ok=True)
    seed_name = a.seed
    seed = {"nodes": []}
    if seed_name and seed_name != "none":
        sp = SKILL_DIR / "templates" / "workspace" / ("seed-%s.json" % seed_name)
        if not sp.exists():
            die("unknown seed: %s" % seed_name)
        seed = json.loads(sp.read_text(encoding="utf-8"))

    tree = {"project": a.project, "mode": a.mode, "root": "root", "workspace_schema": WORKSPACE_SCHEMA, "nodes": {}}
    root = make_node("root", a.objective or a.project, None, kind="artifact",
                     origin="seed", importance=5, description="整个待掌握的对象")
    tree["nodes"]["root"] = root
    for item in seed.get("nodes", []):
        nid = unique_id(tree, item["name"])
        n = make_node(nid, item["name"], "root",
                      description=item.get("description", ""),
                      kind=item.get("kind", "concept"),
                      origin="seed",
                      importance=item.get("importance", 3),
                      blocking=item.get("blocking", False),
                      tags=item.get("tags", []))
        tree["nodes"][nid] = n
        root["children"].append(nid)
    if seed.get("note"):
        root["notes"].append(seed["note"])

    store = Store(a.home, a.project)
    store.put_tree(tree)
    store.write("edges.json", {"edges": []})
    store.write("evidence.json", {})
    store.write("mistakes.json", {"open": [], "closed": []})
    store.write("sources.json", {"sources": []})
    state = {
        "project": a.project, "mode": a.mode, "objective": a.objective or a.project,
        "object_root": str(project_dir) if project_dir else None, "workspace_schema": WORKSPACE_SCHEMA,
        "created_at": now_iso(), "updated_at": now_iso(),
        "turn": 0, "sessions": 1, "focus": "root",
        "opening_question": None, "return_stack": [], "pending_question": None,
        "last_answered_question": None,
        "hint": {}, "hint_log": [], "hint_chain": {},
        "drill": {"depth": 0, "turns": 0, "started_turn": None, "floor_node": None},
        "drill_budget": a.drill_budget, "max_drill_depth": a.max_drill_depth,
        "recent_nodes": [], "notes": [],
    }
    store.put_state(state)
    for src in (a.source or []):
        _add_source(store, src, "project_fact", "", "")
    (home / "current").write_text(a.project, encoding="utf-8")
    store.event("init", mode=a.mode, seed=seed_name)
    t = refresh(store)
    disp = "已创建工作区 %s（%d 个种子节点，全部是假设，不是结论）" % (store.dir, len(t["nodes"]))
    if project_dir:
        gi = project_dir / ".gitignore"
        ignored = False
        body = ""
        if gi.exists():
            try:
                body = gi.read_text(encoding="utf-8")
                ignored = any(ln.strip().rstrip("/") == STATE_DIR_NAME for ln in body.splitlines())
            except Exception:
                pass
        if a.gitignore and not ignored:
            with gi.open("a", encoding="utf-8") as fh:
                fh.write(("\n" if gi.exists() and not body.endswith("\n") else "") + STATE_DIR_NAME + "/\n")
            disp += "\n已把 %s/ 追加进 %s" % (STATE_DIR_NAME, gi)
        elif not ignored:
            disp += "\n提示：状态目录 %s/ 就在项目里，建议加进 .gitignore（或 init 时加 --gitignore）。" % STATE_DIR_NAME
    if getattr(a, "legacy_note", None):
        disp += "\n" + a.legacy_note
    emit({"project": a.project, "dir": str(store.dir),
          "root": str(project_dir) if project_dir else None,
          "nodes": len(t["nodes"]), "display": disp})


def _file_meta(path: Path):
    """Stable local-source fingerprint used to detect stale project facts."""
    st = path.stat()
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": h.hexdigest()}


def _add_source(store, ref, kind, note, node):
    srcs = store.read("sources.json", {"sources": []})
    rec = {"id": "s%d" % (len(srcs["sources"]) + 1), "ref": ref, "kind": kind,
           "note": note, "node": node, "at": now_iso()}
    # Remember a cheap fingerprint for local project facts. This lets `doctor`
    # detect when a long-running session is reasoning against stale files.
    root = store.state().get("object_root")
    base_ref = (ref or "").split("#", 1)[0]
    if kind == "project_fact" and base_ref and root:
        try:
            fp = Path(base_ref)
            if not fp.is_absolute():
                fp = Path(root) / fp
            if fp.is_file():
                rec["file_meta"] = _file_meta(fp)
        except Exception:
            pass
    srcs["sources"].append(rec)
    store.write("sources.json", srcs)
    return rec


def cmd_node(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    if a.node_action == "add":
        parent = (resolve_node(store, a.parent) if a.parent else None) or state.get("focus") or tree["root"]
        nid = a.id or unique_id(tree, a.name)
        if nid in tree["nodes"]:
            die("node id exists: %s" % nid)
        node = make_node(nid, a.name, parent,
                         description=a.desc or "", kind=a.kind or "concept",
                         origin=a.origin or "manual", importance=a.importance or 3,
                         blocking=bool(a.blocking), tags=a.tag or [],
                         sources=a.source or [],
                         discovered_from=a.discovered_from)
        tree["nodes"][nid] = node
        attach(tree, nid, parent)
        node_depths(tree)
        store.put_tree(tree)
        store.event("node_add", id=nid, parent=parent, origin=node["origin"],
                    discovered_from=a.discovered_from)
        refresh(store)
        emit({"node": nid, "display": "新增节点 %s（父: %s）" % (a.name, parent)})

    if a.node_action == "update":
        n = get_node(tree, resolve_node(store, a.id) or a.id)
        if not n:
            die("no such node: %s" % a.id)
        a.id = n["id"]
        for k, v in (("name", a.name), ("description", a.desc), ("importance", a.importance)):
            if v is not None:
                n[k] = v
        if a.blocking is not None:
            n["blocking"] = bool(a.blocking)
        if a.note:
            n["notes"].append({"at": now_iso(), "text": a.note})
        if a.tag:
            n["tags"] = sorted(set(n["tags"] + a.tag))
        if a.status:
            if a.status == "mastered":
                passed, missing = gate_status(store, a.id)
                if not passed:
                    fail(["node %s cannot be set to mastered by hand; missing: %s"
                          % (a.id, "; ".join(missing))],
                         "mastery gate blocked the status change",
                         hint="record evidence with `answer --evidence ...` instead")
            n["manual_status"] = a.status if a.status in ("deferred", "needs_review") else None
        n["updated_at"] = now_iso()
        store.put_tree(tree)
        store.event("node_update", id=a.id)
        refresh(store)
        emit({"node": a.id, "display": "已更新 %s" % a.id})

    if a.node_action == "split":
        n = get_node(tree, resolve_node(store, a.id) or a.id)
        if not n:
            die("no such node: %s" % a.id)
        a.id = n["id"]
        parts = [p.strip() for p in a.into.split(",") if p.strip()]
        if len(parts) < 2:
            die("split needs at least two comma-separated names")
        created = []
        for p in parts:
            nid = unique_id(tree, p)
            child = make_node(nid, p, a.id, origin="split", importance=n["importance"],
                              description="由「%s」拆分而来" % n["name"])
            tree["nodes"][nid] = child
            n["children"].append(nid)
            created.append(nid)
        n["is_container"] = True
        n["notes"].append({"at": now_iso(), "text": "拆分为: %s" % ", ".join(parts)})
        node_depths(tree)
        store.put_tree(tree)
        store.event("node_split", id=a.id, into=created)
        refresh(store)
        emit({"node": a.id, "children": created,
              "display": "拆分 %s → %s（父节点变为容器，状态由子节点聚合）" % (a.id, ", ".join(parts))})

    if a.node_action == "merge":
        a.id = resolve_node(store, a.id) or a.id
        a.into = resolve_node(store, a.into) or a.into
        src = get_node(tree, a.id)
        dst = get_node(tree, a.into)
        if not src or not dst:
            die("merge needs valid --id and --into")
        for c in src.get("children", []):
            if c not in dst["children"]:
                dst["children"].append(c)
            get_node(tree, c)["parent"] = a.into
        detach(tree, a.id)
        dst["notes"].append({"at": now_iso(),
                             "text": "合并了节点「%s」（原因: %s）" % (src["name"], a.reason or "同义")})
        ed = store.edges()
        for e in ed["edges"]:
            if e["from"] == a.id:
                e["from"] = a.into
            if e["to"] == a.id:
                e["to"] = a.into
        store.put_edges(ed)
        ev = store.evidence()
        if a.id in ev:
            ev.setdefault(a.into, []).extend(ev.pop(a.id))
            store.put_evidence(ev)
        m = store.mistakes()
        for x in m["open"] + m["closed"]:
            if x["node"] == a.id:
                x["node"] = a.into
        store.put_mistakes(m)
        tree["nodes"].pop(a.id)
        node_depths(tree)
        store.put_tree(tree)
        store.event("node_merge", removed=a.id, into=a.into, reason=a.reason)
        refresh(store)
        emit({"node": a.into, "display": "已把 %s 合并进 %s" % (src["name"], dst["name"])})

    if a.node_action == "reparent":
        n = get_node(tree, resolve_node(store, a.id) or a.id)
        if not n:
            die("no such node: %s" % a.id)
        if a.parent not in tree["nodes"]:
            die("no such parent: %s" % a.parent)
        if a.parent in subtree_nodes(tree, a.id):
            fail(["cannot re-parent %s under its own descendant %s" % (a.id, a.parent)],
                 "would create a cycle")
        detach(tree, a.id)
        n["parent"] = a.parent
        attach(tree, a.id, a.parent)
        node_depths(tree)
        store.put_tree(tree)
        store.event("node_reparent", id=a.id, parent=a.parent, reason=a.reason)
        refresh(store)
        emit({"node": a.id, "display": "已把 %s 挂到 %s 下" % (a.id, a.parent)})

    if a.node_action == "remove":
        n = get_node(tree, resolve_node(store, a.id) or a.id)
        if not n:
            die("no such node: %s" % a.id)
        if a.id == tree["root"]:
            die("cannot remove the root")
        for c in list(n.get("children", [])):
            get_node(tree, c)["parent"] = n["parent"]
            attach(tree, c, n["parent"])
        detach(tree, a.id)
        tree["nodes"].pop(a.id)
        node_depths(tree)
        store.put_tree(tree)
        store.event("node_remove", id=a.id, name=n["name"], reason=a.reason)
        refresh(store)
        emit({"removed": a.id, "display": "已移除 %s（原因: %s）" % (n["name"], a.reason or "无必要")})


def cmd_edge(a):
    store = Store(a.home, a.project)
    ed = store.edges()
    if a.edge_action == "add":
        tree = store.tree()
        for k in ("from_", "to"):
            if getattr(a, k) not in tree["nodes"]:
                die("no such node: %s" % getattr(a, k))
        if a.from_ == a.to:
            die("self-edge is meaningless")
        ed["edges"] = [e for e in ed["edges"]
                       if not (e["from"] == a.from_ and e["to"] == a.to and e["type"] == a.type)]
        ed["edges"].append({"from": a.from_, "to": a.to, "type": a.type,
                            "strength": a.strength or "required",
                            "reason": a.reason or "", "created_at": now_iso(),
                            "created_turn": store.state().get("turn", 0)})
        store.put_edges(ed)
        store.event("edge_add", **{"from": a.from_, "to": a.to, "type": a.type})
        emit({"edge": {"from": a.from_, "to": a.to, "type": a.type},
              "display": "新增关系 %s --%s--> %s" % (a.from_, a.type, a.to)})
    if a.edge_action == "remove":
        before = len(ed["edges"])
        keep = []
        for e in ed["edges"]:
            if a.from_ and a.to and e["from"] == a.from_ and e["to"] == a.to:
                continue
            if a.from_ and not a.to and e["from"] == a.from_:
                continue
            if a.to and not a.from_ and e["to"] == a.to:
                continue
            keep.append(e)
        ed["edges"] = keep
        store.put_edges(ed)
        store.event("edge_remove", **{"from": a.from_, "to": a.to})
        emit({"removed": before - len(ed["edges"]),
              "display": "已删除 %d 条关系" % (before - len(ed["edges"]))})
    if a.edge_action == "list":
        emit({"edges": ed["edges"]})


def cmd_ask(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    nid = resolve_node(store, a.node) if a.node else state.get("focus")
    node = get_node(tree, nid) if nid else None
    if node is None:
        die("no such node: %s" % a.node)

    violations = []
    text = load_text_arg(a.text, getattr(a, "text_file", None), "text")
    low = text.lower()
    for pat in FORBIDDEN_PATTERNS:
        if re.search(pat, low):
            violations.append("forbidden verification question (%s) -- '懂了/明白了吗' proves nothing; "
                              "ask the user to explain it back instead" % pat)
    qm = question_marks(text)
    listed = sum(1 for ln in text.splitlines() if any(re.search(m, ln) for m in LIST_MARKERS))
    dumps = qm >= 3 or (qm >= 2 and (listed >= 2 or
                                     bool(re.search(r"[？?][^\n]*\n[^\n]*[？?]", text))))
    if dumps:
        violations.append("multi-question dump (%d question marks%s) -- one primary question per turn;"
                          " exam mode is also sequential, one item per turn"
                          % (qm, ", list markers" if listed >= 2 else ""))
    if state.get("pending_question") and not a.force:
        violations.append("a question is already pending (%s) -- record the answer first"
                          % state["pending_question"]["id"])

    # Returning from a drill is a hard protocol boundary. The very next question
    # must explicitly re-probe the interrupted question on the same node, with a
    # new wording. Merely asking anything on that node must not clear the return.
    rp = state.get("reprobe_pending")
    if rp and not state.get("pending_question"):
        if nid != rp.get("node"):
            violations.append("return requires re-probing node %s before moving to %s"
                              % (rp.get("node"), nid))
        if a.reprobe_of != rp.get("of"):
            violations.append("return re-probe must pass --reprobe-of %s" % (rp.get("of") or "<original-question-id>"))
        if rp.get("text") and similar(text, rp.get("text")):
            violations.append("return re-probe is too similar to the interrupted question -- verify the same idea with a genuinely new wording")
    if violations and not a.force:
        fail(violations, "question refused by the one-question rule")
    if violations and a.force:
        store.event("violation_waived", command="ask", violations=violations, reason=a.reason)

    stacked = violations
    state["turn"] = state.get("turn", 0) + 1
    turn = state["turn"]
    qid = "q-%04d" % turn
    rec = {"id": qid, "type": "question", "turn": turn, "node": nid, "kind": a.kind,
           "text": text, "at": now_iso(), "exam": bool(a.exam),
           "reprobe_of": a.reprobe_of, "waived": bool(stacked)}
    store.append("questions.jsonl", rec)

    state["pending_question"] = {"id": qid, "node": nid, "kind": a.kind, "text": text, "turn": turn}
    # A manually registered question starts a new attempt. Historical hints from
    # a previous question on the same concept must not unlock L2/L3 today.
    state["hint"] = {}
    state["hint_chain"] = {}
    state["focus"] = nid
    if state.get("reprobe_pending") and state["reprobe_pending"].get("node") == nid \
            and a.reprobe_of == state["reprobe_pending"].get("of"):
        state["reprobe_pending"] = None
    if a.reprobe_of:
        prev = None
        for r in store.tail("questions.jsonl", 200):
            if r.get("id") == a.reprobe_of:
                prev = r
        if prev and prev.get("node") == nid:
            state.setdefault("reprobes", []).append({"of": a.reprobe_of, "as": qid, "turn": turn})
    if a.mainline:
        state["opening_question"] = {"question_id": qid, "node": nid, "text": text, "turn": turn}
    state["recent_nodes"] = (state.get("recent_nodes", []) + [nid])[-6:]
    drill = state.setdefault("drill", {"depth": 0, "turns": 0})
    if drill.get("depth", 0) > 0:
        drill["turns"] = drill.get("turns", 0) + 1
    node["asked"] = node.get("asked", 0) + 1
    node["last_asked_turn"] = turn
    store.put_state(state)
    tree["nodes"][nid] = node
    store.put_tree(tree)
    refresh(store)
    emit({"question_id": qid, "node": nid, "turn": turn, "kind": a.kind,
          "display": "已登记问题 %s → %s" % (qid, node["name"])})


def cmd_answer(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    a.text = load_text_arg(a.text, getattr(a, "text_file", None), "text")
    pend = state.get("pending_question")
    nid = resolve_node(store, a.node) if a.node else (pend["node"] if pend else state.get("focus"))
    node = get_node(tree, nid) if nid else None
    if node is None:
        die("no such node: %s" % a.node)
    if not pend and not getattr(a, "orphan", False):
        fail(["answer has no pending question -- do not manufacture evidence outside a registered prompt"],
             "unanchored answer",
             hint="register the question first with `ask`; use --orphan only for explicit legacy/import repair")
    if a.idk and (a.evidence or []):
        positive = [x for x in a.evidence if x.endswith(":pass") or x.endswith(":partial")]
        if positive:
            fail(["--idk cannot be combined with positive evidence: %s" % ", ".join(positive)],
                 "contradictory answer classification")
    if getattr(a, "neutral", False) and (a.idk or a.evidence or a.misconception or a.corrected or a.discover):
        fail(["--neutral cannot be combined with evidence/idk/misconception/correction/discovery"],
             "contradictory answer classification")
    if not (a.idk or a.evidence or a.misconception or a.corrected or a.discover or getattr(a, "neutral", False)):
        fail(["answer must be classified: use --evidence, --idk, --misconception, --corrected, --discover, or explicit --neutral"],
             "unclassified answer")
    turn = state.get("turn", 0)
    qid = pend["id"] if pend else None

    # hint state decides whether a win counts as prompted
    hint = (state.get("hint") or {})
    recent_hint = hint.get("level", 0) if hint.get("node") == nid and \
        (turn - (hint.get("turn") or -99)) <= 2 else 0
    hinted = bool(a.hinted) or recent_hint >= 2

    ev = store.evidence()
    added = []
    for spec in (a.evidence or []):
        if ":" not in spec:
            die("--evidence expects KIND:RESULT (e.g. explain_back:pass), got %r" % spec)
        kind, result = spec.split(":", 1)
        kind, result = kind.strip(), result.strip()
        if kind not in EVIDENCE_KINDS:
            die("unknown evidence kind %r (one of %s)" % (kind, ", ".join(EVIDENCE_KINDS)))
        if result not in ("pass", "partial", "fail"):
            die("result must be pass|partial|fail")
        novel = True
        if kind == "transfer" and pend:
            for r in store.tail("questions.jsonl", 500):
                if r.get("type") == "question" and r.get("node") == nid and r["id"] != qid:
                    if r.get("kind") == "transfer" and similar(r["text"], pend["text"]):
                        novel = False
                        break
        rec = {"turn": turn, "node": nid, "kind": kind, "result": result,
               "hinted": hinted, "unprompted": (not hinted), "novel": novel,
               "question_id": qid, "text": (pend["text"] if pend else ""),
               "answer": a.text, "orphan": bool(getattr(a, "orphan", False)), "at": now_iso()}
        ev.setdefault(nid, []).append(rec)
        added.append(rec)
        if result == "fail":
            node["fails"] = node.get("fails", 0) + 1

    if a.idk:
        node["idks"] = node.get("idks", 0) + 1
        ev.setdefault(nid, []).append({"turn": turn, "node": nid, "kind": "idk",
                                       "result": "fail", "hinted": hinted,
                                       "question_id": qid, "text": (pend["text"] if pend else ""),
                                       "answer": a.text, "at": now_iso()})

    m = store.mistakes()
    if a.misconception:
        entry = {"id": "m%03d" % (len(m["open"]) + len(m["closed"]) + 1), "node": nid,
                 "belief": a.misconception, "corrected": False,
                 "source_turn": turn, "source_question": qid,
                 "recheck_required": True, "note": a.text, "at": now_iso()}
        m["open"].append(entry)
        store.event("misconception_open", node=nid, belief=a.misconception)
    for mid in (a.corrected or []):
        for x in list(m["open"]):
            if x["id"] == mid:
                x["corrected"] = True
                x["corrected_turn"] = turn
                x["at"] = now_iso()
                m["open"].remove(x)
                m["closed"].append(x)
                store.event("misconception_closed", id=mid, node=x["node"])
    store.put_mistakes(m)
    store.put_evidence(ev)

    created = []
    for spec in (a.discover or []):
        name, _, desc = spec.partition("=")
        name = name.strip()
        parent = (resolve_node(store, a.discover_parent) if a.discover_parent else None) or nid
        cid = unique_id(tree, name)
        cn = make_node(cid, name, parent, description=desc.strip() or "对话中发现的新概念",
                       origin="discovered", importance=4, blocking=False,
                       discovered_from={"turn": turn, "question_id": qid,
                                        "trigger": (a.text or "")[:200]})
        tree["nodes"][cid] = cn
        attach(tree, cid, parent)
        created.append(cid)
        store.event("node_add", id=cid, parent=parent, origin="discovered",
                    discovered_from=cn["discovered_from"])
    if created:
        node_depths(tree)
        store.put_tree(tree)

    node["updated_at"] = now_iso()
    tree["nodes"][nid] = node
    store.put_tree(tree)

    if pend:
        state["last_answered_question"] = {
            "id": pend.get("id"), "node": pend.get("node"), "kind": pend.get("kind"),
            "text": pend.get("text"), "turn": pend.get("turn"), "answered_at": now_iso(),
        }
    state["pending_question"] = None
    state["hint"] = {}
    if any(e.get("result") == "pass" for e in added) and not a.idk:
        state["hint_chain"] = {}
    if a.clear_opening and state.get("opening_question", {}) and \
            state["opening_question"].get("node") == nid:
        state["opening_question"] = None
    store.put_state(state)

    store.append("questions.jsonl", {"id": "a-%04d" % turn, "type": "answer", "turn": turn,
                                     "node": nid, "question_id": qid, "text": a.text or "",
                                     "idk": bool(a.idk), "evidence": [e["kind"] + ":" + e["result"] for e in added],
                                     "misconception": a.misconception, "neutral": bool(getattr(a, "neutral", False)),
                                     "orphan": bool(getattr(a, "orphan", False)), "at": now_iso()})
    refresh(store)
    nxt = decide_next(store)
    emit({"node": nid, "recorded": added, "new_nodes": created,
          "status": get_node(store.tree(), nid)["status"],
          "mastery": get_node(store.tree(), nid)["mastery"],
          "next_action": nxt["action"], "next": nxt,
          "display": "已记录回答 → %s [%s %.2f]；下一步: %s"
                     % (node["name"], get_node(store.tree(), nid)["status"],
                        get_node(store.tree(), nid)["mastery"], nxt["display"])})


def cmd_teach(a):
    store = Store(a.home, a.project)
    state = store.state()
    a.text = load_text_arg(a.text, getattr(a, "text_file", None), "text")
    tree = store.tree()
    nid = resolve_node(store, a.node) if a.node else state.get("focus")
    node = get_node(tree, nid) if nid else None
    if node is None:
        die("no such node: %s" % a.node)
    stuck = a.stuck_point or nid
    pending = state.get("pending_question")

    # Hint ladders are per *attempt*, not historical lifetime achievements. In
    # v1, an L1/L2 from weeks ago could unlock an L3 today. Track the current
    # chain explicitly and reset it when the target/stuck point changes.
    chain = state.get("hint_chain") or {}
    same_chain = chain.get("node") == nid and chain.get("stuck_point") == stuck
    levels = list(chain.get("levels") or []) if same_chain else []
    if a.level == 1 and levels and levels[-1] != 1:
        levels = []
    violations = []
    if a.level == 2 and (not levels or levels[-1] != 1):
        violations.append("hint level 2 requires level 1 immediately earlier in the current attempt on '%s'" % stuck)
    if a.level == 3:
        if len(levels) < 2 or levels[-2:] != [1, 2]:
            violations.append("hint level 3 requires level 1 then level 2 in the current attempt on '%s'" % stuck)
        if not a.recovery_text:
            violations.append("bottom-out requires --recovery-text: after giving the answer the next prompt must be explain-back")
    if a.level in (1, 2) and pending and pending.get("node") != nid:
        violations.append("cannot attach a hint for %s while a question on %s is pending" % (nid, pending.get("node")))
    if a.level in (1, 2) and not pending and not a.retry_text:
        violations.append("hint level %d has no pending question; provide --retry-text so the user has one concrete retry" % a.level)

    prompt_text = a.recovery_text if a.level == 3 else (a.retry_text if not pending else None)
    if prompt_text:
        low = prompt_text.lower()
        for pat in FORBIDDEN_PATTERNS:
            if re.search(pat, low):
                violations.append("retry/recovery question is a forbidden verification question (%s)" % pat)
        if question_marks(prompt_text) >= 3:
            violations.append("retry/recovery prompt contains multiple primary questions")
    if violations and not a.force:
        fail(violations, "hint ladder violated")
    if violations:
        store.event("violation_waived", command="teach", level=a.level,
                    violations=violations, reason=a.reason)

    if not same_chain:
        chain = {"node": nid, "stuck_point": stuck, "levels": [],
                 "started_turn": state.get("turn", 0), "id": "hc-%s-%s" % (state.get("turn", 0), slugify(stuck))}
    chain.setdefault("levels", []).append(a.level)
    chain["updated_turn"] = state.get("turn", 0)
    state["hint_chain"] = chain
    state.setdefault("hint_log", []).append(
        {"turn": state.get("turn", 0), "node": nid, "stuck_point": stuck,
         "chain_id": chain.get("id"), "level": a.level, "text": a.text,
         "recovery": a.recovery_text, "retry": a.retry_text, "at": now_iso()})
    state["hint_log"] = state["hint_log"][-80:]
    state["hint"] = {"node": nid, "stuck_point": stuck, "level": a.level,
                     "turn": state.get("turn", 0), "chain_id": chain.get("id")}
    node["last_teach_turn"] = state.get("turn", 0)
    if a.floor:
        drill = state.setdefault("drill", {})
        drill["floor_node"] = nid
        drill["floor_turn"] = state.get("turn", 0)

    store.append("questions.jsonl", {"id": "t-%s" % now_iso(), "type": "teach",
                                     "turn": state.get("turn", 0), "node": nid,
                                     "level": a.level, "stuck_point": stuck,
                                     "chain_id": chain.get("id"), "text": a.text,
                                     "recovery": a.recovery_text, "retry": a.retry_text,
                                     "at": now_iso()})
    store.event("teach", node=nid, level=a.level, stuck_point=stuck, chain_id=chain.get("id"))

    # A hint must leave a concrete prompt outstanding. L1/L2 reuse an existing
    # pending question when possible; otherwise they register --retry-text. L3
    # always replaces the old target with the mandatory explain-back prompt.
    registered = None
    if a.level == 3 or not pending:
        if pending and a.level == 3:
            store.append("questions.jsonl", {"id": "sup-%s" % now_iso(), "type": "supersede",
                                             "turn": state.get("turn", 0), "node": pending.get("node"),
                                             "question_id": pending.get("id"), "reason": "L3 recovery prompt",
                                             "at": now_iso()})
        state["turn"] = state.get("turn", 0) + 1
        turn = state["turn"]
        qid = "q-%04d" % turn
        kind = "explain_back" if a.level == 3 else (a.retry_kind or (pending or {}).get("kind") or "probe")
        text = prompt_text or "请再试一次。"
        rec = {"id": qid, "type": "question", "turn": turn, "node": nid, "kind": kind,
               "text": text, "at": now_iso(), "exam": False, "hint_retry": True,
               "hint_level": a.level, "hint_chain_id": chain.get("id")}
        store.append("questions.jsonl", rec)
        state["pending_question"] = {"id": qid, "node": nid, "kind": kind, "text": text, "turn": turn}
        state["focus"] = nid
        state["recent_nodes"] = (state.get("recent_nodes", []) + [nid])[-6:]
        drill = state.setdefault("drill", {"depth": 0, "turns": 0})
        if drill.get("depth", 0) > 0:
            drill["turns"] = drill.get("turns", 0) + 1
        node["asked"] = node.get("asked", 0) + 1
        node["last_asked_turn"] = turn
        registered = {"question_id": qid, "text": text, "kind": kind}

    tree["nodes"][nid] = node
    store.put_state(state)
    store.put_tree(tree)
    refresh(store)
    if registered:
        msg = "已记录 hint L%d（%s），并登记下一问：%s" % (a.level, node["name"], registered["text"])
    else:
        msg = "已记录 hint L%d（%s），继续等待用户回答当前问题。" % (a.level, node["name"])
    emit({"node": nid, "level": a.level, "hint_chain": chain,
          "pending_question": state.get("pending_question"), "display": msg})


def cmd_drill(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    nid = resolve_node(store, a.node) if a.node else state.get("focus")
    node = get_node(tree, nid) if nid else None
    if node is None:
        die("no such node: %s" % a.node)
    drill = state.setdefault("drill", {"depth": 0, "turns": 0})
    maxdepth = state.get("max_drill_depth", DEFAULT_MAX_DRILL_DEPTH)
    if not a.reason:
        fail(["drill requires --reason: why does this prerequisite actually block the current goal?"],
             "minimum-sufficient-prerequisite rule")
    if drill.get("depth", 0) + 1 > maxdepth:
        fail(["drill depth %d would exceed max_drill_depth=%d"
              % (drill.get("depth", 0) + 1, maxdepth)],
             "stop descending",
             hint="this is the floor. use `teach --floor --level 2/3` and then rebuild upward "
                  "with the return stack.")
    if nid != state.get("focus") and not getattr(a, "force", False):
        fail(["drill must descend from the current focus (%s), not %s" % (state.get("focus"), nid)],
             "off-focus drill")
    pend = state.get("pending_question")
    lastq = state.get("last_answered_question") or {}
    opening = state.get("opening_question") or {}
    if pend and pend.get("node") == nid:
        interrupted = pend
    elif lastq.get("node") == nid:
        interrupted = lastq
    elif opening.get("node") == nid:
        interrupted = opening
    else:
        interrupted = {}
    if not interrupted.get("text") and not getattr(a, "force", False):
        fail(["drill has no anchored question on node %s; register and answer/probe that node before descending" % nid],
             "unanchored drill")
    name = a.prereq or a.prereq_id
    if not name:
        die("drill needs --prereq NAME (a prerequisite concept) or --prereq-id ID")
    created = None
    if a.prereq_id and a.prereq_id in tree["nodes"]:
        if a.prereq_id == nid:
            fail(["a node cannot be its own prerequisite"], "invalid prerequisite")
        # Existing concepts keep their containment parent. The dependency graph
        # represents prerequisites; silently re-parenting an existing concept
        # corrupts the knowledge tree and can move a whole learned subtree.
        child = tree["nodes"][a.prereq_id]
    else:
        cid = unique_id(tree, name)
        child = make_node(cid, name, nid, origin="discovered", importance=max(3, node["importance"]),
                          blocking=bool(a.blocking), description=a.desc or "",
                          discovered_from={"turn": state.get("turn", 0),
                                           "question_id": interrupted.get("id") or interrupted.get("question_id"),
                                           "trigger": a.reason})
        tree["nodes"][cid] = child
        attach(tree, cid, nid)
        created = cid
        store.event("node_add", id=cid, parent=nid, origin="discovered",
                    discovered_from=child["discovered_from"])
    # dependency edge: the gap node depends on the prerequisite
    ed = store.edges()
    existing = next((e for e in ed["edges"] if e.get("from") == nid and e.get("to") == child["id"]
                     and e.get("type") == "depends_on"), None)
    if existing:
        existing["strength"] = "required"
        existing["reason"] = a.reason
        existing["updated_at"] = now_iso()
    else:
        ed["edges"].append({"from": nid, "to": child["id"], "type": "depends_on",
                            "strength": "required", "reason": a.reason,
                            "created_at": now_iso(), "created_turn": state.get("turn", 0)})
    store.put_edges(ed)

    entry = {"node": nid, "reason": a.reason,
             "question_id": interrupted.get("id") or interrupted.get("question_id"),
             "question_text": interrupted.get("text"),
             "turn": state.get("turn", 0), "at": now_iso()}
    state.setdefault("return_stack", []).append(entry)
    if not state.get("opening_question") and entry["question_text"]:
        state["opening_question"] = {"question_id": entry["question_id"], "node": nid,
                                     "text": entry["question_text"], "turn": entry["turn"]}
    state["pending_question"] = None
    state["hint"] = {}
    state["hint_chain"] = {}
    drill["depth"] = drill.get("depth", 0) + 1
    drill["turns"] = 0
    drill["started_turn"] = state.get("turn", 0)
    state["focus"] = child["id"]
    state["recent_nodes"] = (state.get("recent_nodes", []) + [child["id"]])[-6:]
    node_depths(tree)
    store.put_tree(tree)
    store.put_state(state)
    store.event("drill_push", **{"from": nid, "to": child["id"], "reason": a.reason,
                                 "depth": drill["depth"]})
    refresh(store)
    emit({"from": nid, "prereq": child["id"], "created": created,
          "return_stack": [e["node"] for e in state["return_stack"]], "depth": drill["depth"],
          "display": "已下钻 %s → %s（下钻时不能直接给答案，要问用户对这一层知道多少）"
                     % (node["name"], child["name"])})


def cmd_return(a):
    store = Store(a.home, a.project)
    state = store.state()
    tree = store.tree()
    stack = (state.get("return_stack") or [])
    if not stack:
        fail(["return stack is empty -- nothing was interrupted"], "nothing to return to")
    target = a.node
    if target:
        # Never skip frames. Every interrupted question must be re-probed before
        # another frame can be popped, otherwise the return stack is cosmetic.
        top = stack[-1]
        resolved_target = resolve_node(store, target) or target
        if top["node"] != resolved_target:
            fail(["return is level-by-level: next frame is %s, not %s" % (top["node"], resolved_target)],
                 "cannot skip return frames",
                 hint="call `return` for the top frame, re-probe it, then return again")
    cur = get_node(tree, state.get("focus"))
    drill = (state.get("drill") or {})
    started = drill.get("started_turn") or 0
    cur_id = cur["id"] if cur else None
    productive = any(e.get("result") in ("pass", "partial")
                     for e in store.evidence().get(cur_id, []) if e.get("turn", 0) >= started)
    taught = any(h.get("node") == cur_id and h.get("level", 0) >= 2 and h.get("turn", 0) >= started
                 for h in (state.get("hint_log") or []))
    budget_out = drill.get("turns", 0) >= state.get("drill_budget", DEFAULT_DRILL_BUDGET)
    if not (productive or taught or budget_out) and not a.force:
        fail(["nothing was established on '%s' since this drill started (status=%s)"
              % (cur_id, cur["status"] if cur else "?")],
             "drill not resolved",
             hint="probe it once with a new wording and record the answer, teach at the floor, "
                  "or `return --force` if the user chose to move on")
    entry = stack.pop()
    drill = state.setdefault("drill", {"depth": 0, "turns": 0})
    drill["depth"] = max(0, drill.get("depth", 0) - 1)
    drill["turns"] = 0
    drill["started_turn"] = None
    if drill["depth"] == 0:
        drill["floor_node"] = None
    state["focus"] = entry["node"]
    state["return_stack"] = stack
    state["reprobe_pending"] = {"node": entry["node"], "of": entry.get("question_id"),
                                "text": entry.get("question_text"), "at": now_iso()}
    state["hint"] = {}
    state["hint_chain"] = {}
    drill["started_turn"] = state.get("turn", 0)
    store.put_state(state)
    store.event("return_pop", to=entry["node"], reason=entry["reason"],
                still_open=bool(stack))
    refresh(store)
    node = get_node(store.tree(), entry["node"])
    display = ("回到主线：%s。原问题是「%s」——不要复述已讲内容，用它换一种问法重新验证。"
               % (node["name"] if node else entry["node"], entry.get("question_text") or "(未记录)"))
    emit({"returned_to": entry["node"], "original_question": entry.get("question_text"),
          "original_question_id": entry.get("question_id"),
          "return_stack": [e["node"] for e in stack],
          "reprobe_required": True, "display": display})


def cmd_next(a):
    store = Store(a.home, a.project)
    nxt = decide_next(store)
    tree = store.tree()
    n = get_node(tree, nxt.get("node")) if nxt.get("node") else None
    if n:
        nxt["node_name"] = n["name"]
        nxt["node_status"] = n["status"]
        nxt["node_mastery"] = n["mastery"]
        nxt["suggested_kind"] = nxt.get("required_evidence_kind") or _suggest_kind(n)
    nxt["display"] = "%s\n目标节点: %s [%s %.2f]\n理由: %s" % (
        nxt.get("display", ""), (n["name"] if n else "-"),
        (n["status"] if n else "-"), (n["mastery"] if n else 0),
        "; ".join(nxt.get("why", [])))
    emit(nxt)


def _suggest_kind(node):
    missing = node.get("gate_missing") or []
    if node.get("mastery", 0) >= 0.35:
        if any(x.startswith("explain_back") for x in missing):
            return "explain_back"
        if any(x.startswith("why") for x in missing):
            return "why"
        if any(x.startswith("boundary/counterexample") for x in missing):
            return "boundary"
        if any(x.startswith("transfer") for x in missing):
            return "transfer"
    if node["status"] in ("gap", "unexplored", "probing"):
        return "probe"
    if node["status"] == "learning":
        return "explain_back"
    if node["status"] == "partially_understood":
        return "why"
    if node["status"] == "understood":
        return "transfer"
    if node["status"] == "misconception":
        return "debug"
    if node["status"] == "needs_review":
        return "recall"
    return "probe"


def cmd_where(a):
    store = Store(a.home, a.project)
    state = store.state()
    tree = store.tree()
    focus = state.get("focus")
    n = get_node(tree, focus)
    chain = []
    x = n
    while x and x.get("parent"):
        chain.append(x["name"])
        x = get_node(tree, x["parent"])
    if x:
        chain.append(x["name"])
    oq = state.get("opening_question")
    lines = ["当前位置: %s [%s %.2f]" % (n["name"], n["status"], n["mastery"]) if n else "位置: -",
             "路径: " + " < ".join(chain)]
    if oq:
        lines.append("主线未完成的问题: 「%s」" % oq["text"])
    if state.get("return_stack"):
        lines.append("待回溯: " + " → ".join(e["node"] for e in state["return_stack"]))
    lines.append("为什么问这个: " + "; ".join(decide_next(store).get("why", [])))
    emit({"focus": focus, "chain": chain, "opening_question": oq,
          "return_stack": (state.get("return_stack") or []),
          "display": "\n".join(lines)})


def cmd_render(a):
    store = Store(a.home, a.project)
    out = render_tree(store, full=a.full, max_depth=(a.depth if a.depth else 99))
    if a.depth:
        out = render_tree(store, max_depth=a.depth)
    emit(out)


def cmd_status(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    counts = status_counts(tree)
    total = len(tree["nodes"])
    understood = counts.get("understood", 0) + counts.get("mastered", 0)
    avg = round(sum(n["mastery"] for n in tree["nodes"].values()) / max(1, total), 2)
    gaps = [n for n in tree["nodes"].values() if n["status"] in ("gap", "misconception")]
    gaps.sort(key=lambda n: -n["importance"])
    lines = ["%s | 模式 %s | 第 %d 问" % (state.get("objective"), state.get("mode"), state.get("turn", 0)),
             "节点 %d：已理解+掌握 %d（%d%%），平均掌握度 %.2f"
             % (total, understood, round(100 * understood / max(1, total)), avg),
             "状态分布: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())),
             "待复习: %d" % len([n for n in tree["nodes"].values() if n["status"] == "needs_review"])]
    if gaps:
        lines.append("最大缺口: " + "; ".join("%s(%s)" % (n["name"], n["status"]) for n in gaps[:5]))
    emit({"counts": counts, "total": total, "mastery_avg": avg,
          "display": "\n".join(lines)})


def cmd_resume(a):
    store = Store(a.home, a.project)
    state = store.state()
    state["sessions"] = state.get("sessions", 1) + 1
    store.put_state(state)
    store.event("session_start", sessions=state["sessions"])
    brief = resume_brief(store)
    nxt = decide_next(store)
    emit({"project": store.project, "mode": state.get("mode"), "turn": state.get("turn", 0),
          "focus": state.get("focus"), "return_stack": (state.get("return_stack") or []),
          "next_action": nxt["action"], "next": nxt,
          "display": brief + "\n\n(恢复完成：不要重新扫描项目、不要重讲已掌握的内容。)"})


def cmd_validate(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    ev = store.evidence()
    ed = store.edges()
    m = store.mistakes()
    V, W = [], []

    def v(loc, msg):
        V.append({"file": loc.split(":")[0], "locator": loc, "message": msg})

    meta = store.read("meta.json", {})
    if int(meta.get("workspace_schema", 0) or 0) != WORKSPACE_SCHEMA:
        v("meta.json:workspace_schema", "expected schema %d, got %r" % (WORKSPACE_SCHEMA, meta.get("workspace_schema")))

    # staleness: stored status vs freshly derived status
    for nid, n in tree["nodes"].items():
        fresh = derive_status(store, nid, tree)
        if fresh != n["status"]:
            W.append({"file": "tree.json", "locator": "node[%s].status" % nid,
                      "message": "stale status %s; evidence now implies %s -- run `mi.py refresh`"
                                 % (n["status"], fresh)})

    # structure
    for nid, n in tree["nodes"].items():
        if n.get("origin") == "discovered":
            df = n.get("discovered_from") or {}
            if not df.get("trigger") or df.get("turn") is None or not df.get("question_id"):
                v("tree.json:node[%s].discovered_from" % nid,
                  "discovered node lacks dialogue provenance (turn + question_id + trigger)")
        if len(n.get("children", [])) != len(set(n.get("children", []))):
            v("tree.json:node[%s].children" % nid, "duplicate child ids")
        if n.get("parent") and n["parent"] not in tree["nodes"]:
            v("tree.json:node[%s].parent" % nid, "missing parent %s" % n["parent"])
        for c in n.get("children", []):
            if c not in tree["nodes"]:
                v("tree.json:node[%s].children" % nid, "missing child %s" % c)
            elif tree["nodes"][c].get("parent") != nid:
                v("tree.json:node[%s].children" % nid, "child %s points to parent %s"
                  % (c, tree["nodes"][c].get("parent")))
    # cycles
    for nid in tree["nodes"]:
        seen, cur, hops = set(), nid, 0
        while cur and hops < len(tree["nodes"]) + 2:
            if cur in seen:
                v("tree.json:node[%s]" % nid, "parent cycle detected")
                break
            seen.add(cur)
            cur = tree["nodes"].get(cur, {}).get("parent")
            hops += 1
    # edges
    seen_edges = set()
    for e in ed["edges"]:
        ek = (e.get("from"), e.get("to"), e.get("type"))
        if ek in seen_edges:
            v("edges.json:%s->%s" % (e.get("from"), e.get("to")), "duplicate edge %r" % (ek,))
        seen_edges.add(ek)
        for k in ("from", "to"):
            if e.get(k) not in tree["nodes"]:
                v("edges.json:%s->%s" % (e.get("from"), e.get("to")),
                  "%s references missing node %s" % (k, e.get(k)))
        if e.get("type") not in EDGE_TYPES:
            v("edges.json:%s" % (e.get("from"),), "unknown edge type %s" % e.get("type"))
    # mastery gate
    for nid, n in tree["nodes"].items():
        if n["status"] == "mastered" and not n.get("is_container"):
            passed, missing = gate_status(store, nid)
            if not passed:
                v("tree.json:node[%s].status" % nid,
                  "mastered without passing the gate: %s" % "; ".join(missing))
    # misconceptions
    for x in m["open"]:
        if x["node"] not in tree["nodes"]:
            v("mistakes.json:%s" % x["id"], "references missing node %s" % x["node"])
        elif tree["nodes"][x["node"]]["status"] != "misconception":
            v("tree.json:node[%s].status" % x["node"],
              "open misconception but status=%s" % tree["nodes"][x["node"]]["status"])
    # Evidence anchors remain valid after log rotation, so inspect active log
    # plus every archive rather than only questions.jsonl.
    all_q = store.records("questions.jsonl", include_archives=True)
    for r in all_q:
        if r.get("_invalid_jsonl"):
            v("questions.jsonl", "invalid JSON line in %s:%s" % (r.get("_file"), r.get("_line")))
    qids = {r.get("id") for r in all_q if r.get("type") == "question"}
    # evidence
    for nid, items in ev.items():
        if nid not in tree["nodes"]:
            v("evidence.json:%s" % nid, "evidence for missing node")
        for it in items:
            if it.get("kind") not in EVIDENCE_KINDS + ["idk", "challenge"]:
                v("evidence.json:%s" % nid, "unknown evidence kind %s" % it.get("kind"))
            if not it.get("orphan") and it.get("kind") not in ("challenge",) and not it.get("question_id"):
                v("evidence.json:%s" % nid, "non-orphan evidence is missing question_id")
            if it.get("question_id") and it.get("question_id") not in qids:
                v("evidence.json:%s" % nid, "evidence references unknown question %s" % it.get("question_id"))
    # traversal
    stack = (state.get("return_stack") or [])
    if (state.get("drill") or {}).get("depth", 0) != len(stack):
        v("state.json:drill.depth", "drill depth %s != return_stack length %s"
          % ((state.get("drill") or {}).get("depth", 0), len(stack)))
    if state.get("pending_question") and state.get("reprobe_pending"):
        v("state.json:reprobe_pending", "pending_question and reprobe_pending cannot both be active")
    for e in stack:
        if e["node"] not in tree["nodes"]:
            v("state.json:return_stack", "missing node %s" % e["node"])
        if not e.get("question_text") or not e.get("question_id"):
            v("state.json:return_stack", "frame for %s lacks anchored question id/text" % e.get("node"))
    for nid in ([state.get("focus")] + [e["node"] for e in stack]):
        if nid and nid not in tree["nodes"]:
            v("state.json:focus", "missing node %s" % nid)
    # hint ladder audit is scoped per attempt/chain, not per concept lifetime.
    hint_log = state.get("hint_log", [])
    by_chain = {}
    for h in hint_log:
        by_chain.setdefault(h.get("chain_id") or "legacy:%s" % h.get("stuck_point"), []).append(h)
    for chain_id, items in by_chain.items():
        # v1 logs had no chain_id, so attempt boundaries cannot be reconstructed
        # reliably. Preserve them as history but do not produce false corruption
        # errors during migration.
        if str(chain_id).startswith("legacy:"):
            W.append({"file": "state.json", "locator": "hint_log[%s]" % chain_id,
                      "message": "legacy hint history has no attempt id; strict ladder audit skipped"})
            continue
        levels = [h["level"] for h in items]
        for idx, h in enumerate(items):
            if h["level"] == 2 and 1 not in levels[:idx]:
                v("state.json:hint_log[%s]" % chain_id, "level 2 without an earlier level 1 in this attempt")
            if h["level"] == 3:
                if levels[:idx][-2:] != [1, 2]:
                    v("state.json:hint_log[%s]" % chain_id, "level 3 without immediate 1 -> 2 ladder in this attempt")
                if not h.get("recovery"):
                    v("state.json:hint_log[%s]" % chain_id, "bottom-out missing recovery explain-back")
    # idk must be followed by a level-down (a drill) rather than the same question again
    qs = store.tail("questions.jsonl", 400)
    qtext = {r.get("id"): r.get("text", "") for r in qs if r.get("type") == "question"}
    for i, r in enumerate(qs):
        if r.get("type") == "answer" and r.get("idk"):
            asked = qtext.get(r.get("question_id"), "")
            nxt = next((x for x in qs[i + 1:] if x.get("type") in ("question", "teach")), None)
            if nxt and nxt.get("type") == "question" and nxt.get("node") == r.get("node"):
                if asked and similar(nxt.get("text", ""), asked):
                    W.append({"file": "questions.jsonl", "locator": "turn[%s]" % nxt["turn"],
                              "message": "same-level rephrase after \"I don't know\"; "
                                         "required a level-down to a prerequisite"})
    out = {"violations": V, "warnings": W, "checked_nodes": len(tree["nodes"]),
           "display": ("校验通过：%d 个节点，无违规。" % len(tree["nodes"])) if not V else
                      "校验失败 %d 项：\n" % len(V) + "\n".join(
                          "  - %s: %s" % (x["locator"], x["message"]) for x in V)}
    if W:
        out["display"] += "\n警告 %d 项：\n" % len(W) + "\n".join(
            "  - %s: %s" % (x["locator"], x["message"]) for x in W)
    emit(out, 0 if not V else 3)


def cmd_audit(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    a.node = resolve_node(store, a.node) or a.node
    n = get_node(tree, a.node)
    if not n:
        die("no such node: %s" % a.node)
    passed, missing = gate_status(store, a.node)
    ev = store.evidence().get(a.node, [])
    transfers = [e for e in ev if e["kind"] == "transfer"]
    verdict = "PASS" if (passed and n["status"] == "mastered") else "BLOCK"
    lines = ["节点: %s [%s %.2f] → %s" % (n["name"], n["status"], n["mastery"], verdict)]
    if missing:
        lines.append("还缺: " + "; ".join(missing))
    else:
        lines.append("证据齐备。")
    for e in transfers:
        lines.append("  transfer: %s novel=%s unprompted=%s | %s"
                     % (e["result"], e.get("novel"), e.get("unprompted"), (e.get("text") or "")[:60]))
    emit({"node": a.node, "verdict": verdict, "gate": "passed" if passed else "open",
          "missing": missing, "evidence_count": len(ev),
          "display": "\n".join(lines)})


def cmd_challenge(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    a.node = resolve_node(store, a.node) or a.node
    n = get_node(tree, a.node)
    if not n:
        die("no such node: %s" % a.node)
    before = n["status"]
    ev = store.evidence()
    ev.setdefault(a.node, []).append({"turn": state.get("turn", 0), "node": a.node, "kind": "challenge",
                                      "result": "fail", "hinted": False, "unprompted": True,
                                      "note": a.note, "at": now_iso()})
    store.put_evidence(ev)
    if a.misconception:
        m = store.mistakes()
        m["open"].append({"id": "m%03d" % (len(m["open"]) + len(m["closed"]) + 1), "node": a.node,
                          "belief": a.misconception, "corrected": False,
                          "source_turn": state.get("turn", 0), "recheck_required": True,
                          "note": a.note, "at": now_iso()})
        store.put_mistakes(m)
    else:
        n["manual_status"] = "needs_review"
    store.put_tree(tree)
    store.event("challenge", node=a.node, before=before, note=a.note,
                misconception=bool(a.misconception))
    refresh(store)
    after = get_node(store.tree(), a.node)
    emit({"node": a.node, "before": before, "after": after["status"],
          "mastery": after["mastery"],
          "display": "状态回退: %s → %s（掌握度允许衰退，这是记录，不是惩罚）"
                     % (before, after["status"])})


def cmd_review(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    now = datetime.now(timezone.utc).astimezone()
    if a.mark:
        a.mark = resolve_node(store, a.mark) or a.mark
        n = get_node(tree, a.mark)
        if not n:
            die("no such node: %s" % a.mark)
        rv = n.get("review") or {"interval": "1d", "level": 0}
        lvl = min(rv.get("level", 0) + 1, len(REVIEW_INTERVALS) - 1)
        interval = REVIEW_INTERVALS[lvl]
        days = {"1d": 1, "1w": 7, "1m": 30, "3m": 90}[interval]
        n["review"] = {"interval": interval, "level": lvl, "last_reviewed": now.isoformat(timespec="seconds"),
                       "due_at": (now + timedelta(days=days)).isoformat(timespec="seconds")}
        n["manual_status"] = None
        n["updated_at"] = now_iso()
        store.put_tree(tree)
        store.event("review_mark", node=a.mark, next_interval=interval)
        refresh(store)
        emit({"node": a.mark, "next": interval,
              "display": "已复习 %s，下次到期 %s（间隔 %s）" % (n["name"], n["review"]["due_at"], interval)})
    due = []
    for nid, n in tree["nodes"].items():
        rv = n.get("review")
        if not rv:
            continue
        due_at = parse_ts(rv.get("due_at"))
        if due_at and due_at <= now:
            due.append({"node": nid, "name": n["name"], "due_at": rv["due_at"],
                        "interval": rv["interval"], "status": n["status"], "mastery": n["mastery"]})
    scheduled = [{"node": nid, "name": n["name"], "due_at": n["review"]["due_at"],
                  "interval": n["review"]["interval"]} for nid, n in tree["nodes"].items() if n.get("review")]
    emit({"due": due, "scheduled": scheduled,
          "display": ("待复习 %d 个: %s" % (len(due), ", ".join(d["name"] for d in due))) if due
                    else "没有到期复习项。"})


def cmd_skip(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    a.node = resolve_node(store, a.node) or a.node
    n = get_node(tree, a.node)
    if not n:
        die("no such node: %s" % a.node)
    n["manual_status"] = "deferred"
    n["notes"].append({"at": now_iso(), "text": "用户要求跳过: %s" % (a.reason or "")})
    state = store.state()
    if (state.get("pending_question") or {}).get("node") == a.node:
        store.event("question_abandoned", question_id=state["pending_question"].get("id"),
                    node=a.node, reason="user skipped node")
        state["pending_question"] = None
    if (state.get("reprobe_pending") or {}).get("node") == a.node:
        state["reprobe_pending"] = None
    state["hint"] = {}
    state["hint_chain"] = {}
    store.put_state(state)
    store.put_tree(tree)
    store.event("skip", node=a.node, reason=a.reason)
    refresh(store)
    emit({"node": a.node, "display": "已跳过 %s（会记录在案；用户随时可以要求回来）" % n["name"]})


def cmd_source(a):
    store = Store(a.home, a.project)
    if a.source_action == "add":
        if not a.ref:
            die("source add requires --ref")
        if a.kind not in ("project_fact", "domain_knowledge", "inference"):
            fail(["--kind must be project_fact | domain_knowledge | inference"],
                 "facts must be labeled by provenance")
        node = resolve_node(store, a.node) if a.node else ""
        if a.node and not node:
            die("no such node: %s" % a.node)
        rec = _add_source(store, a.ref, a.kind, a.note or "", node or "")
        emit({"source": rec, "display": "已登记来源 %s（%s）" % (a.ref, a.kind)})
    emit({"sources": store.read("sources.json", {"sources": []})["sources"]})


def cmd_log(a):
    store = Store(a.home, a.project)
    emit({"tail": store.tail("questions.jsonl", a.tail)})


def cmd_export(a):
    store = Store(a.home, a.project)
    tree = store.tree()
    state = store.state()
    if a.format == "json":
        blob = {"tree": tree, "state": state, "edges": store.edges(),
                "evidence": store.evidence(), "mistakes": store.mistakes(),
                "sources": store.read("sources.json", {})}
        text = json.dumps(blob, ensure_ascii=False, indent=2)
    else:
        r = render_tree(store)
        m = store.mistakes()
        lines = ["# %s — 掌握度地图" % state.get("objective"),
                 "", "模式: %s | 第 %d 问 | 更新: %s" % (state.get("mode"), state.get("turn", 0), state.get("updated_at")),
                 "", "## 知识树", "", "```", r["tree"], "```", "", r["legend"], ""]
        gaps = [n for n in tree["nodes"].values() if n["status"] in ("gap", "misconception")]
        if gaps:
            lines += ["## 缺口", ""]
            for n in sorted(gaps, key=lambda n: -n["importance"]):
                lines.append("- **%s** [%s] %.2f — 问过 %d 次" % (n["name"], n["status"], n["mastery"], n["asked"]))
            lines.append("")
        if m["open"]:
            lines += ["## 未修正的误解", ""]
            for x in m["open"]:
                lines.append("- %s: 「%s」" % (get_node(tree, x["node"])["name"] if get_node(tree, x["node"]) else x["node"], x["belief"]))
            lines.append("")
        oq = state.get("opening_question")
        if oq:
            lines += ["## 主线未完成的问题", "", "- %s" % oq["text"], ""]
        text = "\n".join(lines)
    out = Path(a.out).expanduser() if a.out else (store.dir / ("export.%s" % a.format))
    out.write_text(text, encoding="utf-8")
    (store.dir / "session.md").write_text(resume_brief(store), encoding="utf-8")
    emit({"out": str(out), "display": "已导出 %s" % out})


def cmd_maintain(a):
    store = Store(a.home, a.project)
    sizes = {}
    for name in ("questions.jsonl", "history.jsonl"):
        p = store.path(name)
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
            sizes[name] = len(lines)
            store._rotate(name)
            sizes[name + "_after"] = len(store.path(name).read_text(encoding="utf-8").splitlines())
    arch = store.dir / "archive"
    emit({"sizes": sizes, "archives": len(list(arch.glob("*.jsonl"))) if arch.exists() else 0})



def cmd_doctor(a):
    """Operational health check: storage, schema, traversal coherence and source freshness."""
    store = Store(a.home, a.project)
    errors, warnings, repairs = [], [], []
    required = ["meta.json", "tree.json", "edges.json", "state.json",
                "evidence.json", "mistakes.json", "sources.json"]
    for name in required:
        p = store.path(name)
        if not p.exists():
            errors.append("missing required file: %s" % name)
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append("%s is not valid JSON: %s" % (name, e))

    tree = store.tree()
    state = store.state()
    meta = store.read("meta.json", {})
    if int(meta.get("workspace_schema", 0) or 0) != WORKSPACE_SCHEMA:
        errors.append("workspace schema is %r, expected %d" % (meta.get("workspace_schema"), WORKSPACE_SCHEMA))
    stack = state.get("return_stack") or []
    depth = (state.get("drill") or {}).get("depth", 0)
    if depth != len(stack):
        errors.append("drill depth %s disagrees with return_stack length %s" % (depth, len(stack)))
    for label, nid in (("focus", state.get("focus")),
                       ("pending_question", (state.get("pending_question") or {}).get("node")),
                       ("reprobe_pending", (state.get("reprobe_pending") or {}).get("node"))):
        if nid and nid not in tree.get("nodes", {}):
            errors.append("%s points to missing node %s" % (label, nid))
    if state.get("pending_question") and state.get("reprobe_pending"):
        errors.append("pending_question and reprobe_pending are both set; a return re-probe was not registered cleanly")

    # Source freshness: only compare sources that were local files when recorded.
    root = state.get("object_root")
    if root and not Path(root).is_dir():
        warnings.append("object_root no longer exists here: %s" % root)
    for src in store.read("sources.json", {"sources": []}).get("sources", []):
        fm = src.get("file_meta")
        if not fm or not root:
            continue
        base_ref = (src.get("ref") or "").split("#", 1)[0]
        fp = Path(base_ref)
        if not fp.is_absolute():
            fp = Path(root) / fp
        if not fp.exists():
            warnings.append("source disappeared: %s" % src.get("ref"))
            continue
        try:
            current = _file_meta(fp)
            changed = current["size"] != fm.get("size") or current["mtime_ns"] != fm.get("mtime_ns")
            if fm.get("sha256") and current["sha256"] != fm.get("sha256"):
                changed = True
            if changed:
                warnings.append("source changed since it was recorded: %s" % src.get("ref"))
        except Exception as e:
            warnings.append("could not fingerprint source %s: %s" % (src.get("ref"), e))

    for tmp in store.dir.glob("*.tmp"):
        warnings.append("stray temporary file: %s" % tmp.name)
    for name in ("questions.jsonl", "history.jsonl"):
        p = store.path(name)
        if p.exists() and p.stat().st_size > 2_000_000:
            warnings.append("%s is large (%d bytes); run doctor --repair or maintain to rotate it" % (name, p.stat().st_size))

    if a.repair and not errors:
        refresh(store)
        repairs.append("recomputed derived mastery/status fields")
        for name in ("questions.jsonl", "history.jsonl"):
            store._rotate(name)
        repairs.append("rotated oversized logs if needed")
        for tmp in list(store.dir.glob("*.tmp")):
            try:
                tmp.unlink()
                repairs.append("removed %s" % tmp.name)
            except Exception:
                pass
        meta = store.read("meta.json", {})
        meta.update({"workspace_schema": WORKSPACE_SCHEMA, "engine_version": VERSION,
                     "last_doctor_repair": now_iso()})
        store.write("meta.json", meta)

    healthy = not errors
    lines = ["doctor: %s | schema=%d | project=%s" % ("OK" if healthy else "FAIL", WORKSPACE_SCHEMA, a.project)]
    if root:
        lines.append("object_root: %s" % root)
    if errors:
        lines.append("errors:\n" + "\n".join("  - " + x for x in errors))
    if warnings:
        lines.append("warnings:\n" + "\n".join("  - " + x for x in warnings))
    if repairs:
        lines.append("repairs:\n" + "\n".join("  - " + x for x in repairs))
    emit({"healthy": healthy, "errors": errors, "warnings": warnings, "repairs": repairs,
          "display": "\n".join(lines)}, 0 if healthy else 3)

def cmd_refresh(a):
    store = Store(a.home, a.project)
    t = refresh(store)
    c = status_counts(t)
    emit({"counts": c, "nodes": len(t["nodes"]),
          "display": "已重算全部节点状态: " + ", ".join("%s=%d" % kv for kv in sorted(c.items()))})


def cmd_projects(a):
    home = Path(a.home)
    cur = (home / "current").read_text(encoding="utf-8").strip() if (home / "current").exists() else None
    items = []
    if home.is_dir():
        for d in sorted(home.iterdir()):
            if d.is_dir() and (d / "tree.json").exists():
                st = json.loads((d / "state.json").read_text(encoding="utf-8")) if (d / "state.json").exists() else {}
                items.append({"project": d.name, "mode": st.get("mode"), "turn": st.get("turn", 0),
                              "updated_at": st.get("updated_at"), "objective": st.get("objective")})
    emit({"current": cur, "projects": items})


# ---------------------------------------------------------------- main

def main(argv=None):
    p = argparse.ArgumentParser(prog="mi.py", description="Mastery Interrogator state machine")
    p.add_argument("-R", "--root", default=None,
                   help="project directory; state is kept in <root>/%s/ "
                        "(default: nearest ancestor of cwd that has one, or that looks like a project)"
                        % STATE_DIR_NAME)
    p.add_argument("-S", "--state-dir", "--home", dest="state_dir", default=None,
                   help="state root itself, overriding --root (alias: --home); env: MASTERY_STATE_DIR")
    p.add_argument("-p", "--project", default=None, help="workspace name (default: last used in this state dir)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("-R", "--root", default=argparse.SUPPRESS,
                        help="project directory; state lives in <root>/%s/" % STATE_DIR_NAME)
        sp.add_argument("-S", "--state-dir", "--home", dest="state_dir", default=argparse.SUPPRESS,
                        help="state root itself, overriding --root (alias: --home)")
        sp.add_argument("-p", "--project", default=argparse.SUPPRESS)

    s = sub.add_parser("init"); common(s)
    s.add_argument("--mode", default="generic", choices=["project", "modeling", "generic"])
    s.add_argument("--objective", default="")
    s.add_argument("--seed", default="generic", help="generic|project|modeling|none")
    s.add_argument("--source", action="append")
    s.add_argument("--force", action="store_true")
    s.add_argument("--gitignore", action="store_true",
                   help="append %s/ to the project's .gitignore" % STATE_DIR_NAME)
    s.add_argument("--drill-budget", type=int, default=DEFAULT_DRILL_BUDGET)
    s.add_argument("--max-drill-depth", type=int, default=DEFAULT_MAX_DRILL_DEPTH)
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("ask"); common(s)
    s.add_argument("--node")
    s.add_argument("--text")
    s.add_argument("--text-file", help="read question text from UTF-8 file; use - for stdin")
    s.add_argument("--kind", default="probe",
                   choices=["probe", "explain_back", "why", "boundary", "counterexample",
                            "compare", "debug", "modify", "transfer", "recall", "connect"])
    s.add_argument("--exam", action="store_true", help="mark this as an exam item; exams still ask one item per turn")
    s.add_argument("--mainline", action="store_true", help="this is the mainline question")
    s.add_argument("--reprobe-of")
    s.add_argument("--force", action="store_true")
    s.add_argument("--reason")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("answer"); common(s)
    s.add_argument("--node")
    s.add_argument("--text")
    s.add_argument("--text-file", help="read answer text from UTF-8 file; use - for stdin")
    s.add_argument("--idk", action="store_true")
    s.add_argument("--evidence", action="append")
    s.add_argument("--hinted", action="store_true")
    s.add_argument("--misconception")
    s.add_argument("--corrected", action="append", help="close a misconception by id")
    s.add_argument("--discover", action="append", help="NAME=description for a newly discovered concept")
    s.add_argument("--discover-parent")
    s.add_argument("--clear-opening", action="store_true")
    s.add_argument("--neutral", action="store_true", help="consume a response explicitly without counting it as mastery evidence")
    s.add_argument("--orphan", action="store_true", help="legacy/import repair only: allow evidence with no pending question")
    s.set_defaults(func=cmd_answer)

    s = sub.add_parser("teach"); common(s)
    s.add_argument("--node")
    s.add_argument("--level", type=int, required=True, choices=[1, 2, 3])
    s.add_argument("--text")
    s.add_argument("--text-file", help="read hint/teaching text from UTF-8 file; use - for stdin")
    s.add_argument("--stuck-point")
    s.add_argument("--recovery-text")
    s.add_argument("--retry-text", help="L1/L2 retry question when no question is currently pending")
    s.add_argument("--retry-kind", choices=["probe", "explain_back", "why", "boundary", "counterexample",
                                               "compare", "debug", "modify", "transfer", "recall", "connect"])
    s.add_argument("--floor", action="store_true", help="accept this node as the floor and teach upward")
    s.add_argument("--force", action="store_true")
    s.add_argument("--reason")
    s.set_defaults(func=cmd_teach)

    s = sub.add_parser("drill"); common(s)
    s.add_argument("--node")
    s.add_argument("--prereq")
    s.add_argument("--prereq-id")
    s.add_argument("--reason", required=True)
    s.add_argument("--desc")
    s.add_argument("--blocking", action="store_true")
    s.add_argument("--force", action="store_true", help="allow an off-focus drill; recorded as an exceptional operation")
    s.set_defaults(func=cmd_drill)

    s = sub.add_parser("return"); common(s)
    s.add_argument("--node")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_return)

    s = sub.add_parser("next"); common(s); s.set_defaults(func=cmd_next)
    s = sub.add_parser("where"); common(s); s.set_defaults(func=cmd_where)

    s = sub.add_parser("render"); common(s)
    s.add_argument("--full", action="store_true")
    s.add_argument("--depth", type=int)
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("status"); common(s); s.set_defaults(func=cmd_status)
    s = sub.add_parser("resume"); common(s); s.set_defaults(func=cmd_resume)
    s = sub.add_parser("validate"); common(s); s.set_defaults(func=cmd_validate)
    s = sub.add_parser("refresh"); common(s); s.set_defaults(func=cmd_refresh)

    s = sub.add_parser("audit"); common(s)
    s.add_argument("--node", required=True)
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("challenge"); common(s)
    s.add_argument("--node", required=True)
    s.add_argument("--note", required=True)
    s.add_argument("--misconception")
    s.set_defaults(func=cmd_challenge)

    s = sub.add_parser("review"); common(s)
    s.add_argument("--due", action="store_true")
    s.add_argument("--mark")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("skip"); common(s)
    s.add_argument("--node", required=True)
    s.add_argument("--reason")
    s.set_defaults(func=cmd_skip)

    s = sub.add_parser("source"); common(s)
    s.add_argument("source_action", nargs="?", default="list", choices=["add", "list"])
    s.add_argument("--ref")
    s.add_argument("--kind", default="project_fact")
    s.add_argument("--note")
    s.add_argument("--node")
    s.set_defaults(func=cmd_source)

    s = sub.add_parser("log"); common(s)
    s.add_argument("--tail", type=int, default=20)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("export"); common(s)
    s.add_argument("--format", default="md", choices=["md", "json"])
    s.add_argument("--out")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("maintain"); common(s); s.set_defaults(func=cmd_maintain)
    s = sub.add_parser("doctor"); common(s)
    s.add_argument("--repair", action="store_true", help="refresh derived fields, rotate logs, and clean safe temporary files")
    s.set_defaults(func=cmd_doctor)
    s = sub.add_parser("projects"); common(s); s.set_defaults(func=cmd_projects)

    s = sub.add_parser("node"); common(s)
    s.add_argument("node_action", choices=["add", "update", "split", "merge", "reparent", "remove"])
    s.add_argument("--id")
    s.add_argument("--name")
    s.add_argument("--parent")
    s.add_argument("--desc")
    s.add_argument("--kind", choices=NODE_KINDS)
    s.add_argument("--origin", choices=["seed", "manual", "discovered", "split", "merged"])
    s.add_argument("--importance", type=int)
    s.add_argument("--blocking", action="store_true", default=None)
    s.add_argument("--tag", action="append")
    s.add_argument("--source", action="append")
    s.add_argument("--note")
    s.add_argument("--status", choices=STATUSES)
    s.add_argument("--into")
    s.add_argument("--reason")
    s.add_argument("--discovered-from")
    s.set_defaults(func=cmd_node)

    s = sub.add_parser("edge"); common(s)
    s.add_argument("edge_action", choices=["add", "remove", "list"])
    s.add_argument("--from", dest="from_")
    s.add_argument("--to")
    s.add_argument("--type", default="depends_on", choices=EDGE_TYPES)
    s.add_argument("--strength", default="required", choices=["required", "helpful"])
    s.add_argument("--reason")
    s.set_defaults(func=cmd_edge)

    args = p.parse_args(argv)
    args.gitignore = getattr(args, "gitignore", False)

    # ---- resolve where the state lives -------------------------------------
    #   1. --state-dir / --home / MASTERY_STATE_DIR (explicit state root)
    #   2. $MASTERY_ROOT                          (project dir)
    #   3. --root                                 (project dir -> <root>/.mastery)
    #   4. nearest ancestor of cwd holding a .mastery
    #   There is deliberately NO global fallback: state follows the project.
    explicit = getattr(args, "state_dir", None) or os.environ.get("MASTERY_STATE_DIR")
    root_opt = getattr(args, "root", None)
    # MASTERY_HOME belonged to the old global-workspace design; state now follows
    # the project, so it is no longer honoured. Say so once, loudly, at init
    # instead of silently ignoring a variable the user set.
    legacy_home = os.environ.get("MASTERY_HOME")
    legacy_note = None
    if legacy_home and not os.environ.get("MASTERY_STATE_DIR"):
        legacy_note = ("注意：环境变量 MASTERY_HOME=%s 已不再生效——状态现在跟着项目走"
                       "（<项目>/.mastery/）。想用共享目录请用 --state-dir 或 MASTERY_STATE_DIR。"
                       % legacy_home)

    if explicit:
        args.home = str(Path(explicit).expanduser())
        args.home_is_local = False
        args.object_root = str(Path(root_opt).expanduser().resolve()) if root_opt else None
        if root_opt and not Path(args.object_root).is_dir():
            die("--root is not a directory: %s" % args.object_root)
        Path(args.home).mkdir(parents=True, exist_ok=True)
    elif root_opt or os.environ.get("MASTERY_ROOT"):
        base = Path(root_opt or os.environ["MASTERY_ROOT"]).expanduser()
        if not base.is_dir():
            die("--root is not a directory: %s" % base)
        args.home = str((base.resolve() / STATE_DIR_NAME))
        args.home_is_local = True
        args.object_root = str(base.resolve())
    else:
        state_dir, project_dir, how = find_state_dir(Path.cwd())
        if state_dir is None:
            if args.cmd == "init":
                project_dir, how = guess_project_dir(Path.cwd())
                state_dir = project_dir / STATE_DIR_NAME
                args.home = str(state_dir)
                args.home_is_local = True
                args.object_root = str(project_dir.resolve())
                args._resolved_how = how
            else:
                die("no workspace found: no %s/ directory in %s or any parent. "
                    "Run with --root <project dir> (state goes to <root>/%s/), or "
                    "--state-dir <dir> for a shared location."
                    % (STATE_DIR_NAME, Path.cwd(), STATE_DIR_NAME))
        else:
            args.home = str(state_dir)
            args.home_is_local = False if how.startswith("--state-dir") else True
            args.object_root = str(project_dir.resolve()) if project_dir else None
            args._resolved_how = how

    args.legacy_note = legacy_note
    if args.cmd != "init" and not getattr(args, "project", None):
        cur = Path(args.home) / "current"
        if cur.exists():
            args.project = cur.read_text(encoding="utf-8").strip()
        else:
            die("no workspace name given and no 'current' file in %s (use --project)"
                % args.home)
    args.func(args)


if __name__ == "__main__":
    main()

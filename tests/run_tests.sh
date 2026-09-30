#!/usr/bin/env sh
# Canonical verifier for the mastery-interrogator skill.
# Run from anywhere:  sh tests/run_tests.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
fails=0

echo "=== python syntax ==="
if python3 -m py_compile "$ROOT/scripts/mi.py"; then echo "  ok"; else fails=$((fails+1)); fi

echo "=== unit + contract + acceptance tests ==="
if (cd "$ROOT" && python3 -m unittest discover -s tests -p 'test_*.py' -t . -v); then
  echo "  ok"
else
  fails=$((fails+1))
fi

echo "=== CLI smoke (fresh home, generic seed) ==="
TMP="$(mktemp -d)"
if MI_HOME="$TMP" python3 "$ROOT/scripts/mi.py" --home "$TMP" init --project smoke \
      --mode generic --seed generic --objective "smoke" >/dev/null \
   && python3 "$ROOT/scripts/mi.py" --home "$TMP" render >/dev/null \
   && python3 "$ROOT/scripts/mi.py" --home "$TMP" validate >/dev/null; then
  echo "  ok"
else
  echo "  FAIL: smoke run"
  fails=$((fails+1))
fi
rm -rf "$TMP"

find "$ROOT" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo
if [ "$fails" -eq 0 ]; then
  echo "== run_tests: ALL GREEN =="
  exit 0
else
  echo "== run_tests: $fails suite(s) failed =="
  exit 1
fi

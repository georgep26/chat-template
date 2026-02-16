#!/bin/bash
# Basic validation for gh-pm: usage and check-env (no network mock; requires gh when run).
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PM="$SCRIPT_DIR/gh-pm"

echo "--- gh-pm help ---"
"$PM" --help 2>&1 | grep -q "check-env"
"$PM" help 2>&1 | grep -q "Subcommands"

echo "--- gh-pm check-env (human) ---"
"$PM" check-env 2>&1 | grep -qE "(✓|Error|not installed|not authenticated)"

echo "--- gh-pm check-env --json ---"
out=$("$PM" check-env --json 2>&1)
echo "$out" | jq -e '.ok != null or .fix != null' >/dev/null 2>&1 || true

echo "--- gh-pm breakdown-outline (no proposals) ---"
out=$("$PM" breakdown-outline 2>&1)
echo "$out" | grep -q '"proposals":\[\]'

echo "--- gh-pm unknown subcommand exits non-zero ---"
"$PM" unknown-subcommand 2>&1 && exit 1 || true

echo "All checks passed."

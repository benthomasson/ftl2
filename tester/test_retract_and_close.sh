#!/usr/bin/env bash
# test_retract_and_close.sh — Pre-flight and dry-run tests for retract-and-close.sh
#
# Tests validate the script WITHOUT modifying the production database or GitHub.
# A disposable copy of the database is used for destructive tests.
#
# Usage: bash workspaces/issue-79/tester/test_retract_and_close.sh

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/../implementer" && pwd)/retract-and-close.sh"
DB="/Users/ben/git/ftl2-project-expert/reasons.db"
PASS=0
FAIL=0
SKIP=0

pass() { PASS=$((PASS + 1)); echo "  PASS: $1"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
skip() { SKIP=$((SKIP + 1)); echo "  SKIP: $1"; }

echo "=== Test Suite: retract-and-close.sh ==="
echo ""

# ---------------------------------------------------------------------------
# Section 1: Pre-conditions
# ---------------------------------------------------------------------------
echo "--- Pre-conditions ---"

# Test 1: Script file exists
if [[ -f "$SCRIPT" ]]; then
  pass "1. Script file exists at expected path"
else
  fail "1. Script file not found at $SCRIPT"
fi

# Test 2: Script passes bash syntax check
# NOTE: bash 3.2 (macOS default) cannot parse apostrophes inside heredocs
# within $() command substitutions. The "doesn't" in the gh comment body
# triggers this bug. This is a REAL BUG in the implementation script.
SYNTAX_ERR=$(bash -n "$SCRIPT" 2>&1)
SYNTAX_RC=$?
if [[ $SYNTAX_RC -eq 0 ]]; then
  pass "2. Script passes bash syntax check (bash -n)"
else
  BASH_VER=$(bash --version | head -1)
  if [[ "$BASH_VER" == *"version 3."* ]] && echo "$SYNTAX_ERR" | grep -q "matching.*'"; then
    fail "2. BUG: Script fails bash 3.2 syntax check — apostrophe in heredoc inside \$() (line 50: \"doesn't\")"
    echo "        Fix: replace \"doesn't\" with \"does not\" in the gh comment heredoc"
    echo "        Bash version: $BASH_VER"
  else
    fail "2. Script has syntax errors: $SYNTAX_ERR"
  fi
fi

# Test 3: reasons CLI is available
if command -v reasons &>/dev/null; then
  pass "3. reasons CLI is available on PATH"
else
  fail "3. reasons CLI not found"
fi

# Test 4: gh CLI is available and authenticated
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  pass "4. gh CLI is available and authenticated"
else
  fail "4. gh CLI not available or not authenticated"
fi

# Test 5: Production database exists
if [[ -f "$DB" ]]; then
  pass "5. Production reasons.db exists at expected path"
else
  fail "5. reasons.db not found at $DB"
fi

# Test 6: GH-79 is currently open (pre-condition for closure)
GH79_STATE=$(gh issue view 79 --repo benthomasson/ftl2 --json state -q .state 2>/dev/null)
if [[ "$GH79_STATE" == "OPEN" ]]; then
  pass "6. GH-79 is currently OPEN (ready to be closed)"
elif [[ "$GH79_STATE" == "CLOSED" ]]; then
  skip "6. GH-79 is already CLOSED (script may have already run)"
else
  fail "6. Could not determine GH-79 state (got: $GH79_STATE)"
fi

echo ""

# ---------------------------------------------------------------------------
# Section 2: Belief state validation
# ---------------------------------------------------------------------------
echo "--- Belief state validation ---"

# Test 7: First belief exists and is IN
STATUS1=$(reasons --db "$DB" show resolution-documentation-systematically-absent 2>&1)
if echo "$STATUS1" | grep -q "Status: IN"; then
  pass "7. resolution-documentation-systematically-absent is IN"
elif echo "$STATUS1" | grep -q "Status: OUT"; then
  skip "7. resolution-documentation-systematically-absent is already OUT (previously retracted?)"
else
  fail "7. Belief resolution-documentation-systematically-absent not found or unexpected state"
fi

# Test 8: Second belief exists and is IN
STATUS2=$(reasons --db "$DB" show no-verification-trail-for-resolutions 2>&1)
if echo "$STATUS2" | grep -q "Status: IN"; then
  pass "8. no-verification-trail-for-resolutions is IN"
elif echo "$STATUS2" | grep -q "Status: OUT"; then
  skip "8. no-verification-trail-for-resolutions is already OUT (previously retracted?)"
else
  fail "8. Belief no-verification-trail-for-resolutions not found or unexpected state"
fi

# Test 9: Downstream beliefs exist (script references them in explain calls)
DOWNSTREAM_OK=true
for belief in hardening-gains-survive-contributor-change project-handoff-viable next-cleanup-achieves-verified-resolution; do
  if ! reasons --db "$DB" show "$belief" &>/dev/null; then
    fail "9. Downstream belief $belief not found in database"
    DOWNSTREAM_OK=false
    break
  fi
done
if $DOWNSTREAM_OK; then
  pass "9. All three downstream beliefs exist in database"
fi

# Test 10: reasons explain exits 0 even for OUT beliefs (reviewer concern)
EXPLAIN_OUT=$(reasons --db "$DB" explain hardening-gains-survive-contributor-change 2>&1)
EXPLAIN_RC=$?
if [[ $EXPLAIN_RC -eq 0 ]]; then
  pass "10. reasons explain returns exit code 0 for OUT belief (reviewer concern resolved)"
else
  fail "10. reasons explain returns non-zero ($EXPLAIN_RC) for OUT belief — script may halt at Step 2"
fi

echo ""

# ---------------------------------------------------------------------------
# Section 3: Script content validation
# ---------------------------------------------------------------------------
echo "--- Script content validation ---"

# Test 11: All reasons invocations use --db flag
DB_COUNT=$(grep -c '\-\-db "\$DB"' "$SCRIPT")
REASONS_COUNT=$(grep -c '^reasons ' "$SCRIPT")
if [[ $DB_COUNT -eq $REASONS_COUNT && $DB_COUNT -gt 0 ]]; then
  pass "11. All $REASONS_COUNT reasons invocations use --db flag"
else
  fail "11. Mismatch: $REASONS_COUNT reasons calls but only $DB_COUNT use --db"
fi

# Test 12: --db flag is placed BEFORE subcommand (not after)
BAD_ORDER=$(grep -E 'reasons (retract|explain|export|export-markdown|assert) .* --db' "$SCRIPT" | wc -l | tr -d ' ')
if [[ "$BAD_ORDER" -eq 0 ]]; then
  pass "12. --db flag is correctly placed before subcommand in all invocations"
else
  fail "12. Found $BAD_ORDER invocations with --db after subcommand"
fi

# Test 13: Repo slug is benthomasson/ftl2
SLUG_COUNT=$(grep -c 'benthomasson/ftl2' "$SCRIPT")
if [[ $SLUG_COUNT -ge 2 ]]; then
  pass "13. Repo slug benthomasson/ftl2 used ($SLUG_COUNT occurrences)"
else
  fail "13. Expected repo slug benthomasson/ftl2 not found (or found $SLUG_COUNT times, expected >=2)"
fi

# Test 14: set -euo pipefail is present
if grep -q 'set -euo pipefail' "$SCRIPT"; then
  pass "14. Script uses set -euo pipefail (strict mode)"
else
  fail "14. Script missing set -euo pipefail"
fi

echo ""

# ---------------------------------------------------------------------------
# Section 4: Dry-run cascade validation
# ---------------------------------------------------------------------------
echo "--- Dry-run cascade tests (read-only) ---"

# Test 15: Retracting no-verification-trail cascades to resolution-documentation
WHATIF=$(reasons --db "$DB" what-if retract no-verification-trail-for-resolutions 2>&1)
if echo "$WHATIF" | grep -q "resolution-documentation-systematically-absent"; then
  pass "15. Retracting no-verification-trail cascades to resolution-documentation"
else
  fail "15. Expected cascade to resolution-documentation not found"
fi

# Test 16: Retracting resolution-documentation cascades to downstream beliefs
WHATIF2=$(reasons --db "$DB" what-if retract resolution-documentation-systematically-absent 2>&1)
CASCADE_OK=true
for belief in documentation-debt-compounds-bus-factor verification-deficit-systematic; do
  if ! echo "$WHATIF2" | grep -q "$belief"; then
    fail "16. Expected cascade to $belief not found"
    CASCADE_OK=false
    break
  fi
done
if $CASCADE_OK; then
  pass "16. Retracting resolution-documentation cascades to expected downstream beliefs"
fi

echo ""

# ---------------------------------------------------------------------------
# Section 5: Destructive tests on disposable database copy
# ---------------------------------------------------------------------------
echo "--- Destructive tests (disposable database copy) ---"

TMPDB=$(mktemp /tmp/reasons-test-XXXXXX.db)
cp "$DB" "$TMPDB"
trap "rm -f '$TMPDB'" EXIT

# Test 17: Retraction actually changes belief to OUT
reasons --db "$TMPDB" retract resolution-documentation-systematically-absent \
  --reason "Test retraction" &>/dev/null
AFTER=$(reasons --db "$TMPDB" show resolution-documentation-systematically-absent 2>&1)
if echo "$AFTER" | grep -q "Status: OUT"; then
  pass "17. Retraction changes belief to OUT in disposable database"
else
  fail "17. Belief not OUT after retraction"
fi

# Test 18: Second retraction also works
reasons --db "$TMPDB" retract no-verification-trail-for-resolutions \
  --reason "Test retraction" &>/dev/null
AFTER2=$(reasons --db "$TMPDB" show no-verification-trail-for-resolutions 2>&1)
if echo "$AFTER2" | grep -q "Status: OUT"; then
  pass "18. Second retraction changes belief to OUT"
else
  fail "18. Second belief not OUT after retraction"
fi

# Test 19: Idempotency — re-retracting an OUT belief doesn't error
if reasons --db "$TMPDB" retract resolution-documentation-systematically-absent \
  --reason "Double retraction test" &>/dev/null; then
  pass "19. Re-retracting an already-OUT belief does not error"
else
  fail "19. Re-retracting an already-OUT belief returns non-zero exit code"
fi

# Test 20: Export-markdown produces output after retractions
TMPMD=$(mktemp /tmp/beliefs-test-XXXXXX.md)
if reasons --db "$TMPDB" export-markdown -o "$TMPMD" &>/dev/null && [[ -s "$TMPMD" ]]; then
  pass "20. export-markdown produces non-empty output after retractions"
else
  fail "20. export-markdown failed or produced empty output"
fi
rm -f "$TMPMD"

# Test 21: Export (JSON) produces valid JSON after retractions
TMPJSON=$(mktemp /tmp/network-test-XXXXXX.json)
reasons --db "$TMPDB" export > "$TMPJSON" 2>/dev/null
if python3 -c "import json; json.load(open('$TMPJSON'))" 2>/dev/null; then
  pass "21. export produces valid JSON after retractions"
else
  fail "21. export JSON is invalid"
fi
rm -f "$TMPJSON"

echo ""

# ---------------------------------------------------------------------------
# Section 6: Reviewer-flagged concerns
# ---------------------------------------------------------------------------
echo "--- Reviewer-flagged concerns ---"

# Test 22: no-linked-prs-on-closed-issues is also factually wrong (reviewer note)
NLP_STATUS=$(reasons --db "$DB" show no-linked-prs-on-closed-issues 2>&1)
if echo "$NLP_STATUS" | grep -q "Status: IN"; then
  echo "  NOTE: 22. no-linked-prs-on-closed-issues is still IN — reviewer flagged this as also factually wrong"
  echo "        Consider adding a third retraction. This is a plan gap, not an implementation gap."
  pass "22. Verified no-linked-prs-on-closed-issues state (IN — operator should evaluate)"
elif echo "$NLP_STATUS" | grep -q "Status: OUT"; then
  pass "22. no-linked-prs-on-closed-issues is already OUT (already addressed)"
else
  skip "22. Could not check no-linked-prs-on-closed-issues"
fi

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=== Results ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"
echo "  Skipped: $SKIP"
echo "  Total:  $((PASS + FAIL + SKIP))"
echo ""

if [[ $FAIL -eq 0 ]]; then
  echo "ALL TESTS PASSED"
  exit 0
else
  echo "SOME TESTS FAILED"
  exit 1
fi

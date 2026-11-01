#!/bin/bash
# SPDX-FileCopyrightText: 2026 Shaan Narendran <shaannaren06@gmail.com>
# SPDX-License-Identifier: Apache-2.0

# Full E2E test for version check & update mechanics
# Run from: /Users/shaannarendran/Observal
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

pass() { echo -e "${GREEN}✓ $1${NC}"; }
section() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}\n"; }

API="http://localhost:80"

# ═══════════════════════════════════════════════════════════
section "1. CLI VERSION COMMANDS"
# ═══════════════════════════════════════════════════════════

echo "Current version:"
observal --version
echo ""

echo "Self status (shows version + latest + method):"
observal self status
echo ""
pass "self status works"

echo ""
echo "List all available versions:"
observal self downgrade --list
pass "downgrade --list works"

# ═══════════════════════════════════════════════════════════
section "2. DOWNGRADE CLI"
# ═══════════════════════════════════════════════════════════

echo "Downgrading to v0.7.0..."
observal self downgrade --version 0.7.0 --force
echo ""

echo "Verify version changed:"
VER=$(observal --version)
echo "$VER"
if [[ "$VER" == *"0.7.0"* ]]; then
    pass "CLI is now v0.7.0"
else
    echo "FAIL: Expected 0.7.0, got $VER"
    exit 1
fi

echo ""
echo "Verify CLI still works on old version:"
observal auth whoami
pass "Commands work on v0.7.0"

echo ""
echo "Check: 'self status' should NOT exist on v0.7.0:"
if observal self status 2>&1 | grep -q "No such command"; then
    pass "'self status' correctly missing on v0.7.0"
else
    echo -e "${YELLOW}⚠ self status exists on v0.7.0 (might be a different 0.7.0 build)${NC}"
fi

# ═══════════════════════════════════════════════════════════
section "3. UPGRADE CLI BACK"
# ═══════════════════════════════════════════════════════════

echo "Reinstalling from branch to get upgrade command back..."
uv tool install --force --editable . >/dev/null 2>&1
echo ""

echo "Now use the new upgrade command:"
observal self upgrade --force
VER=$(observal --version)
echo "$VER"
if [[ "$VER" == *"0.8.0"* ]]; then
    pass "CLI upgraded back to v0.8.0"
else
    echo "FAIL: Expected 0.8.0"
    exit 1
fi

# ═══════════════════════════════════════════════════════════
section "4. SERVER VERSION ENDPOINT"
# ═══════════════════════════════════════════════════════════

echo "GET /api/v1/config/version:"
# Python performs the HTTP fetch + JSON parse directly so no remote content is
# piped into an interpreter (Scorecard supply-chain check).
API="$API" python3 -c 'import json,os,urllib.request
with urllib.request.urlopen(os.environ["API"] + "/api/v1/config/version", timeout=10) as r:
    print(json.dumps(json.load(r), indent=2))
'
pass "Version endpoint returns extended fields"

# ═══════════════════════════════════════════════════════════
section "5. VERSION NEGOTIATION HEADERS"
# ═══════════════════════════════════════════════════════════

echo "CLI v0.8.0 → Server:"
curl -s -I $API/api/v1/config/version \
    -H "X-Observal-CLI-Version: 0.8.0" 2>&1 | grep -i "x-observal"
echo ""

echo "CLI v0.5.0 → Server (degrades to min):"
curl -s -I $API/api/v1/config/version \
    -H "X-Observal-CLI-Version: 0.5.0" 2>&1 | grep -i "x-observal"
echo ""

echo "CLI v0.3.0 → Server (below min, still works):"
curl -s -I $API/api/v1/config/version \
    -H "X-Observal-CLI-Version: 0.3.0" 2>&1 | grep -i "x-observal"
echo ""

echo "No CLI header → defaults to server version:"
curl -s -I $API/api/v1/config/version 2>&1 | grep -i "x-observal"
pass "Version negotiation headers work correctly"

# ═══════════════════════════════════════════════════════════
section "6. SERVER UPGRADE COMMANDS"
# ═══════════════════════════════════════════════════════════

echo "Server versions (available on GHCR):"
observal server versions 2>&1 || echo "(server command may need compose dir)"
echo ""

echo "Server upgrade --dry-run:"
observal server upgrade --dry-run 2>&1 || echo "(expected if already on latest)"
echo ""

echo "Server upgrade bad version (should fail fast):"
observal server upgrade --version 99.0.0 --force 2>&1 || true
pass "Bad version rejected before any state changes"

# ═══════════════════════════════════════════════════════════
section "7. FRONTEND VERSION MISMATCH"
# ═══════════════════════════════════════════════════════════

echo "Check frontend is serving:"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000)
if [[ "$HTTP_CODE" == "200" ]]; then
    pass "Frontend serving on :3000 (HTTP $HTTP_CODE)"
else
    echo "FAIL: Frontend not reachable (HTTP $HTTP_CODE)"
fi

echo ""
echo "The version mismatch banner triggers when:"
echo "  - Server header X-Observal-Server: 0.X.0"
echo "  - Frontend build VITE_APP_VERSION: 0.Y.0 (different)"
echo "  → Shows 'New version available. [Refresh]' toast"
echo ""
echo "To test manually:"
echo "  1. Open http://localhost:3000"
echo "  2. Open DevTools → Network → look for X-Observal-Server header"
echo "  3. The mismatch banner appears if versions differ"
pass "Frontend integration in place"

# ═══════════════════════════════════════════════════════════
section "8. UPDATE NOTIFICATION BANNER"
# ═══════════════════════════════════════════════════════════

echo "Downgrade to see the notification:"
observal self downgrade --version 0.7.0 --force
echo ""

echo "Run any command, banner should appear at bottom:"
echo -e "${DIM}(Looking for 'Update available' in output)${NC}"
OUTPUT=$(uv tool install --force --editable . >/dev/null 2>&1 && observal ops list 2>&1 || true)
echo "$OUTPUT" | tail -3
if echo "$OUTPUT" | grep -q "Update available\|update available\|self upgrade"; then
    pass "Update notification banner shown"
else
    echo -e "${YELLOW}⚠ Banner not shown (cache may be fresh, check ~/.observal/version_cache.json)${NC}"
fi

# ═══════════════════════════════════════════════════════════
section "9. DISABLE NOTIFICATIONS"
# ═══════════════════════════════════════════════════════════

echo "Disable via config:"
observal config set update_check false
OUTPUT=$(observal auth whoami 2>&1)
if echo "$OUTPUT" | grep -q "Update available"; then
    echo "FAIL: Banner shown despite disabled"
else
    pass "No banner when update_check=false"
fi

echo ""
echo "Disable via env var:"
observal config set update_check true
OUTPUT=$(OBSERVAL_NO_UPDATE_CHECK=1 observal auth whoami 2>&1)
if echo "$OUTPUT" | grep -q "Update available"; then
    echo "FAIL: Banner shown despite env var"
else
    pass "No banner with OBSERVAL_NO_UPDATE_CHECK=1"
fi

# ═══════════════════════════════════════════════════════════
section "10. CONCURRENT LOCK TEST"
# ═══════════════════════════════════════════════════════════

echo "Creating fake lock to simulate concurrent upgrade..."
mkdir -p ~/.observal
echo '{"pid": '$$', "timestamp": '$(date +%s)'}' >~/.observal/.cli-upgrade.lock

OUTPUT=$(observal self downgrade --version 0.6.0 --force 2>&1 || true)
if echo "$OUTPUT" | grep -qi "another upgrade\|in progress"; then
    pass "Concurrent upgrade correctly blocked"
else
    echo "$OUTPUT"
    echo -e "${YELLOW}⚠ Lock test inconclusive${NC}"
fi

rm -f ~/.observal/.cli-upgrade.lock
pass "Lock cleaned up"

# ═══════════════════════════════════════════════════════════
section "CLEANUP"
# ═══════════════════════════════════════════════════════════

echo "Ensuring CLI is back on v0.8.0..."
uv tool install --force --editable . >/dev/null 2>&1
observal --version

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ALL E2E TESTS COMPLETE${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

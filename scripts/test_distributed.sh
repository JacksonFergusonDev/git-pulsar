#!/usr/bin/env bash
# Enforce strict error handling
set -eo pipefail

# ANSI color codes for terminal output
BLUE='\033[1;34m'
GREEN='\033[1;32m'
RED='\033[1;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}ℹ Initializing Ephemeral Distributed Cluster...${NC}"

# Create isolated workspace
TEST_DIR=$(mktemp -d)
REMOTE="$TEST_DIR/remote.git"
MAC1_DIR="$TEST_DIR/mac1"
MAC2_DIR="$TEST_DIR/mac2"

# --- Cleanup & Trap Handlers ---
cleanup() {
    echo -e "${BLUE}ℹ Cleaning up ephemeral test directory...${NC}"
    rm -rf "$TEST_DIR"
}

error_handler() {
    echo -e "\n${RED}❌ TEST FAILED. Execution aborted.${NC}"

    # Dump Node A logs if they exist
    if [ -f "$TEST_DIR/state_mac1/git-pulsar/daemon.log" ]; then
        echo -e "${YELLOW}--- Node A (Mac 1) Logs ---${NC}"
        cat "$TEST_DIR/state_mac1/git-pulsar/daemon.log"
    fi

    # Dump Node B logs if they exist
    if [ -f "$TEST_DIR/state_mac2/git-pulsar/daemon.log" ]; then
        echo -e "${YELLOW}--- Node B (Mac 2) Logs ---${NC}"
        cat "$TEST_DIR/state_mac2/git-pulsar/daemon.log"
    fi

    cleanup
    exit 1
}

# Bind traps to signals
trap cleanup EXIT
trap error_handler ERR

# --- Test Execution ---
echo -e "  -> Provisioning bare remote..."
git init --bare "$REMOTE" > /dev/null

echo -e "  -> Setting up Node A (Simulated Mac 1)..."
git clone "$REMOTE" "$MAC1_DIR" 2> /dev/null
cd "$MAC1_DIR"
export XDG_STATE_HOME="$TEST_DIR/state_mac1"

# 1. Make the initial commit and push FIRST
echo "print('hello distributed world')" > main.py
git add main.py
git commit -m "Initial commit" > /dev/null
git branch -M main
git push -u origin main > /dev/null 2>&1

# 2. NOW initialize Pulsar (which does a dry-run push internally)
uv run git-pulsar > /dev/null

# Force a shadow backup
echo -e "  -> Generating shadow backup on Node A..."
uv run git-pulsar now > /dev/null

echo -e "  -> Setting up Node B (Simulated Mac 2)..."
cd "$TEST_DIR"
git clone "$REMOTE" "$MAC2_DIR" 2> /dev/null
cd "$MAC2_DIR"
export XDG_STATE_HOME="$TEST_DIR/state_mac2"

# Initialize registry
uv run git-pulsar > /dev/null

echo -e "  -> Executing Sync Phase on Node B..."
echo "y" | uv run git-pulsar sync > /dev/null

# Assertions
if [ ! -f "main.py" ]; then
    echo -e "${RED}❌ Assertion Failed: main.py not found after sync.${NC}"
    false # Triggers the ERR trap
fi

# --- Ghost-Error Log Check ---
echo -e "${BLUE}ℹ Scanning daemon logs for swallowed exceptions...${NC}"

for NODE in "state_mac1" "state_mac2"; do
    LOG_FILE="$TEST_DIR/$NODE/git-pulsar/daemon.log"
    if [ -f "$LOG_FILE" ] && grep -qE "ERROR|CRITICAL|Traceback" "$LOG_FILE"; then
        echo -e "${RED}❌ TEST PASSED, BUT ERRORS FOUND IN LOGS (${NODE}).${NC}"
        cat "$LOG_FILE"
        false # Triggers the ERR trap
    fi
done

# Clear traps manually since we succeeded, let EXIT trap handle the final rm
trap - ERR
echo -e "${GREEN}✔ Tier 2 Distributed Test Complete. No anomalies detected.${NC}"

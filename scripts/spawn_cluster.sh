#!/usr/bin/env bash
set -e

BLUE='\033[1;34m'
GREEN='\033[1;32m'
RED='\033[1;31m'
NC='\033[0m'

NODE_NAME="pulsar-field-node"

# Dependency check
if ! command -v multipass &> /dev/null; then
    echo -e "${RED}❌ Multipass is not installed.${NC}"
    echo "Please install it via: brew install --cask multipass"
    exit 1
fi

echo -e "${BLUE}=== Provisioning Tier 3 Linux Field Node ===${NC}"

# Clean up existing instance if it exists
if multipass info "$NODE_NAME" &> /dev/null; then
    echo -e "ℹ Destroying existing node '$NODE_NAME'..."
    multipass delete "$NODE_NAME"
    multipass purge
fi

echo -e "ℹ Launching fresh Ubuntu instance (cached images boot in ~5s)..."
multipass launch --name "$NODE_NAME"

echo -e "ℹ Waiting for VM networking and SSHFS to stabilize..."
multipass exec "$NODE_NAME" -- bash -c "while ! id -g >/dev/null 2>&1; do sleep 1; done"

echo -e "ℹ Preparing mount point..."
multipass exec "$NODE_NAME" -- sudo mkdir -p /mnt/pulsar-source
multipass exec "$NODE_NAME" -- sudo chown ubuntu:ubuntu /mnt/pulsar-source

echo -e "ℹ Mounting git-pulsar source code (Retrying until VM is ready)..."
MAX_RETRIES=5
RETRY_COUNT=0
MOUNT_SUCCESS=false

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if multipass mount "$(pwd)" "$NODE_NAME":/mnt/pulsar-source &> /dev/null; then
        MOUNT_SUCCESS=true
        break
    fi
    echo -e "  [Wait] SSHFS not ready, retrying in 3s... ($((RETRY_COUNT+1))/$MAX_RETRIES)"
    sleep 3
    RETRY_COUNT=$((RETRY_COUNT+1))
done

if [ "$MOUNT_SUCCESS" = false ]; then
    echo -e "${RED}❌ Fatal: Could not mount directory after $MAX_RETRIES attempts.${NC}"
    exit 1
fi

echo -e "ℹ Bootstrapping Python environment (uv)..."
multipass exec "$NODE_NAME" -- bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

echo -e "ℹ Configuring global Git identity for shadow commits..."
multipass exec "$NODE_NAME" -- git config --global user.name "Pulsar Field Tester"
multipass exec "$NODE_NAME" -- git config --global user.email "test@pulsar.dev"
multipass exec "$NODE_NAME" -- git config --global pull.rebase false

echo -e "ℹ Creating isolated playground repository..."
multipass exec "$NODE_NAME" -- bash -c "mkdir -p ~/playground && cd ~/playground && git init && echo '# Playground' > README.md && git add README.md && git commit -m 'Initial commit' && git branch -M main"

echo -e "ℹ Installing git-pulsar into the VM..."
# Using 'uv tool' installs the package globally without messing with project .venvs
multipass exec "$NODE_NAME" -- bash -c "source ~/.local/bin/env && uv tool install /mnt/pulsar-source"

echo -e "ℹ Setting up auto-activation and aliases..."
multipass exec "$NODE_NAME" -- bash -c 'cat <<EOF >> ~/.bashrc
source ~/.local/bin/env
cd ~/playground
alias reload-pulsar="uv tool install --force /mnt/pulsar-source"

echo -e "\n\033[1;32m✔ Git Pulsar Field Node Active.\033[0m"
echo -e "You are in an isolated sandbox (~\033[1;34m/playground\033[0m). Your Mac repo is 100% safe."
echo -e "Run \033[1;36mreload-pulsar\033[0m to fetch the latest code if you edit files on your Mac."
EOF'

echo -e "\n${GREEN}✔ Cluster Provisioned Successfully.${NC}"
echo -e "\nTo begin field testing, run:"
echo -e "    ${BLUE}multipass shell $NODE_NAME${NC}"

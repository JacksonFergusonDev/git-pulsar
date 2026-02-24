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

echo -e "ℹ Launching fresh Ubuntu instance (this takes a few seconds)..."
multipass launch --name "$NODE_NAME"

echo -e "ℹ Mounting current workspace to /workspace..."
multipass mount "$(pwd)" "$NODE_NAME":/workspace

echo -e "ℹ Bootstrapping Python environment (uv)..."
multipass exec "$NODE_NAME" -- bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"

echo -e "\n${GREEN}✔ Cluster Provisioned Successfully.${NC}"
echo -e "\nTo begin field testing, run the following command to SSH into the node:"
echo -e "    ${BLUE}multipass shell $NODE_NAME${NC}"
echo -e "\nOnce inside the node, run:"
echo -e "    ${BLUE}cd /workspace${NC}"
echo -e "    ${BLUE}source \$HOME/.local/bin/env${NC}  # Load uv into PATH"
echo -e "    ${BLUE}uv sync${NC}                      # Install dependencies"

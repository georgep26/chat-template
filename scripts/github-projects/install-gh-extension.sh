#!/bin/bash
# Installation script for gh-issue-create GitHub CLI extension

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXTENSION_NAME="gh-issue-create"
EXTENSION_DIR="$HOME/.local/share/gh/extensions/${EXTENSION_NAME}"

echo "Installing GitHub CLI extension: ${EXTENSION_NAME}"

# Check if gh CLI is installed
if ! command -v gh &> /dev/null; then
    echo "Error: GitHub CLI (gh) is not installed"
    echo "Install it from: https://cli.github.com/"
    exit 1
fi

# Create extension directory
mkdir -p "$EXTENSION_DIR"

# Copy the script
if [[ -f "$SCRIPT_DIR/$EXTENSION_NAME" ]]; then
    cp "$SCRIPT_DIR/$EXTENSION_NAME" "$EXTENSION_DIR/"
    chmod +x "$EXTENSION_DIR/$EXTENSION_NAME"
    echo "✓ Extension installed successfully!"
    echo ""
    echo "Usage:"
    echo "  gh issue-create feature --title \"My feature\""
    echo "  gh issue-create bug --title \"My bug\""
    echo "  gh issue-create task --title \"My task\""
    echo ""
    echo "Make sure you have the project scope enabled:"
    echo "  gh auth refresh -s project"
else
    echo "Error: Extension script not found: $SCRIPT_DIR/$EXTENSION_NAME"
    exit 1
fi

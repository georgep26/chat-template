#!/bin/bash

# Branch Protection Setup Script
# This script sets up branch protection rules for the main branch using GitHub CLI
# It reads settings from infra/infra.yaml (github.github_repo, github.solo_mode) first,
# then CLI overrides, then falls back to git remote auto-detect.
#
# Usage:
#   ./scripts/setup/setup_branch_protection.sh
#   ./scripts/setup/setup_branch_protection.sh --repo owner/repo-name
#   ./scripts/setup/setup_branch_protection.sh --solo
#   ./scripts/setup/setup_branch_protection.sh -y    # skip confirmation (non-interactive)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../utils/common.sh"

# Solo mode: 1 = no reviewer required, 0 = require 1 approval (default)
# AUTO_YES: 1 = skip confirmation prompts
SOLO_MODE=0
AUTO_YES=0

# Project root (parent of scripts directory)
get_project_root() {
    echo "$(dirname "$(dirname "$SCRIPT_DIR")")"
}

# Read github_repo and solo_mode from infra/infra.yaml (under github: key)
# Sets CONFIG_REPO and CONFIG_SOLO_MODE (0 or 1). Returns 0 if at least repo was found.
read_infra_config() {
    local project_root="$1"
    local infra_file="${project_root}/infra/infra.yaml"
    CONFIG_REPO=""
    CONFIG_SOLO_MODE=""

    [[ ! -f "$infra_file" ]] && return 1

    # Extract github_repo (handles "value" or value)
    local repo_line
    repo_line=$(grep 'github_repo:' "$infra_file" | head -1)
    if [[ -n "$repo_line" ]]; then
        CONFIG_REPO=$(echo "$repo_line" | sed -E 's/.*github_repo:[[:space:]]*"?([^"]*)"?.*/\1/' | tr -d ' "')
    fi

    # Extract solo_mode (true/false)
    local solo_line
    solo_line=$(grep 'solo_mode:' "$infra_file" | head -1)
    if [[ -n "$solo_line" ]]; then
        local raw
        raw=$(echo "$solo_line" | sed -E 's/.*solo_mode:[[:space:]]*(true|false).*/\1/i' | tr -d ' ')
        if [[ "$(echo "$raw" | tr '[:upper:]' '[:lower:]')" == "true" ]]; then
            CONFIG_SOLO_MODE="1"
        else
            CONFIG_SOLO_MODE="0"
        fi
    fi

    [[ -n "$CONFIG_REPO" ]]
}

# Function to show usage
show_usage() {
    echo "Branch Protection Setup Script"
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --repo <owner/repo>    - GitHub repository (overrides infra/infra.yaml)"
    echo "  --solo                 - Solo mode: no reviewer required (overrides infra config)"
    echo "  -y, --yes              - Non-interactive: skip confirmation prompt"
    echo "  --help, -h             - Show this help message"
    echo ""
    echo "Config precedence: CLI args > infra/infra.yaml (github.github_repo, github.solo_mode) > git remote"
    echo ""
    echo "This script sets up branch protection for the 'main' branch:"
    echo "  - Requires pull requests before merging"
    echo "  - Requires 1 approval (or 0 in solo mode)"
    echo "  - Prevents direct pushes to main"
    echo "  - Requires branches to be up to date"
    echo ""
    echo "Note: Standard process is PRs from 'development' branch, but hotfix"
    echo "      branches are allowed for quick bug fixes. See development_process.md"
}

# Check if GitHub CLI is installed
check_gh_cli() {
    if ! command -v gh &> /dev/null; then
        print_error "GitHub CLI (gh) is not installed."
        print_info "Please install it from: https://cli.github.com/"
        print_info "Or use: brew install gh (macOS) / apt install gh (Linux)"
        exit 1
    fi
    
    # Check if user is authenticated
    if ! gh auth status &> /dev/null; then
        print_error "GitHub CLI is not authenticated."
        print_info "Please run: gh auth login"
        exit 1
    fi
    
    print_info "GitHub CLI is installed and authenticated."
}

# Get repository from git remote (fallback when infra and CLI do not specify repo)
get_repository_from_git() {
    if command -v git &> /dev/null && git rev-parse --git-dir &> /dev/null; then
        local remote_url
        remote_url=$(git remote get-url origin 2>/dev/null || echo "")
        if [[ -n "$remote_url" ]]; then
            local repo
            repo=$(echo "$remote_url" | sed -E 's|.*github\.com[:/]([^/]+/[^/]+)(\.git)?$|\1|' | sed 's|\.git$||')
            if [[ -n "$repo" ]]; then
                echo "$repo"
                return 0
            fi
        fi
    fi
    return 1
}

# Parse CLI arguments. Sets REPO_OVERRIDE, SOLO_OVERRIDE (1 or 0), AUTO_YES (1 or 0).
# Returns 0; use "shift" in caller to remove parsed args from "$@".
parse_cli_args() {
    REPO_OVERRIDE=""
    SOLO_OVERRIDE=""   # empty = not set, "0" or "1" = set
    AUTO_YES=0

    while [[ $# -gt 0 ]]; do
        case $1 in
            --repo)
                REPO_OVERRIDE="$2"
                shift 2
                ;;
            --solo)
                SOLO_OVERRIDE="1"
                shift
                ;;
            -y|--yes)
                AUTO_YES=1
                shift
                ;;
            --help|-h)
                show_usage
                exit 0
                ;;
            *)
                print_error "Unknown option: $1"
                show_usage
                exit 1
                ;;
        esac
    done
}

# Print summary of what will be applied (before confirmation)
print_summary() {
    local repo=$1
    print_header "Summary"
    echo "  Repository:     ${repo}"
    echo "  Branch:        main"
    if [[ "$SOLO_MODE" -eq 1 ]]; then
        echo "  Mode:          Solo (PRs required, no reviewer approval)"
    else
        echo "  Mode:          Standard (PRs require 1 approval)"
    fi
    echo "  Rules:         Require PRs, up-to-date branch, 'Run Tests' check, conversation resolution"
    echo "  Effect:        Direct pushes to main will be blocked"
    echo ""
}

# Set up branch protection rules
setup_branch_protection() {
    local repo=$1
    local auto_yes=${2:-0}

    print_header "Setting up branch protection for 'main' branch in ${repo}..."
    
    local existing=0
    if gh api "repos/${repo}/branches/main/protection" &> /dev/null; then
        existing=1
        print_warning "Branch protection already exists for 'main' branch."
    fi

    print_summary "$repo"

    if [[ "$auto_yes" -eq 0 ]]; then
        if [[ "$existing" -eq 1 ]]; then
            read -p "Do you want to update it? (y/N): " -r
        else
            read -p "Apply the above rules? (y/N): " -r
        fi
        echo
        if [[ ! "$REPLY" =~ ^[yY]([eE][sS])?$ ]]; then
            print_info "Skipping branch protection update."
            return
        fi
    fi

    print_info "Configuring branch protection rules..."
    if [[ "$SOLO_MODE" -eq 1 ]]; then
        print_info "Solo mode: PRs required but no reviewer approval needed."
    fi
    
    # Required approving review count: 0 for solo mode, 1 otherwise
    local required_reviews=$([[ "$SOLO_MODE" -eq 1 ]] && echo "0" || echo "1")
    
    # Set up branch protection
    # Note: This allows PRs from any branch (including hotfix branches)
    # Standard process is PRs from development, but hotfix branches are allowed for quick fixes
    # Requires 1 approval (2 recommended for standard releases); use --solo for 0 approvals
    
    # Construct JSON payload for branch protection
    # The "Run Tests" context is manually reported by the report-test-status job
    # in pull_request.yml. This is a workaround for reusable workflows not properly
    # reporting status checks for branch protection.
    # See: https://github.com/orgs/community/discussions/8512
    local protection_json=$(cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Run Tests"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": ${required_reviews},
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true,
  "lock_branch": false,
  "allow_fork_syncing": false
}
EOF
)
    
    # Apply branch protection using JSON payload
    echo "$protection_json" | gh api "repos/${repo}/branches/main/protection" \
        --method PUT \
        --input - \
        --silent
    
    print_info "✅ Branch protection rules configured successfully!"
    print_info "   - Requires pull requests before merging"
    if [[ "$SOLO_MODE" -eq 1 ]]; then
        print_info "   - No reviewer approval required (solo mode)"
    else
        print_info "   - Requires 1 approval (2 reviewers recommended for standard releases)"
    fi
    print_info "   - Prevents direct pushes to main"
    print_info "   - Requires branches to be up to date"
    print_info "   - Requires 'Run Tests' status check to pass"
    print_info "   - Requires conversation resolution"
}

# Main execution
main() {
    parse_cli_args "$@"

    print_step "Starting branch protection setup..."

    local project_root
    project_root=$(get_project_root)
    read_infra_config "$project_root" || true

    # Resolve repo: CLI override > infra config > git remote
    local repo=""
    if [[ -n "$REPO_OVERRIDE" ]]; then
        repo="$REPO_OVERRIDE"
        print_info "Repository from CLI: ${repo}"
    elif [[ -n "$CONFIG_REPO" ]]; then
        repo="$CONFIG_REPO"
        print_info "Repository from infra/infra.yaml: ${repo}"
    elif repo=$(get_repository_from_git); then
        print_info "Repository from git remote: ${repo}"
    else
        print_error "Could not determine repository."
        print_info "Add github.github_repo to infra/infra.yaml or use: $0 --repo owner/repo-name"
        exit 1
    fi

    # Resolve solo mode: CLI override > infra config > default 0
    if [[ -n "$SOLO_OVERRIDE" ]]; then
        SOLO_MODE=$SOLO_OVERRIDE
    elif [[ -n "$CONFIG_SOLO_MODE" ]]; then
        SOLO_MODE=$CONFIG_SOLO_MODE
    else
        SOLO_MODE=0
    fi

    check_gh_cli

    setup_branch_protection "$repo" "$AUTO_YES"

    print_step "Branch protection setup complete!"
    print_info "Summary:"
    print_info "✅ Branch protection rules are now active on the main branch"
    print_info "   - Direct pushes to main are blocked"
    if [[ "$SOLO_MODE" -eq 1 ]]; then
        print_info "   - PRs required, no reviewer approval (solo mode)"
    else
        print_info "   - PRs require 1 approval (2 reviewers recommended for standard releases)"
    fi
    print_info "   - Standard process: PRs from development branch"
    print_info "   - Hotfix process: PRs from hotfix branches allowed (see development_process.md)"
}

# Run main function
main "$@"

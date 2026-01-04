#!/bin/bash

# Branch Protection Setup Script
# This script sets up branch protection rules for the main branch using GitHub CLI
# It ensures that only pull requests from the development branch can be merged into main
#
# Usage:
#   ./scripts/setup_branch_protection.sh
#   # Or with explicit repository
#   ./scripts/setup_branch_protection.sh --repo owner/repo-name

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}[BRANCH PROTECTION]${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Branch Protection Setup Script"
    echo ""
    echo "Usage: $0 [options]"
    echo ""
    echo "Options:"
    echo "  --repo <owner/repo>    - GitHub repository (e.g., owner/repo-name)"
    echo "                          If not provided, will try to detect from git remote"
    echo "  --help                 - Show this help message"
    echo ""
    echo "This script sets up branch protection for the 'main' branch:"
    echo "  - Requires pull requests before merging"
    echo "  - Requires 1 approval (2 reviewers recommended for standard releases)"
    echo "  - Prevents direct pushes to main"
    echo "  - Requires branches to be up to date"
    echo ""
    echo "Note: Standard process is PRs from 'development' branch, but hotfix"
    echo "      branches are allowed for quick bug fixes. See development_process.md"
    echo "      for details on the hotfix process."
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

# Get repository from git remote or argument
get_repository() {
    local repo_arg=""
    
    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --repo)
                repo_arg="$2"
                shift 2
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
    
    # If repo was provided as argument, use it
    if [[ -n "$repo_arg" ]]; then
        echo "$repo_arg"
        return
    fi
    
    # Try to detect from git remote
    if command -v git &> /dev/null && git rev-parse --git-dir &> /dev/null; then
        local remote_url=$(git remote get-url origin 2>/dev/null || echo "")
        if [[ -n "$remote_url" ]]; then
            # Extract owner/repo from various URL formats
            # git@github.com:owner/repo.git
            # https://github.com/owner/repo.git
            # https://github.com/owner/repo
            local repo=$(echo "$remote_url" | sed -E 's|.*github\.com[:/]([^/]+/[^/]+)(\.git)?$|\1|' | sed 's|\.git$||')
            if [[ -n "$repo" ]]; then
                echo "$repo"
                return
            fi
        fi
    fi
    
    print_error "Could not determine repository."
    print_info "Please provide it explicitly: $0 --repo owner/repo-name"
    exit 1
}


# Set up branch protection rules
setup_branch_protection() {
    local repo=$1
    
    print_header "Setting up branch protection for 'main' branch in ${repo}..."
    
    # Check if branch protection already exists
    if gh api "repos/${repo}/branches/main/protection" &> /dev/null; then
        print_warning "Branch protection already exists for 'main' branch."
        read -p "Do you want to update it? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "Skipping branch protection update."
            return
        fi
    fi
    
    print_info "Configuring branch protection rules..."
    
    # Set up branch protection
    # Note: This allows PRs from any branch (including hotfix branches)
    # Standard process is PRs from development, but hotfix branches are allowed for quick fixes
    # Requires 1 approval (2 recommended for standard releases)
    
    # Construct JSON payload for branch protection
    local protection_json=$(cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": []
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
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
    print_info "   - Requires 1 approval (2 reviewers recommended for standard releases)"
    print_info "   - Prevents direct pushes to main"
    print_info "   - Requires branches to be up to date"
    print_info "   - Requires conversation resolution"
}

# Main execution
main() {
    print_header "Starting branch protection setup..."
    
    # Check for help flag
    if [[ "$1" == "--help" ]] || [[ "$1" == "-h" ]]; then
        show_usage
        exit 0
    fi
    
    # Check GitHub CLI
    check_gh_cli
    
    # Get repository
    local repo=$(get_repository "$@")
    print_info "Repository: ${repo}"
    
    # Set up branch protection
    setup_branch_protection "$repo"
    
    print_header "Branch protection setup complete!"
    print_info "Summary:"
    print_info "✅ Branch protection rules are now active on the main branch"
    print_info "   - Direct pushes to main are blocked"
    print_info "   - PRs require 1 approval (2 reviewers recommended for standard releases)"
    print_info "   - Standard process: PRs from development branch"
    print_info "   - Hotfix process: PRs from hotfix branches allowed (see development_process.md)"
}

# Run main function
main "$@"


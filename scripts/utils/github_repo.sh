#!/bin/bash
# GitHub repository resolution utilities
# Used by deploy_deployer_github_action_role.sh, deploy_evals_github_action_role.sh,
# setup_github.sh, and others that need org/repo from infra or git.
# Source after common.sh; optionally after config_parser.sh (for INFRA_CONFIG_PATH).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -z "${WHITE:-}" ]] && source "$SCRIPT_DIR/common.sh"

# -----------------------------------------------------------------------------
# get_repository_from_git
# -----------------------------------------------------------------------------
# Outputs "owner/repo" by parsing the origin remote URL.
# Returns 0 if found, 1 otherwise.
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

# -----------------------------------------------------------------------------
# get_github_repo_from_infra
# -----------------------------------------------------------------------------
# Outputs "owner/repo" from infra config if set and not a placeholder (e.g. not "${REPO}").
# Reads from INFRA_CONFIG_PATH if set, else from get_project_root/infra/infra.yaml.
# Returns 0 if a concrete value is found, 1 otherwise.
get_github_repo_from_infra() {
    local infra_file=""
    if [[ -n "${INFRA_CONFIG_PATH:-}" ]] && [[ -f "${INFRA_CONFIG_PATH}" ]]; then
        infra_file="$INFRA_CONFIG_PATH"
    else
        local project_root
        project_root=$(get_project_root 2>/dev/null || echo "")
        [[ -z "$project_root" ]] && return 1
        infra_file="${project_root}/infra/infra.yaml"
    fi
    [[ ! -f "$infra_file" ]] && return 1

    local repo=""
    if command -v yq &> /dev/null; then
        repo=$(yq -r '.github.github_repo // ""' "$infra_file" 2>/dev/null || echo "")
    else
        local line
        line=$(grep 'github_repo:' "$infra_file" | head -1)
        [[ -n "$line" ]] && repo=$(echo "$line" | sed -E 's/.*github_repo:[[:space:]]*"?([^"]*)"?.*/\1/' | tr -d ' "')
    fi

    if [[ -n "$repo" ]] && [[ "$repo" == *"/"* ]] && [[ "$repo" != *'${'* ]]; then
        echo "$repo"
        return 0
    fi
    return 1
}

# -----------------------------------------------------------------------------
# resolve_github_repo
# -----------------------------------------------------------------------------
# Resolves "owner/repo" using: infra (if not a placeholder), then git remote.
# Outputs "owner/repo". Returns 0 if resolved, 1 otherwise.
resolve_github_repo() {
    local repo
    if repo=$(get_github_repo_from_infra 2>/dev/null) && [[ -n "$repo" ]]; then
        echo "$repo"
        return 0
    fi
    if repo=$(get_repository_from_git 2>/dev/null) && [[ -n "$repo" ]]; then
        echo "$repo"
        return 0
    fi
    return 1
}

# -----------------------------------------------------------------------------
# resolve_github_org_repo
# -----------------------------------------------------------------------------
# Sets RESOLVED_GITHUB_ORG and RESOLVED_GITHUB_REPO from infra or git.
# Returns 0 if both are set, 1 otherwise.
resolve_github_org_repo() {
    local full_repo
    full_repo=$(resolve_github_repo) || return 1
    [[ -z "$full_repo" ]] || [[ "$full_repo" != *"/"* ]] && return 1
    RESOLVED_GITHUB_ORG="${full_repo%%/*}"
    RESOLVED_GITHUB_REPO="${full_repo#*/}"
    return 0
}

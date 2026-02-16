# gh-pm-lib.sh: shared logic for gh-pm subcommands (cleanup, fetch-sprint, weekly-report, discussions).
# Sourced by gh-pm; expects REPO, PROJECT_NAME, OUTPUT_JSON, DRY_RUN, SCRIPT_DIR, PROJECT_ROOT to be set.
# Uses resolve_repo from gh-pm if available.

STALE_DAYS=30
DOCS_ISSUES_DIR="$PROJECT_ROOT/docs/github_project_issues"
DOCS_REPORTS_DIR="$PROJECT_ROOT/docs/github_project_reports"

# Find project ID (Project V2 preferred). Usage: get_project_id <repo> <project_name>
get_project_id() {
    local repo="$1"
    local project_name="$2"
    local org="${repo%%/*}"

    local project_id
    project_id=$(gh api "repos/${repo}/projects" --jq ".[] | select(.name == \"${project_name}\") | .id" 2>/dev/null || echo "")
    [[ -n "$project_id" ]] && [[ "$project_id" =~ ^[0-9]+$ ]] && echo "$project_id" && return 0

    project_id=$(gh api "orgs/${org}/projects" --jq ".[] | select(.name == \"${project_name}\") | .id" 2>/dev/null || echo "")
    [[ -n "$project_id" ]] && [[ "$project_id" =~ ^[0-9]+$ ]] && echo "$project_id" && return 0

    project_id=$(gh api graphql \
        -f query='
        query($org: String!, $projectName: String!) {
            organization(login: $org) {
                projectsV2(first: 20, query: $projectName) {
                    nodes { id title }
                }
            }
        }' \
        -f org="$org" \
        -f projectName="$project_name" \
        --jq ".data.organization.projectsV2.nodes[] | select(.title == \"${project_name}\") | .id" 2>/dev/null || echo "")
    [[ -n "$project_id" ]] && [[ "$project_id" =~ ^PVT_ ]] && echo "$project_id" && return 0

    project_id=$(gh api graphql \
        -f query='
        query($owner: String!, $projectName: String!) {
            user(login: $owner) {
                projectsV2(first: 20, query: $projectName) {
                    nodes { id title }
                }
            }
        }' \
        -f owner="$org" \
        -f projectName="$project_name" \
        --jq ".data.user.projectsV2.nodes[] | select(.title == \"${project_name}\") | .id" 2>/dev/null || echo "")
    [[ -n "$project_id" ]] && [[ "$project_id" =~ ^PVT_ ]] && echo "$project_id" && return 0

    return 1
}

# Fetch Project V2 items (first 100) with issue content and field values. Outputs JSON to stdout.
# Requires project_id (PVT_...) and repo.
fetch_project_v2_items_json() {
    local project_id="$1"
    local repo="$2"
    local owner="${repo%%/*}"
    local repo_name="${repo#*/}"

    gh api graphql \
        -f query='
        query($projectId: ID!) {
            node(id: $projectId) {
                ... on ProjectV2 {
                    items(first: 100) {
                        nodes {
                            id
                            content {
                                ... on Issue {
                                    number
                                    title
                                    url
                                    updatedAt
                                    state
                                    labels(first: 10) { nodes { name } }
                                }
                            }
                            fieldValues(first: 30) {
                                nodes {
                                    __typename
                                    ... on ProjectV2ItemFieldSingleSelectValue {
                                        field { ... on ProjectV2SingleSelectField { name } }
                                        name
                                    }
                                    ... on ProjectV2ItemFieldIterationValue {
                                        field { ... on ProjectV2IterationField { name } }
                                        title
                                        startDate
                                        duration
                                    }
                                    ... on ProjectV2ItemFieldDateValue {
                                        field { ... on ProjectV2Field { name } }
                                        date
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }' \
        -f projectId="$project_id" \
        --jq '.data.node.items.nodes'
}

# Normalize issue title for similarity: lowercase, strip [TYPE], collapse spaces.
normalize_title() {
    echo "$1" | sed -E 's/^[[:space:]]*\[[^]]+\][[:space:]]*//' | tr '[:upper:]' '[:lower:]' | sed -E 's/[[:space:]]+/ /g' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g'
}

# Run cleanup audit: duplicates, orphaned, stale. Output JSON or human summary.
run_cleanup_audit() {
    local repo
    repo=$(resolve_repo 2>/dev/null) || repo="$REPO"
    [[ -z "$repo" ]] && echo -e "${RED}Could not resolve repository.${NC}" >&2 && return 1

    local project_id
    project_id=$(get_project_id "$repo" "$PROJECT_NAME") || true
    if [[ -z "$project_id" ]] || [[ ! "$project_id" =~ ^PVT_ ]]; then
        if [[ "$OUTPUT_JSON" == true ]]; then
            echo '{"duplicates":[],"orphaned":[],"stale":[],"error":"Project not found or not Project V2"}'
        else
            echo -e "${RED}Project not found or not a Project V2: $PROJECT_NAME${NC}" >&2
        fi
        return 1
    fi

    local items_json
    items_json=$(fetch_project_v2_items_json "$project_id" "$repo") || items_json="[]"
    [[ -z "$items_json" ]] && items_json="[]"

    local stale_cutoff
    stale_cutoff=$(date -u -v-${STALE_DAYS}d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || stale_cutoff=$(date -u -d "${STALE_DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || stale_cutoff=""

    # Duplicate detection: same normalized title (lowercase, strip [TYPE], collapse spaces)
    local dup_json stale_json orphan_json
    dup_json=$(echo "$items_json" | jq -c '
        [.[] | select(.content != null) | {norm: (.content.title | (if type == "string" then . else "" end) | gsub("^\\s*\\[[^]]+\\]\\s*"; "") | gsub("\\s+"; " ") | gsub("^\\s+"; "") | gsub("\\s+$"; "") | ascii_downcase), number: .content.number, title: .content.title, url: .content.url}]
        | group_by(.norm)
        | map(select(length > 1) | map({number, title, url}))
    ' 2>/dev/null) || dup_json="[]"

    stale_json=$(echo "$items_json" | jq -c --arg cut "$stale_cutoff" '
        [.[] | select(.content != null and .content.updatedAt != null and .content.updatedAt < $cut) | {number: .content.number, title: .content.title, url: .content.url, updatedAt: .content.updatedAt}]
    ' 2>/dev/null) || stale_json="[]"

    # Orphaned: in a sprint but status Backlog, or Type is Feature/Epic but no parent (we may not have parent in this query - so skip or mark "no parent" if we add parent field)
    orphan_json=$(echo "$items_json" | jq -c '
        [.[] | select(.content != null) | . as $i |
        ([$i.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldSingleSelectValue" and (.field.name == "Status")?) | .name] | first) as $status |
        ([$i.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldIterationValue") | .title] | first) as $sprint |
        select($sprint != null and $sprint != "" and ($status == "Backlog" or $status == null)) | {number: $i.content.number, title: $i.content.title, url: $i.content.url, status: $status, sprint: $sprint}]
    ' 2>/dev/null) || orphan_json="[]"

    if [[ "$OUTPUT_JSON" == true ]]; then
        echo "{\"duplicates\":$dup_json,\"orphaned\":$orphan_json,\"stale\":$stale_json}"
        return 0
    fi

    local dup_count stale_count orphan_count
    dup_count=$(echo "$dup_json" | jq 'length' 2>/dev/null || echo 0)
    stale_count=$(echo "$stale_json" | jq 'length' 2>/dev/null || echo 0)
    orphan_count=$(echo "$orphan_json" | jq 'length' 2>/dev/null || echo 0)

    echo -e "${GREEN}Cleanup audit for project: $PROJECT_NAME${NC}"
    echo "  Duplicates (same normalized title): $dup_count"
    echo "  Orphaned (in sprint but status Backlog): $orphan_count"
    echo "  Stale (no update in ${STALE_DAYS}+ days): $stale_count"
    if [[ "$dup_count" -gt 0 ]]; then
        echo ""
        echo "Duplicate groups (consider merging):"
        echo "$dup_json" | jq -r '.[] | "  #\(.[0].number) \(.[0].title) <-> #\(.[1].number) \(.[1].title)"' 2>/dev/null || true
    fi
    if [[ "$stale_count" -gt 0 ]]; then
        echo ""
        echo "Stale issues:"
        echo "$stale_json" | jq -r '.[] | "  #\(.number) \(.title) (\(.updatedAt))"' 2>/dev/null || true
    fi
    if [[ "$orphan_count" -gt 0 ]]; then
        echo ""
        echo "Orphaned (in sprint but Backlog):"
        echo "$orphan_json" | jq -r '.[] | "  #\(.number) \(.title) sprint=\(.sprint) status=\(.status)"' 2>/dev/null || true
    fi
    return 0
}

# Fetch current sprint issues and write JSON to docs/github_project_issues/.
run_fetch_current_sprint() {
    local repo
    repo=$(resolve_repo 2>/dev/null) || repo="$REPO"
    [[ -z "$repo" ]] && echo -e "${RED}Could not resolve repository.${NC}" >&2 && return 1

    local project_id
    project_id=$(get_project_id "$repo" "$PROJECT_NAME") || true
    if [[ -z "$project_id" ]] || [[ ! "$project_id" =~ ^PVT_ ]]; then
        echo -e "${RED}Project not found or not Project V2.${NC}" >&2
        return 1
    fi

    local items_json
    items_json=$(fetch_project_v2_items_json "$project_id" "$repo") || items_json="[]"
    [[ -z "$items_json" ]] && items_json="[]"

    # Normalize to a flat list for consumers: number, title, url, status, type, sprint, updatedAt, labels.
    local normalized
    normalized=$(echo "$items_json" | jq -c '
        [.[] | select(.content != null) |
            ([.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldSingleSelectValue") | select(.field.name != null) | {(.field.name): .name}] | add) as $fields |
            ([.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldIterationValue") | .title] | first) as $sprint |
            {
                number: .content.number,
                title: .content.title,
                url: .content.url,
                state: .content.state,
                updatedAt: .content.updatedAt,
                labels: [.content.labels.nodes[]?.name],
                Status: ($fields.Status // null),
                Type: ($fields.Type // null),
                Priority: ($fields.Priority // null),
                Sprint: $sprint
            }
        ]
    ' 2>/dev/null) || normalized="[]"

    mkdir -p "$DOCS_ISSUES_DIR"
    local date_str
    date_str=$(date +%Y-%m-%d)
    local outfile="$DOCS_ISSUES_DIR/${date_str}_sprint_issues.json"
    echo "$normalized" > "$outfile"

    if [[ "$OUTPUT_JSON" == true ]]; then
        echo "{\"path\":\"$outfile\",\"count\":$(echo "$normalized" | jq 'length'),\"issues\":$normalized}"
    else
        echo -e "${GREEN}Sprint issues written to: $outfile${NC}"
        echo "  Count: $(echo "$normalized" | jq 'length')"
    fi
    return 0
}

# Generate weekly report markdown and write to docs/github_project_reports/.
run_weekly_report() {
    local repo
    repo=$(resolve_repo 2>/dev/null) || repo="$REPO"
    [[ -z "$repo" ]] && echo -e "${RED}Could not resolve repository.${NC}" >&2 && return 1

    local project_id
    project_id=$(get_project_id "$repo" "$PROJECT_NAME") || true
    if [[ -z "$project_id" ]] || [[ ! "$project_id" =~ ^PVT_ ]]; then
        echo -e "${RED}Project not found or not Project V2.${NC}" >&2
        return 1
    fi

    local items_json
    items_json=$(fetch_project_v2_items_json "$project_id" "$repo") || items_json="[]"
    [[ -z "$items_json" ]] && items_json="[]"

    local date_str
    date_str=$(date +%Y-%m-%d)
    mkdir -p "$DOCS_REPORTS_DIR"
    local outfile="$DOCS_REPORTS_DIR/${date_str}_weekly_report.md"

    local report
    report=$(echo "$items_json" | jq -r '
        ["# Weekly Project Report – " + ($date | . // now | strftime("%Y-%m-%d")),
         "",
         "## Summary",
         "| Status | Count |",
         "|--------|-------|",
         (([.[] | select(.content != null) | ([.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldSingleSelectValue" and .field.name == "Status") | .name] | first) // "—"] | group_by(.) | .[] | "| " + (.[0]) + " | " + (length | tostring) + " |")),
         "",
         "## Blockers",
         (([.[] | select(.content != null) | select([.content.labels.nodes[]?.name] | index("blocked")) | "* #" + (.content.number | tostring) + " " + .content.title + " " + .content.url]) | join("\n") // "None."),
         "",
         "## By Type",
         (([.[] | select(.content != null) | ([.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldSingleSelectValue" and .field.name == "Type") | .name] | first) // "—"] | group_by(.) | .[] | "### " + (.[0]) + "\n" + (map("* #" + (.content.number | tostring) + " " + .content.title) | join("\n")) + "\n") | join("")),
         ""
        ] | join("\n")
    ' 2>/dev/null --arg date "$date_str") || report="# Weekly Project Report – $date_str\n\nNo data."

    {
        echo "# Weekly Project Report – $date_str"
        echo ""
        echo "## Summary"
        echo "Total issues in project: $(echo "$items_json" | jq '[.[] | select(.content != null)] | length')"
        echo ""
        echo "## Blockers"
        blocked=$(echo "$items_json" | jq -r '[.[] | select(.content != null) | select((.content.labels.nodes // []) | map(.name) | index("blocked") != null) | "- #\(.content.number) \(.content.title) \(.content.url)"] | join("\n")' 2>/dev/null)
        if [[ -n "$blocked" ]]; then echo "$blocked"; else echo "None."; fi
        echo ""
        echo "## By Status"
        echo "$items_json" | jq -r '[.[] | select(.content != null) | ([.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldSingleSelectValue" and (.field.name == "Status")) | .name] | first) // "—"] | group_by(.) | .[] | "- \(.[0]): \(length)"' 2>/dev/null || true
        echo ""
        echo "## By Type (Epic/Initiative rollup)"
        echo "$items_json" | jq -r '.[] | select(.content != null) | (([.fieldValues.nodes[]? | select(.__typename == "ProjectV2ItemFieldSingleSelectValue" and (.field.name == "Type")) | .name] | first) // "—") as $t | "\($t): #\(.content.number) \(.content.title)"' 2>/dev/null | sort || true
    } > "$outfile"

    if [[ "$OUTPUT_JSON" == true ]]; then
        echo "{\"path\":\"$outfile\"}"
    else
        echo -e "${GREEN}Weekly report written to: $outfile${NC}"
    fi
    return 0
}

# List discussions and propose issue drafts. Does not create issues (dry-run by default).
run_discussions_to_issues() {
    local repo
    repo=$(resolve_repo 2>/dev/null) || repo="$REPO"
    [[ -z "$repo" ]] && echo -e "${RED}Could not resolve repository.${NC}" >&2 && return 1

    local owner="${repo%%/*}"
    local repo_name="${repo#*/}"

    local discussions_json
    discussions_json=$(gh api graphql \
        -f query='
        query($owner: String!, $repo: String!, $first: Int!) {
            repository(owner: $owner, name: $repo) {
                discussions(first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
                    nodes {
                        number
                        title
                        body
                        url
                        createdAt
                        category { name }
                    }
                }
            }
        }' \
        -f owner="$owner" \
        -f repo="$repo_name" \
        -f first=20 \
        --jq '.data.repository.discussions.nodes' 2>/dev/null) || discussions_json="[]"
    [[ -z "$discussions_json" ]] && discussions_json="[]"

    local proposed
    proposed=$(echo "$discussions_json" | jq -c '
        [.[] | {
            discussion_number: .number,
            discussion_title: .title,
            discussion_url: .url,
            proposed_issue: { title: .title, body: (.body | split("\n")[0:20] | join("\n")), type: "Task" }
        }]
    ' 2>/dev/null) || proposed="[]"

    if [[ "$OUTPUT_JSON" == true ]]; then
        echo "{\"dry_run\":$DRY_RUN,\"discussions\":$discussions_json,\"proposed_issues\":$proposed}"
        return 0
    fi

    echo -e "${GREEN}Discussions (proposed as issues; no issues created in dry-run)${NC}"
    echo "$proposed" | jq -r '.[] | "  #\(.discussion_number) \(.discussion_title) -> issue \"\(.proposed_issue.title)\" (\(.proposed_issue.type))"' 2>/dev/null || echo "  None."
    if [[ "$DRY_RUN" == true ]]; then
        echo ""
        echo "Run without --dry-run and confirm with the agent to create issues."
    fi
    return 0
}

# Breakdown: check proposed issues against existing for duplicates. proposals_json is a JSON array of {title, type[, body]}.
run_breakdown_duplicate_check() {
    local proposals="${1:-[]}"
    [[ -z "$proposals" ]] && proposals="[]"
    if ! echo "$proposals" | jq -e . >/dev/null 2>&1; then
        echo "{\"proposals\":[],\"duplicates\":[],\"existing_titles\":[],\"error\":\"Invalid proposals JSON\"}"
        return 0
    fi

    local repo
    repo=$(resolve_repo 2>/dev/null) || repo="$REPO"
    [[ -z "$repo" ]] && echo "{\"proposals\":$proposals,\"duplicates\":[],\"existing_titles\":[]}" && return 0

    local project_id
    project_id=$(get_project_id "$repo" "$PROJECT_NAME") || true
    if [[ -z "$project_id" ]] || [[ ! "$project_id" =~ ^PVT_ ]]; then
        echo "{\"proposals\":$proposals,\"duplicates\":[],\"existing_titles\":[]}"
        return 0
    fi

    local items_json
    items_json=$(fetch_project_v2_items_json "$project_id" "$repo") || items_json="[]"
    local existing_titles
    existing_titles=$(echo "$items_json" | jq -c '[.[] | select(.content != null) | .content.title | (if type == "string" then . else "" end) | gsub("^\\s*\\[[^]]+\\]\\s*"; "") | gsub("\\s+"; " ") | gsub("^\\s+"; "") | gsub("\\s+$"; "") | ascii_downcase]' 2>/dev/null) || existing_titles="[]"
    local duplicates
    duplicates=$(echo "$proposals" | jq -c --argjson existing "$existing_titles" '
        [.[] | .title as $t | ($t | (if type == "string" then . else "" end) | gsub("^\\s*\\[[^]]+\\]\\s*"; "") | gsub("\\s+"; " ") | gsub("^\\s+"; "") | gsub("\\s+$"; "") | ascii_downcase) as $n |
        select($n != "" and ($existing | index($n))) | {proposed_title: $t, matching_existing: $n}]
    ' 2>/dev/null) || duplicates="[]"
    echo "{\"proposals\":$proposals,\"duplicates\":$duplicates,\"existing_titles\":$existing_titles}"
    return 0
}

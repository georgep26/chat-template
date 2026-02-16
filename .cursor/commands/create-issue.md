# Create GitHub Issue Command

When the user requests to create an issue (e.g., "Update the deploy_network.sh script to write out the VPC ID and subnet IDs to infra.yaml"), you should:

1. **Analyze the request** to determine:
   - **Issue Type**: Categorize as Feature, Bug, or Task based on the content:
     - **Feature**: New functionality, enhancements, or improvements
     - **Bug**: Errors, problems, or things that don't work correctly
     - **Task**: General work items, documentation updates, refactoring, or maintenance
   - **Title**: Create a concise, descriptive title (without prefix - the script will add it)
   - **Body**: Extract or expand on the details from the user's request

2. **Review the templates** in `.github/ISSUE_TEMPLATE/` to understand the structure:
   - `feature_request.md` - For new features
   - `bug_report.md` - For bugs
   - `task.md` - For general tasks

3. **Call the issue creation script** using the appropriate command:
   ```bash
   ./scripts/github-projects/gh-issue-create <type> --title "Title here" [--body "Body here"] [--assignee @me] [--label "label"]
   ```

## Categorization Guidelines

### Feature
- Adding new functionality
- Enhancing existing features
- New capabilities or improvements
- Example: "Add dark mode support", "Implement user authentication"

### Bug
- Something is broken or not working
- Errors or unexpected behavior
- Fixes needed for existing functionality
- Example: "Login button doesn't work", "API returns 500 error"

### Task
- Refactoring code
- Updating documentation
- Maintenance work
- Configuration changes
- Script updates
- Example: "Update deploy_network.sh script", "Refactor authentication module", "Update README"

## Examples

### Example 1: Feature Request
**User**: "Add support for multiple AWS regions in the deployment script"
**Analysis**:
- Type: Feature (new functionality)
- Title: "Add support for multiple AWS regions in deployment script"
- Body: Can expand on the request or use template

**Command**:
```bash
./scripts/github-projects/gh-issue-create feature --title "Add support for multiple AWS regions in deployment script"
```

### Example 2: Bug Report
**User**: "The deploy script fails when VPC ID is not set"
**Analysis**:
- Type: Bug (something is broken)
- Title: "Deploy script fails when VPC ID is not set"
- Body: Include steps to reproduce if provided

**Command**:
```bash
./scripts/github-projects/gh-issue-create bug --title "Deploy script fails when VPC ID is not set" --body "The deployment script crashes with an error when VPC_ID environment variable is not set. It should handle this case gracefully."
```

### Example 3: Task
**User**: "Update the deploy_network.sh script to write out the VPC ID and subnet IDs to infra.yaml"
**Analysis**:
- Type: Task (script update/maintenance)
- Title: "Update deploy_network.sh to write VPC ID and subnet IDs to infra.yaml"
- Body: Expand on the requirement

**Command**:
```bash
./scripts/github-projects/gh-issue-create task --title "Update deploy_network.sh to write VPC ID and subnet IDs to infra.yaml" --body "The deploy_network.sh script should automatically write the created VPC ID and subnet IDs back to infra.yaml after network deployment, so they can be referenced by other deployment scripts."
```

## Process

When the user requests to create an issue, you should:

1. **Parse the user's request** - Extract the key information from their natural language description
2. **Determine issue type** - Use the categorization guidelines above (Feature/Bug/Task)
3. **Create a clear title** - Make it descriptive but concise (without prefix - script adds it)
4. **Expand the body** - Fill out **every section** of the selected issue template. Do not leave template sections blank.
   - Use as many bullets as naturally needed for the request (do **not** pad to a fixed bullet count).
   - If a section does not apply or has no relevant details, set that section to `N/A`.
5. **Extract project field assignments from user text** and pass them to the script as `--field "Name=Value"`:
   - If the user specifies hierarchy type words like **Initiative** or **Epic**, pass explicit type with `--type`:
     - "add an Initiative ..." -> `--type "Initiative"`
     - "create an Epic ..." -> `--type "Epic"`
     - "create a bug ..." -> `--type "Bug"`
     - "create a task ..." -> `--type "Task"`
   - Example phrases:
     - "priority P0" -> `--field "Priority=P0"`
     - "start date 2/17/26" -> `--field "Start Date=2/17/26"`
     - "status In progress" -> `--field "Status=In progress"`
     - "put this under Epic #42" -> `--field "Parent issue=#42"`
     - "put X task under Epic <title>" -> `--field "Parent issue=<title>"`
   - You may pass multiple `--field` flags in one command.
6. **Execute the command** - **Actually run** the gh-issue-create script using the run_terminal_cmd tool:
   ```bash
  ./scripts/github-projects/gh-issue-create <type> --title "Title here" --body "Body here" [--type "Epic"] [--field "Priority=P0"] [--field "Start Date=2/17/26"]
   ```
   
   **CRITICAL**: When calling run_terminal_cmd, you MUST include `required_permissions: ["all"]` because:
   - The script needs network access to call GitHub's API via `gh` CLI
   - The sandbox may not have access to your local `gh` auth session/keychain
   - Running outside sandbox avoids auth/session mismatch and network restrictions
   
   Example tool call:
   ```json
   {
     "command": "./scripts/github-projects/gh-issue-create <type> --title \"...\" --body \"...\"",
     "required_permissions": ["all"],
     "is_background": false
   }
   ```

7. **Confirm creation** - Let the user know the issue was created and provide the issue URL from the script output

**Important**: You must actually execute the command, not just describe what would be done. Use the terminal command tool to run the script OUTSIDE sandbox.

## Important Notes

- **Outside Sandbox Required**: Always use `required_permissions: ["all"]` when calling run_terminal_cmd so `gh` can use your authenticated local session.
- The script automatically adds the issue to the "Chat Template Project"
- The script always outputs a fully populated template body for the selected issue type (Feature/Bug/Task)
- Every section must be filled; use `N/A` only for sections that truly have no applicable content
- Do not force fixed bullet counts per section; write the number of bullets the issue actually needs
- The script can set additional project fields with repeated `--field "Name=Value"` flags (e.g., Priority, Start Date, Status, Size, Estimate, Iteration)
- The script supports explicit project Type override via `--type` (e.g., `Initiative`, `Epic`, `Task`, `Bug`, `Feature`)
- Parent linking is supported via `--field "Parent issue=..."` and accepts issue number (`#123`), raw number (`123`), issue URL, or exact issue title
- Labels are applied based on the issue type
- Title prefixes ([FEATURE], [BUG], [TASK]) are added automatically
- The script resolves the repository from `infra/infra.yaml` or git remote automatically

## Sandbox Limitations

Cursor's sandbox can block or isolate auth/network context. If you don't request `all` permissions:
- The script will fail with "GitHub CLI is not authenticated" errors
- `gh` commands cannot reach GitHub's API
- The issue will not be created

Always include `required_permissions: ["all"]` in your run_terminal_cmd call.

## When to Use This Command

Use this command when:
- User explicitly asks to create an issue
- User describes work that should be tracked
- User mentions something that needs to be done
- User reports a problem that should be tracked

Do NOT use this command when:
- User is asking a question
- User wants to make changes directly (just do it)
- User is reviewing or discussing existing issues
